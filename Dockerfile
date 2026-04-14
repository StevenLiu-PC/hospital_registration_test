# syntax=docker/dockerfile:1

FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

FROM base AS test
COPY . .
RUN chmod +x docker/run-tests.sh
CMD ["sh", "docker/run-tests.sh"]

FROM base AS runtime
COPY . .
EXPOSE 5000
CMD ["flask", "--app", "api.mock_server:app", "run", "--host=0.0.0.0", "--port=5000"]