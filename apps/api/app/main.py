from fastapi import FastAPI, HTTPException
from sqlalchemy import text

from app.api.decisions import router as decisions_router
from app.api.opportunities import router as opportunities_router
from app.db.session import get_engine

app = FastAPI(title="Decision.ai", version="0.1.0")
app.include_router(opportunities_router)
app.include_router(decisions_router)


@app.get("/health")
def health():
    try:
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=503,
            detail={"status": "error", "db": "disconnected"},
        ) from exc
    return {"status": "ok", "db": "connected"}
