"""Check the running HR workspace, source fidelity and real local-model chat."""
import argparse
import hashlib
import json
from pathlib import Path
import time
import httpx

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--url',default='http://localhost:8080');parser.add_argument('--with-model',action='store_true');args=parser.parse_args()
    client=httpx.Client(base_url=args.url,timeout=60,trust_env=False)
    if client.get('/api/portal/session/config').json()['browser_storage']:
        response=client.post('/api/portal/session',json={});response.raise_for_status()
        client.headers['X-Workspace-Session']=response.json()['token']
    def get(path):
        result=client.get('/api/portal'+path);result.raise_for_status();return result.json()
    def post(path,data):
        result=client.post('/api/portal'+path,json=data);result.raise_for_status();return result.json()
    def wait(ident):
        deadline=time.monotonic()+600;last=None
        while time.monotonic()<deadline:
            job=get('/jobs/'+ident)
            current=(job['state'],job['message'])
            if current!=last:print(*current,flush=True);last=current
            if job['state'] not in {'queued','running'}:return job
            time.sleep(.5)
        raise TimeoutError('Локальная задача не завершилась')
    overview=get('/workspace');assert len(overview['vacancies'])>=3 and overview['candidate_count']>=4
    candidates=get('/candidates');expected={'Иванов Алексей':'Вакансия 3.docx','Петров Иван':'Вакансия 1.docx','Петрова Лилия':'Вакансия 2.docx','Семенов Алексей':'Вакансия 1.docx'}
    for candidate in candidates:
        if candidate['name'] not in expected:continue
        report=get('/candidates/'+candidate['id']+'/vacancies')
        assert report['vacancies'][0]['name']==expected[candidate['name']],candidate
        texts={d['id']:d['text'] for d in report['documents']}
        for vacancy in report['vacancies']:
            pair=get(f"/vacancies/{vacancy['id']}/candidates/{candidate['id']}")
            assert len(pair['vacancy']['rows'])==len(vacancy['rows'])
            for row in vacancy['rows']:
                for source in [row['vacancy_source']]+row['resume_sources']:assert source['quote'] in texts[source['document_id']]
        for format in ['html','json','txt','original']:
            response=client.get(f"/api/portal/candidates/{candidate['id']}/export/{format}")
            assert response.status_code==200,(candidate,format)
            assert 'attachment' in response.headers['content-disposition']
            if format=='original':
                source=next(p for p in (ROOT/'examples/documents').glob('*.docx') if p.name==candidate['filename'])
                assert hashlib.sha256(response.content).digest()==hashlib.sha256(source.read_bytes()).digest()
    print('PASS: 4 supplied resumes, all vacancy rankings, both-source quotes, pair detail and four exports',flush=True)
    records={'candidate_count':overview['candidate_count'],'vacancy_count':len(overview['vacancies']),'model_checks':[]}
    if args.with_model:
        sem=next(c for c in candidates if c['name'].startswith('Семенов'))
        started=time.monotonic();job=post('/candidates/'+sem['id']+'/refine',{})
        result=wait(job['id']);assert result['state']=='done' and result['result']['model']['used'],result
        report=get('/candidates/'+sem['id']+'/vacancies')
        assert report['model']['used'] and report['vacancies'][0]['name']=='Вакансия 1.docx'
        records['model_checks'].append({'kind':'refine','seconds':round(time.monotonic()-started,2),'result':result})
        started=time.monotonic();job=post('/chat',{'message':'Какие альтернативные вакансии подходят Семенову и почему?','candidate_id':sem['id']})
        result=wait(job['id']);assert result['state']=='done',result
        answer=result['result'];assert answer['model_used'] and answer['intent']=='rank_vacancies' and len(answer['links'])>=3 and answer['citations'],answer
        for link in answer['links']:
            assert link['href'].startswith('#') and link['candidate_id']==sem['id']
            get(f"/vacancies/{link['vacancy_id']}/candidates/{link['candidate_id']}")
        records['model_checks'].append({'kind':'chat','seconds':round(time.monotonic()-started,2),'result':result})
        print('PASS: actual inference, saved per-vacancy provenance, chat alternatives, citations and working links',flush=True)
    target=ROOT/'benchmarks/results-portal';target.mkdir(parents=True,exist_ok=True)
    (target/'verification.json').write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__':main()
