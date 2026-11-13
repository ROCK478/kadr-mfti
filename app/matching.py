from __future__ import annotations

import math
import re
from collections import Counter

from .documents import citation, facts, normalize, professional_blocks
from .interview import interview_tasks

STATUSES = {"full": "Полное соответствие", "partial": "Частичное соответствие",
            "none": "Нет соответствия", "missing": "Не указано"}
WEIGHTS = {"required": 3, "duty": 1, "desired": 1}
PRIORITIES = {"required": "Обязательное", "duty": "Задача роли", "desired": "Желательное"}
METHOD_VERSION = "evidence-v2-components"

# General capability lexicon, shared by every document. No filenames, candidate
# names, job IDs or hand-entered scores participate in matching.
CONCEPTS = {
    "html": [r"html(?:5)?", r"семантическ\w*\s+(?:верст|размет)"],
    "css": [r"css(?:3)?", r"flexbox", r"css.?grid", r"псевдокласс", r"позиционирован"],
    "javascript": [r"javascript", r"\bjs\b", r"es6", r"async/await", r"промис", r"\bdom\b"],
    "typescript": [r"typescript", r"\bts\b"],
    "vue": [r"vue(?:[ .]?js)?", r"composition\s*api", r"v-model"],
    "tailwind": [r"tailwind", r"утилитарн\w*\s+класс"],
    "figma": [r"figma"], "pinia": [r"pinia"], "nuxt": [r"nuxt"], "monaco": [r"monaco"],
    "ssr": [r"\bssr\b", r"серверн\w*\s+рендер"], "spa": [r"\bspa\b", r"одностраничн"],
    "python": [r"python", r"pandas"], "sql": [r"\bsql\b", r"sql-запрос"],
    "postgresql": [r"postgres(?:ql)?", r"постгрес"], "powerbi": [r"power\s*bi"],
    "excel": [r"excel", r"электронн\w*\s+таблиц", r"табличн\w*\s+данн"],
    "sheets": [r"google\s*(?:sheets|таблиц)", r"гугл.?таблиц"],
    "tables": [r"таблиц", r"excel", r"sheets"],
    "documents": [r"документ", r"делопроизвод", r"документооборот", r"акт\w*\s+прием", r"договор"],
    "reporting": [r"отчет", r"отчетност", r"статистическ\w*\s+данн", r"аналитик"],
    "communication": [r"коммуника", r"переписк", r"согласован", r"переговор", r"взаимодейств", r"заказчик", r"клиент"],
    "organization": [r"самоорганиз", r"ответственн", r"контроль\s+срок", r"планирован", r"организац\w*\s+встреч", r"сдач\w*\s+в\s+срок"],
    "detail": [r"внимательн", r"точност", r"детал", r"корректност", r"аккуратн", r"читаемост", r"код.стайл", r"рефакторинг"],
    "testing": [r"тестирован", r"тест\w*\s+интерфейс", r"vitest", r"jest", r"провер\w*\s+(?:адаптив|верст|в\s+браузер)"],
    "adaptive": [r"адаптивн", r"кроссбраузер", r"брейкпоинт", r"breakpoint"],
    "api": [r"\bapi\b", r"fetch", r"axios", r"rest"],
    "education_work": [r"(?:сфер|проект|организац|программ)\w*\s+образован", r"образовательн", r"учебн\w*\s+(?:портал|процесс|платформ)", r"онлайн.?курс", r"lms"],
    "public_sector": [r"государствен", r"муниципальн", r"ифнс", r"налогов\w*\s+(?:орган|проверк)"] ,
    "accounting": [r"бухгалтер", r"учет", r"учетн", r"1с"],
    "assets": [r"основн\w*\s+средств", r"\bос\b", r"\bтмц\b", r"складск\w*\s+учет", r"инвентаризац"],
    "inventory": [r"инвентаризац", r"инвентарн\w*\s+номер"],
    "economics": [r"экономическ", r"финансы\s+и\s+кредит", r"финансов\w*\s+(?:факультет|образован)", r"\bэкономика\b"],
    "higher": [r"высшее", r"бакалавр", r"магистр", r"специалитет"],
    "team": [r"команд", r"agile", r"scrum", r"code\s*review", r"ревью", r"дизайнер"],
    "automation": [r"автоматиз", r"автоматическ", r"по\s+расписани", r"скрипт"],
    "own_pc": [r"(?:личн|собственн)\w*\s+(?:ноутбук|компьютер|пк)"],
    "office": [r"очн\w*\s+формат", r"офис", r"на\s+месте\s+работодателя", r"расположени\w*"],
}
INTENT = re.compile(r"готов\w*\s+(?:осваивать|изучать|научиться)|готовность\s+(?:осваивать|изучать)|хочу\s+освоить", re.I)
NEGATION = re.compile(r"\bне\s+(?:работ\w*|использ\w*|примен\w*|име\w*|владе\w*)|\bнет\b|\bне\s+описан", re.I)
STOP_WORDS = {"опыт", "работы", "работа", "навыки", "наличие", "уверенное", "хорошее", "уровне", "знание", "требования", "умение", "должен", "кандидат", "разработка", "использованием"}
DETAILS = {
    "Flexbox": r"flexbox", "Grid": r"\bgrid\b", "адаптивность": r"адаптив|responsive",
    "семантическая вёрстка": r"семантическ", "анимации": r"анимац", "позиционирование": r"позиционир",
    "псевдоклассы": r"псевдокласс", "DOM": r"\bdom\b", "события": r"событи|events",
    "fetch или axios": r"fetch|axios", "промисы": r"промис|promise", "асинхронность": r"асинхрон|async",
    "обработка ошибок": r"обработк\w*\s+ошиб|состояни\w*\s+.*ошиб|error.handl",
    "props": r"\bprops\b|пропс", "emit": r"\bemit\b", "v-model": r"v-model",
    "конфигурация": r"конфиг|config", "брейкпоинты": r"брейкпоинт|breakpoint",
    "кастомизация темы": r"кастомизац\w*\s+тем|кастомн\w*\s+.*тем|тем\w*\s+дизайн",
    "контроль комплектности": r"комплектност", "проверка документов": r"провер\w*\s+(?:первичн\w*\s+)?(?:документ|договор)|контрол\w*\s+.*документ",
    "перемещение ОС": r"перемещен", "списание ОС": r"списани", "субсидии или гранты": r"субсиди|грант",
    "инвентаризация": r"инвентаризац", "учебная успеваемость": r"успеваемост|контрольн\w*\s+срез",
}


