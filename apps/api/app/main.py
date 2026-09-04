from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api.admin_client_uploads import router as admin_client_uploads_router
from app.api.admin_model_registry import router as admin_model_registry_router
from app.api.admin_ml_verifications import router as admin_ml_verifications_router
from app.api.admin_monitoring import router as admin_monitoring_router
from app.api.admin_organizations import router as admin_organizations_router
from app.api.auth import router as auth_router
from app.api.business_explorer import router as business_explorer_router
from app.api.client_labs import router as client_labs_router
from app.api.decisions import router as decisions_router
from app.api.deps import require_admin, require_client, require_development
from app.api.development import router as development_router
from app.api.insights import router as insights_router
from app.api.lab import router as lab_router
from app.api.opportunities import router as opportunities_router
from app.api.observability import admin_router as admin_observability_router
from app.api.observability import business_router as business_observability_router
from app.api.platform_explorer import router as platform_explorer_router
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

# Route trees are separated by product responsibility and guarded at their parent
# router, so new endpoints inherit the correct authorization boundary by default.
admin_api = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])
client_api = APIRouter(prefix="/app", dependencies=[Depends(require_client)])
business_api = APIRouter(prefix="/business", dependencies=[Depends(require_client)])
development_api = APIRouter(
    prefix="/development", dependencies=[Depends(require_development)]
)

# Platform surface: full ML detail for the DCLab team. Platform developers may
# inspect it, while method-aware authorization reserves writes for platform admins.
# `simulations`
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
admin_api.include_router(admin_ml_verifications_router)
admin_api.include_router(admin_observability_router)
admin_api.include_router(platform_explorer_router)

# Technical workspace administration is deliberately separate from the translated
# end-user `/app` surface. It shares the same persisted workspace authorization
# context, but may expose the precise pipeline vocabulary administrators need.
business_api.include_router(business_observability_router)

# Shared ML-engineering surface. Personal and Business members use the same core
# workspace resources here; Business organization/admin controls remain separate.
development_api.include_router(development_router)

# Client surface: business objects only, always through app.translation.
client_api.include_router(opportunities_router)
client_api.include_router(decisions_router)
client_api.include_router(insights_router)
client_api.include_router(client_labs_router)

app.include_router(auth_router)
app.include_router(business_explorer_router)
app.include_router(admin_api)
app.include_router(business_api)
app.include_router(development_api)
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
