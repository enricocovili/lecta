# Lecta application image: used by both the `backend` (FastAPI) and the
# `worker` (job queue) services.
FROM python:3.12.14-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HUB_DISABLE_TELEMETRY=1 \
    DO_NOT_TRACK=1 \
    LECTA_MODEL_DIR=/opt/models

# pandoc (Markdown -> LaTeX, run with --sandbox); libgomp for onnxruntime.
RUN apt-get update \
 && apt-get install -y --no-install-recommends pandoc libgomp1 tini \
 && rm -rf /var/lib/apt/lists/*

RUN groupadd -g 1000 app && useradd -u 1000 -g app -m -s /usr/sbin/nologin app \
 && mkdir -p /data /latex-work /run/lecta /secrets /opt/models /app \
 && chown app:app /data /latex-work /run/lecta /opt/models /app

WORKDIR /app
COPY requirements.txt requirements-dev.txt ./
RUN pip install -r requirements.txt -r requirements-dev.txt

# The local embedding model is downloaded at build time, never at runtime.
COPY scripts/download_models.py scripts/download_models.py
RUN su app -s /bin/sh -c "python scripts/download_models.py"
ENV HF_HUB_OFFLINE=1

COPY --chown=app:app . .
USER app
ENTRYPOINT ["/usr/bin/tini", "--", "/app/scripts/entrypoint.sh"]
CMD ["backend"]
