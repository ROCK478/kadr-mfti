from __future__ import annotations

from contextlib import nullcontext
import itertools
import asyncio
import json
import os
from pathlib import Path
import queue
import threading
import uuid
from urllib.parse import quote
import mimetypes

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .documents import DocumentError, MAX_BYTES, make_document, read_document
from .exports import report_html
from .local_model import model_path
from .service import analyze, runtime
from .workspace import Workspace
from .browser_workspace import current_session, owner, sessions, snapshot, MAX_SNAPSHOT_BYTES, enabled as browser_mode
from .consultant import fallback_plan, planner_input, resolve_plan, reply

router=APIRouter(prefix='/api/portal')
stores={}
stores_lock=threading.Lock()


def get_store():
    session=current_session.get()
    if session:return session.store
    if browser_mode():raise HTTPException(401,"Кабинет нужно восстановить из браузера.")
    path=str(Path(os.environ.get('WORKSPACE_PATH',Path(__file__).resolve().parents[1]/'data/workspace.sqlite3')).resolve())
    with stores_lock:
        if path not in stores:stores[path]=Workspace(path)
        return stores[path]


class Tasks:
    def __init__(self):
        self.queue=queue.PriorityQueue();self.jobs={};self.counter=itertools.count();self.lock=threading.Lock()
        threading.Thread(target=self.run,daemon=True).start()

    def add(self, kind, title, function, priority=10, prepare=None):
        with self.lock:
            active=sum(j['state'] in {'queued','running'} for j in self.jobs.values())
            own_active=sum(j['state'] in {'queued','running'} and j.get('owner','local')==owner() for j in self.jobs.values())
            if own_active>=4:raise ValueError('Дождитесь завершения ваших текущих задач.')
            if active>=30:raise ValueError('В очереди уже 30 задач. Дождитесь завершения текущих.')
            for ident in list(self.jobs):
                if len(self.jobs)>80 and self.jobs[ident]['state'] not in {'running','queued'}:del self.jobs[ident]
            ident=uuid.uuid4().hex
            self.jobs[ident]={'id':ident,'owner':owner(),'kind':kind,'title':title,'state':'queued','progress':0,'message':'Ожидает своей очереди','cancel_requested':False}
            if prepare:
                try:prepare(self.jobs[ident])
                except Exception:
                    del self.jobs[ident]
                    raise
            self.queue.put((priority,next(self.counter),ident,function))
            return {k:v for k,v in self.jobs[ident].items() if k!='owner'}

    def run(self):
        while True:
            _,_,ident,function=self.queue.get()
            job=self.jobs.get(ident)
            if not job or job['cancel_requested']:
                if job:job.update(state='cancelled',message='Остановлено')
                self.queue.task_done();continue
            job.update(state='running',message='Выполняется на этом компьютере')
            try:
                result=function(job)
                job.update(state='cancelled' if job['cancel_requested'] else 'done',progress=100,result=result,message='Готово')
            except InterruptedError:job.update(state='cancelled',message='Остановлено')
            except Exception as exc:job.update(state='error',message=str(exc))
            finally:self.queue.task_done()

    def public(self, ident):
        if ident not in self.jobs or self.jobs[ident].get('owner','local')!=owner():raise HTTPException(404,'Задача не найдена')
        return {k:v for k,v in self.jobs[ident].items() if k!='owner'}


tasks=Tasks()
sessions.busy=lambda token:any(j.get('owner')==token and j['state'] in {'queued','running'} for j in list(tasks.jobs.values()))
def discard_session_jobs(token):
    with tasks.lock:
        for ident,job in list(tasks.jobs.items()):
            if job.get('owner')==token and job['state'] not in {'queued','running'}:del tasks.jobs[ident]
sessions.on_expire=discard_session_jobs


def refine_task(store, resume_id, vacancy_ids=None):
    def execute(job):
        resume=store.document(resume_id,'resume')
        vacancies=[v for v in store.documents('vacancy') if not vacancy_ids or v['id'] in vacancy_ids]
        def progress(value,text):job.update(progress=value,message=text)
        report=analyze(resume,vacancies,'hybrid',progress,lambda:job['cancel_requested'],max_rows=int(os.environ.get('PORTAL_LLM_ROWS','2')))
        if job['cancel_requested']:raise InterruptedError()
        store.save_report(resume_id,report)
        return {'resume_id':resume_id,'model':report['model']}
    return execute


