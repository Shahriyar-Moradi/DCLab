# Future containerized image. Local development uses venv + uvicorn (`make run`).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Dependencies come from the locked set (psycopg wheels are bundled).
COPY pyproject.toml requirements.lock ./
COPY apps ./apps

RUN pip install --no-cache-dir -r requirements.lock && \
    pip install --no-cache-dir -e . --no-deps

EXPOSE 8001

# Step 9 will finalize this with automatic migrations via an entrypoint.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]
