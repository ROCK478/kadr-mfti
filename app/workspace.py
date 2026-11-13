"""Local, durable HR workspace. Evidence calculations stay independent of UI groups."""
from __future__ import annotations

import json
from contextlib import contextmanager
import os
from pathlib import Path
import re
import sqlite3
import threading
import unicodedata
import uuid

from .documents import facts, normalize, read_document, professional_blocks
from .matching import build_report, requirements, finalize
from .service import analyze

ROOT = Path(__file__).resolve().parents[1]
GROUPS = {
    'strong': 'Высокое соответствие',
    'partial': 'Частичное соответствие',
    'mismatch': 'Есть несоответствия',
    'unknown': 'Недостаточно данных',
}
GROUP_ORDER = {name: index for index, name in enumerate(GROUPS)}


def candidate_name(document):
    name = unicodedata.normalize('NFC', document['name'])
    name = re.sub(r'_?резюме\s*\d*', '', name, flags=re.I)
    name = re.sub(r'\.(?:docx|pdf|txt|md)$', '', name, flags=re.I).replace('_', ' ').strip()
    if not name or normalize(name) in {'resume', 'cv', 'резюме', 'документ', 'текст документа'}:
        name = document['text'].splitlines()[0].strip()
    return name[:100] or 'Кандидат'


def category(value):
    """Four dossier groups are a summary, never a replacement for row statuses."""
    rows = value['rows']
    if value['coverage'] < 25 or not any(r['status'] in {'full', 'partial'} for r in rows):
        return 'unknown' if not value['required_gaps'] else 'mismatch'
    if value['required_gaps']:
        return 'mismatch'
    if value['score'] >= 75 and value['required_unknown'] == 0:
        return 'strong'
    return 'partial'


