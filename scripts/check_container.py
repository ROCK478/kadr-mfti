"""Exercise the shipped HTTP service through real uploads, reports and exports."""
import argparse
import json
from pathlib import Path
import time
import httpx

ROOT=Path(__file__).resolve().parents[1]


def main():
    p=argparse.ArgumentParser();p.add_argument('--url',default='http://localhost:8080');p.add_argument('--with-model',action='store_true');p.add_argument('--output',default='benchmarks/results-container');args=p.parse_args()
    out=ROOT/args.output;out.mkdir(parents=True,exist_ok=True)
    client=httpx.Client(base_url=args.url,timeout=30,trust_env=False)
    records=[]
    def post(path,**kwargs):
        r=client.post(path,**kwargs);r.raise_for_status();return r.json()
    def wait(ident,deadline=900):
        until=time.monotonic()+deadline;last=None
        while time.monotonic()<until:
            r=client.get('/api/analyses/'+ident);r.raise_for_status();j=r.json()
            if (j['progress'],j['message'])!=last:
                print(j['progress'],j['message'],flush=True);last=(j['progress'],j['message'])
            if j['state']!='running':return j
            time.sleep(.5)
        raise TimeoutError('Container analysis timeout')
    expected={'Иванов':'Вакансия 3.docx','Петров Иван':'Вакансия 1.docx','Семенов':'Вакансия 1.docx','Петрова':'Вакансия 2.docx'}
    docs=[]
    for file in sorted((ROOT/'examples/documents').glob('*.docx')):
        docs.append(post('/api/documents',files={'file':(file.name,file.read_bytes(),'application/vnd.openxmlformats-officedocument.wordprocessingml.document')}))
    vacancies=[d for d in docs if d['name'].startswith('Вакансия')]
    resumes=[d for d in docs if not d['name'].startswith('Вакансия')]
    for resume in resumes:
        started=time.perf_counter();mode='hybrid' if args.with_model else 'rules'
        job=post('/api/analyses',json={'resume_id':resume['id'],'vacancy_ids':[d['id'] for d in vacancies],'mode':mode})
        job=wait(job['id']);assert job['state']=='done',job
        report=job['result'];top=report['vacancies'][0]['name'];target=next(v for k,v in expected.items() if resume['name'].startswith(k))
        assert top==target,(resume['name'],top,target)
        if args.with_model:assert report['model']['used'],report['model']
        source_text={d['id']:d['text'] for d in report['documents']}
        for vacancy in report['vacancies']:
            for row in vacancy['rows']:
                for source in [row['vacancy_source']]+row['resume_sources']:
                    assert source['quote'] in source_text[source['document_id']],source
        html=client.get(f"/api/analyses/{job['id']}/export/html");raw=client.get(f"/api/analyses/{job['id']}/export/json")
        assert html.status_code==raw.status_code==200
        assert 'attachment' in html.headers['content-disposition']
        (out/(resume['id']+'.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        records.append({'resume':resume['name'],'mode':mode,'top':top,'seconds':round(time.perf_counter()-started,2),'model':report['model']})
        (out/'summary.json').write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8')
        print('PASS',records[-1],flush=True)
    # Corruption must not produce an empty, apparently successful result.
    bad=client.post('/api/documents',files={'file':('bad.docx',b'broken')})
    assert bad.status_code==422,bad.text
    print('PASS: corruption and both exports; all supplied files uploaded to the running container',flush=True)


if __name__=='__main__':main()
