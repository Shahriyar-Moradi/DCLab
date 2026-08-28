from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api.admin_client_uploads import router as admin_client_uploads_router
from app.api.admin_model_registry import router as admin_model_registry_router
from app.api.admin_monitoring import router as admin_monitoring_router
from app.api.admin_organizations import router as admin_organizations_router
from app.api.auth import router as auth_router
from app.api.client_labs import router as client_labs_router
from app.api.decisions import router as decisions_router
from app.api.deps import require_admin, require_client
from app.api.insights import router as insights_router
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

# Two route trees, separated by role rather than by convention. The guard lives on
# the parent router, so every route mounted underneath inherits it — a new admin
# endpoint cannot be added without the admin check, and the Step 0 audit asserts
# this by enumerating the live route table rather than a hand-maintained list.
admin_api = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])
client_api = APIRouter(prefix="/app", dependencies=[Depends(require_client)])

# Admin surface: full, unrestricted ML detail for the DCLab team. `simulations`
# lives here (not on /app) because it retrains models on demand and returns raw
# metrics/candidate/fusion detail by design — it is an internal engine harness,
# not a client insight. Step 5 (Client Labs) builds the bounded, translated
# client-facing equivalent on top of the same engine.
admin_api.include_router(lab_router)
admin_api.include_router(simulations_router)
admin_api.include_router(admin_organizations_router)
admin_api.include_router(admin_model_registry_router)
admin_api.include_router(admin_monitoring_router)
admin_api.include_router(admin_client_uploads_router)

# Client surface: business objects only, always through app.translation.
client_api.include_router(opportunities_router)
client_api.include_router(decisions_router)
client_api.include_router(insights_router)
client_api.include_router(client_labs_router)

app.include_router(auth_router)
app.include_router(admin_api)
app.include_router(client_api)


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
