import io
import wave

from fastapi.testclient import TestClient
import pytest

from app.main import app
from app.speech import pcm_frames


def recording(seconds=1, rate=16000, channels=1):
    output = io.BytesIO()
    with wave.open(output, 'wb') as audio:
        audio.setnchannels(channels)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(b'\0\0' * int(seconds * rate) * channels)
    return output.getvalue()


@pytest.mark.parametrize('audio', [b'not a wave file', recording(rate=48000),
                                    recording(channels=2), recording(seconds=61),
                                    recording(seconds=.05), recording()[:-20]])
def test_rejects_invalid_or_unbounded_recordings(audio):
    with pytest.raises(ValueError):
        pcm_frames(audio)


def test_wav_frame_validation_preserves_audio():
    assert pcm_frames(recording()) == b'\0\0' * 16000


def test_speech_api_limits_and_unconfigured_setup(monkeypatch, tmp_path):
    monkeypatch.setenv('SPEECH_MODEL_DIR', str(tmp_path))
    with TestClient(app) as client:
        assert client.get('/api/portal/speech').json() == {
            'stt': False, 'tts': False, 'processing': 'local', 'max_seconds': 60}
        assert client.post('/api/portal/speech/transcribe', files={'file': ('x.wav', recording())}).status_code == 503
        assert client.post('/api/portal/speech/transcribe', files={'file': ('x.wav', b'wrong')}).status_code == 422
        assert client.post('/api/portal/speech/transcribe', files={'file': ('x.wav', b'0' * 2_000_001)}).status_code == 413
        assert client.post('/api/portal/speech/synthesize', json={'text': 'Привет'}).status_code == 503
        assert client.post('/api/portal/speech/synthesize', json={'text': '  '}).status_code == 422
        assert client.post('/api/portal/speech/synthesize', json={'text': 'a' * 20001}).status_code == 422
