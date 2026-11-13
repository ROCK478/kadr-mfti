"""Run this inside `docker run --network none` with the local weights mounted."""
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.local_model import Engine

engine=Engine()
cases=[
    {'requirement':'Практический опыт создания компонентов Vue 3.',
     'blocks':[{'id':'source-positive','text':'Создавал компоненты каталога на Vue 3, использовал props, emit и v-model.'}]},
    {'requirement':'Практический опыт Tailwind CSS.',
     'blocks':[{'id':'source-negative','text':'С Tailwind CSS не работал.'}]},
]
for case,expected in zip(cases,[{'full','partial'},{'none'}]):
    result=engine.compare(case)
    print(json.dumps(result,ensure_ascii=False),flush=True)
    # This is an availability/isolation smoke check. Detailed quality checks
    # operate on the complete, source-validated product reports.
    assert result['status'] in expected,result
    assert set(result['evidence_ids']) <= {b['id'] for b in case['blocks']},result
print('PASS: actual local model, positive and negative evidence; run with OS-level network isolation.',flush=True)
