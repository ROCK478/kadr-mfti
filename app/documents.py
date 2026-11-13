from __future__ import annotations

import hashlib
import io
from pathlib import Path
import re
import zipfile

MAX_BYTES = 12 * 1024 * 1024
MAX_CHARS = 80000


class DocumentError(ValueError):
    pass


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("ё", "е").lower()).strip()


def make_document(name: str, text: str, warnings=None) -> dict:
    text = text.replace("\x00", "").strip()
    if len(text) < 30 or len(re.findall(r"[\w]", text)) < 20:
        raise DocumentError("Не найден содержательный текст. Для скана нужен OCR; вставьте распознанный текст.")
    if len(text) > MAX_CHARS:
        raise DocumentError(f"Документ содержит {len(text)} символов. Лимит {MAX_CHARS}; текст не обрезается автоматически.")
    ident = hashlib.sha256(text.encode()).hexdigest()[:14]
    blocks = []
    paragraphs = []
    # Keep source substrings, including punctuation and non-breaking spaces.
    for paragraph in text.splitlines():
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        paragraphs.append({"id": f"{ident}:p{len(paragraphs)+1}", "index": len(paragraphs)+1, "text": paragraph})
        for part in re.split(r"(?<=[.!?])\s+(?=[А-ЯA-Z–—\-])|(?<=[.!?])\s*(?=[-•])|(?<=[.!?])(?=[А-Я])|(?<=;)\s*(?=[-•])", paragraph):
            part = part.strip()
            if part:
                blocks.append({"id": f"{ident}:b{len(blocks) + 1}", "index": len(blocks) + 1, "text": part})
    return {"id": ident, "name": Path(name).name, "text": text, "blocks": blocks, "paragraphs": paragraphs,
            "characters": len(text), "warnings": warnings or []}


def read_document(name: str, data: bytes) -> dict:
    if len(data) > MAX_BYTES:
        raise DocumentError("Файл больше 12 МБ. Подготовьте текстовую копию меньшего размера.")
    ext = Path(name).suffix.lower()
    warnings = []
    try:
        if ext == ".docx":
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                if len(z.infolist()) > 2000 or sum(x.file_size for x in z.infolist()) > 60 * 1024 * 1024:
                    raise DocumentError("Слишком большой распакованный DOCX.")
            from docx import Document
            from docx.oxml.ns import qn
            doc = Document(io.BytesIO(data))
            # Iterating physical paragraphs avoids repeating merged table cells.
            paragraphs = []
            for p in doc.element.body.iter(qn("w:p")):
                pieces = []
                for node in p.iter():
                    if node.tag == qn("w:t"):
                        pieces.append(node.text or "")
                    elif node.tag in {qn("w:br"), qn("w:cr")}:
                        pieces.append("\n")
                    elif node.tag == qn("w:tab"):
                        pieces.append("\t")
                text = "".join(pieces).strip()
                if text:
                    paragraphs.append(text)
            text = "\n".join(paragraphs)
        elif ext == ".pdf":
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(data))
            if reader.is_encrypted:
                raise DocumentError("PDF защищён паролем. Загрузите незашифрованную копию.")
            if len(reader.pages) > 40:
                raise DocumentError("Лимит PDF — 40 страниц.")
            text = "\n".join(p.extract_text() or "" for p in reader.pages)
            warnings.append("Для PDF проверяйте порядок извлечённого текста в источнике; OCR не выполняется.")
        elif ext in {".txt", ".md"}:
            try:
                text = data.decode("utf-8-sig")
            except UnicodeDecodeError:
                text = data.decode("cp1251")
                warnings.append("Текст прочитан в кодировке Windows-1251.")
        else:
            raise DocumentError("Поддерживаются DOCX, PDF с текстом, TXT и MD.")
        return make_document(name, text, warnings)
    except DocumentError:
        raise
    except Exception as exc:
        raise DocumentError("Файл повреждён или не соответствует формату. Попробуйте сохранить его заново либо вставить текст.") from exc


PRIVATE_LINE = re.compile(r"^(?:мужчина|женщина|гражданство|разрешение на работу|проживает|г\.|опыт вождения|права категории|\S+@\S+)", re.I)


def professional_blocks(document: dict) -> list[dict]:
    return [b for b in document["blocks"] if not PRIVATE_LINE.search(b["text"].strip())]


def facts(document: dict) -> dict:
    result = {"experience": [], "skills": [], "education": [], "other": []}
    category = "other"
    for b in professional_blocks(document):
        n = normalize(b["text"])
        if re.search(r"пишу по вакансии|^здравствуйте|^добрый день|^привет", n):
            continue
        if n in {"опыт работы", "профессиональный опыт"} or n.startswith("опыт работы —"):
            category = "experience"
        elif n in {"образование", "обучение"}:
            category = "education"
        elif n in {"навыки", "ключевые навыки", "компетенции", "знание языков"}:
            category = "skills"
        elif n in {"дополнительно", "обо мне", "дополнительная информация", "личные качества"}:
            category = "other"
        elif len(n) > 18 or (category == "education" and len(n) > 2 and n not in {"уровень"}):
            inferred = category
            if re.search(r"(?:высшее|бакалавр|магистр|диплом|университет|институт профессион|переквалификац)", n):
                inferred = "education"
            elif re.search(r"(?:опыт работы|\d+\s*(?:года?|лет|месяц)|разработчик|бухгалтер|практикант)", n) and category == "other":
                inferred = "experience"
            result[inferred].append({"text": b["text"], "source": citation(document, b)})
    return result


def citation(document: dict, block: dict) -> dict:
    return {"document_id": document["id"], "document_name": document["name"],
            "block_id": block["id"], "block_index": block["index"], "quote": block["text"]}
