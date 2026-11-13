from pathlib import Path
import pytest
from app.documents import make_document, read_document, facts
from app.matching import build_report, match_requirement, requirements, score_rows, durations

ROOT = Path(__file__).resolve().parents[1]


def doc(text): return make_document('new-document.txt', text)
def match(requirement, resume):
    vacancy = doc('Новая вакансия\nТребования:\n' + requirement)
    return match_requirement(requirements(vacancy)[0], doc(resume))


def test_extraction_distinguishes_required_desired_and_duties():
    vacancy = doc('Аналитик\nЗадачи:\nПодготовка регулярной отчётности.\nТребования:\nОпыт работы с SQL и PostgreSQL.\nБудет плюсом:\nЗнание Python и pandas.\nУсловия:\nУдобный офис рядом с метро.')
    rows = requirements(vacancy)
    assert [r['priority'] for r in rows] == ['duty', 'required', 'desired']
    assert all(r['source']['quote'] in vacancy['text'] for r in rows)
    assert not any('офис' in r['text'] for r in rows)


def test_unusual_document_without_heading_is_disclosed():
    rows = requirements(doc('Вакансия аналитика данных\nНеобходим опыт работы с Python и SQL для отчётности.'))
    assert rows and 'эвристика' in rows[0]['extraction']


def test_missing_is_not_a_negative_fact():
    row = match('Практический опыт Tailwind CSS.', 'Готовил регулярные отчёты в Excel для руководителя.')
    assert row['status'] == 'missing'


def test_direct_denial_is_none():
    row = match('Практический опыт Tailwind CSS.', 'Фронтенд-разработчик. С Tailwind CSS пока не работал.')
    assert row['status'] == 'none'
    assert row['resume_sources'][0]['quote'] in 'Фронтенд-разработчик. С Tailwind CSS пока не работал.'


def test_denial_of_one_tool_does_not_negate_another_tool():
    text='Коммерческие проекты разрабатываю на Vue.js, но с Tailwind не работал.'
    assert match('Опыт коммерческой разработки на Vue.js.',text)['status']=='full'
    assert match('Практический опыт Tailwind CSS.',text)['status']=='none'
    assert match('Практический опыт Vue.js и Tailwind CSS.',text)['status']=='partial'


def test_full_compound_match_preserves_all_supporting_sources():
    resume='Использовал CSS3 и Flexbox.\nПрименял Grid.\nДелал адаптивные страницы.\nИспользовал позиционирование.\nНастраивал псевдоклассы.\nСоздавал анимации.'
    row=match('CSS3: Flexbox, Grid, адаптивная вёрстка, позиционирование, псевдоклассы и анимации.',resume)
    assert row['status']=='full'
    quotes=' '.join(s['quote'] for s in row['resume_sources'])
    assert all(term in quotes.lower() for term in ['grid','позиционирован','псевдокласс','анимац'])


def test_question_does_not_presuppose_denied_commercial_projects():
    row=match('Опыт коммерческой разработки на Vue.js.','О Vue.js: коммерческих проектов на нём у меня нет.')
    assert row['status']=='none'
    # A hypothetical skill task does not assume a denied project actually existed.
    assert 'В Vue 3 форма' in row['question']
    assert 'ваших коммерческих' not in row['question'] and 'в вашем проекте' not in row['question'].lower()


def test_aspiration_is_not_experience():
    row = match('Опыт работы с основными средствами не менее 3 лет.', 'Бухгалтер. Готовность осваивать специфику учёта основных средств.')
    assert row['status'] != 'full'


def test_aliases_and_compound_desirable_requirement():
    row = match('Опыт работы с Pinia, Nuxt, Monaco Editor.', 'Frontend-разработчик. Стек: Nuxt.js, VueJS и Pinia. Использовал их в проектах.')
    assert row['status'] == 'partial'


