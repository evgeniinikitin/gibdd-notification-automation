FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    apt-get update \
    && apt-get install -y --no-install-recommends curl libmagic1 libreoffice-writer libreoffice-calc poppler-utils \
    && true

COPY pyproject.toml ./
RUN --mount=type=cache,target=/root/.cache/pip \
    python -c 'import subprocess, sys, tomllib; dependencies = tomllib.load(open("pyproject.toml", "rb"))["project"]["dependencies"]; subprocess.check_call([sys.executable, "-m", "pip", "install", "--upgrade", "pip"]); subprocess.check_call([sys.executable, "-m", "pip", "install", *dependencies])'

RUN playwright install --with-deps chromium

COPY app ./app
COPY assets ./assets
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-deps .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
