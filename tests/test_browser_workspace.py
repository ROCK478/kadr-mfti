import copy
import time

import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.browser_workspace import sessions
from app import portal


@pytest.fixture
def client(monkeypatch,tmp_path):
    monkeypatch.setenv('BROWSER_WORKSPACE','1')
    path=tmp_path/'must-not-be-created.sqlite3';monkeypatch.setenv('WORKSPACE_PATH',str(path))
    sessions.clear()
    with TestClient(app) as c:yield c
    assert not path.exists()
    sessions.clear()


def open_browser(client,snapshot=None):
    response=client.post('/api/portal/session',json={'snapshot':snapshot})
    assert response.status_code==200,response.text
    return {'X-Workspace-Session':response.json()['token']}


def test_speech_discovery_is_stateless_before_opening_a_browser_session(client):
    response=client.get('/api/portal/speech')
    assert response.status_code==200
    assert response.json()['processing']=='local'
    assert {'stt','tts','max_seconds'} <= response.json().keys()
    assert not sessions.entries
    assert client.get('/api/portal/workspace').status_code==401
    assert client.get('/api/portal/speech-extra').status_code==401


def test_browsers_isolate_documents_selections_archives_and_history(client):
    a=open_browser(client);b=open_browser(client)
    data=client.get('/api/portal/workspace',headers=a).json()
    vid=data['vacancies'][0]['id'];cid=data['vacancies'][0]['candidates'][0]['id']
    chat=client.post('/api/portal/chats',headers=a).json()
    assert client.get('/api/portal/chats/'+chat['id'],headers=b).status_code==404
    assert client.put(f'/api/portal/vacancies/{vid}/selections/{cid}',headers=a,json={'stage':'selected'}).status_code==200
    client.put(f'/api/portal/vacancies/{vid}/state',headers=a,json={'closed':True})
    client.delete('/api/portal/documents/resume/'+cid,headers=a)
    added=client.post('/api/portal/documents/text',headers=a,json={'kind':'vacancy','name':'Личная роль.txt','text':'Личная роль frontend\nТребования:\nЗнание Vue и CSS. Опыт разработки интерфейсов.','auto_refine':False}).json()
    assert client.get('/api/portal/vacancies/'+added['id'],headers=b).status_code==404
    other=client.get('/api/portal/workspace',headers=b).json()
    assert len(other['vacancies'])==3 and other['candidate_count']==4 and not other['closed_vacancies']
    assert all(not v['selections'] for v in other['vacancies'])
    assert client.get('/api/portal/chats',headers=b).json()==[]
    assert client.get('/api/portal/documents/deleted',headers=b).json()==[]
    assert client.get('/api/portal/workspace').status_code==401
    assert client.post('/api/documents/text',json={'text':'test'}).status_code==404


def test_browser_snapshot_restores_after_server_restart_with_originals(client):
    a=open_browser(client)
    data=client.get('/api/portal/workspace',headers=a).json();vid=data['vacancies'][0]['id'];cid=data['vacancies'][0]['candidates'][0]['id']
    original=client.get(f'/api/portal/candidates/{cid}/export/original',headers=a).content
    client.put('/api/portal/profile',headers=a,json={'name':'Только мой браузер'})
    client.put(f'/api/portal/vacancies/{vid}/selections/{cid}',headers=a,json={'stage':'contacted'})
    client.put(f'/api/portal/vacancies/{vid}/state',headers=a,json={'closed':True})
    chat=client.post('/api/portal/chats',headers=a).json()
    client.put('/api/portal/chats/'+chat['id']+'/draft',headers=a,json={'draft':'Вопрос после перезапуска','candidate_id':cid})
    response=client.get('/api/portal/session/snapshot',headers=a);saved=response.json()
    assert client.get('/api/portal/session/snapshot',headers={**a,'If-None-Match':response.headers['etag']}).status_code==304
    sessions.clear()
    assert client.get('/api/portal/workspace',headers=a).status_code==401
    restored=open_browser(client,saved)
    data=client.get('/api/portal/workspace',headers=restored).json()
    assert data['profile']['name']=='Только мой браузер'
    assert len(data['vacancies'])==2 and data['closed_vacancies'][0]['selections'][0]['stage']=='contacted'
    assert client.get('/api/portal/chats/'+chat['id'],headers=restored).json()['draft']=='Вопрос после перезапуска'
    assert client.get(f'/api/portal/candidates/{cid}/export/original',headers=restored).content==original
    assert client.get('/api/portal/workspace',headers=open_browser(client)).json()['profile'] is None


