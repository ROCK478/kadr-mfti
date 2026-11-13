FROM python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df AS build
ENV CMAKE_BUILD_PARALLEL_LEVEL=2 PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1 CMAKE_ARGS="-DGGML_NATIVE=OFF -DGGML_METAL=OFF -DGGML_CUDA=OFF"
RUN apt-get update && apt-get install -y --no-install-recommends build-essential cmake pkg-config && rm -rf /var/lib/apt/lists/*
WORKDIR /build
COPY requirements.txt .
RUN python -m pip wheel --wheel-dir=/wheels -r requirements.txt

FROM python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 MODEL_PATH=/models/Qwen3-4B-Q4_K_M.gguf MODEL_THREADS=4 LLM_MAX_ROWS=6 MODEL_TIMEOUT=180 WORKSPACE_PATH=/data/workspace.sqlite3 PORTAL_LLM_ROWS=2
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 libatomic1 ca-certificates && rm -rf /var/lib/apt/lists/* && useradd --create-home --uid 10001 appuser
RUN mkdir -p /data && chown 10001:10001 /data
WORKDIR /app
COPY --from=build /wheels /wheels
RUN python -m pip install --no-index --find-links=/wheels /wheels/*.whl && rm -rf /wheels
COPY app ./app
COPY scripts ./scripts
COPY examples ./examples
COPY docs ./docs
COPY README.md LICENSE THIRD_PARTY.md ./
COPY licenses ./licenses
USER appuser
EXPOSE 8000
HEALTHCHECK --interval=5s --timeout=3s --start-period=15s --retries=12 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