class Workspace:
    def __init__(self, path, *, initialize=True):
        self.memory = str(path) == ":memory:"
        self.path = Path(path)
        if not self.memory:self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self._depth = threading.local()
        if self.memory:
            self._memory_connection = sqlite3.connect(":memory:",check_same_thread=False)
            self._memory_connection.row_factory = sqlite3.Row
            self._memory_connection.execute("PRAGMA temp_store=MEMORY")
        with self.connection() as connection:
            connection.executescript('''
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS documents (id TEXT, kind TEXT, body TEXT NOT NULL,
                PRIMARY KEY(id, kind));
            CREATE TABLE IF NOT EXISTS applications (vacancy_id TEXT, resume_id TEXT, source TEXT NOT NULL,
                PRIMARY KEY(vacancy_id, resume_id));
            CREATE TABLE IF NOT EXISTS originals (id TEXT, kind TEXT, filename TEXT NOT NULL, data BLOB NOT NULL, PRIMARY KEY(id, kind));
            CREATE TABLE IF NOT EXISTS evaluations (vacancy_id TEXT, resume_id TEXT, body TEXT NOT NULL,
                model TEXT NOT NULL, PRIMARY KEY(vacancy_id, resume_id));
            CREATE TABLE IF NOT EXISTS chats (id TEXT PRIMARY KEY, title TEXT NOT NULL,
                body TEXT NOT NULL, position INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS archived_documents (id TEXT, kind TEXT, PRIMARY KEY(id,kind));
            CREATE TABLE IF NOT EXISTS vacancy_lifecycle (vacancy_id TEXT PRIMARY KEY, closed INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS selections (vacancy_id TEXT, resume_id TEXT, stage TEXT NOT NULL,
                PRIMARY KEY(vacancy_id,resume_id));
            ''')
        if initialize:
            self.seed()
            self.seed_originals()
            self.refresh_chat_titles()

    @contextmanager
    def connection(self):
        if self.memory:
            with self.lock:
                depth=getattr(self._depth,'value',0)
                self._depth.value=depth+1
                try:
                    if depth:
                        yield self._memory_connection
                    else:
                        with self._memory_connection:
                            yield self._memory_connection
                finally:self._depth.value=depth
            return
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def close(self):
        if self.memory:
            with self.lock:self._memory_connection.close()


    def save_original(self, ident, kind, filename, data):
        with self.lock, self.connection() as connection:
            connection.execute('INSERT OR IGNORE INTO originals VALUES (?,?,?,?)', (ident, kind, filename, data))

    def original(self, ident, kind='resume'):
        with self.connection() as connection:
            row=connection.execute('SELECT filename,data FROM originals WHERE id=? AND kind=?',(ident,kind)).fetchone()
        return (row['filename'],row['data']) if row else None

    def seed_originals(self):
        for path in (ROOT/'examples/documents').glob('*.docx'):
            document=read_document(path.name,path.read_bytes())
            kind='vacancy' if path.name.startswith('Вакансия') else 'resume'
            try:self.document(document['id'],kind)
            except KeyError:continue
            if not self.original(document['id'],kind):self.save_original(document['id'],kind,path.name,path.read_bytes())

    def get_meta(self, key, default=None):
        with self.connection() as connection:
            row = connection.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
        return json.loads(row['value']) if row else default

    def set_meta(self, key, value):
        with self.lock, self.connection() as connection:
            connection.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', (key, json.dumps(value, ensure_ascii=False)))

    def chats(self):
        with self.connection() as connection:
            rows = connection.execute('SELECT id,title FROM chats ORDER BY position DESC, rowid DESC').fetchall()
        return [dict(row) for row in rows]

    def chat_title(self, context):
        parts = []
        candidate = context.get('candidate') or context.get('candidate_id')
        vacancy = context.get('vacancy') or context.get('vacancy_id')
        if candidate:
            try:parts.append(candidate_name(self.document(candidate, 'resume', include_deleted=True)))
            except KeyError:pass
        if vacancy:
            try:parts.append(self.document(vacancy, 'vacancy', include_deleted=True)['text'].splitlines()[0])
            except KeyError:pass
        return ' · '.join(parts) or 'Подбор персонала'

    def refresh_chat_titles(self):
        """Migrate former question titles using the actual first resolved subject."""
        with self.lock, self.connection() as connection:
            rows = connection.execute('SELECT id,body FROM chats').fetchall()
            for row in rows:
                body = json.loads(row['body'])
                if not body['messages']:
                    continue
                topic = body.get('topic') or next((m['answer'].get('context') for m in body['messages']
                         if m.get('answer', {}).get('context')), None) or body['context']
                body['topic'] = topic
                connection.execute('UPDATE chats SET title=?,body=? WHERE id=?',
                                   (self.chat_title(topic), json.dumps(body, ensure_ascii=False), row['id']))

    def create_chat(self):
        ident = uuid.uuid4().hex
        body = {'messages': [], 'context': {'vacancy': None, 'candidate': None}, 'draft': '', 'pending_job': None}
        with self.lock, self.connection() as connection:
            position = connection.execute('SELECT COALESCE(MAX(position),0)+1 FROM chats').fetchone()[0]
            connection.execute('INSERT INTO chats VALUES (?,?,?,?)', (ident, 'Новый диалог', json.dumps(body), position))
        return {'id': ident, 'title': 'Новый диалог', **body}

    def chat(self, ident):
        with self.connection() as connection:
            row = connection.execute('SELECT title,body FROM chats WHERE id=?', (ident,)).fetchone()
        if row is None:
            raise KeyError('Диалог не найден')
        return {'id': ident, 'title': row['title'], **json.loads(row['body'])}

    def save_chat_draft(self, ident, draft, context):
        with self.lock, self.connection() as connection:
            row = connection.execute('SELECT body FROM chats WHERE id=?', (ident,)).fetchone()
            if row is None:
                raise KeyError('Диалог не найден')
            body = json.loads(row['body'])
            if not body['pending_job']:
                body.update(draft=draft, context=context)
                connection.execute('UPDATE chats SET body=? WHERE id=?', (json.dumps(body, ensure_ascii=False), ident))

    def begin_chat_turn(self, ident, question, context, job_id, topic=None):
        with self.lock, self.connection() as connection:
            row = connection.execute('SELECT title,body FROM chats WHERE id=?', (ident,)).fetchone()
            if row is None:
                raise KeyError('Диалог не найден')
            body = json.loads(row['body'])
            if body['pending_job']:
                raise ValueError('В этом диалоге уже готовится ответ. Дождитесь его или остановите.')
            title = row['title']
            if not body['messages']:
                body['topic'] = topic or context
                title = self.chat_title(body['topic'])
            body['messages'].append({'role': 'user', 'text': question})
            body.update(context=context, draft='', pending_job=job_id)
            position = connection.execute('SELECT COALESCE(MAX(position),0)+1 FROM chats').fetchone()[0]
            connection.execute('UPDATE chats SET title=?,body=?,position=? WHERE id=?',
                               (title, json.dumps(body, ensure_ascii=False), position, ident))

    def finish_chat_turn(self, ident, job_id, answer=None, error=None, restore_draft=False):
        with self.lock, self.connection() as connection:
            row = connection.execute('SELECT title,body FROM chats WHERE id=?', (ident,)).fetchone()
            if row is None:
                raise KeyError('Диалог не найден')
            body = json.loads(row['body'])
            if body['pending_job'] != job_id:
                return  # A late completion cannot overwrite a different turn.
            title = row['title']
            if answer is not None:
                first_answer = not any(m.get('answer') for m in body['messages'])
                body['messages'].append({'role': 'assistant', 'answer': answer})
                context = answer.get('context')
                if context:
                    body['context'] = {'vacancy': context.get('vacancy_id'), 'candidate': context.get('candidate_id')}
                    if first_answer and any(context.values()):
                        body['topic'] = context
                        title = self.chat_title(context)
            else:
                body['messages'].append({'role': 'assistant', 'error': True, 'text': error or 'Не удалось завершить ответ.'})
                if restore_draft:
                    body['draft'] = next((m['text'] for m in reversed(body['messages']) if m['role'] == 'user'), '')
            body['pending_job'] = None
            connection.execute('UPDATE chats SET title=?,body=? WHERE id=?', (title, json.dumps(body, ensure_ascii=False), ident))

    def documents(self, kind):
        with self.connection() as connection:
            rows = connection.execute('SELECT d.body FROM documents d WHERE d.kind=? AND NOT EXISTS '
                                      '(SELECT 1 FROM archived_documents a WHERE a.id=d.id AND a.kind=d.kind) ORDER BY d.rowid', (kind,)).fetchall()
        return [json.loads(row['body']) for row in rows]

    def document(self, ident, kind, include_deleted=False):
        with self.connection() as connection:
            row = connection.execute('SELECT d.body FROM documents d WHERE d.id=? AND d.kind=? ' +
                ('' if include_deleted else 'AND NOT EXISTS (SELECT 1 FROM archived_documents a WHERE a.id=d.id AND a.kind=d.kind)'), (ident, kind)).fetchone()
        if row is None:
            raise KeyError('Документ не найден')
        return json.loads(row['body'])

    def is_closed(self, vacancy_id):
        with self.connection() as connection:
            row=connection.execute('SELECT closed FROM vacancy_lifecycle WHERE vacancy_id=?',(vacancy_id,)).fetchone()
        return bool(row and row['closed'])

    def open_vacancies(self):
        return [v for v in self.documents('vacancy') if not self.is_closed(v['id'])]

    def set_vacancy_closed(self, vacancy_id, closed):
        with self.lock, self.connection() as connection:
            self.document(vacancy_id,'vacancy')
            connection.execute('INSERT INTO vacancy_lifecycle VALUES (?,?) ON CONFLICT(vacancy_id) DO UPDATE SET closed=excluded.closed',
                               (vacancy_id,int(closed)))
        return self.vacancy(vacancy_id)

    def selections(self, vacancy_id):
        with self.connection() as connection:
            rows=connection.execute('SELECT resume_id,stage FROM selections WHERE vacancy_id=? ORDER BY rowid',(vacancy_id,)).fetchall()
        items=[]
        for row in rows:
            # Keep historical people visible after removal from the active pool.
            resume=self.document(row['resume_id'],'resume',include_deleted=True)
            try:self.document(row['resume_id'],'resume');deleted=False
            except KeyError:deleted=True
            items.append({'id':resume['id'],'name':candidate_name(resume),'stage':row['stage'],'deleted':deleted})
        return items

    def select_candidate(self, vacancy_id, resume_id, stage):
        if stage not in {'selected','contacted','remove'}:raise ValueError('Неизвестный этап кандидата')
        with self.lock, self.connection() as connection:
            self.document(vacancy_id,'vacancy')
            if self.is_closed(vacancy_id):raise ValueError('Сначала верните вакансию в работу')
            self.document(resume_id,'resume')
            if stage=='remove':
                connection.execute('DELETE FROM selections WHERE vacancy_id=? AND resume_id=?',(vacancy_id,resume_id))
            else:
                # A repeated selection must never erase the contact confirmation.
                connection.execute('INSERT INTO selections VALUES (?,?,?) ON CONFLICT(vacancy_id,resume_id) DO UPDATE SET '
                                   "stage=CASE WHEN excluded.stage='contacted' THEN excluded.stage ELSE selections.stage END",
                                   (vacancy_id,resume_id,stage))
                connection.execute('INSERT OR IGNORE INTO applications VALUES (?,?,?)',(vacancy_id,resume_id,'Выбран HR'))
        return self.selections(vacancy_id)

    def archive_document(self, ident, kind):
        if kind not in {'resume','vacancy'}:raise ValueError('Неизвестный тип документа')
        with self.lock, self.connection() as connection:
            self.document(ident, kind)
            connection.execute('INSERT OR IGNORE INTO archived_documents VALUES (?,?)',(ident,kind))

    def deleted_documents(self):
        with self.connection() as connection:
            rows=connection.execute('SELECT d.id,d.kind,d.body FROM documents d JOIN archived_documents a '
                                    'ON a.id=d.id AND a.kind=d.kind ORDER BY a.rowid DESC').fetchall()
        return [{'id':row['id'],'kind':row['kind'],'name':candidate_name(json.loads(row['body'])) if row['kind']=='resume'
                 else json.loads(row['body'])['text'].splitlines()[0]} for row in rows]

    def restore_document(self, ident, kind):
        with self.lock:
            self.document(ident,kind,include_deleted=True)
            with self.connection() as connection:
                connection.execute('DELETE FROM archived_documents WHERE id=? AND kind=?',(ident,kind))
            # Documents added while this entry was deleted have no stored pair yet.
            resumes=[self.document(ident,'resume')] if kind=='resume' else self.documents('resume')
            vacancies=[self.document(ident,'vacancy')] if kind=='vacancy' else self.documents('vacancy')
            for resume in resumes:
                for vacancy in vacancies:
                    try:self.evaluation(vacancy['id'],resume['id'])
                    except KeyError:
                        self.save_report(resume['id'],analyze(resume,[vacancy],'rules'))
                        self.link(vacancy['id'],resume['id'],'Подбор из базы')

    def _write_document(self, document, kind):
        with self.connection() as connection:
            connection.execute('INSERT OR IGNORE INTO documents VALUES (?,?,?)',
                               (document['id'], kind, json.dumps(document, ensure_ascii=False)))

    def save_report(self, resume_id, report):
        with self.lock, self.connection() as connection:
            for vacancy in report['vacancies']:
                model = dict(report['model'])
                checked = [ident for ident in model.get('checked_requirement_ids', []) if ident.startswith(vacancy['id']+':')]
                model.update(checked_requirement_ids=checked, checked_rows=len(checked), used=bool(checked), total_rows=len(vacancy['rows']))
                connection.execute('INSERT OR REPLACE INTO evaluations VALUES (?,?,?,?)',
                    (vacancy['id'], resume_id, json.dumps(vacancy, ensure_ascii=False),
                     json.dumps(model, ensure_ascii=False)))

    def link(self, vacancy_id, resume_id, source):
        with self.connection() as connection:
            connection.execute('INSERT OR REPLACE INTO applications VALUES (?,?,?)', (vacancy_id, resume_id, source))

    def seed(self):
        with self.lock:
            if self.get_meta('seeded', False):
                return
            supplied = [read_document(path.name, path.read_bytes()) for path in sorted((ROOT/'examples/documents').glob('*.docx'))]
            vacancies = [d for d in supplied if d['name'].startswith('Вакансия')]
            resumes = [d for d in supplied if not d['name'].startswith('Вакансия')]
            for document in vacancies:
                document['requirements'] = requirements(document)
                self._write_document(document, 'vacancy')
            for resume in resumes:
                self._write_document(resume, 'resume')
                self.save_report(resume['id'], analyze(resume, vacancies, 'rules'))
                for vacancy in vacancies:
                    self.link(vacancy['id'], resume['id'], 'Учебный пример')
            self.set_meta('seeded', True)

    def add(self, document, kind, vacancy_id=None):
        with self.lock:
            if any(d['id']==document['id'] and d['kind']==kind for d in self.deleted_documents()):
                raise ValueError('Документ уже находится в удалённых. Восстановите его через корзину.')
            existing = self.documents(kind)
            duplicate = any(d['id'] == document['id'] for d in existing)
            if not duplicate and len(existing) >= (20 if kind == 'vacancy' else 100):
                raise ValueError('Достигнут лимит локального кабинета: 20 вакансий и 100 резюме.')
            if duplicate:
                if kind=='resume' and vacancy_id:
                    self.document(vacancy_id, 'vacancy')
                    self.link(vacancy_id, document['id'], 'Добавлено HR')
                return document['id'], [], True
            if kind == 'vacancy':
                document['requirements'] = requirements(document)
                if not document['requirements']:
                    raise ValueError('Не удалось выделить требования. Добавьте в документ раздел «Требования» и загрузите его снова.')
                if len(document['requirements']) > 80:
                    raise ValueError('В одной вакансии не более 80 требований. Разделите документ.')
                self._write_document(document, kind)
                resumes = self.documents('resume')
                for resume in resumes:
                    self.save_report(resume['id'], analyze(resume, [document], 'rules'))
                    self.link(document['id'], resume['id'], 'Подбор из базы')
                return document['id'], [r['id'] for r in resumes], duplicate
            vacancies = self.documents('vacancy')
            if vacancy_id and vacancy_id not in {v['id'] for v in vacancies}:
                raise KeyError('Вакансия не найдена')
            if vacancy_id and self.is_closed(vacancy_id):raise ValueError('Сначала верните вакансию в работу')
            self._write_document(document, kind)
            if vacancies:
                self.save_report(document['id'], analyze(document, vacancies, 'rules'))
                for vacancy in vacancies:
                    if not vacancy_id or vacancy_id == vacancy['id']:
                        self.link(vacancy['id'], document['id'], 'Добавлено HR' if vacancy_id else 'Подбор из базы')
            return document['id'], [document['id']], duplicate

    def evaluation(self, vacancy_id, resume_id):
        with self.connection() as connection:
            row = connection.execute('SELECT body, model FROM evaluations WHERE vacancy_id=? AND resume_id=?', (vacancy_id, resume_id)).fetchone()
        if row is None:
            raise KeyError('Сравнение не найдено')
        value=json.loads(row['body'])
        value.update(finalize(self.document(vacancy_id,'vacancy'),value['rows']))
        value['ranking_reason']=f"Подтверждено {value['credit']:g} из {value['weights_total']} взвешенных пунктов: индекс {value['score']} из 100, покрытие данных {value['coverage']}%."
        return value, json.loads(row['model'])

    def candidate_summary(self, resume, value=None):
        professional = facts(resume)
        # Only an explicit total next to the resume's experience heading is a
        # total. A single job's 10 months must not stand for a 14-year career.
        duration_match = None
        blocks = professional_blocks(resume)
        pattern = r'(?<![\d])\d+\s*(?:лет|года?|месяц\w*)(?:\s+\d+\s*месяц\w*)?'
        for index, block in enumerate(blocks):
            if not re.match(r'^опыт\s+работы(?:\s*[:—-]|\s*$)', normalize(block['text'])):
                continue
            direct = re.search(pattern, block['text'], re.I)
            if direct:
                duration_match = direct
                break
            if index+1<len(blocks) and re.match(r'^\d+\s*(?:лет|года?|месяц)', blocks[index+1]['text'], re.I):
                duration_match = re.search(pattern, blocks[index+1]['text'], re.I)
                break
        experience_label = 'В резюме: ' + duration_match[0] if duration_match else 'Опыт описан в резюме' if professional['experience'] else 'Уточните опыт'
        result = {'id': resume['id'], 'experience_label': experience_label, 'name': candidate_name(resume), 'filename': resume['name'],
                  'experience': [f['text'] for f in professional['experience'][:2]],
                  'education': [f['text'] for f in professional['education'][:2]],
                  'skills': [f['text'] for f in professional['skills'][:2]]}
        if value:
            group = category(value)
            result.update(score=value['score'], coverage=value['coverage'], upper=value['upper'],
                required_gaps=value['required_gaps'], required_unknown=value['required_unknown'],
                group=group, group_label=GROUPS[group],
                strengths=[r['requirement'] for r in value['strengths'][:2]],
                questions=value['questions'][:2], counts=value['counts'])
        return result

    def vacancy(self, vacancy_id):
        vacancy = self.document(vacancy_id, 'vacancy')
        with self.connection() as connection:
            applications = connection.execute('SELECT resume_id, source FROM applications WHERE vacancy_id=? ORDER BY rowid', (vacancy_id,)).fetchall()
        candidates = []
        for application in applications:
            try:resume = self.document(application['resume_id'], 'resume')
            except KeyError:continue
            value, model = self.evaluation(vacancy_id, resume['id'])
            result = self.candidate_summary(resume, value)
            result.update(source=application['source'], model=model)
            candidates.append(result)
        candidates.sort(key=lambda c: (GROUP_ORDER[c['group']], -c['score'], c['name']))
        for i, candidate in enumerate(candidates):
            candidate['rank'] = i+1
            candidate['ranking_reason'] = f"Группа «{candidate['group_label']}», затем подтверждённый индекс {candidate['score']} из 100. Сведения есть по {candidate['coverage']}% требований с учётом весов."
        counts = {name: sum(c['group'] == name for c in candidates) for name in GROUPS}
        return {'id': vacancy['id'], 'title': vacancy['text'].splitlines()[0], 'filename': vacancy['name'],
                'requirements': vacancy['requirements'], 'requirement_count': len(vacancy['requirements']),
                'candidates': candidates, 'counts': counts, 'application_count': len(candidates),
                'document': vacancy, 'closed':self.is_closed(vacancy_id), 'selections':self.selections(vacancy_id)}

    def overview(self):
        vacancies = []
        for document in self.documents('vacancy'):
            vacancy = self.vacancy(document['id'])
            vacancies.append({k: v for k, v in vacancy.items() if k not in {'document', 'requirements'}})
        return {'profile': self.get_meta('profile'), 'vacancies': [v for v in vacancies if not v['closed']],
                'closed_vacancies':[v for v in vacancies if v['closed']],
                'candidate_count': len(self.documents('resume')), 'groups': GROUPS,
                'application_count': sum(v['application_count'] for v in vacancies)}

    def candidate_report(self, resume_id):
        resume = self.document(resume_id, 'resume')
        vacancies = self.open_vacancies()
        rows = {}
        model_rows = 0
        for vacancy in vacancies:
            value, model = self.evaluation(vacancy['id'], resume_id)
            rows[vacancy['id']] = value['rows']
            model_rows += model.get('checked_rows', 0)
        return build_report(resume, vacancies, rows, {'mode': 'workspace', 'used': model_rows > 0,
            'checked_rows': model_rows, 'total_rows': sum(len(r) for r in rows.values()), 'warnings': [],
            'note': 'Те же попарные результаты, отсортированные по единой формуле вакансий для одного резюме.'})

    def pair(self, vacancy_id, resume_id):
        resume = self.document(resume_id, 'resume')
        vacancy = self.document(vacancy_id, 'vacancy')
        value, model = self.evaluation(vacancy_id, resume_id)
        return {'candidate': self.candidate_summary(resume, value), 'vacancy': value,
                'resume_document': resume, 'vacancy_document': vacancy,
                'facts': facts(resume), 'model': model, 'closed':self.is_closed(vacancy_id),
                'selection':next((s for s in self.selections(vacancy_id) if s['id']==resume_id),None), 'has_original': self.original(resume_id) is not None}

    def candidates(self):
        results = []
        for resume in self.documents('resume'):
            report = self.candidate_report(resume['id'])
            item = self.candidate_summary(resume)
            item['best_vacancy'] = {k: report['vacancies'][0][k] for k in ['id', 'title', 'score']} if report['vacancies'] else None
            results.append(item)
        results.sort(key=lambda item: (-(item['best_vacancy']['score'] if item['best_vacancy'] else -1),item['name']))
        for rank,item in enumerate(results,1):item['rank']=rank
        return results
