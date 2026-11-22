from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.clients import managers, staff
from app.closing import boundaries
from app.core.audit import record
from app.core.config import get_business_tz
from app.db import get_db
from app.models import (
    Appointment, Expense, Incident, IncidentStatus, ManualRecord, OrderStatus, Payment,
    PurchaseOrder, Role, ServiceRequest, TrainingEvaluation, User, WorkOrder, WorkProgress, utcnow,
)
from app.operations import lock_sqlite, serialize_date, utc_input

router = APIRouter(tags=["Continuidad y contingencia"])
PROCESS_CODES = {"P01", "P02", "P03", "P04", "P05", "P06"}
ENTITY_MAP = {
    "P01": ("solicitud", ServiceRequest), "P02": ("cita", Appointment),
    "P03": ("orden", WorkOrder), "P04": ("pedido", PurchaseOrder),
    "P05": ("pago", Payment), "P06": ("egreso", Expense),
}


class IncidentCreate(BaseModel):
    process_code: str = Field(pattern=r"^P0[1-6]$")
    severity: str = Field(pattern=r"^(baja|media|alta|critica)$")
    description: str = Field(min_length=5, max_length=3000)
    impact: str = Field(min_length=5, max_length=3000)


class IncidentResolve(BaseModel):
    action_taken: str = Field(min_length=5, max_length=5000)


class ManualRecordCreate(BaseModel):
    temp_code: str = Field(min_length=5, max_length=80, pattern=r"^[A-Za-z0-9._-]+$")
    process_code: str = Field(pattern=r"^P0[1-6]$")
    description: str = Field(min_length=5, max_length=5000)
    occurred_at: datetime


class ReconcileIn(BaseModel):
    entity: str
    entity_id: int = Field(gt=0)
    evidence: str = Field(min_length=5, max_length=3000)


class TrainingCreate(BaseModel):
    user_id: int = Field(gt=0)
    tasks_correct: int = Field(ge=0)
    tasks_total: int = Field(gt=0)
    notes: str | None = Field(default=None, max_length=3000)


def manual_view(item: ManualRecord) -> dict:
    return {"temp_code": item.temp_code, "process_code": item.process_code, "description": item.description, "occurred_at": serialize_date(item.occurred_at), "recorded_by": item.recorded_by, "reconciled_entity": item.reconciled_entity, "reconciled_id": item.reconciled_id, "reconciled_at": serialize_date(item.reconciled_at), "evidence": item.evidence}


@router.post("/incidents", status_code=201)
def report_incident(data: IncidentCreate, actor: User = Depends(staff), db: Session = Depends(get_db)):
    item = Incident(**data.model_dump(), reported_by=actor.id)
    db.add(item)
    db.flush()
    record(db, actor, "reportar", "incidente", item.id)
    db.commit()
    return {"id": item.id, "status": item.status.value, "created_at": serialize_date(item.created_at)}


@router.get("/incidents")
def list_incidents(status: IncidentStatus | None = None, _: User = Depends(staff), db: Session = Depends(get_db)):
    query = db.query(Incident)
    if status:
        query = query.filter(Incident.status == status)
    rows = query.order_by(Incident.id.desc()).limit(100).all()
    return [{"id": row.id, "process_code": row.process_code, "severity": row.severity, "description": row.description, "impact": row.impact, "action_taken": row.action_taken, "status": row.status.value, "reported_by": row.reported_by, "created_at": serialize_date(row.created_at), "resolved_at": serialize_date(row.resolved_at)} for row in rows]


@router.post("/incidents/{incident_id}/resolve")
def resolve_incident(incident_id: int, data: IncidentResolve, actor: User = Depends(managers), db: Session = Depends(get_db)):
    item = db.get(Incident, incident_id)
    if not item:
        raise HTTPException(404, "Incidente no encontrado")
    if item.status == IncidentStatus.RESUELTO:
        raise HTTPException(409, "El incidente ya fue resuelto")
    item.status, item.action_taken, item.resolved_by, item.resolved_at = IncidentStatus.RESUELTO, data.action_taken, actor.id, utcnow()
    record(db, actor, "resolver", "incidente", item.id, data.action_taken)
    db.commit()
    return {"id": item.id, "status": item.status.value, "resolved_at": serialize_date(item.resolved_at)}


@router.post("/manual-records", status_code=201)
def capture_manual(data: ManualRecordCreate, actor: User = Depends(staff), db: Session = Depends(get_db)):
    occurred = utc_input(data.occurred_at)
    if occurred > utcnow():
        raise HTTPException(422, "La operación manual no puede tener fecha futura")
    item = ManualRecord(temp_code=data.temp_code, process_code=data.process_code, description=data.description, occurred_at=occurred, recorded_by=actor.id)
    db.add(item)
    try:
        db.flush()
        record(db, actor, "registrar", "operacion_manual", 0, data.temp_code)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "El código temporal ya existe") from None
    return manual_view(item)


