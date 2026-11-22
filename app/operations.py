from datetime import datetime, timezone
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.audit import record
from app.models import (
    OrderEvent, Payment, PaymentStatus, Quote, QuoteStatus, Role, User,
    WorkOrder, utcnow,
)


def utc_input(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise HTTPException(422, "La fecha debe incluir zona horaria")
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def lock_sqlite(db: Session) -> None:
    if db.bind.dialect.name == "sqlite":
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")


def order_or_404(db: Session, order_id: int, lock: bool = False) -> WorkOrder:
    query = db.query(WorkOrder).filter(WorkOrder.id == order_id)
    if lock and db.bind.dialect.name != "sqlite":
        query = query.with_for_update()
    order = query.first()
    if not order:
        raise HTTPException(404, "Orden no encontrada")
    return order


def assigned_or_owner(order: WorkOrder, user: User) -> None:
    if user.role not in (Role.GERENTE, Role.SUBGERENTE) and order.assigned_to != user.id:
        raise HTTPException(403, "La orden no está asignada a este técnico")


def authorized_total(db: Session, order_id: int) -> Decimal:
    total = db.query(func.coalesce(func.sum(Quote.amount), 0)).filter(Quote.order_id == order_id, Quote.status == QuoteStatus.AUTORIZADO).scalar()
    return Decimal(total).quantize(Decimal("0.01"))


def valid_paid(db: Session, order_id: int) -> Decimal:
    total = db.query(func.coalesce(func.sum(Payment.amount), 0)).filter(Payment.order_id == order_id, Payment.status == PaymentStatus.VALIDO).scalar()
    return Decimal(total).quantize(Decimal("0.01"))


def balance(db: Session, order_id: int) -> Decimal:
    return authorized_total(db, order_id) - valid_paid(db, order_id)


def event(db: Session, order: WorkOrder, actor: User, action: str, detail: str | None = None) -> None:
    db.add(OrderEvent(order_id=order.id, actor_id=actor.id, action=action, detail=detail))
    record(db, actor, action, "orden", order.id, detail)


def serialize_date(value: datetime | None) -> str | None:
    return value.replace(tzinfo=timezone.utc).isoformat() if value else None
