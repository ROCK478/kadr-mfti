"""The model interprets a question; factual answers and links come from records."""
from __future__ import annotations
import re

from .documents import normalize
from .workspace import candidate_name
from .matching import STATUSES

INTENTS = {'rank_candidates','rank_vacancies','questions','gaps','strengths','message','compare','search','help'}


def fallback_plan(question, vacancies, candidates, vacancy_id=None, candidate_id=None):
    text = normalize(question)
    def mentioned_role(label):
        words = [w for w in re.findall(r'[a-zа-я0-9]+',normalize(label)) if len(w)>3]
        return sum(bool(re.search(r'\b'+re.escape(w[:6]), text)) for w in words)
    def mentioned_name(label):
        score=0
        for word in re.findall(r'[a-zа-я]+', normalize(label)):
            if len(word)<3:continue
            if word.endswith('ей'):
                pattern=re.escape(word[:-1])+r'(?:й|я|ю|ем|е)'
            elif word.endswith(('ова','ева','ина')):
                pattern=re.escape(word[:-1])+r'(?:а|ой|у|е|ы)'
            else:
                pattern=re.escape(word)+r'(?:а|у|е|ым|ом|ой|ы)?'
            if re.search(r'\b'+pattern+r'\b',text):score+=1
        return score
    ranked = sorted(((mentioned_role(v['title']),v['id']) for v in vacancies),reverse=True)
    chosen_vacancy = ranked[0][1] if ranked and ranked[0][0] else vacancy_id
    named=[(mentioned_name(c['name']),c['id']) for c in candidates]
    selected=[ident for score,ident in named if score]
    if selected and max(score for score,_ in named)>1:
        best=max(score for score,_ in named);selected=[ident for score,ident in named if score==best]
    named_candidates=bool(selected)
    selected = selected or ([candidate_id] if candidate_id else [])
    if re.search(r'сравн',text):intent='compare'
    elif re.search(r'вопрос|спрос|что.*уточн',text):intent='questions'
    elif re.search(r'письмо|сообщени|напиши',text):intent='message'
    elif re.search(r'пробел|не хватает|риск|несоответ',text):intent='gaps'
    elif re.search(r'альтернатив|какие.*вакан|куда.*(?:предлож|подойд)|подбери.*вакан|подобрать.*вакан',text):intent='rank_vacancies'
    elif re.search(r'сильн|совпад|подтвержд',text):intent='strengths'
    elif re.search(r'кто|лучше|подход.*кандидат|кандидат.*подход',text):intent='rank_candidates'
    elif re.search(r'умеешь|помощь|привет|здравств',text):intent='help'
    else:intent='search'
    ambiguous=len(selected)>1 and intent!='compare'
    if ambiguous and candidate_id in selected:selected=[candidate_id];ambiguous=False
    return {'intent':intent,'vacancy_id':chosen_vacancy,'candidate_ids':selected,'keywords':[],
            'needs_clarification':ambiguous,'named_candidates':named_candidates,'explicit_intent':intent!='search'}


def planner_input(question, vacancies, candidates, vacancy_id, candidate_id, history):
    vs={f'v{i+1}':v['id'] for i,v in enumerate(vacancies)}
    cs={f'c{i+1}':c['id'] for i,c in enumerate(candidates)}
    reverse_v={v:k for k,v in vs.items()};reverse_c={v:k for k,v in cs.items()}
    data={'task':'chat','question':question,
        'vacancies':[{'id':key,'name':next(v['title'] for v in vacancies if v['id']==ident)} for key,ident in vs.items()],
        'candidates':[{'id':key,'name':next(c['name'] for c in candidates if c['id']==ident)} for key,ident in cs.items()],
        'context':{'vacancy':reverse_v.get(vacancy_id),'candidate':reverse_c.get(candidate_id)},
        'previous_questions':history[-3:]}
    return data,vs,cs


