from pathlib import Path
import time
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.workspace import Workspace, category
from app.documents import make_document
from app.consultant import planner_input, resolve_plan, fallback_plan


@pytest.fixture
def workspace(tmp_path):
    return Workspace(tmp_path/'workspace.sqlite3')


@pytest.fixture
def portal(tmp_path,monkeypatch):
    monkeypatch.setenv('WORKSPACE_PATH',str(tmp_path/'workspace.sqlite3'))
    with TestClient(app) as client:yield client


def test_seed_is_durable_and_idempotent(workspace):
    assert len(workspace.documents('vacancy'))==3
    assert len(workspace.documents('resume'))==4
    workspace.set_meta('profile',{'name':'Тестовый HR'})
    reopened=Workspace(workspace.path)
    assert reopened.get_meta('profile')['name']=='Тестовый HR'
    assert reopened.overview()['application_count']==12


def test_all_six_case_outputs_remain_available_per_candidate(workspace):
    for resume in workspace.documents('resume'):
        report=workspace.candidate_report(resume['id'])
        assert len(report['vacancies'])==3
        assert report['recommendation']
        assert [v['score'] for v in report['vacancies']]==sorted([v['score'] for v in report['vacancies']],reverse=True)
        sources={doc['id']:doc['text'] for doc in report['documents']}
        for vacancy in report['vacancies']:
            assert all(key in vacancy for key in ['strengths','gaps','unknowns','questions','communication'])
            for row in vacancy['rows']:
                assert row['status'] in {'full','partial','none','missing'}
                assert row['comment'] and row['question']
                for source in [row['vacancy_source']]+row['resume_sources']:
                    assert source['quote'] in sources[source['document_id']]


def test_import_links_only_selected_vacancy_but_compares_all(workspace):
    vacancy=workspace.documents('vacancy')[0]
    document=make_document('Мария.txt','Мария\nFrontend-разработчик. Создавала компоненты на Vue 3 и Tailwind CSS. Работала в команде.')
    ident,_,_=workspace.add(document,'resume',vacancy['id'])
    assert len(workspace.candidate_report(ident)['vacancies'])==3
    for other in workspace.overview()['vacancies']:
        found=[c for c in other['candidates'] if c['id']==ident]
        assert bool(found)==(other['id']==vacancy['id'])
    duplicate,queued,is_duplicate=workspace.add(document,'resume',vacancy['id'])
    assert is_duplicate and queued==[] and duplicate==ident
    assert len(workspace.documents('resume'))==5


def test_new_vacancy_automatically_compares_existing_pool(workspace):
    document=make_document('Дизайнер.txt','Дизайнер интерфейсов\nТребования:\nОпыт создания прототипов в Figma.\nГотовность работать в команде.')
    ident,_,_=workspace.add(document,'vacancy')
    vacancy=workspace.vacancy(ident)
    assert len(vacancy['candidates'])==4
    assert all(c['source']=='Подбор из базы' for c in vacancy['candidates'])
    for resume in workspace.documents('resume'):
        assert len(workspace.candidate_report(resume['id'])['vacancies'])==4


@pytest.mark.parametrize('score,coverage,known,gaps,unknown,expected',[
    (90,90,True,0,0,'strong'),(90,90,True,0,1,'partial'),
    (45,80,True,0,0,'partial'),(60,90,True,1,0,'mismatch'),
    (0,0,False,0,1,'unknown'),(15,20,True,0,1,'unknown'),
])
def test_dossier_groups_preserve_uncertainty(score,coverage,known,gaps,unknown,expected):
    value={'score':score,'coverage':coverage,'rows':[{'status':'partial' if known else 'missing'}],
           'required_gaps':gaps,'required_unknown':unknown}
    assert category(value)==expected


def test_portal_profile_validation_and_all_exports(portal):
    assert portal.put('/api/portal/profile',json={'name':'   '}).status_code==422
    assert portal.put('/api/portal/profile',json={'name':'Анна'}).json()['name']=='Анна'
    data=portal.get('/api/portal/workspace').json()
    assert data['profile']['name']=='Анна'
    candidate=data['vacancies'][0]['candidates'][0]
    for format in ['html','json','txt','original']:
        result=portal.get(f"/api/portal/candidates/{candidate['id']}/export/{format}")
        assert result.status_code==200 and 'attachment' in result.headers['content-disposition']
    assert portal.get('/api/portal/candidates/no-such-person/vacancies').status_code==404
    assert portal.post('/api/portal/documents',data={'kind':'resume','auto_refine':'false'},files={'file':('bad.docx',b'bad')}).status_code==422


def test_empty_or_headingless_vacancy_has_clear_error(portal):
    result=portal.post('/api/portal/documents/text',json={'kind':'vacancy','text':'Мы очень хорошая компания с прекрасным офисом и социальным пакетом.','auto_refine':False})
    assert result.status_code==422 and 'Требования' in result.json()['detail']


