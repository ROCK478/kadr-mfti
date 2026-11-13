"""Browser-owned persistence. Server copies exist in RAM only and expire."""
from __future__ import annotations

import base64
import hashlib
from contextvars import ContextVar
from dataclasses import dataclass
import json
import os
import secrets
import threading
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from .workspace import Workspace

MAX_SNAPSHOT_BYTES = 48 * 1024 * 1024
TABLES = {
    'meta': ('key','value'),
    'documents': ('id','kind','body'),
    'applications': ('vacancy_id','resume_id','source'),
    'originals': ('id','kind','filename','data'),
    'evaluations': ('vacancy_id','resume_id','body','model'),
    'chats': ('id','title','body','position'),
    'archived_documents': ('id','kind'),
    'vacancy_lifecycle': ('vacancy_id','closed'),
    'selections': ('vacancy_id','resume_id','stage'),
}
LIMITS = {'meta':10,'documents':100,'originals':100,'evaluations':2500,'applications':2500,
          'chats':100,'archived_documents':100,'vacancy_lifecycle':100,'selections':2500}
current_session:ContextVar[Session | None] = ContextVar('browser_workspace',default=None)
router=APIRouter(prefix='/api/portal/session')


def enabled():return os.environ.get('BROWSER_WORKSPACE','0')=='1'
def owner():
    session=current_session.get()
    return session.token if session else 'local'


def snapshot(store):
    result={}
    with store.connection() as db:
        for table,columns in TABLES.items():
            rows=[]
            for entry in db.execute(f'SELECT {",".join(columns)} FROM {table} ORDER BY rowid'):
                row=list(entry)
                if table=='originals':row[-1]=base64.b64encode(row[-1]).decode('ascii')
                rows.append(row)
            result[table]=rows
    return {'format':1,'tables':result}


def validated_snapshot(value):
    """Only data enter parameterized INSERTs, never SQL from the browser."""
    if not isinstance(value,dict) or value.get('format')!=1 or not isinstance(value.get('tables'),dict):
        raise ValueError('Не удалось прочитать сохранённый кабинет.')
    if len(json.dumps(value,ensure_ascii=False).encode())>MAX_SNAPSHOT_BYTES:raise ValueError('Кабинет слишком велик для восстановления.')
    tables=value['tables']
    if set(tables)!=set(TABLES):raise ValueError('Неподдерживаемый состав сохранённых данных.')
    result={}
    for table,columns in TABLES.items():
        rows=tables[table]
        if not isinstance(rows,list) or len(rows)>LIMITS[table]:raise ValueError('Слишком много записей в кабинете.')
        result[table]=[]
        for row in rows:
            if not isinstance(row,list) or len(row)!=len(columns) or any(type(cell) not in (str,int) for cell in row):
                raise ValueError('Сохранённые данные повреждены.')
            item=list(row)
            if table=='originals':
                try:item[-1]=base64.b64decode(item[-1],validate=True)
                except (ValueError,TypeError):raise ValueError('Исходный файл повреждён.')
                if len(item[-1])>12*1024*1024:raise ValueError('Исходный файл слишком велик.')
            for index,column in enumerate(columns):
                if column in {'body','model','value'}:
                    try:parsed=json.loads(item[index])
                    except (ValueError,TypeError):raise ValueError('Сохранённая запись повреждена.')
                    if column in {'body','model'} and not isinstance(parsed,dict):raise ValueError('Неверный формат записи.')
            if table in {'documents','originals','archived_documents'} and item[1] not in {'resume','vacancy'}:
                raise ValueError('Неизвестный тип документа.')
            if table=='documents':
                document=json.loads(item[2])
                if document.get('id')!=item[0] or not isinstance(document.get('text'),str) or not isinstance(document.get('blocks'),list):
                    raise ValueError('Сохранённый документ повреждён.')
            if table=='selections' and item[2] not in {'selected','contacted'}:raise ValueError('Неверный этап подбора.')
            result[table].append(item)
    if ['seeded','true'] not in result['meta']:raise ValueError('Не найдено состояние кабинета.')
    return result