def test_parallel_experience_periods_are_not_summed():
    row = match('Опыт работы от 5 лет.', 'Опыт работы: 2 года 10 месяцев\nРазработчик — 2 года 10 месяцев.\nАналитик — 2 года 10 месяцев.')
    assert row['status'] != 'full'


def test_durations_handle_months_and_reject_glued_calendar_years():
    assert durations('Стаж 10 месяцев') == [10/12]
    assert durations('Стаж 2 года 10 месяцев') == [2+10/12]
    assert durations('Август 20261 год 4 месяца') == []


def test_incomplete_higher_education_is_not_completed():
    row = match('Образование: высшее экономическое.', 'Образование\nНеоконченное высшее. Экономика, бакалавриат, обучение продолжается.')
    assert row['status'] == 'partial'


def test_computer_ownership_cannot_be_inferred_from_profession():
    row = match('Наличие личного ноутбука или ПК.', 'Frontend-разработчик. Разворачивал сервисы на своём VPS и писал код на Python.')
    assert row['status'] == 'missing'


def test_missing_and_none_have_different_uncertainty():
    a=score_rows([{'weight':3,'priority':'required','status':'missing'}])
    b=score_rows([{'weight':3,'priority':'required','status':'none'}])
    assert a['score'] == b['score'] == 0
    assert a['upper'] == 100 and a['coverage'] == 0
    assert b['upper'] == 0 and b['coverage'] == 100 and b['required_gaps'] == 1


def supplied():
    data = [read_document(p.name,p.read_bytes()) for p in (ROOT/'examples/documents').glob('*.docx')]
    return [x for x in data if x['name'].startswith('Вакансия')], [x for x in data if not x['name'].startswith('Вакансия')]


@pytest.mark.parametrize('name,expected', [('Иванов','Вакансия 3.docx'),('Петров Иван','Вакансия 1.docx'),('Семенов','Вакансия 1.docx'),('Петрова','Вакансия 2.docx')])
def test_supplied_case_top_vacancy_against_manual_expectation(name,expected):
    vacancies,resumes=supplied()
    resume=next(r for r in resumes if r['name'].startswith(name))
    report=build_report(resume,vacancies)
    assert report['vacancies'][0]['name'] == expected
    sources={d['id']:d['text'] for d in report['documents']}
    for v in report['vacancies']:
        for row in v['rows']:
            assert row['vacancy_source']['quote'] in sources[row['vacancy_source']['document_id']]
            for source in row['resume_sources']:
                assert source['quote'] in sources[source['document_id']]


def test_lilia_general_accounting_tenure_does_not_prove_asset_tenure():
    vacancies,resumes=supplied()
    report=build_report(next(r for r in resumes if r['name'].startswith('Петрова')),vacancies)
    v=next(v for v in report['vacancies'] if v['name']=='Вакансия 2.docx')
    row=next(r for r in v['rows'] if 'не менее 3 лет' in r['requirement'])
    assert row['status'] in {'partial','missing'}


def test_reordering_or_renaming_documents_cannot_change_scores():
    vacancies,resumes=supplied();resume=resumes[0]
    first=build_report(resume,vacancies)
    for i,v in enumerate(vacancies):v['name']=f'Новый документ {i}.docx'
    second=build_report(resume,list(reversed(vacancies)))
    assert [(v['id'],v['score']) for v in first['vacancies']] == [(v['id'],v['score']) for v in second['vacancies']]


def test_sensitive_personal_lines_do_not_enter_professional_facts():
    result=facts(doc('Иван Иванов\nМужчина, 45 лет\nГражданство: Россия\nОпыт работы\nFrontend-разработчик, 3 года.'))
    assert '45 лет' not in str(result) and 'Гражданство' not in str(result)