def test_untrusted_model_ids_do_not_become_links(workspace):
    vacancies=workspace.overview()['vacancies'];candidates=workspace.candidates()
    fallback=fallback_plan('Какие альтернативы есть для Семенова?',vacancies,candidates)
    _,vs,cs=planner_input('вопрос',vacancies,candidates,None,None,[])
    plan=resolve_plan({'intent':'rank_vacancies','vacancy':'https://evil.example','candidates':['made-up-id']},vs,cs,fallback)
    assert plan['vacancy_id'] is None
    assert plan['candidate_ids']==fallback['candidate_ids']


def test_chat_answers_from_actual_comparisons_and_safe_links(portal,monkeypatch):
    from app import portal as module
    monkeypatch.setattr(module.runtime,'ask',lambda *a,**k:{'intent':'rank_vacancies','candidates':['fabricated'],'needs_clarification':False})
    job=portal.post('/api/portal/chat',json={'message':'Какие альтернативы есть для Семенова?'}).json()
    deadline=time.monotonic()+10
    while time.monotonic()<deadline:
        result=portal.get('/api/portal/jobs/'+job['id']).json()
        if result['state'] not in {'queued','running'}:break
        time.sleep(.02)
    assert result['state']=='done',result
    answer=result['result'];assert answer['model_used']
    assert answer['links'] and all(link['href'].startswith('#') for link in answer['links'])
    assert 'Frontend' in str(answer['blocks'])
    assert answer['citations']
    context=answer['context'];assert context['candidate_id']
    followup=portal.post('/api/portal/chat',json={'message':'Какие вопросы задать кандидату?','candidate_id':context['candidate_id']}).json()
    deadline=time.monotonic()+10
    while time.monotonic()<deadline:
        second=portal.get('/api/portal/jobs/'+followup['id']).json()
        if second['state'] not in {'queued','running'}:break
        time.sleep(.02)
    assert second['state']=='done' and second['result']['intent']=='questions'
    assert second['result']['context']['candidate_id']==context['candidate_id']
    for link in answer['links']:
        assert portal.get(f"/api/portal/vacancies/{link['vacancy_id']}/candidates/{link['candidate_id']}").status_code==200


def test_supplied_original_resume_downloads_unchanged(workspace):
    resume=workspace.documents('resume')[0]
    name,content=workspace.original(resume['id'])
    source=Path(__file__).resolve().parents[1]/'examples/documents'/name
    assert content==source.read_bytes()


def test_model_provenance_counts_only_rows_of_selected_vacancy(workspace):
    resume=workspace.documents('resume')[0]
    report=workspace.candidate_report(resume['id'])
    chosen=report['vacancies'][0]
    report['model']={'mode':'hybrid','used':True,'checked_rows':1,'checked_requirement_ids':[chosen['rows'][0]['requirement_id']]}
    workspace.save_report(resume['id'],report)
    for vacancy in report['vacancies']:
        _,model=workspace.evaluation(vacancy['id'],resume['id'])
        assert model['used']==(vacancy['id']==chosen['id'])
        assert model['checked_rows']==(1 if vacancy['id']==chosen['id'] else 0)
    assert workspace.candidate_report(resume['id'])['model']['checked_rows']==1


@pytest.mark.parametrize('question,surname',[
    ('Какие вакансии можно предложить Иванову и почему?','Иванов'),
    ('Какие альтернативные вакансии подходят Семенову?','Семенов'),
    ('Подбери вакансии для Петрова Ивана','Петров Иван'),
])
def test_chat_cannot_reverse_candidate_to_vacancy_direction(workspace,question,surname):
    vacancies=workspace.overview()['vacancies'];candidates=workspace.candidates()
    fallback=fallback_plan(question,vacancies,candidates)
    _,vs,cs=planner_input(question,vacancies,candidates,None,None,[])
    wrong={'intent':'rank_candidates','vacancy':'v1','candidates':['c2'],'needs_clarification':False}
    plan=resolve_plan(wrong,vs,cs,fallback)
    expected=next(c['id'] for c in candidates if c['name'].startswith(surname))
    assert plan['intent']=='rank_vacancies' and plan['candidate_ids']==[expected]
    assert not plan['needs_clarification']



def test_short_job_period_is_not_presented_as_total_experience(workspace):
    resume=make_document('Новый.txt','Новый кандидат\nОпыт работы\nБухгалтер\nКомпания: 10 месяцев. Проверял документы и готовил отчёты.')
    assert workspace.candidate_summary(resume)['experience_label']=='Опыт описан в резюме'


def test_explicit_experience_heading_preserves_total(workspace):
    resume=make_document('Новый.txt','Новый кандидат\nОпыт работы — 14 лет 3 месяца\nБухгалтер, проверка первичных документов.')
    assert workspace.candidate_summary(resume)['experience_label']=='В резюме: 14 лет 3 месяца'
