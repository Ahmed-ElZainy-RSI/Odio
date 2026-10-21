# Reading First AI Engine -- one image, three roles (T-7.1).
#
# BUILD CONTEXT IS THE REPOSITORY ROOT, not this directory:
#
#     docker build -f service/Dockerfile -t ai-engine:dev .
#
# because the service wraps the legacy pipeline that lives in `src/` and `tools/` (see
# ai_engine/adapters/ai/legacy.py). The root `.dockerignore` keeps the 780 MB of `assets/`
# out of the context.

# ── Builder: compile wheels once, so the runtime layer stays free of toolchains ──
FROM python:3.11-slim-bookworm AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# Copy only what pip needs to resolve dependencies first: this layer is cached until the
# dependency list itself changes, which is the difference between a 4-second rebuild and a
# 4-minute one.
COPY service/pyproject.toml ./pyproject.toml
# Pinned runtime dependencies (spec 011): the same versions every time the image builds.
COPY service/requirements.lock ./requirements.lock
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install -r requirements.lock

COPY service/ai_engine ./ai_engine
RUN /opt/venv/bin/pip install --no-deps .

# ── Optional: the local diacritiser (DIACRITIZER=local, spec 016) ────────────────
# Off by default: it pulls in torch. Turn on with `--build-arg INSTALL_DIACRITIZER=1`;
# a worker started with DIACRITIZER=local on an image without it refuses to start.
ARG INSTALL_DIACRITIZER=0
RUN if [ "$INSTALL_DIACRITIZER" = "1" ]; then \
      /opt/venv/bin/pip install '.[diacritize]' ; \
    fi

# ── Optional: bake the whisper weights in ────────────────────────────────────────
#
# 464 MB for `small`. Off by default: most deployments would rather mount a shared model
# cache than push half a gigabyte per image. Turn on with
# `--build-arg PREFETCH_WHISPER=1` for an image that starts offline with no cold-start
# download, which is what an air-gapped or autoscaling deployment wants.
FROM builder AS models
ARG PREFETCH_WHISPER=0
ARG WHISPER_MODEL=small
ENV HF_HOME=/models
RUN if [ "$PREFETCH_WHISPER" = "1" ]; then \
      /opt/venv/bin/python -c "from faster_whisper import WhisperModel; WhisperModel('${WHISPER_MODEL}', device='cpu', compute_type='int8')" ; \
    else \
      mkdir -p /models ; \
    fi

# ── Runtime ──────────────────────────────────────────────────────────────────────
FROM python:3.11-slim-bookworm AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    HF_HOME=/models \
    # Where `legacy.py` looks for `src/pipeline`.
    LEGACY_REPO_ROOT=/app \
    JOB_STORE=sqlite \
    JOB_STORE_PATH=/var/lib/ai-engine/jobs.db \
    SCRATCH_DIR=/var/lib/ai-engine/scratch

# libgomp is ctranslate2's OpenMP runtime (faster-whisper); the rest of the stack ships
# manylinux wheels and needs no build tools at runtime.
RUN apt-get update \
    && apt-get install --no-install-recommends -y libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv
COPY --from=models /models /models

WORKDIR /app
COPY src ./src
COPY tools ./tools
# the price table for cost metering (spec 025, GET /ops/cost)
COPY config ./config

# Non-root, and it owns exactly the two writable paths: the job database and scratch.
RUN useradd --system --create-home --uid 10001 engine \
    && mkdir -p /var/lib/ai-engine/scratch \
    && chown -R engine:engine /var/lib/ai-engine /models
USER engine

EXPOSE 9099

# No HEALTHCHECK here on purpose. This one image runs three roles and only `api` serves
# HTTP, so an image-level probe against :9099 would mark every worker and reconciler
# unhealthy. The API's probe lives on the `api` service in docker-compose.yml (and would be
# a readinessProbe in Kubernetes); worker liveness is the process itself.
#
# For the record, when you do probe the API: `/health` is liveness and performs NO
# dependency checks -- one there would restart every container during a brief database
# blip. Readiness is `/ready`.

# The role is the command: `ai-engine-api`, `ai-engine-worker`, `ai-engine-reconciler`.
CMD ["ai-engine-api"]
