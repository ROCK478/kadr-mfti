"""Regression with a real model: an inflected name must keep resume→vacancies."""
import argparse,json,time
from pathlib import Path
import httpx

p=argparse.ArgumentParser();p.add_argument('--url',default='http://localhost:8080');args=p.parse_args()
client=httpx.Client(base_url=args.url,timeout=60,trust_env=False)
def get(path):
 r=client.get('/api/portal'+path);r.raise_for_status();return r.json()
candidate=next(c for c in get('/candidates') if c['name'].startswith('Иванов'))
started=time.monotonic();r=client.post('/api/portal/chat',json={'message':'Какие вакансии можно предложить Иванову и почему?'})
r.raise_for_status();job=r.json();print('Real model job',job['id'],flush=True)
for _ in range(1200):
 result=get('/jobs/'+job['id'])
 if result['state'] not in {'queued','running'}:break
 time.sleep(.4)
assert result['state']=='done',result
answer=result['result']
assert answer['model_used'] and answer['intent']=='rank_vacancies',answer
assert len(answer['links'])>=3
assert all(link['candidate_id']==candidate['id'] for link in answer['links']),answer
assert answer['links'][0]['vacancy_id']==get('/candidates/'+candidate['id']+'/vacancies')['vacancies'][0]['id']
assert answer['context']['candidate_id']==candidate['id']
for link in answer['links']:get(f"/vacancies/{link['vacancy_id']}/candidates/{link['candidate_id']}")
record={'seconds':round(time.monotonic()-started,2),'question':'Какие вакансии можно предложить Иванову и почему?','result':answer}
target=Path(__file__).resolve().parents[1]/'benchmarks/results-portal';target.mkdir(parents=True,exist_ok=True)
(target/'ivanov-chat.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
print('PASS: actual local model + direction/name guard; Ivanov only; alternative vacancy ranking, citations and working links;',record['seconds'],'seconds',flush=True)