def test_live_session_is_not_overwritten_by_stale_tab_snapshot(client):
    a=open_browser(client);snapshot=client.get('/api/portal/session/snapshot',headers=a).json()
    client.put('/api/portal/profile',headers=a,json={'name':'Новое имя'})
    r=client.post('/api/portal/session',json={'token':a['X-Workspace-Session'],'snapshot':snapshot})
    assert r.json()['token']==a['X-Workspace-Session']
    assert client.get('/api/portal/workspace',headers=a).json()['profile']['name']=='Новое имя'


def test_jobs_cannot_be_read_cancelled_or_injected_into_another_browser_chat(client):
    a=open_browser(client);b=open_browser(client)
    own=a['X-Workspace-Session'];ident='private-qa-job'
    portal.tasks.jobs[ident]={'id':ident,'owner':own,'kind':'analysis','state':'running','cancel_requested':False,'result':{'private':'value'}}
    try:
        assert client.get('/api/portal/jobs/'+ident,headers=a).json()['result']['private']=='value'
        assert client.get('/api/portal/jobs/'+ident,headers=b).status_code==404
        assert client.post('/api/portal/jobs/'+ident+'/cancel',headers=b).status_code==404
        assert not portal.tasks.jobs[ident]['cancel_requested']
        assert client.get('/api/portal/workspace',headers=b).json()['tasks']==[]
        assert 'owner' not in client.get('/api/portal/workspace',headers=a).json()['tasks'][0]
    finally:portal.tasks.jobs.pop(ident)


def test_bad_snapshot_is_rejected_without_creating_an_empty_replacement(client):
    a=open_browser(client);value=client.get('/api/portal/session/snapshot',headers=a).json()
    bad=copy.deepcopy(value);bad['tables']['documents; DROP TABLE chats']=[]
    assert client.post('/api/portal/session',json={'snapshot':bad}).status_code==422
    assert len(sessions.entries)==1
    assert client.get('/api/portal/workspace',headers=a).json()['candidate_count']==4


def test_oversized_import_rolls_back_instead_of_losing_browser_restorability(client,monkeypatch):
    a=open_browser(client)
    monkeypatch.setattr(portal,'MAX_SNAPSHOT_BYTES',100)
    r=client.post('/api/portal/documents/text',headers=a,json={'kind':'resume','name':'Слишком большой кабинет.txt','text':'Новый кандидат. Опыт разработки на Vue и CSS. Разработка интерфейсов.','auto_refine':False})
    assert r.status_code==422
    assert client.get('/api/portal/workspace',headers=a).json()['candidate_count']==4


def test_expiration_keeps_running_work_then_discards_completed_private_results(client,monkeypatch):
    monkeypatch.setenv('BROWSER_SESSION_TTL','1')
    a=open_browser(client);token=a['X-Workspace-Session'];ident='expiration-qa-job'
    sessions.entries[token].touched=time.monotonic()-10
    portal.tasks.jobs[ident]={'id':ident,'owner':token,'kind':'analysis','state':'running','cancel_requested':False}
    try:
        open_browser(client)
        assert token in sessions.entries
        portal.tasks.jobs[ident].update(state='done',result={'private':'data'})
        sessions.entries[token].touched=time.monotonic()-10
        open_browser(client)
        assert token not in sessions.entries and ident not in portal.tasks.jobs
    finally:portal.tasks.jobs.pop(ident,None)
