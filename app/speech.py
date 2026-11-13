"""Offline Russian speech. Models are provisioned explicitly, never at request time."""
from __future__ import annotations

import io
import importlib.util
import json
import os
from pathlib import Path
import threading
import wave

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

router = APIRouter(prefix="/api/portal/speech")
MAX_AUDIO_BYTES = 2_000_000
MAX_SECONDS = 60
_lock = threading.Lock()
_recognizer_model = None
_voice = None


def model_directory():
    return Path(os.environ.get("SPEECH_MODEL_DIR", Path(__file__).resolve().parents[1] / "models/speech"))


def availability():
    root = model_directory()
    return {"stt": importlib.util.find_spec("vosk") is not None and (root / "vosk-model-small-ru-0.22/conf/model.conf").is_file(),
            "tts": importlib.util.find_spec("piper") is not None and all((root / name).is_file() for name in
                       ("ru_RU-denis-medium.onnx", "ru_RU-denis-medium.onnx.json")),
            "processing": "local", "max_seconds": MAX_SECONDS}


def pcm_frames(data):
    try:
        with wave.open(io.BytesIO(data), "rb") as audio:
            if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate(), audio.getcomptype()) != (1, 2, 16000, "NONE"):
                raise ValueError("Нужна запись WAV: один канал, 16 кГц, PCM 16 бит.")
            count = audio.getnframes()
            if not 1600 <= count <= MAX_SECONDS * 16000:
                raise ValueError("Запишите от 0,1 до 60 секунд речи.")
            frames = audio.readframes(count)
            if len(frames) != count * 2:
                raise ValueError("Аудиозапись повреждена. Запишите вопрос ещё раз.")
            return frames
    except (wave.Error, EOFError):
        raise ValueError("Не удалось прочитать запись. Запишите вопрос ещё раз.")


def transcribe(data):
    frames = pcm_frames(data)
    if not availability()["stt"]:
        raise HTTPException(503, "Голосовой ввод пока не настроен. Установите речевые модели по инструкции запуска.")
    if not _lock.acquire(blocking=False):
        raise HTTPException(429, "Речь уже обрабатывается. Дождитесь завершения и повторите.")
    try:
        global _recognizer_model
        from vosk import Model, KaldiRecognizer, SetLogLevel
        SetLogLevel(-1)
        if _recognizer_model is None:
            _recognizer_model = Model(str(model_directory() / "vosk-model-small-ru-0.22"))
        recognizer = KaldiRecognizer(_recognizer_model, 16000)
        parts = []
        for offset in range(0, len(frames), 8000):
            if recognizer.AcceptWaveform(frames[offset:offset + 8000]):
                parts.append(json.loads(recognizer.Result()).get("text", ""))
        parts.append(json.loads(recognizer.FinalResult()).get("text", ""))
        text = " ".join(part for part in parts if part).strip()
        if not text:
            raise ValueError("Речь не распознана. Попробуйте говорить ближе к микрофону или введите вопрос текстом.")
        return {"text": text, "processing": "local"}
    finally:
        _lock.release()


def synthesize(text):
    text = text.strip()
    if not text:
        raise ValueError("Нет текста для озвучивания.")
    if not availability()["tts"]:
        raise HTTPException(503, "Озвучивание пока не настроено. Установите речевые модели по инструкции запуска.")
    if not _lock.acquire(blocking=False):
        raise HTTPException(429, "Речь уже обрабатывается. Дождитесь завершения и повторите.")
    try:
        global _voice
        if _voice is None:
            import onnxruntime
            onnxruntime.disable_telemetry_events()
            from piper import PiperVoice
            _voice = PiperVoice.load(str(model_directory() / "ru_RU-denis-medium.onnx"))
        output = io.BytesIO()
        with wave.open(output, "wb") as audio:
            _voice.synthesize_wav(text, audio)
        return output.getvalue()
    finally:
        _lock.release()


@router.get("")
def status():
    return availability()


@router.post("/transcribe")
async def transcribe_upload(file: UploadFile = File(...)):
    try:
        data = await file.read(MAX_AUDIO_BYTES + 1)
        if len(data) > MAX_AUDIO_BYTES:
            raise HTTPException(413, "Запись слишком большая. Максимум — 60 секунд.")
        return await run_in_threadpool(transcribe, data)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    finally:
        await file.close()


class SpeechText(BaseModel):
    text: str = Field(min_length=1, max_length=20000)


@router.post("/synthesize")
def synthesize_text(data: SpeechText):
    try:
        return Response(synthesize(data.text), media_type="audio/wav")
    except ValueError as exc:
        raise HTTPException(422, str(exc))
