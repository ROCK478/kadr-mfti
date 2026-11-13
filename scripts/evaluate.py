"""Manual-expectation checks on all supplied files, not a general accuracy claim."""
import argparse
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app.documents import read_document
from app.matching import build_report
from app.service import analyze,runtime


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--with-model',action='store_true');parser.add_argument('--output',type=Path,default=ROOT/'benchmarks/results')
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    docs=[read_document(p.name,p.read_bytes()) for p in (ROOT/'examples/documents').glob('*.docx')]
    vacancies=[d for d in docs if d['name'].startswith('Вакансия')]
    expectations={'Иванов':'Вакансия 3.docx','Петров Иван':'Вакансия 1.docx','Семенов':'Вакансия 1.docx','Петрова':'Вакансия 2.docx'}
    summary=[];start=time.perf_counter()
    try:
        for resume in [d for d in docs if d not in vacancies]:
            mode='hybrid' if args.with_model else 'rules'
            report=analyze(resume,vacancies,mode,lambda p,t:print(p,t,flush=True))
            expected=next(value for prefix,value in expectations.items() if resume['name'].startswith(prefix))
            passed=report['vacancies'][0]['name']==expected
            print(resume['name'],[(v['name'],v['score']) for v in report['vacancies']],passed,flush=True)
            summary.append({'resume':resume['name'],'expected_top':expected,'actual_top':report['vacancies'][0]['name'],'passed':passed,'model':report['model']})
            (args.output/(resume['id']+'.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        result={'mode':mode,'resumes':4,'vacancies':3,'pairs':12,'seconds':round(time.perf_counter()-start,2),'expectations':summary,
                'note':'Ожидания размечены вручную на выданном наборе. Это не общая точность на новых данных.'}
        (args.output/'summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        assert all(x['passed'] for x in summary),summary
    finally:runtime.cancel()


if __name__=='__main__':main()