def concepts(text: str) -> set[str]:
    n = normalize(text)
    hits = {key for key, aliases in CONCEPTS.items() if any(re.search(pattern, n) for pattern in aliases)}
    # Avoid broad bookkeeping overlaps when precise technical skills are named.
    if hits & {"excel", "sheets"}:
        hits.discard("tables")
    if hits & {"html", "css", "javascript", "vue", "tailwind"}:
        hits.discard("detail") if "код" not in n and "аккурат" not in n else None
    return hits


def scoped_concepts(text: str, negative=False) -> set[str]:
    """Scope a denial to its clause, not every technology in the paragraph."""
    found = set()
    for clause in re.split(r"[,;\n]|(?<=[.!?])\s+|\b(?:но|однако|зато)\b", normalize(text)):
        clause = normalize(clause)
        if INTENT.search(clause) or re.search(r"пишу по вакансии|по вакансии", clause):
            continue
        lack = re.search(r"не\s+(?:описан|указан|упомянут)|нет\s+(?:информации|сведений|примеров|данных)", clause)
        denial = bool(NEGATION.search(clause)) and not lack
        if lack:
            continue
        if denial == negative:
            found |= concepts(clause)
    return found


def terms(text: str) -> set[str]:
    return {t[:6] for t in re.findall(r"[a-zа-я][a-zа-я0-9]+", normalize(text)) if len(t) > 3 and t not in STOP_WORDS}