def restore(store, value):
    tables=validated_snapshot(value)
    with store.lock,store.connection() as db:
        for table,columns in TABLES.items():
            db.execute(f'DELETE FROM {table}')
            db.executemany(f'INSERT INTO {table} ({",".join(columns)}) VALUES ({",".join("?" for _ in columns)})',tables[table])


@dataclass
class Session:
    token:str
    store:Workspace
    touched:float
    requests:int=0


class Sessions:
    def __init__(self):
        self.entries={};self.lock=threading.RLock();self.template=None
        self.busy=lambda token:False
        self.on_expire=lambda token:None
        threading.Thread(target=self._reap_loop,daemon=True).start()

    def _reap_loop(self):
        while True:
            threading.Event().wait(60)
            with self.lock:self._prune()

    def _prune(self):
        ttl=int(os.environ.get('BROWSER_SESSION_TTL','1800'))
        for token,entry in list(self.entries.items()):
            if time.monotonic()-entry.touched>ttl and not entry.requests and not self.busy(token):
                del self.entries[token];entry.store.close();self.on_expire(token)

    def create(self, token=None, value=None):
        with self.lock:
            self._prune()
            if token and token in self.entries:
                entry=self.entries[token];entry.touched=time.monotonic();return entry
            if len(self.entries)>=int(os.environ.get('BROWSER_SESSION_LIMIT','12')):
                raise HTTPException(429,'Все места обработки заняты. Попробуйте чуть позже; ваш кабинет сохранён в браузере.')
            if self.template is None:
                clean=Workspace(':memory:')
                self.template=snapshot(clean);clean.close()
            store=Workspace(':memory:',initialize=False)
            try:
                restore(store, value if value is not None else self.template)
                # Validate restored documents/relationships before using them in UI.
                store.overview();store.chats()
            except Exception as exc:
                store.close()
                raise ValueError('Не удалось восстановить кабинет. Локальная копия сохранена.') from exc
            entry=Session(secrets.token_urlsafe(32),store,time.monotonic())
            self.entries[entry.token]=entry
            return entry

    def acquire(self,token):
        with self.lock:
            self._prune()
            entry=self.entries.get(token)
            if not entry:raise HTTPException(401,'Кабинет нужно восстановить из браузера.')
            entry.requests+=1;entry.touched=time.monotonic();return entry

    def release(self,entry):
        with self.lock:entry.requests-=1;entry.touched=time.monotonic()

    def clear(self):
        with self.lock:
            for entry in self.entries.values():
                entry.store.close();self.on_expire(entry.token)
            self.entries.clear()


sessions=Sessions()


@router.get('/config')
def config():return {'browser_storage':enabled(),'max_snapshot_bytes':MAX_SNAPSHOT_BYTES}


class SessionInput(BaseModel):
    token:str|None=Field(default=None,max_length=100)
    snapshot:dict|None=None


@router.post('')
def start(data:SessionInput):
    if not enabled():raise HTTPException(404,'Браузерный режим не включён')
    try:entry=sessions.create(data.token,data.snapshot)
    except ValueError as exc:raise HTTPException(422,str(exc))
    return {'token':entry.token}


@router.get('/snapshot')
def checkpoint(request:Request):
    entry=current_session.get()
    if not entry:raise HTTPException(404,'Браузерный режим не включён')
    with entry.store.lock:
        etag=f'"{hashlib.sha256(entry.token.encode()).hexdigest()[:16]}:{entry.store._memory_connection.total_changes}"'
        if request.headers.get('if-none-match')==etag:return Response(status_code=304,headers={'ETag':etag})
        value=snapshot(entry.store)
    return JSONResponse(value,headers={'ETag':etag})
