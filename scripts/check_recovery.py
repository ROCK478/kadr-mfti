"""Cancel a real local inference, then analyze novel documents in the same service."""
import argparse
import json
from pathlib import Path
import time

import httpx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='http://localhost:8080')
    args = parser.parse_args()
    client = httpx.Client(base_url=args.url, timeout=30, trust_env=False)

    def post(path, **kwargs):
        result = client.post(path, **kwargs)
        result.raise_for_status()
        return result.json()

    resume = post('/api/documents/text', json={
        'name': 'Новый профиль.txt',
        'text': 'Frontend-разработчик. Создавал компоненты каталога на Vue 3. Использовал props, emit и v-model.'})
    vacancy = post('/api/documents/text', json={
        'name': 'Новая роль.txt',
        'text': 'Разработчик интерфейсов\nТребования:\nПрактический опыт Vue 3 и Nuxt.'})
    payload = {'resume_id': resume['id'], 'vacancy_ids': [vacancy['id']], 'mode': 'hybrid'}

    def status(ident):
        result = client.get('/api/analyses/' + ident)
        result.raise_for_status()
        return result.json()

    def wait(ident, predicate, seconds=300):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            job = status(ident)
            if predicate(job):
                return job
            time.sleep(.15)
        raise TimeoutError('Сервис не достиг ожидаемого состояния')

    first = post('/api/analyses', json=payload)
    active = wait(first['id'], lambda j: j['progress'] >= 38 or j['state'] != 'running')
    assert active['state'] == 'running', active
    time.sleep(1)
    post('/api/analyses/' + first['id'] + '/cancel')
    stopped = wait(first['id'], lambda j: j['state'] != 'running', seconds=30)
    assert stopped['state'] == 'cancelled', stopped
    print('PASS: cancelled actual hybrid analysis', flush=True)

    started = time.monotonic()
    second = post('/api/analyses', json=payload)
    done = wait(second['id'], lambda j: j['state'] != 'running')
    assert done['state'] == 'done', done
    report = done['result']
    assert report['model']['used'] and report['model']['checked_rows'] == 1, report['model']
    assert not report['model']['warnings'], report['model']
    assert report['vacancies'][0]['rows'][0]['status'] == 'partial', report['vacancies']
    sources = {d['id']: d['text'] for d in report['documents']}
    for row in report['vacancies'][0]['rows']:
        for source in [row['vacancy_source']] + row['resume_sources']:
            assert source['quote'] in sources[source['document_id']]
    result = {'cancelled': True, 'retry': 'done', 'seconds': round(time.monotonic()-started, 2),
              'report': report}
    target = Path(__file__).resolve().parents[1] / 'benchmarks/results-recovery'
    target.mkdir(exist_ok=True, parents=True)
    (target/'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print('PASS: retry with new local-model process and novel documents; partial evidence preserved', flush=True)


if __name__ == '__main__':
    main()