def requirements(document: dict) -> list[dict]:
    mode = None
    rows = []
    seen = set()
    source_paragraphs = document.get("paragraphs", document["blocks"])
    grouped = []
    i = 0
    while i < len(source_paragraphs):
        block = dict(source_paragraphs[i])
        n = normalize(block["text"]).rstrip(":")
        is_heading = re.match(r"^(?:требования|обязанности|задачи|будет|желательн|чем предстоит|условия)", n)
        if block["text"].endswith(":") and not is_heading:
            start = document["text"].find(block["text"])
            end = start + len(block["text"])
            while i + 1 < len(source_paragraphs) and re.match(r"^[-•–]", source_paragraphs[i+1]["text"]):
                i += 1
                child = source_paragraphs[i]["text"]
                end = document["text"].find(child, end) + len(child)
            block["text"] = document["text"][start:end]
        grouped.append(block)
        i += 1
    source_paragraphs = grouped
    for block in source_paragraphs:
        text = block["text"].strip()
        n = normalize(text).rstrip(":.")
        if re.match(r"^(?:требования|что мы ожидаем|квалификац|вы нам подходите|необходимо|обязательные требования)(?:\b|:)", n):
            mode = "required"
            tail = re.split(":", text, maxsplit=1)
            if len(tail) < 2 or len(tail[1].strip()) < 15:
                continue
            text = tail[1].strip()
        elif re.match(r"^(?:будет\s+(?:плюсом|преимуществом)|желательн|преимуществ|дополнительные требования)", n):
            mode = "desired"
            continue
        elif re.match(r"^(?:обязанности|чем предстоит заниматься|задачи|вам предстоит)", n):
            mode = "duty"
            continue
        elif re.match(r"^(?:условия|что мы предлагаем|мы предлагаем|о компании|контакты|в сопроводительном)", n):
            mode = None
            continue
        if mode is None or len(text) < 12:
            continue
        text = re.sub(r"^\s*[-•–]\s*", "", text)
        if normalize(text) in seen:
            continue
        seen.add(normalize(text))
        rows.append({"id": f"{document['id']}:r{len(rows) + 1}", "text": text,
                     "priority": mode, "weight": WEIGHTS[mode], "source": citation(document, block),
                     "concepts": sorted(concepts(text)), "extraction": "раздел документа"})
    if not rows:
        # Documents without headings remain usable; disclose this extraction mode.
        for block in source_paragraphs:
            if re.search(r"нужен|требует|необходим|знани|опыт|умени|владени|образован", normalize(block["text"])):
                rows.append({"id": f"{document['id']}:r{len(rows) + 1}", "text": block["text"],
                             "priority": "required", "weight": 3, "source": citation(document, block),
                             "concepts": sorted(concepts(block["text"])), "extraction": "эвристика без заголовков; проверьте"})
    return rows


def durations(text: str) -> list[float]:
    values = []
    pattern = r"(?<![\d.,])(\d+(?:[.,]\d+)?)\s*(лет|год[а]?|месяц\w*|мес\b)"
    for m in re.finditer(pattern, normalize(text)):
        value = float(m[1].replace(",", "."))
        if m[2].startswith("мес"):
            before = normalize(text)[:m.start()]
            if not re.search(r"(?:лет|года?)\s*$", before) and value <= 960:
                values.append(value / 12)
            continue
        if value > 80:
            # A glued calendar year or malformed field is not career tenure.
            continue
        after = normalize(text)[m.end():m.end() + 25]
        months = re.match(r"\s*(\d+)\s*(?:месяц|мес)", after)
        values.append(value + (int(months[1]) / 12 if months else 0))
    return values