@pytest.mark.parametrize('requirement,evidence,detail,expected,unknown',[
 ('Хорошее знание CSS3: Flexbox, Grid, адаптивная верстка, позиционирование, псевдоклассы и анимации.',
  'CSS3: Flexbox, Grid, адаптивная верстка, псевдоклассы и анимации.',
  {'confirmed':['Flexbox','Grid','адаптивность','псевдоклассы','анимации'],'not_stated':['позиционирование']},
  'Flexbox','позиционирование'),
 ('Опыт работы с Pinia, Nuxt, Monaco Editor.',
  'Знакомство с Pinia (базовые сторы).',{},'Pinia (базовое знакомство)','Nuxt, Monaco Editor'),
])
def test_component_assessment_explains_gap_and_is_idempotent(requirement,evidence,detail,expected,unknown):
    from app.matching import explain_partial, score_rows
    row={'status':'partial','requirement':requirement,'subcriteria':detail,
         'resume_sources':[{'quote':evidence}],'comment':'Общий комментарий', 'weight':3,'priority':'required'}
    explain_partial(row)
    assert expected in row['component_evidence']['confirmed']
    assert unknown == ', '.join(row['component_evidence']['not_stated'])
    assert row['comment_focus']==unknown and 'веса' in row['comment']
    if detail:
        assert row['status']=='full' and row['clarification']
        assert score_rows([row])['score']==91.7
    else:
        assert row['status']=='partial' and score_rows([row])['score']==16.7
    once=dict(row);explain_partial(row)
    assert row==once


@pytest.mark.parametrize('evidence,expected,upper',[
    ('Работа с Pinia в проекте.',33.3,100),
    ('Работа с Pinia. Не работал с Nuxt и Monaco Editor.',33.3,33.3),
    ('Базовое знакомство с Pinia.',16.7,100),
    ('Работа с Pinia, Nuxt и Monaco Editor.',100,100),
])
def test_separate_tools_keep_proportion_and_unknown_bounds(evidence,expected,upper):
    from app.matching import explain_partial
    row=match('Опыт работы с Pinia, Nuxt и Monaco Editor.','Опыт работы\n'+evidence+'\nРазработка пользовательских интерфейсов.')
    explain_partial(row)
    value=score_rows([row])
    assert value['score']==expected and value['upper']==upper
    assert 0<=value['score']<=value['upper']<=100


def test_explicit_minor_denial_is_never_promoted_to_full():
    from app.matching import explain_partial
    row=match('CSS3: Flexbox, Grid, адаптивная верстка, позиционирование, псевдоклассы и анимации.',
              'CSS3: Flexbox, Grid, адаптивная верстка, псевдоклассы и анимации. Не использовал позиционирование.')
    explain_partial(row)
    assert row['status']!='full'
    assert 'позиционирование' in row['component_evidence']['contradicted']


def test_basic_qualifier_is_attached_to_the_named_skill():
    from app.matching import explain_partial
    row=match('Уверенное знание HTML5 и принципов семантической верстки.',
              'HTML5: семантическая верстка, доступность (a11y) на базовом уровне, структура страниц под SEO.')
    explain_partial(row)
    assert row['status']=='full'
    assert 'comment_focus' not in row


def test_tailwind_css_is_one_skill_with_details_not_two_products():
    from app.matching import explain_partial
    row=match('Опыт работы с Tailwind CSS: конфигурация, брейкпоинты, кастомизация темы.',
              'Tailwind CSS: конфигурация проекта, адаптивные брейкпоинты. Разработка компонентов интерфейса.')
    explain_partial(row)
    assert row['status']=='partial' and score_rows([row])['score']==83.3
    assert row['component_evidence']['not_stated']==['кастомизация темы']


def test_component_credit_does_not_erase_short_experience_guard():
    from app.matching import explain_partial
    row=match('Опыт работы с CSS от 3 лет: Flexbox, Grid, адаптивность, позиционирование, псевдоклассы, анимации.',
              'Опыт работы: 1 год. CSS: Flexbox, Grid, адаптивность, псевдоклассы, анимации.')
    explain_partial(row)
    assert row['status']=='partial' and score_rows([row])['score']<=50
