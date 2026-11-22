import json
from datetime import date, datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.clients import managers
from app.closing import assert_open, boundaries, build_report, snapshot_json
from app.core.audit import record
from app.core.config import get_business_tz
from app.core.dependencies import require_roles
from app.db import get_db
from app.models import CloseEvent, CloseFrequency, ClosePeriod, CloseStatus, Expense, ReconciliationIssue, Role, User, utcnow
from app.operations import lock_sqlite, serialize_date

router = APIRouter(tags=["Cierre y reportes P06"])
manager = require_roles(Role.GERENTE)


class PeriodCreate(BaseModel):
    frequency: CloseFrequency
    start_date: date
    end_date: date


class ExpenseCreate(BaseModel):
    category: str = Field(min_length=2, max_length=60)
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    description: str = Field(min_length=5, max_length=3000)
    evidence: str = Field(min_length=5, max_length=3000)


class IssueCreate(BaseModel):
    category: str = Field(pattern=r"^(ventas|cobros|compras|egresos|otros)$")
    description: str = Field(min_length=5, max_length=3000)
    evidence: str = Field(min_length=5, max_length=3000)


class IssueResolve(BaseModel):
    resolution: str = Field(min_length=5, max_length=3000)
    evidence: str = Field(min_length=5, max_length=3000)


class Reason(BaseModel):
    reason: str = Field(min_length=5, max_length=3000)


def valid_period(data: PeriodCreate) -> None:
    boundaries(data.start_date, data.end_date)
    if data.frequency == CloseFrequency.SEMANAL and (data.start_date.weekday() != 0 or data.end_date != data.start_date + timedelta(days=6)):
        raise HTTPException(422, "La semana debe ser de lunes a domingo")
    if data.frequency == CloseFrequency.MENSUAL:
        next_month = date(data.start_date.year + (data.start_date.month == 12), data.start_date.month % 12 + 1, 1)
        if data.start_date.day != 1 or data.end_date != next_month - timedelta(days=1):
            raise HTTPException(422, "El mes debe abarcar desde su primer hasta su último día")


def period_or_404(db: Session, period_id: int, lock: bool = False) -> ClosePeriod:
    query = db.query(ClosePeriod).filter(ClosePeriod.id == period_id)
    if lock and db.bind.dialect.name != "sqlite":
        query = query.with_for_update()
    period = query.first()
    if not period:
        raise HTTPException(404, "Periodo no encontrado")
    return period


def period_view(db: Session, period: ClosePeriod) -> dict:
    report = json.loads(period.snapshot) if period.status == CloseStatus.CERRADO and period.snapshot else build_report(db, period.start_date, period.end_date)
    return {"id": period.id, "frequency": period.frequency.value, "status": period.status.value, "start_date": period.start_date, "end_date": period.end_date, "confirmed_by": period.confirmed_by, "confirmed_at": serialize_date(period.confirmed_at), "report": report}


@router.get("/reports")
def preview_report(frequency: CloseFrequency, start_date: date, end_date: date, _: User = Depends(managers), db: Session = Depends(get_db)):
    valid_period(PeriodCreate(frequency=frequency, start_date=start_date, end_date=end_date))
    return build_report(db, start_date, end_date)


@router.post("/expenses", status_code=201)
def add_expense(data: ExpenseCreate, actor: User = Depends(managers), db: Session = Depends(get_db)):
    lock_sqlite(db)
    assert_open(db, utcnow())
    expense = Expense(**data.model_dump(), created_by=actor.id)
    db.add(expense)
    db.flush()
    record(db, actor, "registrar", "egreso", expense.id, data.evidence)
    db.commit()
    return {"id": expense.id, "amount": str(expense.amount), "created_at": serialize_date(expense.created_at)}


@router.get("/expenses")
def list_expenses(offset: int = Query(0, ge=0), limit: int = Query(30, ge=1, le=100), _: User = Depends(managers), db: Session = Depends(get_db)):
    rows = db.query(Expense).order_by(Expense.id.desc()).offset(offset).limit(limit).all()
    return [{"id": row.id, "category": row.category, "amount": str(row.amount), "description": row.description, "evidence": row.evidence, "created_at": serialize_date(row.created_at), "voided_at": serialize_date(row.voided_at), "void_reason": row.void_reason} for row in rows]


@router.post("/expenses/{expense_id}/void")
def void_expense(expense_id: int, data: Reason, actor: User = Depends(managers), db: Session = Depends(get_db)):
    lock_sqlite(db)
    expense = db.get(Expense, expense_id)
    if not expense:
        raise HTTPException(404, "Egreso no encontrado")
    if expense.voided_at:
        raise HTTPException(409, "Egreso ya anulado")
    assert_open(db, utcnow())
    expense.voided_at, expense.voided_by, expense.void_reason = utcnow(), actor.id, data.reason
    record(db, actor, "anular", "egreso", expense.id, data.reason)
    db.commit()
    return {"id": expense.id, "voided_at": serialize_date(expense.voided_at)}


@router.post("/periods", status_code=201)
def create_period(data: PeriodCreate, actor: User = Depends(managers), db: Session = Depends(get_db)):
    valid_period(data)
    if data.end_date >= datetime.now(get_business_tz()).date():
        raise HTTPException(422, "Solo se puede revisar un periodo ya terminado")
    period = ClosePeriod(**data.model_dump(), created_by=actor.id)
    db.add(period)
    try:
        db.flush()
        db.add(CloseEvent(period_id=period.id, actor_id=actor.id, action="crear", detail="Periodo creado para revisión"))
        record(db, actor, "crear", "cierre", period.id)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Este periodo ya existe") from None
    db.refresh(period)
    return period_view(db, period)