def candidates_for(req: dict, resume: dict, limit=4) -> list[tuple[float, dict]]:
    wanted = set(req["concepts"])
    qt = terms(req["text"])
    scored = []
    for b in professional_blocks(resume):
        bc = concepts(b["text"])
        semantic = len(wanted & bc) / max(1, len(wanted))
        lexical = len(qt & terms(b["text"])) / max(1, len(qt))
        score = semantic * 0.75 + lexical * 0.25
        if score > 0:
            scored.append((score, b))
    return sorted(scored, key=lambda x: (-x[0], x[1]["index"]))[:limit]


def question(req: dict, status: str) -> str:
    return interview_tasks(req['text'],status,req.get('id'))[0]['question']


def match_requirement(req: dict, resume: dict) -> dict:
    scored = candidates_for(req, resume, 6)
    wanted = set(req["concepts"])
    candidates = [b for _, b in scored]
    best = candidates[0] if candidates else None
    evidence = []
    status = "missing"
    reason = "В резюме не найдено достаточного подтверждения. Это не означает отсутствия навыка."
    query = normalize(req["text"])
    details = {name: pattern for name, pattern in DETAILS.items() if re.search(pattern, query)}
    def supports_detail(block):
        text = normalize(block["text"])
        return not NEGATION.search(text) and not INTENT.search(text) and any(re.search(pattern, text) for pattern in details.values())
    negative = [b for b in candidates if wanted & scoped_concepts(b["text"], negative=True)]
    # A direct denial of a named tool overrides other, purely conceptual mentions.
    exact_tools = wanted & {"vue", "tailwind", "python", "sql", "postgresql", "powerbi", "monaco", "pinia", "nuxt"}
    tool_negative = [b for b in negative if exact_tools & scoped_concepts(b["text"], negative=True)]
    if tool_negative:
        best = tool_negative[0]
        positives = [b for b in candidates if exact_tools & scoped_concepts(b["text"])]
        positive_tools = set().union(*(scoped_concepts(b["text"]) & exact_tools for b in positives)) if positives else set()
        denied_tools = set().union(*(scoped_concepts(b["text"], negative=True) & exact_tools for b in tool_negative))
        if positive_tools - denied_tools:
            status = "partial"
            evidence = list({b["id"]: b for b in positives + tool_negative}.values())
            reason = "Часть требуемых инструментов подтверждена, а часть явно отрицается."
        else:
            status = "none"
            reason = "В резюме прямо указано отсутствие требуемого практического опыта."
    elif best:
        positive = [b for b in candidates if (wanted & scoped_concepts(b["text"])) or supports_detail(b) or (not wanted and not INTENT.search(b["text"]) and not NEGATION.search(normalize(b["text"])))]
        covered = set().union(*(scoped_concepts(b["text"]) & wanted for b in positive)) if positive else set()
        effective = wanted - {"api"} if "javascript" in wanted else wanted
        covered_ratio = len(covered & effective) / max(1, len(effective))
        lexical = scored[0][0]
        if positive:
            evidence = positive
            best = positive[0]
            if (effective and covered_ratio >= 0.99) or (not wanted and lexical >= 0.48):
                status = "full"
                reason = "В резюме есть описание соответствующих навыков или действий. Проверьте их на интервью."
            elif covered or lexical >= 0.18:
                status = "partial"
                reason = "Есть связанные навыки или опыт, но подтверждены не все части требования."
        if not positive or (candidates and all(INTENT.search(b["text"]) for b in candidates)):
            status = "missing"
            reason = "Найденные фрагменты не подтверждают уже имеющийся опыт; намерение или отсутствие описания не подменяет факт."
        # Do not turn an unrelated 14-year career into 3 years of asset accounting.
        needed = durations(req["text"])
        if needed:
            required_years = max(needed)
            specific = wanted & {"assets", "inventory", "vue", "tailwind", "python", "sql", "postgresql"}
            duration_candidates = []
            blocks = professional_blocks(resume)
            for b in blocks:
                ds = durations(b["text"])
                if not ds:
                    continue
                n = normalize(b["text"])
                if re.search(r"мужчина|женщина|возраст", n):
                    continue
                vicinity = " ".join(x["text"] for x in blocks if abs(x["index"] - b["index"]) <= 1)
                if specific and not specific & concepts(vicinity):
                    continue
                if specific and INTENT.search(vicinity):
                    continue
                duration_candidates.append((max(ds), b))
            if duration_candidates:
                actual, duration_block = max(duration_candidates, key=lambda x: x[0])
                evidence = [duration_block] + [b for b in evidence if b["id"] != duration_block["id"]][:2]
                if actual >= required_years:
                    status = "full"
                    reason = f"В подходящем фрагменте указан стаж {actual:.1f} года при требовании от {required_years:g}. Параллельные периоды не суммируются."
                else:
                    status = "partial"
                    reason = f"Указанный релевантный стаж {actual:.1f} года меньше требуемых {required_years:g}."
            else:
                status = "partial" if evidence and any(not INTENT.search(b["text"]) for b in evidence) else "missing"
                reason = "Срок именно релевантного опыта не подтверждён. Общий стаж не подменяет профильный."
        if "higher" in wanted:
            education = [b for b in professional_blocks(resume) if concepts(b["text"]) & {"higher", "economics"}]
            all_blocks = professional_blocks(resume)
            def incomplete_context(block):
                context = " ".join(b["text"] for b in all_blocks if 0 <= block["index"] - b["index"] <= 2)
                return re.search(r"неокончен|не окончен|незакончен|обучение продолжается", normalize(context))
            completed = [b for b in education if "higher" in concepts(b["text"]) and not incomplete_context(b)]
            economics = [b for b in completed if re.search(r"финансы\s+и\s+кредит|высшее\s+экономическ|направлен\w*.*эконом|бакалавр.*эконом|\bэкономика\b", normalize(b["text"]))]
            evidence = education[:3]
            if "economics" in wanted:
                status = "full" if economics else "partial" if education else "missing"
                reason = "Указано оконченное высшее экономическое образование." if economics else "Образование указано, но оконченный экономический профиль не подтверждён."
            else:
                status = "full" if completed else "partial" if education else "missing"
        if "own_pc" in wanted:
            direct = [b for b in professional_blocks(resume) if "own_pc" in concepts(b["text"])]
            status = "full" if direct else "missing"
            evidence = direct[:1]
            reason = "Личный компьютер указан явно." if direct else "Собственный компьютер не указан. Профессия и VPS не доказывают его наличие."
    if status == "none" and best:
        evidence = [best]
    if not evidence and best and status in {"partial", "missing"}:
        evidence = [best]
    result = {"requirement_id": req["id"], "requirement": req["text"], "priority": req["priority"],
              "weight": req["weight"], "vacancy_source": req["source"], "status": status,
              "resume_sources": [citation(resume, b) for b in evidence], "comment": reason,
              "question": question(req, status), "method": "правила и словарь синонимов",
              "relevance": round(scored[0][0], 3) if scored else 0}
    if details and status in {"full", "partial"}:
        positive_text = " ".join(b["text"] for b in evidence if wanted & scoped_concepts(b["text"]) or supports_detail(b))
        confirmed = [name for name, pattern in details.items() if re.search(pattern, normalize(positive_text))]
        missing = [name for name in details if name not in confirmed]
        result["subcriteria"] = {"confirmed": confirmed, "not_stated": missing}
        # Keep explicit denials of a listed detail alongside the positive quote.
        for block in professional_blocks(resume):
            text = normalize(block['text'])
            if NEGATION.search(text) and not INTENT.search(text) and not re.search(r'не\s+(?:описан|указан|упомянут)|нет\s+(?:информации|сведений|данных)', text) and any(re.search(pattern,text) for pattern in details.values()):
                source = citation(resume,block)
                if source not in result['resume_sources']:result['resume_sources'].append(source)
        if missing and status == "full":
            result["status"] = "partial"
            result["comment"] = "Часть составного требования не описана явно: " + ", ".join(missing) + ". Это не доказательство отсутствия навыка."
            result["question"] = question(req, "partial")
    if result["status"] == "full" and {"communication", "education_work"} <= wanted:
        same_context = any({"communication", "education_work"} <= concepts(b["text"]) for b in candidates if not INTENT.search(b["text"]))
        if not same_context:
            result["status"] = "partial"
            result["comment"] = "Коммуникации и связанный опыт описаны, но взаимодействие именно с образовательными организациями в том же контексте не подтверждено."
    if result["status"] == "full" and re.search(r"от\s+\d+\s+до\s+\d+\s+организац", query):
        if not any(re.search(r"\d+.*организац", normalize(b["text"])) for b in candidates):
            result["status"] = "partial"
            result["comment"] = "Есть связанные навыки, но объём одновременно сопровождаемых организаций не указан."
    return result


