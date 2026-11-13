# Компоненты и источники

| Компонент | Назначение | Условия |
| --- | --- | --- |
| Исходники приложения | Локальный веб и сопоставление | GPL-3.0-or-later, LICENSE |
| FastAPI и Starlette | HTTP API и локальные файлы | MIT / BSD-3-Clause |
| Uvicorn | ASGI-процесс | BSD-3-Clause |
| Pydantic | Проверка запросов | MIT |
| python-multipart | Импорт файлов | Apache-2.0 |
| python-docx / lxml | Чтение DOCX | MIT / BSD и notices libxml2/libxslt |
| pypdf | Извлечение PDF | BSD-3-Clause |
| llama-cpp-python / llama.cpp | Локальный inference | MIT |
| Qwen3 4B GGUF | Веса Q4_K_M | Apache-2.0, licenses/Qwen-Apache-2.0.txt |
| NumPy, Jinja2, MarkupSafe, diskcache | Зависимости локальной модели | BSD-3-Clause / Apache-2.0; notices в пакетах |
| certifi | TLS только для установки весов | MPL-2.0 |
| Python и Debian base image | Контейнерная среда | PSF и лицензии включённых Debian-пакетов |

В активном продукте нет PyQt6, Qt, PyInstaller или зависимости от desktop-сборки. Входные DOCX предоставлены организаторами для кейса; исходники этих материалов не редактировались. Они не получают лицензию приложения автоматически.

Официальная модель: https://huggingface.co/Qwen/Qwen3-4B-GGUF
Ревизия: bc640142c66e1fdd12af0bd68f40445458f3869b
SHA-256: 7485fe6f11af29433bc51cab58009521f205840f5b4ae3a32fa7f92e8534fdf5

Сетевые сервисы во время анализа отсутствуют. Docker Hub, PyPI и Hugging Face используются только для установки. Docker Desktop имеет отдельные условия и не обязателен: подходят Docker Engine и Compose.

Фирменные материалы Такта: оригинальный SVG-знак, ICO и кодовая векторная система иконок; абстрактная растровая иллюстрация создана для прототипа средствами imagegen. Во время работы приложения генерация изображений и внешние графические сервисы не используются. SQLite поставляется с Python.

Golos Text — https://github.com/googlefonts/golos-text, SIL Open Font License1.1. Лицензия: licenses/Golos-Text-OFL.txt; бинарный файл поставляется локально, SHA-256 записан в app/static/assets/fonts/SOURCE.md.

## Речь

- Vosk 0.3.45 и русская vosk-model-small-ru-0.22 — Apache-2.0. https://alphacephei.com/vosk/models и https://github.com/alphacep/vosk-api ; лицензия licenses/Vosk-Apache-2.0.txt.
- Piper 1.8.0 — GPL-3.0, https://github.com/OHF-Voice/piper1-gpl ; licenses/Piper-GPL-3.0.txt. Включает eSpeak NG и его notices в пакете. Лицензия приложения GPL-3.0-or-later сохранена.
- Denis medium, ru_RU — карточка licenses/Denis-MODEL_CARD.txt, источник https://huggingface.co/rhasspy/piper-voices/tree/ae9d8352b73c1b1750748843e753a504b00e05b0/ru/ru_RU/denis/medium ; в карточке указан CC0 для набора данных и базового голоса. Это не передача лицензии приложения входным материалам.
- ONNX Runtime — MIT, https://github.com/microsoft/onnxruntime . Телеметрия отключается до создания inference-сессии. Зависимости и их notices поставляются в Python-пакетах контейнера.
- Ревизия голоса и SHA-256 всех трёх загрузок закреплены в scripts/download_speech_models.py. Веса models/speech исключены из Git и build context, монтируются read-only.
