import pytest
from app.documents import make_document
from app.matching import match_requirement,requirements
from app.service import refine


def inputs():
    resume=make_document('resume.txt','Резюме специалиста. Опыт SQL не описан в предоставленном документе.')
    vacancy=make_document('vacancy.txt','Вакансия аналитика\nТребования:\nПрактический опыт SQL для обработки данных.')
    req=requirements(vacancy)[0]
    return resume,req,match_requirement(req,resume)


def test_ai_cannot_call_missing_information_a_negative_fact():
    resume,req,row=inputs()
    source=next(b for b in resume['blocks'] if 'не описан' in b['text'])
    result=refine(row,req,resume,{'status':'none','evidence_ids':[source['id']],'reason':'Нет навыка SQL'})
    assert result['status']!='none'


def test_ai_cannot_invent_evidence_identifiers():
    resume,req,row=inputs()
    with pytest.raises(ValueError,match='проверяемое'):
        refine(row,req,resume,{'status':'full','evidence_ids':['invented-id'],'reason':'Есть опыт'})


def test_ai_cannot_upgrade_an_intention_to_experience():
    resume=make_document('resume.txt','Резюме бухгалтера. Готовность осваивать учёт основных средств и инвентаризацию.')
    vacancy=make_document('v.txt','Новая вакансия\nТребования:\nОпыт учёта основных средств и инвентаризации.')
    req=requirements(vacancy)[0];row=match_requirement(req,resume)
    source=next(b for b in resume['blocks'] if 'Готовность' in b['text'])
    assert refine(row,req,resume,{'status':'full','evidence_ids':[source['id']],'reason':'Есть опыт'})['status']=='missing'
