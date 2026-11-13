"""Real offline TTS → STT check, plus silence recovery. Run inside the image."""
import io
import json
from pathlib import Path
import sys
import time
import wave

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.speech import availability, synthesize, transcribe


def wav16(audio):
    with wave.open(io.BytesIO(audio), 'rb') as source:
        samples = np.frombuffer(source.readframes(source.getnframes()), dtype='<i2')
        rate = source.getframerate()
    count = round(len(samples) * 16000 / rate)
    samples = np.interp(np.arange(count) * rate / 16000, np.arange(len(samples)), samples).astype('<i2')
    output = io.BytesIO()
    with wave.open(output, 'wb') as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(16000)
        target.writeframes(samples.tobytes())
    return output.getvalue()


if __name__ == '__main__':
    assert availability()['stt'] and availability()['tts'], availability()
    phrase = 'Какие вакансии можно предложить Иванову и почему?'
    start = time.monotonic()
    audio = synthesize(phrase)
    tts_seconds = time.monotonic() - start
    start = time.monotonic()
    result = transcribe(wav16(audio))
    stt_seconds = time.monotonic() - start
    assert all(word in result['text'] for word in ['вакансии', 'предложить', 'почему']), result
    silence = io.BytesIO()
    with wave.open(silence, 'wb') as target:
        target.setnchannels(1); target.setsampwidth(2); target.setframerate(16000)
        target.writeframes(b'\0\0' * 16000)
    try:
        transcribe(silence.getvalue())
        raise AssertionError('Silence was accepted')
    except ValueError as error:
        assert 'не распознана' in str(error)
    print(json.dumps({'input': phrase, 'recognized': result['text'], 'audio_bytes': len(audio),
                      'tts_seconds': round(tts_seconds, 2), 'stt_seconds': round(stt_seconds, 2),
                      'silence_rejected': True, 'processing': 'local'}, ensure_ascii=False, indent=2))
