"""Install pinned offline speech weights; no documents or audio are uploaded."""
import argparse
import hashlib
from pathlib import Path
import ssl
import urllib.request
import zipfile

import certifi

REVISION = "ae9d8352b73c1b1750748843e753a504b00e05b0"
VOICE_BASE = f"https://huggingface.co/rhasspy/piper-voices/resolve/{REVISION}/ru/ru_RU/denis/medium"
FILES = [
    ("vosk.zip", "https://alphacephei.com/vosk/models/vosk-model-small-ru-0.22.zip",
     "961d5ff98a17f4aa6de69864d0aa71fa5bac682301d2b5d17a3f24c5c99a46d4"),
    ("ru_RU-denis-medium.onnx", VOICE_BASE + "/ru_RU-denis-medium.onnx",
     "879bc365cb14d1380e3f5ed6044162ccf54c99f6161871569b7f830aec3ce841"),
    ("ru_RU-denis-medium.onnx.json", VOICE_BASE + "/ru_RU-denis-medium.onnx.json",
     "e4fb4ab60262d1ab802c8c39301976c4135e545bc834a33c6eedc1f764df3bd6"),
]


def digest(path):
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def install(directory):
    directory.mkdir(parents=True, exist_ok=True)
    context = ssl.create_default_context(cafile=certifi.where())
    for name, url, expected in FILES:
        target = directory / name
        if not target.is_file() or digest(target) != expected:
            temporary = directory / (name + ".part")
            print("Загружаем", name, flush=True)
            with urllib.request.urlopen(url, context=context, timeout=90) as source, temporary.open("wb") as output:
                while chunk := source.read(1024 * 1024):
                    output.write(chunk)
            if digest(temporary) != expected:
                temporary.unlink()
                raise RuntimeError("Контрольная сумма не совпала: " + name)
            temporary.replace(target)
        print("SHA-256 проверена:", name, flush=True)
    with zipfile.ZipFile(directory / "vosk.zip") as archive:
        for member in archive.infolist():
            destination = (directory / member.filename).resolve()
            if not destination.is_relative_to(directory.resolve()):
                raise RuntimeError("Недопустимый путь в архиве модели")
        archive.extractall(directory)
    print("Речевые модели готовы. Обработка речи работает без интернета.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, default=Path("models/speech"))
    install(parser.parse_args().directory)
