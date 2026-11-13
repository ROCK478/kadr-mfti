import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.workspace import Workspace
from app.documents import make_document
from app.interview import interview_tasks


def test_pool_sorted_by_best_score_and_places(tmp_path):
    store=Workspace(tmp_path/'workspace.sqlite3')
    candidates=store.candidates()
    scores=[c['best_vacancy']['score'] for c in candidates]
    assert scores==sorted(scores,reverse=True)
    assert 'Семенов' in candidates[0]['name'] and candidates[0]['best_vacancy']['score']==96.8
    assert [c['rank'] for c in candidates]==list(range(1,len(candidates)+1))


@pytest.mark.parametrize('kind',['resume','vacancy'])
def test_soft_deletion_survives_reopen_and_restores_sources(tmp_path,kind):
    path=tmp_path/'workspace.sqlite3'
    store=Workspace(path)
    document=store.documents(kind)[0]
    original=store.original(document['id'],kind)
    store.archive_document(document['id'],kind)
    reopened=Workspace(path)
    assert len(reopened.documents(kind))==(3 if kind=='resume' else 2)
    with pytest.raises(KeyError):reopened.document(document['id'],kind)
    assert any(item['id']==document['id'] for item in reopened.deleted_documents())
    assert all(c['id']!=document['id'] for v in reopened.overview()['vacancies'] for c in v['candidates'])
    reopened.restore_document(document['id'],kind)
    assert reopened.document(document['id'],kind)==document
    assert reopened.original(document['id'],kind)==original
    assert len(reopened.candidates())==4
    assert len(reopened.overview()['vacancies'])==3
    assert all(len(reopened.candidate_report(c['id'])['vacancies'])==3 for c in reopened.candidates())


def test_restore_calculates_pairs_added_while_deleted(tmp_path):
    store=Workspace(tmp_path/'workspace.sqlite3')
    vacancy=store.documents('vacancy')[0]
    store.archive_document(vacancy['id'],'vacancy')
    document=make_document('Проверка.txt','Проверочный кандидат\nFrontend-разработчик. Vue 3, HTML, CSS, Tailwind. Разработка компонентов.')
    ident,_,_=store.add(document,'resume')
    assert len(store.candidate_report(ident)['vacancies'])==2
    store.restore_document(vacancy['id'],'vacancy')
    assert len(store.candidate_report(ident)['vacancies'])==3
    assert any(c['id']==ident for c in store.vacancy(vacancy['id'])['candidates'])


def test_deletion_api_clears_live_context_but_preserves_conversation(tmp_path,monkeypatch):
    monkeypatch.setenv('WORKSPACE_PATH',str(tmp_path/'workspace.sqlite3'))
    with TestClient(app) as client:
        candidate=client.get('/api/portal/candidates').json()[0]
        chat=client.post('/api/portal/chats').json()
        client.put('/api/portal/chats/'+chat['id']+'/draft',json={'candidate_id':candidate['id'],'draft':'Черновик'})
        response=client.delete('/api/portal/documents/resume/'+candidate['id'])
        assert response.json()=={'deleted':True,'restorable':True}
        assert client.get('/api/portal/chats/'+chat['id']).json()['context']['candidate'] is None
        assert client.get('/api/portal/chats/'+chat['id']).json()['draft']=='Черновик'
        assert client.get('/api/portal/candidates/'+candidate['id']+'/vacancies').status_code==404
        assert client.get('/api/portal/documents/deleted').json()[0]['id']==candidate['id']
        assert client.post('/api/portal/documents/resume/'+candidate['id']+'/restore').status_code==200


def test_questions_are_practical_diverse_and_do_not_change_scores(tmp_path):
    store=Workspace(tmp_path/'workspace.sqlite3')
    for candidate in store.candidates():
        report=store.candidate_report(candidate['id'])
        for value in report['vacancies']:
            tasks=value['interview_questions']
            assert tasks and len({t['question'] for t in tasks})==len(tasks)
            assert all(t['listen_for'] and t['requirement_id'] in {r['requirement_id'] for r in value['rows']} for t in tasks)
            assert not any('Приведите конкретный пример из вашей работы по пункту' in t['question'] for t in tasks)
    vue=interview_tasks('Опыт коммерческой разработки на Vue.js.','none')[0]
    assert 'props' in vue['question'] and 'v-model' in vue['question']
    assert vue['follow_up'].startswith('Если')
    tools=interview_tasks('Опыт работы с Pinia, Nuxt, Monaco Editor.','partial')
    assert {t['topic'] for t in tools}=={'pinia','nuxt','monaco'}
