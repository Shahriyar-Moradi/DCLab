from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api.decisions import router as decisions_router
from app.api.lab import router as lab_router
from app.api.opportunities import router as opportunities_router
from app.api.simulations import router as simulations_router
from app.config import get_settings
from app.db.session import get_engine

app = FastAPI(title="Decision.ai", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in get_settings().cors_origins.split(",") if origin.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(opportunities_router)
app.include_router(decisions_router)
app.include_router(simulations_router)
app.include_router(lab_router)


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