def score_rows(rows: list[dict]) -> dict:
    total = sum(r["weight"] for r in rows)
    credit = sum(r["weight"] * r.get("match_fraction", {"full": 1, "partial": 0.5, "none": 0, "missing": 0}[r["status"]]) for r in rows)
    unknown = sum(r["weight"] * r.get("unknown_fraction", float(r["status"] == "missing")) for r in rows)
    known = total - unknown
    counts = dict(Counter(r["status"] for r in rows))
    return {"score": round(100 * credit / total, 1) if total else 0,
            "upper": round(100 * (credit + unknown) / total, 1) if total else 0,
            "coverage": round(100 * known / total, 1) if total else 0,
            "known_fit": round(100 * credit / known, 1) if known else 0,
            "weights_total": total, "credit": credit, "unknown_weight": unknown, "counts": counts,
            "required_gaps": sum(r["priority"] == "required" and r["status"] == "none" for r in rows),
            "required_unknown": sum(r["priority"] == "required" and r["status"] == "missing" for r in rows)}


def explain_partial(row: dict) -> None:
    """Recompute component credit from citations, including stored v1 reports."""
    base = row.get('status_before_components', row['status'])
    row['status'] = base
    for key in ('match_fraction', 'unknown_fraction', 'clarification', 'comment_lead', 'comment_focus', 'comment_tail', 'component_evidence'):
        row.pop(key, None)
    if base not in {'partial', 'full'}:
        return
    evidence = ' '.join(source['quote'] for source in row.get('resume_sources', []))
    positive, negative = scoped_concepts(evidence), scoped_concepts(evidence, negative=True)
    wanted = concepts(row['requirement'])
    tool_names = {'html':'HTML', 'css':'CSS', 'javascript':'JavaScript', 'vue':'Vue', 'tailwind':'Tailwind CSS',
                  'figma':'Figma', 'pinia':'Pinia', 'nuxt':'Nuxt', 'monaco':'Monaco Editor', 'python':'Python', 'sql':'SQL',
                  'postgresql':'PostgreSQL', 'powerbi':'Power BI', 'excel':'Excel', 'sheets':'Google Sheets', 'typescript':'TypeScript'}
    tools = [key for key in tool_names if key in wanted]
    # Product aliases do not create extra independent skills.
    remainder = re.sub(r'tailwind\s*css|vue[ .]?js', '', normalize(row['requirement']))
    for core in ('css','javascript'):
        if core in tools and core not in concepts(remainder):tools.remove(core)
    if base == 'partial' and (durations(row['requirement']) or re.search(r'коммерческ', normalize(row['requirement']))):
        return
    basic = {key for key in tools if key in positive and key not in negative and re.search(
        r'(?:знакомств\w*\s+с\s+|базов\w*\s+(?:знани\w*|навык\w*)\s+)'+re.escape(key)+r'\b|\b'+re.escape(key)+r'\s*[:(—-]?\s*(?:базов|знакомств)', normalize(evidence))}
    detail = row.get('subcriteria', {})
    names = list(dict.fromkeys(detail.get('confirmed', [])+detail.get('not_stated', [])))
    clauses = re.split(r'[,;\n]|(?<=[.!?])\s+|\b(?:но|однако|зато)\b', normalize(evidence))
    clauses = [normalize(c) for c in clauses]
    available = [c for c in clauses if not INTENT.search(c) and not re.search(r'не\s+(?:описан|указан|упомянут)|нет\s+(?:информации|сведений|примеров|данных)', c)]
    confirmed = [n for n in names if n in DETAILS and any(not NEGATION.search(c) and re.search(DETAILS[n], c) for c in available)]
    contradicted = [n for n in names if n not in confirmed and n in DETAILS and any(NEGATION.search(c) and re.search(DETAILS[n], c) for c in available)]
    missing = [n for n in names if n not in confirmed+contradicted]
    # A broad skill and its listed techniques: half for the core, half for
    # details. Independent products never share this core credit.
    broad = len(tools) == 1 and tools[0] in {'html','css','javascript','vue','tailwind'} and len(names) >= 3
    if broad and tools[0] in positive and tools[0] not in negative:
        count = len(names)
        fraction = (.25 if tools[0] in basic else .5) + .5*len(confirmed)/count
        unknown = (.25 if tools[0] in basic else 0) + .5*len(missing)/count
    elif len(tools) >= 2 and not re.search(r'\bили\b', normalize(row['requirement'])):
        confirmed = [tool_names[k]+(' (базовое знакомство)' if k in basic else '') for k in tools if k in positive and k not in negative]
        missing = [tool_names[k] for k in tools if k not in positive and k not in negative]
        contradicted = [tool_names[k] for k in tools if k in negative]
        fraction = sum(.5 if k in basic else 1 for k in tools if k in positive and k not in negative)/len(tools)
        unknown = (len(missing)+.5*len(basic))/len(tools)
    elif names:
        fraction = len(confirmed)/len(names)
        unknown = len(missing)/len(names)
    else:
        return
    # A separate unmet qualifier (years, depth, context) must not be erased
    # merely because every technology name occurs in a quote.
    if not missing and not contradicted and not basic:
        return
    row['status_before_components'] = base
    row['comment_base'] = row.get('comment_base', row['comment'])
    row['component_evidence'] = {'confirmed':confirmed, 'not_stated':missing, 'contradicted':contradicted}
    row['match_fraction'] = fraction
    row['unknown_fraction'] = unknown
    near = broad and fraction >= .9 and not contradicted and not basic
    row['status'] = 'full' if near or fraction == 1 else 'partial'
    row['clarification'] = near and bool(missing)
    percent=f'{fraction*100:.1f}'.replace('.',',')
    if near and missing:
        lead = f'Основной навык и {len(confirmed)} из {len(names)} деталей подтверждены — это небольшое уточнение. Не описано: '
        tail = f'. Проверьте эту деталь на встрече; за пункт засчитано {percent}% веса.'
    else:
        lead = 'Подтверждена только часть составного требования. '
        if basic:
            lead += 'Базовое знакомство пока не подтверждает самостоятельную работу. '
        lead += 'Нужно проверить: '
        tail = f'. Засчитано {percent}% веса пункта; непроверенные части не считаются доказанным отсутствием навыка.'
    focus = ', '.join(missing + [name+' — прямо указан отказ от опыта' for name in contradicted])
    if not focus:
        focus = 'глубину практики с '+', '.join(tool_names[k] for k in tools if k in basic)
    row.update(comment_lead=lead, comment_focus=focus, comment_tail=tail, comment=lead+focus+tail)


