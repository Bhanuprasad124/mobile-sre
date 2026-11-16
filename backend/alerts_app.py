from fastapi import FastAPI
from backend.main import app as dashboard_app
from backend.alerts_api import router as alerts_router

# Register alert API routes before mounting the existing dashboard app so
# /api/alerts is not shadowed by its static-file mount.
app = FastAPI(title="Mobile SRE", version="0.6.0")
app.include_router(alerts_router)
app.mount("/", dashboard_app)
