import io
import pytest
from app.documents import DocumentError, make_document, read_document


def test_docx_merged_cells_do_not_repeat():
    from docx import Document
    doc = Document()
    doc.add_paragraph('Резюме специалиста по работе с документами')
    row = doc.add_table(rows=1, cols=3).rows[0]
    row.cells[0].merge(row.cells[2]).text = 'Уникальное подтверждение навыка SQL'
    f = io.BytesIO(); doc.save(f)
    result = read_document('resume.docx', f.getvalue())
    assert result['text'].count('Уникальное подтверждение') == 1
    assert all(b['text'] in result['text'] for b in result['blocks'])


def test_docx_soft_line_breaks_separate_calendar_and_tenure():
    from docx import Document
    doc = Document(); p = doc.add_paragraph()
    p.add_run('Май 2025 — Август 2026').add_break()
    p.add_run('1 год 4 месяца'); doc.add_paragraph('Frontend-разработчик на Vue.js')
    f=io.BytesIO();doc.save(f)
    result=read_document('resume.docx',f.getvalue())
    assert '2026\n1 год' in result['text']


@pytest.mark.parametrize('name,data', [('broken.docx', b'not-a-zip'), ('scan.pdf', b'not-a-pdf'), ('old.doc', b'data'), ('empty.txt', b'')])
def test_corrupt_empty_and_unsupported_inputs_are_errors(name, data):
    with pytest.raises(DocumentError): read_document(name, data)


def test_cp1251_and_unicode_content():
    result = read_document('резюме.txt', 'Опыт работы с документами и таблицами Excel'.encode('cp1251'))
    assert 'Excel' in result['text'] and result['warnings']


def test_large_input_is_not_silently_truncated():
    with pytest.raises(DocumentError, match='не обрезается'):
        make_document('big.txt', 'x' * 80001)


def test_pdf_scan_reports_ocr_requirement():
    from pypdf import PdfWriter
    writer = PdfWriter(); writer.add_blank_page(200, 200)
    f = io.BytesIO(); writer.write(f)
    with pytest.raises(DocumentError, match='OCR'):
        read_document('scan.pdf', f.getvalue())
