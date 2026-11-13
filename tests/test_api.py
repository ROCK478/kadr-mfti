import io
import time
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from app.main import app, documents, jobs


@pytest.fixture
def client():
    documents.clear(); jobs.clear()
    with TestClient(app) as c:yield c


def test_health_and_static_page(client):
    assert client.get('/health').json()['external_services']==[]
    page=client.get('/')
    assert page.status_code==200 and 'кадр' in page.text.lower() and 'name-form' in page.text
    assert "connect-src 'self'" in page.headers['content-security-policy']
    assert client.get('/static/app.js').status_code==200


def test_corrupt_upload_has_actionable_error(client):
    r=client.post('/api/documents',files={'file':('resume.docx',b'broken','application/octet-stream')})
    assert r.status_code==422 and 'повреждён' in r.json()['detail']


def test_cross_origin_upload_is_rejected(client):
    r=client.post('/api/documents/text',headers={'origin':'https://external.example'},json={'name':'x.txt','text':'a'*40})
    assert r.status_code==403


def test_end_to_end_api_ranking_and_both_exports(client):
    examples=client.get('/api/examples').json()
    candidate=next(x for x in examples['resumes'] if x['name'].startswith('Иванов'))
    resume=client.post('/api/examples/'+candidate['key']).json()
    vac=[client.post('/api/examples/'+v['key']).json() for v in examples['vacancies']]
    start=client.post('/api/analyses',json={'resume_id':resume['id'],'vacancy_ids':[v['id'] for v in vac],'mode':'rules'})
    assert start.status_code==200
    ident=start.json()['id']
    deadline=time.monotonic()+10
    while time.monotonic()<deadline:
        job=client.get('/api/analyses/'+ident).json()
        if job['state']!='running':break
        time.sleep(.02)
    assert job['state']=='done',job
    assert job['result']['vacancies'][0]['name']=='Вакансия 3.docx'
    html=client.get(f'/api/analyses/{ident}/export/html')
    raw=client.get(f'/api/analyses/{ident}/export/json')
    assert html.status_code==raw.status_code==200
    assert 'attachment' in html.headers['content-disposition']
    assert 'Требование' in html.text and 'vacancies' in raw.json()
    assert client.delete('/api/session').json()['status']=='cleared'
    assert client.get('/api/analyses/'+ident).status_code==404


def test_input_and_model_failure_are_not_silently_accepted(client):
    assert client.post('/api/documents/text',json={'text':'  '}).status_code==422
    assert client.post('/api/analyses',json={'resume_id':'missing','vacancy_ids':['missing'],'mode':'cloud'}).status_code==422


def test_html_export_escapes_uploaded_markup():
    from app.documents import make_document
    from app.matching import build_report
    from app.exports import report_html
    resume=make_document('x.txt','<script>alert(1)</script>\nОпыт Python и работа с данными в pandas.')
    vacancy=make_document('y.txt','Новая вакансия\nТребования:\nПрактический опыт Python для обработки данных.')
    output=report_html(build_report(resume,[vacancy]))
    assert '<script>' not in output