def finalize(vacancy: dict, rows: list[dict]) -> dict:
    for row in rows:
        explain_partial(row)
        row['interview']=interview_tasks(row['requirement'],row['status'],row['requirement_id'])
        row['question']=row['interview'][0]['question']
    score = score_rows(rows)
    ordered = sorted(rows, key=lambda r: (-r["weight"], r["requirement_id"]))
    result = {"id": vacancy["id"], "title": vacancy["text"].splitlines()[0], "name": vacancy["name"],
            "rows": rows, **score,
            "strengths": [r for r in ordered if r["status"] == "full"][:6],
            "gaps": [r for r in ordered if r["status"] in {"partial", "none"}],
            "unknowns": [r for r in ordered if r["status"] == "missing"],
            "clarifications": [r for r in ordered if r.get("clarification")],
            "questions": [r["question"] for r in ordered if r["status"] != "full"][:6]}
    targets=[r for r in ordered if r['status']!='full' or r.get('clarification')]+[r for r in ordered if r['status']=='full' and not r.get('clarification')]
    interviews=[];seen=set()
    for row in targets:
        for item in row['interview']:
            if item['topic'] not in seen:
                seen.add(item['topic']);interviews.append(item)
    result['interview_questions']=interviews[:6]
    result['questions']=[item['question'] for item in result['interview_questions']]
    result["communication"] = communication_for(result)
    return result


