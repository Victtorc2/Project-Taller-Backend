from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app import appointments, auth, clients, continuity, orders, parts, payments, reports, requests
from app.core.config import get_settings
from app.db import get_db

settings = get_settings()
app = FastAPI(title="Taller Automotriz Córdova API", version="0.3.0", description="Fases 0–8: procesos P01–P06 y continuidad")
app.add_middleware(CORSMiddleware, allow_origins=[origin.strip() for origin in settings.cors_origins.split(",") if origin.strip()], allow_credentials=True, allow_methods=["GET", "POST", "PATCH"], allow_headers=["Authorization", "Content-Type"])
for router in (auth.router, clients.router, requests.router, appointments.router, orders.router, parts.router, payments.router, reports.router, continuity.router):
    app.include_router(router, prefix="/api/v1")


@app.get("/health", tags=["Sistema"])
def health():
    return {"status": "ok"}


@app.get("/health/db", tags=["Sistema"])
def database_health(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError:
        raise HTTPException(503, "Base de datos no disponible") from None
    return {"status": "ok"}