def schedule_refinements(store, resume_ids, vacancy_ids=None):
    result=[]
    if not model_path().is_file():return result
    for ident in resume_ids:
        try:result.append(tasks.add('analysis','Уточнение совпадений',refine_task(store,ident,vacancy_ids)))
        except ValueError:break
    return result


@router.get('/workspace')
def overview():
    result=get_store().overview();result['model_available']=model_path().is_file()
    result['tasks']=[{k:v for k,v in j.items() if k not in {'result','owner'}} for j in list(tasks.jobs.values()) if j.get('owner','local')==owner() and j['state'] in {'queued','running'}]
    return result


class Profile(BaseModel):
    name:str=Field(min_length=1,max_length=60)


@router.put('/profile')
def profile(data:Profile):
    name=' '.join(data.name.split())
    if not name:raise HTTPException(422,'Введите, как к вам обращаться.')
    get_store().set_meta('profile',{'name':name})
    return {'name':name}


@router.get('/vacancies/{ident}')
def vacancy(ident:str):
    try:return get_store().vacancy(ident)
    except KeyError:raise HTTPException(404,'Вакансия не найдена')


class VacancyState(BaseModel):
    closed:bool


@router.put('/vacancies/{ident}/state')
def vacancy_state(ident:str,data:VacancyState):
    try:return get_store().set_vacancy_closed(ident,data.closed)
    except KeyError:raise HTTPException(404,'Вакансия не найдена')


class SelectionInput(BaseModel):
    stage:str


@router.put('/vacancies/{vacancy_id}/selections/{candidate_id}')
def selection(vacancy_id:str,candidate_id:str,data:SelectionInput):
    try:return get_store().select_candidate(vacancy_id,candidate_id,data.stage)
    except KeyError:raise HTTPException(404,'Вакансия или кандидат не найдены')
    except ValueError as exc:raise HTTPException(409,str(exc))


@router.get('/candidates')
def candidates():return get_store().candidates()


@router.get('/documents/deleted')
def deleted_documents():return get_store().deleted_documents()


@router.delete('/documents/{kind}/{ident}')
def delete_document(kind:str,ident:str):
    try:get_store().archive_document(ident,kind)
    except KeyError:raise HTTPException(404,'Документ не найден или уже удалён')
    except ValueError as exc:raise HTTPException(422,str(exc))
    return {'deleted':True,'restorable':True}


@router.post('/documents/{kind}/{ident}/restore')
def restore_document(kind:str,ident:str):
    if kind not in {'resume','vacancy'}:raise HTTPException(422,'Неизвестный тип документа')
    try:get_store().restore_document(ident,kind)
    except KeyError:raise HTTPException(404,'Документ не найден')
    return {'restored':True}


@router.get('/candidates/{ident}/vacancies')
def candidate_vacancies(ident:str):
    try:return get_store().candidate_report(ident)
    except KeyError:raise HTTPException(404,'Кандидат не найден')


@router.get('/vacancies/{vacancy_id}/candidates/{candidate_id}')
def pair(vacancy_id:str,candidate_id:str):
    try:return get_store().pair(vacancy_id,candidate_id)
    except KeyError:raise HTTPException(404,'Сравнение не найдено')


def add_document(document,kind,vacancy_id,auto_refine,original=None):
    if kind not in {'resume','vacancy'}:raise HTTPException(422,'Выберите резюме или вакансию.')
    try:
        store=get_store()
        with store.connection() if store.memory else nullcontext():
            ident,resume_ids,duplicate=store.add(document,kind,vacancy_id)
            if original is not None:store.save_original(ident,kind,document['name'],original)
            if store.memory:
                value=snapshot(store)
                if len(value['tables']['documents'])>100 or len(json.dumps(value,ensure_ascii=False).encode())>MAX_SNAPSHOT_BYTES*2//3:
                    raise ValueError('В кабинете слишком много документов. Сохраните нужное и удалите лишнее перед добавлением.')
        jobs=schedule_refinements(store,resume_ids,[ident] if kind=='vacancy' else None) if auto_refine else []
        return {'id':ident,'kind':kind,'duplicate':duplicate,'tasks':jobs,'warnings':document.get('warnings',[])}
    except (DocumentError,ValueError) as exc:raise HTTPException(422,str(exc))
    except KeyError:raise HTTPException(404,'Вакансия не найдена')