def communication_for(vacancy: dict) -> str:
    text = f"Здравствуйте! Спасибо за резюме. Предлагаем обсудить вакансию «{vacancy['title']}». "
    strengths = vacancy["strengths"]
    if strengths and strengths[0]["resume_sources"]:
        text += f"В вашем резюме указано: «{strengths[0]['resume_sources'][0]['quote']}». "
    questions = vacancy["questions"][:3]
    if questions:
        text += "Хотели бы уточнить:\n" + "\n".join(f"{i+1}. {q}" for i,q in enumerate(questions))
    return text


def build_report(resume: dict, vacancies: list[dict], rows_by_vacancy: dict | None = None, model_info=None) -> dict:
    results = []
    for vacancy in vacancies:
        reqs = vacancy.get("requirements") or requirements(vacancy)
        rows = rows_by_vacancy.get(vacancy["id"]) if rows_by_vacancy else None
        rows = rows if rows is not None else [match_requirement(r, resume) for r in reqs]
        results.append(finalize(vacancy, rows))
    results.sort(key=lambda v: (-v["score"], v["required_gaps"], -v["coverage"], v["title"]))
    for index, v in enumerate(results):
        v["rank"] = index + 1
        v["ranking_reason"] = f"Подтверждено {v['credit']:g} из {v['weights_total']} взвешенных пунктов: индекс {v['score']} из 100, покрытие данных {v['coverage']}%."
    top = results[0] if results else None
    recommendation = (f"Первой обсудить вакансию «{top['title']}»: у неё наибольшее подтверждённое соответствие в этом наборе. "
                      f"До следующего шага уточните обязательные пробелы ({top['required_gaps']}) и недостающие сведения ({top['required_unknown']}).") if top and top["score"] > 0 else "Недостаточно подтверждений для обоснованного выбора вакансии. Уточните профессиональный опыт кандидата."
    strengths = top["strengths"][:2] if top else []
    communication = "Здравствуйте! Спасибо за резюме. "
    if top:
        communication += f"Предлагаем обсудить вакансию «{top['title']}». "
        if strengths and strengths[0]["resume_sources"]:
            communication += f"В вашем резюме указано: «{strengths[0]['resume_sources'][0]['quote']}». "
        communication += "Хотели бы уточнить:\n" + "\n".join(f"{i+1}. {q}" for i,q in enumerate(top["questions"][:3]))
    return {"resume": {"id": resume["id"], "name": resume["name"], "facts": facts(resume)},
            "vacancies": results, "recommendation": recommendation, "communication": communication,
            "method_version": METHOD_VERSION, "weights": WEIGHTS, "model": model_info or {"mode": "rules", "used": False},
            "documents": [resume] + vacancies,
            "disclaimer": "Индекс отражает подтверждения в документах, не вероятность успеха и не решение о найме. Неуказанное не означает отсутствие навыка."}
