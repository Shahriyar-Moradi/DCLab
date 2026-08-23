# Future containerized image. Local development uses venv + uvicorn (`make run`).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Build deps for psycopg2-binary are already bundled, but keep this minimal image lean.
COPY pyproject.toml ./
COPY apps ./apps

RUN pip install --no-cache-dir -e .

EXPOSE 8000

# Step 9 will finalize this with automatic migrations via an entrypoint.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
