import time

from fastapi.testclient import TestClient
import pytest

from app.main import app
from app.workspace import Workspace
from app import portal


def test_dialogue_isolation_and_durable_answers(tmp_path):
    path = tmp_path / 'workspace.sqlite3'
    store = Workspace(path)
    first, second = store.create_chat(), store.create_chat()
    context = {'vacancy': 'v1', 'candidate': 'c1'}
    store.begin_chat_turn(first['id'], 'Какие роли подходят Иванову?', context, 'job1')
    with pytest.raises(ValueError):
        store.begin_chat_turn(first['id'], 'Другой вопрос', context, 'job2')
    answer = {'blocks': [{'type': 'text', 'text': 'Первая роль'}],
              'links': [{'href': '#candidate/c1'}], 'context': {'candidate_id': 'c1', 'vacancy_id': None}}
    store.finish_chat_turn(first['id'], 'job1', answer=answer)
    store.finish_chat_turn(first['id'], 'job1', answer={'wrong': 'late duplicate'})
    store.save_chat_draft(second['id'], 'Черновик второго разговора', {'candidate': 'c2', 'vacancy': None})
    reopened = Workspace(path)
    saved = reopened.chat(first['id'])
    assert len(saved['messages']) == 2 and saved['messages'][1]['answer'] == answer
    assert saved['context'] == {'candidate': 'c1', 'vacancy': None}
    assert saved['pending_job'] is None
    assert reopened.chat(second['id'])['messages'] == []
    assert reopened.chat(second['id'])['draft'] == 'Черновик второго разговора'
    assert reopened.chats()[0]['title'] == 'Подбор персонала'


def test_lost_job_is_recovered_once_with_question_for_retry(tmp_path, monkeypatch):
    monkeypatch.setenv('WORKSPACE_PATH', str(tmp_path / 'workspace.sqlite3'))
    store = portal.get_store()
    thread = store.create_chat()
    store.begin_chat_turn(thread['id'], 'Найди альтернативы', {'vacancy': None, 'candidate': None}, 'old-process-job')
    with TestClient(app) as client:
        recovered = client.get('/api/portal/chats/' + thread['id']).json()
        assert recovered['pending_job'] is None
        assert recovered['draft'] == 'Найди альтернативы'
        assert recovered['messages'][-1]['error'] is True
        assert len(client.get('/api/portal/chats/' + thread['id']).json()['messages']) == 2
        assert client.get('/api/portal/chats/missing').status_code == 404


def test_chat_api_saves_answer_title_and_context(tmp_path, monkeypatch):
    monkeypatch.setenv('WORKSPACE_PATH', str(tmp_path / 'workspace.sqlite3'))
    monkeypatch.setattr(portal.runtime, 'ask', lambda *args: (_ for _ in ()).throw(RuntimeError('No model in this API test')))
    with TestClient(app) as client:
        first = client.post('/api/portal/chats').json()
        second = client.post('/api/portal/chats').json()
        question = 'Какие вакансии можно предложить Иванову и почему?'
        response = client.post('/api/portal/chat', json={'chat_id': first['id'], 'message': question})
        assert response.status_code == 200
        job = response.json()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            result = client.get('/api/portal/jobs/' + job['id']).json()
            if result['state'] in {'done', 'error'}:
                break
            time.sleep(.03)
        assert result['state'] == 'done', result
        thread = client.get('/api/portal/chats/' + first['id']).json()
        assert thread['title'] == 'Иванов Алексей'
        assert thread['messages'][0]['text'] == question
        assert thread['messages'][1]['answer']['model_used'] is False
        assert thread['messages'][1]['answer']['links']
        assert thread['context']['candidate']
        assert client.get('/api/portal/chats/' + second['id']).json()['messages'] == []
        assert client.put('/api/portal/chats/' + second['id'] + '/draft', json={'draft': 'Не отправлен'}).status_code == 200
        assert client.get('/api/portal/chats/' + second['id']).json()['draft'] == 'Не отправлен'


def test_chat_titles_use_real_entities_and_migrate_old_questions(tmp_path):
    import json
    store = Workspace(tmp_path / 'workspace.sqlite3')
    candidate = store.documents('resume')[0]
    vacancy = store.documents('vacancy')[0]
    candidate_context = {'candidate': candidate['id'], 'vacancy': None}
    vacancy_context = {'candidate': None, 'vacancy': vacancy['id']}
    pair_context = {'candidate': candidate['id'], 'vacancy': vacancy['id']}
    assert store.chat_title(vacancy_context) == vacancy['text'].splitlines()[0]
    assert store.chat_title(pair_context) == store.chat_title(candidate_context) + ' · ' + store.chat_title(vacancy_context)
    thread = store.create_chat()
    store.begin_chat_turn(thread['id'], 'Первоначальный длинный вопрос', pair_context, 'job')
    store.finish_chat_turn(thread['id'], 'job', answer={'context': {'candidate_id': candidate['id'], 'vacancy_id': vacancy['id']}})
    legacy = store.chat(thread['id'])
    legacy.pop('id');legacy.pop('title');legacy.pop('topic')
    with store.connection() as connection:
        connection.execute('UPDATE chats SET title=?,body=? WHERE id=?', ('Старый вопрос', json.dumps(legacy), thread['id']))
    reopened = Workspace(store.path)
    assert reopened.chat(thread['id'])['title'] == store.chat_title(pair_context)
    assert len(reopened.chat(thread['id'])['messages']) == 2
