from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import threading
import time
import uuid

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .documents import DocumentError, MAX_BYTES, facts, make_document, read_document
from .exports import report_html
from .local_model import MODEL_NAME, model_path
from .matching import requirements
from .service import analyze, runtime
from .portal import router as portal_router
from .speech import router as speech_router
from .browser_workspace import router as session_router, enabled as browser_mode, sessions, current_session, MAX_SNAPSHOT_BYTES

ROOT = Path(__file__).resolve().parents[1]
documents = {}
jobs = {}
lock = threading.Lock()


@asynccontextmanager
async def lifespan(app):
    yield
    runtime.cancel()


app = FastAPI(title="Кадр — кабинет HR", version="1.0", lifespan=lifespan)
app.include_router(portal_router)
app.include_router(speech_router)
app.include_router(session_router)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=[host.strip() for host in os.environ.get("ALLOWED_HOSTS","localhost,127.0.0.1,[::1],testserver").split(",") if host.strip()])


@app.middleware("http")
async def local_requests(request: Request, call_next):
    origin = request.headers.get("origin")
    if origin and origin != str(request.base_url).rstrip("/"):
        return JSONResponse({"detail": "Разрешены запросы только из этого приложения."}, status_code=403)
    if browser_mode() and request.url.path.startswith('/api/') and not request.url.path.startswith('/api/portal/'):
        return JSONResponse({'detail':'Этот раздел недоступен в демонстрации.'},status_code=404)
    if browser_mode() and request.url.path=='/api/portal/session' and int(request.headers.get('content-length','0') or 0)>MAX_SNAPSHOT_BYTES:
        return JSONResponse({'detail':'Сохранённый кабинет слишком велик.'},status_code=413)
    entry=None;context_token=None
    needs_session=(browser_mode() and request.url.path.startswith('/api/portal/') and
                   not (request.url.path=='/api/portal/speech' or request.url.path.startswith('/api/portal/speech/')) and
                   request.url.path not in {'/api/portal/session','/api/portal/session/config'})
    if needs_session:
        try:entry=sessions.acquire(request.headers.get('x-workspace-session'))
        except HTTPException as exc:return JSONResponse({'detail':exc.detail},status_code=exc.status_code)
        context_token=current_session.set(entry)
    try:response = await call_next(request)
    finally:
        if context_token is not None:current_session.reset(context_token)
        if entry:sessions.release(entry)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; worker-src 'self'; style-src 'self'; img-src 'self' data:; media-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; object-src 'none'"
    return response


def store(doc):
    if len(documents) >= 100:
        raise HTTPException(429, "Слишком много документов в сессии. Перезапустите приложение для очистки памяти.")
    documents[doc["id"]] = doc
    return {**doc, "requirements": requirements(doc), "facts": facts(doc)}


@app.get("/health")
def health():
    return {"status": "ok", "model_available": model_path().is_file(), "model": MODEL_NAME,
            "external_services": [], "processing": "local"}


@app.get("/api/examples")
def examples():
    result = {"resumes": [], "vacancies": []}
    for p in sorted((ROOT / "examples/documents").glob("*.docx")):
        key = "vacancies" if p.name.startswith("Вакансия") else "resumes"
        result[key].append({"name": p.name, "key": p.name})
    return result


@app.post("/api/examples/{name}")
def load_example(name: str):
    folder = ROOT / "examples/documents"
    path = folder / Path(name).name
    if path.parent != folder or not path.is_file() or path.suffix != ".docx":
        raise HTTPException(404, "Пример не найден")
    return store(read_document(path.name, path.read_bytes()))


@app.post("/api/documents")
async def upload(file: UploadFile = File(...)):
    data = await file.read(MAX_BYTES + 1)
    try:
        return store(await asyncio.to_thread(read_document, file.filename or "document.txt", data))
    except DocumentError as exc:
        raise HTTPException(422, str(exc))
    finally:
        await file.close()