def resolve_plan(raw, vs, cs, fallback):
    intent=raw.get('intent')
    if intent not in INTENTS:raise ValueError('Модель не распознала действие. Вопрос разобран без модели.')
    # A local model must not reverse the explicitly requested direction of matching.
    if fallback.get('explicit_intent'):intent=fallback['intent']
    candidates=raw.get('candidates',[])
    if not isinstance(candidates,list):candidates=[]
    keywords=raw.get('keywords',[])
    if not isinstance(keywords,list):keywords=[]
    chosen=[cs[c] for c in candidates if isinstance(c,str) and c in cs]
    if fallback.get('candidate_ids'):chosen=fallback['candidate_ids']
    elif intent!='compare':chosen=[]
    vacancy=fallback.get('vacancy_id')
    # No selected role/name means a clarification, rather than an arbitrary v1.
    if intent=='rank_vacancies':vacancy=None
    return {'intent':intent,'vacancy_id':vacancy,'candidate_ids':chosen,
        'keywords':[str(k)[:60] for k in keywords[:5]],
        'needs_clarification':fallback.get('needs_clarification',False) or (raw.get('needs_clarification') is True and not fallback.get('explicit_intent'))}


def reply(store, question, plan, model_used, warning=None):
    blocks=[];links=[];citations=[]
    def text(value):blocks.append({'type':'text','text':value})
    def heading(value):blocks.append({'type':'heading','text':value})
    def vacancy_link(vacancy, resume_id=None):
        links.append({'kind':'candidate' if resume_id else 'vacancy','label':vacancy['title'],
            'vacancy_id':vacancy['id'],'candidate_id':resume_id,
            'href':f"#vacancy/{vacancy['id']}"+(f"/candidate/{resume_id}" if resume_id else '')})
    def candidate_link(candidate, vacancy_id=None):
        links.append({'kind':'candidate','label':candidate['name'],'candidate_id':candidate['id'],
            'vacancy_id':vacancy_id,'href':f"#candidate/{candidate['id']}"+(f"/vacancy/{vacancy_id}" if vacancy_id else '')})
    def cite(row):
        for source in [row['vacancy_source']]+row['resume_sources'][:1]:
            if source not in citations:citations.append(source)
    vacancies=store.overview()['vacancies'];candidates=store.candidates()
    vid=plan.get('vacancy_id');cids=plan.get('candidate_ids',[])
    valid_candidates={c['id']:c for c in candidates}
    cids=[c for c in cids if c in valid_candidates]
    intent=plan['intent']
    if plan.get('needs_clarification') and cids:
        text('Уточните, какого кандидата вы имеете в виду. Выберите полное имя ниже или в контексте вопроса.')
        for cid in cids:candidate_link(valid_candidates[cid],vid)
    elif plan.get('needs_clarification') or intent=='help':
        text('Я помогаю найти подходящие вакансии для кандидата, сравнить отклики, увидеть пробелы и подготовить вопросы по вашим документам. Выберите вакансию или кандидата либо укажите имя в вопросе.')
        for vacancy in vacancies[:4]:vacancy_link(vacancy)
    elif intent=='rank_candidates':
        if not vid:
            text('По какой вакансии посмотреть отклики? Выберите её ниже или укажите название в вопросе.')
            for vacancy in vacancies[:5]:vacancy_link(vacancy)
        else:
            vacancy=store.vacancy(vid);heading(vacancy['title'])
            if not vacancy['candidates']:text('Пока нет откликов. Добавьте резюме — сравнение появится автоматически.')
            for index,candidate in enumerate(vacancy['candidates'][:3]):
                text(f"{index+1}. {candidate['name']} — {candidate['score']} из 100, {candidate['group_label'].lower()}. Обязательных явных пробелов: {candidate['required_gaps']}; не указано: {candidate['required_unknown']}.")
                candidate_link(candidate,vid)
                if index==0:
                    value,_=store.evaluation(vid,candidate['id'])
                    for row in value['strengths'][:2]:cite(row)
            if vacancy['candidates']:text('Это порядок работы по подтверждениям в документах. Перед решением проверьте источники и неизвестные сведения.')
    elif intent=='rank_vacancies':
        if not cids:
            text('Для кого подобрать альтернативные вакансии? Укажите имя кандидата или выберите его ниже.')
            for candidate in candidates[:5]:candidate_link(candidate)
        else:
            for cid in cids[:2]:
                report=store.candidate_report(cid);heading('Подбор вакансий · '+valid_candidates[cid]['name'])
                for vacancy in report['vacancies'][:4]:
                    why = 'Подтверждено: '+vacancy['strengths'][0]['requirement'] if vacancy['strengths'] else 'Полностью подтверждённых требований пока нет.'
                    needs = sorted([r for r in vacancy['rows'] if r['status']!='full'], key=lambda r:(-r['weight'], {'none':0,'missing':1,'partial':2}[r['status']]))
                    clarification = ('Уточнить: '+needs[0]['requirement']+' ('+STATUSES[needs[0]['status']]+').') if needs else 'Все выделенные требования подтверждены.'
                    text(f"{vacancy['rank']}. {vacancy['title']} — {vacancy['score']} из 100.\n{why}\n{clarification}")
                    vacancy_link(vacancy,cid)
                    for row in vacancy['strengths'][:1]:cite(row)
                    for row in needs[:1]:cite(row)
                text(report['recommendation'])
    elif intent=='compare':
        if not vid or len(cids)<2:
            text('Выберите вакансию и укажите двух кандидатов для сравнения. Можно написать: «Сравни Семенова и Петрова на frontend».')
            for vacancy in vacancies[:3]:vacancy_link(vacancy)
        else:
            heading('Сравнение по выбранной вакансии')
            values=[]
            for cid in cids[:4]:
                value,_=store.evaluation(vid,cid);values.append((cid,value))
            for cid,value in sorted(values,key=lambda item:-item[1]['score']):
                text(f"{valid_candidates[cid]['name']}: {value['score']} из 100; покрытие {value['coverage']}%; явных обязательных пробелов {value['required_gaps']}, неизвестных {value['required_unknown']}.")
                candidate_link(valid_candidates[cid],vid)
                for row in value['strengths'][:1]:cite(row)
                for row in value['gaps'][:1]:cite(row)
    else:
        if not cids:
            text('Чьё резюме разобрать? Выберите кандидата в откликах или укажите его имя.')
            for candidate in candidates[:5]:candidate_link(candidate,vid)
        else:
            cid=cids[0]
            if not vid:
                report=store.candidate_report(cid)
                if report['vacancies']:
                    vid=report['vacancies'][0]['id']
                    text('Рассматриваю вакансию с наибольшим подтверждённым индексом для этого резюме.')
            if not vid:text('Пока нет вакансий для сопоставления. Добавьте первую вакансию.')
            else:
                value,_=store.evaluation(vid,cid);heading(valid_candidates[cid]['name']+' · '+value['title'])
                candidate_link(valid_candidates[cid],vid)
                if intent=='message':
                    text(value['communication']);text('Это черновик. Проверьте его перед отправкой; приложение никому не отправляет сообщения.')
                elif intent=='questions':
                    for index,question_text in enumerate(value['questions'][:6]):text(f'{index+1}. {question_text}')
                    if not value['questions']:text('Неуточнённых требований не найдено. Попросите привести примеры наиболее важных навыков.')
                    for row in (value['gaps']+value['unknowns'])[:2]:cite(row)
                else:
                    rows=value['strengths'] if intent=='strengths' else value['gaps']+value['unknowns'] if intent=='gaps' else value['rows']
                    if intent=='search':
                        keywords=plan.get('keywords') or [w for w in re.findall(r'[a-zа-я0-9]+',normalize(question)) if len(w)>4 and w not in {'какие','какой','навыки','кандидат','опыта','работы','расскажи'}]
                        rows=[r for r in rows if any(normalize(k)[:6] in normalize(r['requirement']) for k in keywords)]
                    if not rows:text('Не найдено отдельного подтверждения по этому вопросу. Это не означает отсутствия навыка. Откройте полный разбор и уточните у кандидата.')
                    for row in rows[:5]:
                                text(row['requirement']+' — '+STATUSES[row['status']]+'. '+row['comment']);cite(row)
    return {'blocks':blocks,'links':list({item['href']:item for item in links}.values()),'citations':citations[:8],
            'model_used':model_used,'intent':intent,'warning':warning,
            'context':{'vacancy_id':vid,'candidate_id':cids[0] if len(cids)==1 else None},
            'note':'Локальная модель распознаёт вопрос; факты и ссылки берутся из проверяемых сравнений.'}
