import html
import json

from .matching import STATUSES, PRIORITIES


def report_html(report):
    esc = lambda s: html.escape(str(s))
    parts = ["<!doctype html><html lang='ru'><meta charset='utf-8'><meta name='viewport' content='width=device-width'>",
             "<title>Кадр · разбор вакансий</title><style>body{font:16px system-ui;color:#1c2933;max-width:1200px;margin:40px auto;padding:0 20px}"
             "table{border-collapse:collapse;width:100%;font-size:14px}td,th{border:1px solid #d4ddd7;padding:14px;text-align:left;vertical-align:top}"
             "th{background:#eef2f5}blockquote{margin:10px 0;padding:12px;background:#f2f4ee}h2{margin-top:40px}small{color:#52616d}</style>",
             "<h1>Кадр · разбор вакансий</h1>", f"<p>Резюме: {esc(report['resume']['name'])}</p>",
             f"<p>{esc(report['recommendation'])}</p><p>{esc(report['disclaimer'])}</p>",
             "<p>Единые веса: обязательное 3, задача роли 1, желательное 1. Полное = 1; составные пункты — по подтверждённым частям, базовое знакомство — половина доли инструмента; прочее частичное = 0,5, "
             "явное несоответствие = 0. «Не указано» выделяется отдельно, расширяет диапазон и снижает покрытие данных.</p>"]
    facts = report['resume'].get('facts', {})
    parts += ['<h2>Профессиональные факты</h2>']
    for key, label in [('experience', 'Опыт'), ('skills', 'Навыки'), ('education', 'Образование')]:
        parts.append(f'<h3>{label}</h3><ul>')
        for fact in facts.get(key, []):
            parts.append(f"<li>{esc(fact['text'])}<br><small>{esc(fact['source']['document_name'])} · фрагмент {fact['source']['block_index']}</small></li>")
        parts.append('</ul>')
    for v in report['vacancies']:
        parts += [f"<h2>{v['rank']}. {esc(v['title'])}</h2><p>{esc(v['ranking_reason'])}</p>",
                  f"<p>Подтверждённый индекс {v['score']}; верхняя граница при подтверждении неизвестного {v['upper']}. "
                  f"Покрытие {v['coverage']}%. Это не вероятность успеха.</p>",
                  "<table><tr><th>Требование</th><th>Статус</th><th>Подтверждение и комментарий</th></tr>"]
        for row in v['rows']:
            quotes = ''.join(f"<blockquote>{esc(s['quote'])}<br><small>{esc(s['document_name'])} · фрагмент {s['block_index']}</small></blockquote>" for s in row['resume_sources'])
            parts.append(f"<tr><td>{esc(row['requirement'])}<p><small>{PRIORITIES[row['priority']]}, вес {row['weight']}</small></p>"
                         f"<details><summary>Источник вакансии</summary>{esc(row['vacancy_source']['quote'])}</details></td>"
                         f"<td>{STATUSES[row['status']]}</td><td>{quotes or 'Подтверждение не найдено'}<p>{esc(row['comment'])}</p>"
                         f"<p>{esc(row['question'])}</p></td></tr>")
        parts += ['</table><h3>Сильные стороны</h3><ul>']
        parts += [f"<li>{esc(row['requirement'])}</li>" for row in v['strengths']]
        parts += ['</ul><h3>Пробелы, риски и неизвестные сведения</h3><ul>']
        parts += [f"<li>{STATUSES[row['status']]}: {esc(row['requirement'])}<br>{esc(row['comment'])}</li>" for row in v['gaps']+v['unknowns']]
        parts += ["</ul><h3>Вопросы для разговора</h3><ul>", *[f"<li>{esc(q)}</li>" for q in v['questions']], "</ul>"]
    parts += ["<h2>Черновик сообщения</h2>", f"<pre style='white-space:pre-wrap'>{esc(report['communication'])}</pre>", "</html>"]
    return '\n'.join(parts)