@router.get("/manual-records")
def list_manual(pending: bool | None = None, _: User = Depends(staff), db: Session = Depends(get_db)):
    query = db.query(ManualRecord)
    if pending is not None:
        query = query.filter(ManualRecord.reconciled_at.is_(None) if pending else ManualRecord.reconciled_at.is_not(None))
    return [manual_view(item) for item in query.order_by(ManualRecord.captured_at.desc()).limit(100).all()]


@router.post("/manual-records/{temp_code}/reconcile")
def reconcile_manual(temp_code: str, data: ReconcileIn, actor: User = Depends(managers), db: Session = Depends(get_db)):
    lock_sqlite(db)
    item = db.get(ManualRecord, temp_code)
    if not item:
        raise HTTPException(404, "Registro temporal no encontrado")
    if item.reconciled_at:
        raise HTTPException(409, "Registro temporal ya conciliado")
    expected, model = ENTITY_MAP[item.process_code]
    if data.entity != expected or not db.get(model, data.entity_id):
        raise HTTPException(422, f"Vincule una entidad '{expected}' existente del proceso {item.process_code}")
    if db.query(ManualRecord.temp_code).filter(ManualRecord.process_code == item.process_code, ManualRecord.reconciled_entity == data.entity, ManualRecord.reconciled_id == data.entity_id).first():
        raise HTTPException(409, "La operación digital ya está vinculada a otro registro temporal")
    item.reconciled_entity, item.reconciled_id, item.evidence = data.entity, data.entity_id, data.evidence
    item.reconciled_by, item.reconciled_at = actor.id, utcnow()
    record(db, actor, "conciliar", "operacion_manual", data.entity_id, f"{item.temp_code}: {data.evidence}")
    db.commit()
    return manual_view(item)


@router.post("/training/evaluations", status_code=201)
def evaluate_training(data: TrainingCreate, actor: User = Depends(managers), db: Session = Depends(get_db)):
    user = db.get(User, data.user_id)
    if not user or not user.active or user.role == Role.SOPORTE:
        raise HTTPException(422, "Seleccione un trabajador activo del taller")
    if data.tasks_correct > data.tasks_total:
        raise HTTPException(422, "Las tareas correctas no pueden superar el total")
    evaluation = TrainingEvaluation(**data.model_dump(), evaluated_by=actor.id)
    db.add(evaluation)
    db.flush()
    record(db, actor, "evaluar_capacitacion", "usuario", user.id, f"{data.tasks_correct}/{data.tasks_total}")
    db.commit()
    return {"id": evaluation.id, "user_id": user.id, "tasks_correct": data.tasks_correct, "tasks_total": data.tasks_total, "score_percent": round(data.tasks_correct * 100 / data.tasks_total, 2), "meets_target": data.tasks_correct * 5 >= data.tasks_total * 4}


@router.get("/training/status")
def training_status(_: User = Depends(managers), db: Session = Depends(get_db)):
    workers = db.query(User).filter(User.active.is_(True), User.role.in_([Role.GERENTE, Role.SUBGERENTE, Role.TECNICO])).order_by(User.id).all()
    result = []
    for user in workers:
        latest = db.query(TrainingEvaluation).filter(TrainingEvaluation.user_id == user.id).order_by(TrainingEvaluation.id.desc()).first()
        result.append({"user_id": user.id, "name": user.full_name, "role": user.role.value, "evaluated": latest is not None, "tasks_correct": latest.tasks_correct if latest else None, "tasks_total": latest.tasks_total if latest else None, "meets_target": latest.tasks_correct * 5 >= latest.tasks_total * 4 if latest else False})
    return result


@router.get("/metrics/repair-updates")
def repair_update_metric(_: User = Depends(managers), db: Session = Depends(get_db)):
    today = datetime.now(get_business_tz()).date()
    start, end = boundaries(today, today)
    active_states = (OrderStatus.REPARACION, OrderStatus.ESPERA_REPUESTO, OrderStatus.CORRECCION)
    order_ids = [row[0] for row in db.query(WorkOrder.id).filter(WorkOrder.status.in_(active_states)).all()]
    updated = 0
    if order_ids:
        updated = len({row[0] for row in db.query(WorkProgress.order_id).filter(WorkProgress.order_id.in_(order_ids), WorkProgress.created_at >= start, WorkProgress.created_at < end).all()})
    return {"date": today, "active_repairs": len(order_ids), "updated_today": updated, "percentage": round(updated * 100 / len(order_ids), 2) if order_ids else None, "target_percentage": 90}
