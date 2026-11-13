from __future__ import annotations

import copy
import os
import re

from .documents import citation, normalize
from .local_model import MODEL_NAME, Runtime
from .matching import (NEGATION, INTENT, build_report, candidates_for, concepts,
                       durations, match_requirement, question, requirements, DETAILS)

runtime = Runtime()


def refine(row, req, resume, raw):
    status = raw.get("status")
    blocks = {b["id"]: b for b in resume["blocks"]}
    ids = raw.get("evidence_ids", [])
    selected = [blocks[i] for i in ids if isinstance(i, str) and i in blocks] if isinstance(ids, list) else []
    if status not in {"full", "partial", "none", "missing"}:
        raise ValueError("Модель вернула неизвестный статус")
    if status != "missing" and not selected:
        raise ValueError("Модель не указала проверяемое основание")
    evidence = " ".join(b["text"] for b in selected)
    wanted = set(req["concepts"])
    seen = concepts(evidence)
    # Keep deterministic, directly quoted contradictions and numeric constraints.
    if row["status"] == "none":
        return row
    missing_statement = re.search(r"не\s+(?:указан|описан|упомянут)|нет\s+(?:информации|сведений|данных|примеров)", normalize(evidence))
    if status == "none" and (not NEGATION.search(normalize(evidence)) or missing_statement or INTENT.search(evidence)):
        status = "partial" if row["status"] == "partial" else "missing"
    if status == "full" and INTENT.search(evidence):
        status = "missing"
    exact = wanted & {"vue", "tailwind", "pinia", "nuxt", "monaco", "sql", "postgresql", "python", "powerbi", "excel", "sheets"}
    if status == "full" and exact - seen:
        status = "partial"
    if status == "full" and row.get("subcriteria", {}).get("not_stated"):
        if any(not re.search(DETAILS[name], normalize(evidence)) for name in row["subcriteria"]["not_stated"]):
            status = "partial"
    if status == "full" and durations(req["text"]) and "Срок именно" in row["comment"]:
        status = row["status"]
    if "own_pc" in wanted and "own_pc" not in seen:
        status = "missing"
    result = copy.deepcopy(row)
    for key in ('status_before_components','match_fraction','unknown_fraction','clarification','component_evidence','comment_base','comment_lead','comment_focus','comment_tail'):
        result.pop(key,None)
    result.update(status=status, method="локальная Qwen3 + проверка оснований",
                  resume_sources=[citation(resume, b) for b in selected],
                  comment=str(raw.get("reason", "Вывод локальной модели требует проверки.")),
                  question=question(req, status))
    if status != raw.get("status"):
        result["comment"] = "Вывод модели ограничен проверкой источников: подтверждены не все части или отсутствует прямое основание. " + row["comment"]
    return result


def analyze(resume, vacancies, mode="hybrid", progress=lambda p,t: None, cancelled=lambda: False, max_rows=None):
    progress(8, "Выделяем требования и профессиональные факты…")
    rows_by_vacancy = {}
    candidates = []
    for vacancy in vacancies:
        reqs = vacancy.get("requirements") or requirements(vacancy)
        if not reqs:
            raise ValueError(f"В «{vacancy['name']}» не найдены требования. Добавьте явный раздел «Требования» или исправьте текст.")
        if len(reqs) > 80:
            raise ValueError("В одной вакансии более 80 требований. Разделите документ на отдельные вакансии.")
        vacancy["requirements"] = reqs
        rows = [match_requirement(r, resume) for r in reqs]
        rows_by_vacancy[vacancy["id"]] = rows
        for index, (req, row) in enumerate(zip(reqs, rows)):
            evidence = candidates_for(req, resume, 5)
            if row["status"] in {"partial", "missing"} and evidence and row["relevance"] > 0.08 and "own_pc" not in req["concepts"]:
                candidates.append((row["weight"] + row["relevance"], vacancy["id"], index, req, [b for _, b in evidence]))
    if cancelled():
        raise InterruptedError("Разбор отменён")
    progress(35, "Построена карта требований. Рассчитываем подтверждения…")
    warnings = []
    checked = 0
    requested = []
    if mode == "hybrid":
        budget = int(os.environ.get("LLM_MAX_ROWS", "6")) if max_rows is None else max_rows
        selected = sorted(candidates, key=lambda c: -c[0])[:budget]
        for i, (_, vid, index, req, blocks) in enumerate(selected):
            if cancelled():
                raise InterruptedError("Разбор отменён")
            progress(38 + int(48 * i / max(1, len(selected))), f"Локальный ИИ проверяет спорный пункт {i+1} из {len(selected)}…")
            try:
                raw = runtime.ask({"requirement": req["text"], "blocks": blocks}, cancelled,
                                  lambda text: progress(38, text))
                rows_by_vacancy[vid][index] = refine(rows_by_vacancy[vid][index], req, resume, raw)
                requested.append(req["id"])
                checked += 1
            except InterruptedError:
                raise
            except Exception as exc:
                warnings.append(str(exc))
                if "отсутствует" in str(exc) or "не установлена" in str(exc):
                    break
    progress(93, "Собираем рейтинг, источники и вопросы рекрутеру…")
    result = build_report(resume, vacancies, rows_by_vacancy, {
        "mode": mode, "name": MODEL_NAME if mode == "hybrid" else None,
        "used": checked > 0, "checked_rows": checked, "checked_requirement_ids": requested,
        "total_rows": sum(len(r) for r in rows_by_vacancy.values()),
        "warnings": list(dict.fromkeys(warnings)),
        "note": "Локальная модель уточняет ограниченное число неоднозначных пунктов; остальные обрабатывает общий словарь и проверяемые правила."
    })
    progress(100, "Разбор завершён")
    return result
