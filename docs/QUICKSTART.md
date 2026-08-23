# Quickstart

## 1. Create environment

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## 2. Start infrastructure

```bash
docker compose up -d
```

## 3. Start API

```bash
uvicorn app.main:app --reload
```

## 4. Check

```bash
curl http://127.0.0.1:8000/health
```

## 5. Next implementation

Build the purchase_probability vertical slice in `docs/FIRST_EXPERIMENT.md` before expanding the platform.