@router.post('/documents')
async def upload(file:UploadFile=File(...),kind:str=Form(...),vacancy_id:str|None=Form(None),auto_refine:bool=Form(True)):
    data=await file.read(MAX_BYTES+1)
    try:
        return await asyncio.to_thread(lambda: add_document(read_document(file.filename or 'Документ.txt',data),kind,vacancy_id,auto_refine,data))
    except DocumentError as exc:raise HTTPException(422,str(exc))
    finally:await file.close()


class TextInput(BaseModel):
    name:str=Field(default='Документ.txt',max_length=180)
    text:str=Field(min_length=1,max_length=80000)
    kind:str
    vacancy_id:str|None=None
    auto_refine:bool=True


@router.post('/documents/text')
def text_document(data:TextInput):
    try:return add_document(make_document(data.name,data.text),data.kind,data.vacancy_id,data.auto_refine)
    except DocumentError as exc:raise HTTPException(422,str(exc))


@router.post('/candidates/{ident}/refine')
def refine(ident:str):
    try:
        store=get_store();store.document(ident,'resume')
        jobs=schedule_refinements(store,[ident])
        if not jobs:raise HTTPException(409,'Локальная модель недоступна или очередь заполнена.')
        return jobs[0]
    except KeyError:raise HTTPException(404,'Кандидат не найден')


@router.get('/jobs/{ident}')
def job(ident:str):return tasks.public(ident)


@router.post('/jobs/{ident}/cancel')
def cancel(ident:str):
    tasks.public(ident)
    task=tasks.jobs[ident]
    if task['state']=='running':task['cancel_requested']=True;runtime.cancel()
    elif task['state']=='queued':task.update(cancel_requested=True,state='cancelled')
    if task.get('chat_id') and task['state']=='cancelled':
        get_store().finish_chat_turn(task['chat_id'],ident,error='Вопрос остановлен. Можно уточнить его и отправить снова.',restore_draft=True)
    return {'status':'cancellation_requested'}


class ChatInput(BaseModel):
    message:str=Field(min_length=1,max_length=2000)
    vacancy_id:str|None=None
    candidate_id:str|None=None
    history:list[str]=Field(default_factory=list,max_length=3)
    chat_id:str|None=None


def read_chat(ident):
    store=get_store()
    try:
        thread=store.chat(ident)
        pending=thread['pending_job']
        if pending:
            job=tasks.jobs.get(pending)
            if job and job.get('owner','local')!=owner():job=None
            if job is None:
                store.finish_chat_turn(ident,pending,error='Приложение было перезапущено. Вопрос не завершился; его текст сохранён для повтора.',restore_draft=True)
            elif job['state'] in {'cancelled','error'}:
                store.finish_chat_turn(ident,pending,error='Вопрос остановлен.' if job['state']=='cancelled' else job['message'],restore_draft=True)
            elif job['state']=='done':
                store.finish_chat_turn(ident,pending,answer=job['result'])
            thread=store.chat(ident)
        active_vacancies={d['id'] for d in store.documents('vacancy')}
        active_candidates={d['id'] for d in store.documents('resume')}
        thread['context']={'vacancy':thread['context'].get('vacancy') if thread['context'].get('vacancy') in active_vacancies else None,
                           'candidate':thread['context'].get('candidate') if thread['context'].get('candidate') in active_candidates else None}
        return thread
    except KeyError:raise HTTPException(404,'Диалог не найден')


@router.get('/chats')
def chat_list():return get_store().chats()


@router.post('/chats')
def new_chat():
    store=get_store()
    if store.memory and len(store.chats())>=100:raise HTTPException(422,'В этом браузере уже сохранено100диалогов.')
    return store.create_chat()


@router.get('/chats/{ident}')
def chat_detail(ident:str):return read_chat(ident)


