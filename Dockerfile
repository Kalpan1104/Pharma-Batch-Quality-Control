# One image = one validated release: code + model + validation report, identified by its digest.
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY pyproject.toml .
COPY src ./src
RUN pip install --no-deps .

# ---- train + validate inside the build: a model that fails acceptance criteria can't be built ----
FROM base AS train
ARG MODEL_VERSION=dev
ARG GIT_SHA=unknown
ARG TRAIN_ARGS=""
ENV MODEL_VERSION=${MODEL_VERSION} GIT_SHA=${GIT_SHA}
COPY data ./data
RUN python -m batchguard.train --data-dir data --out /app/artifacts ${TRAIN_ARGS}

# ---- runtime ----
FROM base AS runtime
ARG MODEL_VERSION=dev
ARG GIT_SHA=unknown
LABEL org.opencontainers.image.revision="${GIT_SHA}" \
      org.opencontainers.image.title="batchguard" \
      batchguard.model_version="${MODEL_VERSION}"
ENV MODEL_VERSION=${MODEL_VERSION} MODEL_PATH=/app/artifacts/model.joblib PORT=8080
COPY --from=train /app/artifacts /app/artifacts
RUN useradd --create-home --uid 10001 app && chown -R app /app
USER app
EXPOSE 8080
# The API by default; the monitoring Cloud Run Job runs `python -m batchguard.monitor` from this same image.
CMD exec gunicorn --bind ":${PORT}" --workers 1 --threads 8 --timeout 60 "batchguard.app:create_app()"