@router.get("/periods")
def list_periods(_: User = Depends(managers), db: Session = Depends(get_db)):
    rows = db.query(ClosePeriod).order_by(ClosePeriod.start_date.desc()).limit(100).all()
    return [{"id": row.id, "frequency": row.frequency.value, "start_date": row.start_date, "end_date": row.end_date, "status": row.status.value, "confirmed_by": row.confirmed_by} for row in rows]


@router.get("/periods/{period_id}")
def get_period(period_id: int, _: User = Depends(managers), db: Session = Depends(get_db)):
    return period_view(db, period_or_404(db, period_id))


@router.post("/periods/{period_id}/issues", status_code=201)
def add_issue(period_id: int, data: IssueCreate, actor: User = Depends(managers), db: Session = Depends(get_db)):
    lock_sqlite(db)
    period = period_or_404(db, period_id, lock=True)
    if period.status != CloseStatus.BORRADOR:
        raise HTTPException(409, "Reabra el cierre para registrar diferencias")
    issue = ReconciliationIssue(period_id=period.id, **data.model_dump(), created_by=actor.id)
    db.add(issue)
    db.flush()
    db.add(CloseEvent(period_id=period.id, actor_id=actor.id, action="diferencia", detail=f"Diferencia {issue.id}: {data.category}"))
    db.commit()
    return {"id": issue.id, "resolved": False}


@router.get("/periods/{period_id}/issues")
def list_issues(period_id: int, _: User = Depends(managers), db: Session = Depends(get_db)):
    period_or_404(db, period_id)
    rows = db.query(ReconciliationIssue).filter(ReconciliationIssue.period_id == period_id).order_by(ReconciliationIssue.id).all()
    return [{"id": row.id, "category": row.category, "description": row.description, "evidence": row.evidence, "resolution": row.resolution, "resolved_by": row.resolved_by, "resolved_at": serialize_date(row.resolved_at)} for row in rows]


@router.post("/periods/{period_id}/issues/{issue_id}/resolve")
def resolve_issue(period_id: int, issue_id: int, data: IssueResolve, actor: User = Depends(managers), db: Session = Depends(get_db)):
    lock_sqlite(db)
    period = period_or_404(db, period_id, lock=True)
    issue = db.get(ReconciliationIssue, issue_id)
    if not issue or issue.period_id != period_id:
        raise HTTPException(404, "Diferencia no encontrada")
    if period.status != CloseStatus.BORRADOR or issue.resolved_at:
        raise HTTPException(409, "La diferencia ya fue resuelta o el periodo está cerrado")
    issue.resolution, issue.evidence, issue.resolved_by, issue.resolved_at = data.resolution, data.evidence, actor.id, utcnow()
    db.add(CloseEvent(period_id=period.id, actor_id=actor.id, action="resolver", detail=f"Diferencia {issue.id}: {data.resolution}; evidencia: {data.evidence}"))
    db.commit()
    return {"id": issue.id, "resolved_at": serialize_date(issue.resolved_at)}


@router.post("/periods/{period_id}/confirm")
def confirm_period(period_id: int, actor: User = Depends(manager), db: Session = Depends(get_db)):
    lock_sqlite(db)
    period = period_or_404(db, period_id, lock=True)
    if period.status != CloseStatus.BORRADOR:
        raise HTTPException(409, "El periodo ya está cerrado")
    if db.query(ReconciliationIssue.id).filter(ReconciliationIssue.period_id == period_id, ReconciliationIssue.resolved_at.is_(None)).first():
        raise HTTPException(409, "Resuelva las diferencias documentadas antes del cierre")
    period.snapshot = snapshot_json(build_report(db, period.start_date, period.end_date))
    period.status, period.confirmed_by, period.confirmed_at = CloseStatus.CERRADO, actor.id, utcnow()
    db.add(CloseEvent(period_id=period.id, actor_id=actor.id, action="confirmar", detail=period.snapshot))
    record(db, actor, "confirmar", "cierre", period.id)
    db.commit()
    return period_view(db, period)


@router.post("/periods/{period_id}/reopen")
def reopen_period(period_id: int, data: Reason, actor: User = Depends(manager), db: Session = Depends(get_db)):
    lock_sqlite(db)
    period = period_or_404(db, period_id, lock=True)
    if period.status != CloseStatus.CERRADO:
        raise HTTPException(409, "El periodo ya está abierto")
    db.add(CloseEvent(period_id=period.id, actor_id=actor.id, action="reabrir", detail=f"{data.reason}; snapshot anterior: {period.snapshot}"))
    period.status, period.snapshot, period.confirmed_by, period.confirmed_at = CloseStatus.BORRADOR, None, None, None
    record(db, actor, "reabrir", "cierre", period.id, data.reason)
    db.commit()
    return period_view(db, period)


@router.get("/periods/{period_id}/history")
def period_history(period_id: int, _: User = Depends(managers), db: Session = Depends(get_db)):
    period_or_404(db, period_id)
    rows = db.query(CloseEvent).filter(CloseEvent.period_id == period_id).order_by(CloseEvent.id).all()
    return [{"id": row.id, "action": row.action, "actor_id": row.actor_id, "detail": row.detail, "created_at": serialize_date(row.created_at)} for row in rows]