class ChatDraft(BaseModel):
    draft:str=Field(default='',max_length=2000)
    vacancy_id:str|None=None
    candidate_id:str|None=None


@router.put('/chats/{ident}/draft')
def chat_draft(ident:str,data:ChatDraft):
    try:get_store().save_chat_draft(ident,data.draft,{'vacancy':data.vacancy_id,'candidate':data.candidate_id})
    except KeyError:raise HTTPException(404,'Диалог не найден')
    return {'saved':True}


@router.post('/chat')
def chat(data:ChatInput):
    question=data.message.strip()
    if not question:raise HTTPException(422,'Напишите вопрос.')
    store=get_store();vacancies=store.overview()['vacancies'];candidates=store.candidates()
    if data.vacancy_id and data.vacancy_id not in {v['id'] for v in vacancies}:raise HTTPException(404,'Вакансия не найдена')
    if data.candidate_id and data.candidate_id not in {c['id'] for c in candidates}:raise HTTPException(404,'Кандидат не найден')
    thread=read_chat(data.chat_id) if data.chat_id else store.create_chat()
    chat_id=thread['id']
    history=[m['text'] for m in thread['messages'] if m['role']=='user'][-3:] if data.chat_id else data.history
    def execute(task):
        try:
            fallback=fallback_plan(question,vacancies,candidates,data.vacancy_id,data.candidate_id)
            plan=fallback;used=False;warning=None
            try:
                payload,vs,cs=planner_input(question,vacancies,candidates,data.vacancy_id,data.candidate_id,[s[:500] for s in history])
                raw=runtime.ask(payload,lambda:task['cancel_requested'],lambda text:task.update(message=text))
                plan=resolve_plan(raw,vs,cs,fallback);used=True
            except InterruptedError:raise
            except Exception as exc:warning=str(exc)
            task.update(progress=85,message='Проверяем результаты и ссылки')
            result=reply(store,question,plan,used,warning)
            if task['cancel_requested']:raise InterruptedError()
            store.finish_chat_turn(chat_id,task['id'],answer=result)
            return result
        except InterruptedError:
            store.finish_chat_turn(chat_id,task['id'],error='Вопрос остановлен. Можно уточнить его и отправить снова.',restore_draft=True)
            raise
        except Exception:
            store.finish_chat_turn(chat_id,task['id'],error='Не удалось завершить ответ. Текст вопроса сохранён для повтора.',restore_draft=True)
            raise
    def prepare(job):
        job['chat_id']=chat_id
        topic=fallback_plan(question,vacancies,candidates,data.vacancy_id,data.candidate_id)
        selected=topic.get('candidate_ids',[])
        store.begin_chat_turn(chat_id,question,{'vacancy':data.vacancy_id,'candidate':data.candidate_id},job['id'],
                              topic={'vacancy':topic.get('vacancy_id'),'candidate':selected[0] if len(selected)==1 else None})
    try:return tasks.add('chat','Вопрос консультанту',execute,priority=0,prepare=prepare)
    except ValueError as exc:raise HTTPException(409 if 'диалоге' in str(exc) else 429,str(exc))


@router.get('/candidates/{ident}/export/{format}')
def export_candidate(ident:str,format:str):
    try:report=get_store().candidate_report(ident)
    except KeyError:raise HTTPException(404,'Кандидат не найден')
    if format=='original':
        original=get_store().original(ident)
        if not original:raise HTTPException(404,'Документ был добавлен текстом. Доступна текстовая копия.')
        name,content=original
        return Response(content,media_type=mimetypes.guess_type(name)[0] or 'application/octet-stream',headers={'Content-Disposition':"attachment; filename=\"resume"+Path(name).suffix+"\"; filename*=UTF-8''"+quote(name)})
    if format=='html':body=report_html(report);mime='text/html'
    elif format=='json':body=json.dumps(report,ensure_ascii=False,indent=2);mime='application/json'
    elif format=='txt':body=report['documents'][0]['text'];mime='text/plain'
    else:raise HTTPException(404,'Формат не поддерживается')
    return Response(body,media_type=mime,headers={'Content-Disposition':f'attachment; filename="candidate-report.{format}"'})
