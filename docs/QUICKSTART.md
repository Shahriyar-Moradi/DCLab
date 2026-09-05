# Quickstart

Native Postgres + venv (not Docker) is the daily path.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[boosting]"
cp .env.example .env
make db
make migrate
make train
make run
```

In another terminal:

```bash
make web
make seed
dclab experiment run --dataset synthetic --task purchase_prediction
```

Docker (`make up`) is behind Compose profile `docker` and does not run migrations automatically yet.
