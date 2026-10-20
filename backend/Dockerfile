FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY pyproject.toml ./
RUN mkdir app && pip install --no-cache-dir . && rm -rf app

COPY . .
RUN pip install --no-cache-dir --no-deps . && useradd --create-home truthsplit && mkdir -p /data/storage && chown truthsplit /data/storage
USER truthsplit

EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