class TextDocument(BaseModel):
    name: str = Field(default="Текст документа.txt", min_length=1, max_length=180)
    text: str = Field(min_length=1, max_length=80000)


@app.post("/api/documents/text")
def text_document(data: TextDocument):
    try:
        return store(make_document(data.name, data.text))
    except DocumentError as exc:
        raise HTTPException(422, str(exc))


class AnalysisRequest(BaseModel):
    resume_id: str
    vacancy_ids: list[str] = Field(min_length=1, max_length=8)
    mode: str = "hybrid"


def run_job(job, resume, vacancies, mode):
    def progress(value, text):
        job.update(progress=value, message=text)
    try:
        result = analyze(resume, vacancies, mode, progress, lambda: job.get("cancel_requested", False))
        if job.get("cancel_requested"):
            job.update(state="cancelled", message="Разбор остановлен")
        else:
            job.update(state="done", progress=100, result=result, message="Разбор готов")
    except InterruptedError:
        job.update(state="cancelled", message="Разбор остановлен")
    except Exception as exc:
        job.update(state="error", message=str(exc))


@app.post("/api/analyses")
def start_analysis(data: AnalysisRequest):
    if data.mode not in {"hybrid", "rules"}:
        raise HTTPException(422, "Неизвестный режим анализа")
    try:
        resume = documents[data.resume_id]
        ids = list(dict.fromkeys(data.vacancy_ids))
        vacancies = [documents[i] for i in ids]
    except KeyError:
        raise HTTPException(404, "Документ не найден. Загрузите документы заново.")
    if data.resume_id in ids:
        raise HTTPException(422, "Резюме и вакансия должны быть разными документами")
    with lock:
        if any(j["state"] == "running" for j in jobs.values()):
            raise HTTPException(409, "Уже идёт локальный разбор. Дождитесь его или остановите.")
        if len(jobs) >= 30:
            oldest = next(iter(jobs))
            del jobs[oldest]
        ident = uuid.uuid4().hex
        job = {"id": ident, "state": "running", "progress": 0, "message": "Готовим документы…"}
        jobs[ident] = job
        threading.Thread(target=run_job, args=(job, resume, vacancies, data.mode), daemon=True).start()
    return {k: v for k, v in job.items() if k != "result"}


@app.get("/api/analyses/{ident}")
def job_status(ident: str):
    if ident not in jobs:
        raise HTTPException(404, "Разбор не найден. Сессия могла быть очищена.")
    return jobs[ident]


@app.post("/api/analyses/{ident}/cancel")
def cancel_job(ident: str):
    if ident not in jobs:
        raise HTTPException(404, "Разбор не найден")
    job = jobs[ident]
    if job["state"] == "running":
        job["cancel_requested"] = True
        runtime.cancel()
    return {"status": "cancellation_requested"}


@app.get("/api/analyses/{ident}/export/{format}")
def export(ident: str, format: str):
    job = jobs.get(ident)
    if not job or job["state"] != "done":
        raise HTTPException(409, "Сначала завершите разбор")
    if format == "json":
        body = json.dumps(job["result"], ensure_ascii=False, indent=2)
        mime = "application/json"
    elif format == "html":
        body = report_html(job["result"])
        mime = "text/html"
    else:
        raise HTTPException(404, "Формат не поддерживается")
    return Response(body, media_type=mime, headers={"Content-Disposition": f'attachment; filename="vacancies-report.{format}"'})


@app.delete("/api/session")
def clear_session():
    if any(j["state"] == "running" for j in jobs.values()):
        raise HTTPException(409, "Сначала остановите текущий разбор")
    documents.clear()
    jobs.clear()
    runtime.cancel()
    return {"status": "cleared"}


@app.get("/")
def index():
    return FileResponse(ROOT / "app/static/index.html")


app.mount("/static", StaticFiles(directory=ROOT / "app/static"), name="static")
