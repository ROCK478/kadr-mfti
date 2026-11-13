import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.workspace import Workspace


def test_multiple_selections_contact_archive_reopen_are_durable(tmp_path):
    path=tmp_path/'hr.sqlite3'
    store=Workspace(path)
    vacancy=store.documents('vacancy')[0]['id']
    first,second=[r['id'] for r in store.documents('resume')[:2]]
    for ident in (first,second):store.select_candidate(vacancy,ident,'selected')
    store.select_candidate(vacancy,first,'contacted')
    store.select_candidate(vacancy,first,'selected')  # double-click/retry keeps progress
    store.set_vacancy_closed(vacancy,True)
    reopened=Workspace(path)
    assert len(reopened.overview()['vacancies'])==2
    archived=reopened.overview()['closed_vacancies'][0]
    assert archived['id']==vacancy and len(archived['selections'])==2
    assert reopened.pair(vacancy,first)['selection']['stage']=='contacted'
    assert vacancy not in [v['id'] for v in reopened.candidate_report(first)['vacancies']]
    assert reopened.deleted_documents()==[]
    with pytest.raises(ValueError):reopened.select_candidate(vacancy,second,'remove')
    reopened.set_vacancy_closed(vacancy,False)
    assert len(reopened.overview()['vacancies'])==3
    assert vacancy in [v['id'] for v in reopened.candidate_report(first)['vacancies']]
    reopened.select_candidate(vacancy,second,'remove')
    assert [c['id'] for c in reopened.selections(vacancy)]==[first]


def test_deleted_people_and_vacancies_keep_hiring_history(tmp_path):
    store=Workspace(tmp_path/'hr.sqlite3')
    vacancy=store.documents('vacancy')[0]['id'];person=store.documents('resume')[0]['id']
    store.select_candidate(vacancy,person,'selected')
    store.archive_document(person,'resume')
    assert store.selections(vacancy)[0]['deleted']
    store.set_vacancy_closed(vacancy,True)
    store.archive_document(vacancy,'vacancy')
    assert not store.overview()['closed_vacancies']
    store.restore_document(vacancy,'vacancy')
    assert store.vacancy(vacancy)['closed'] and store.vacancy(vacancy)['selections'][0]['id']==person
    store.restore_document(person,'resume')
    assert not store.selections(vacancy)[0]['deleted']


def test_hiring_api_validates_context_and_preserves_selected_people(tmp_path,monkeypatch):
    monkeypatch.setenv('WORKSPACE_PATH',str(tmp_path/'hr.sqlite3'))
    with TestClient(app) as client:
        data=client.get('/api/portal/workspace').json()
        vacancy=data['vacancies'][0]['id'];person=client.get('/api/portal/candidates').json()[0]['id']
        path=f'/api/portal/vacancies/{vacancy}'
        assert client.put(path+'/selections/'+person,json={'stage':'invented'}).status_code==409
        assert client.put(path+'/selections/unknown',json={'stage':'selected'}).status_code==404
        assert client.put(path+'/selections/'+person,json={'stage':'selected'}).status_code==200
        assert client.put(path+'/state',json={'closed':True}).json()['closed']
        assert client.put(path+'/selections/'+person,json={'stage':'remove'}).status_code==409
        assert client.put(path+'/state',json={'closed':False}).json()['selections'][0]['id']==person
        assert client.get(path+'/candidates/'+person).json()['selection']['stage']=='selected'
