from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.clients import managers
from app.closing import assert_open
from app.core.config import get_business_tz
from app.db import get_db
from app.models import OrderStatus, Payment, PaymentCommitment, PaymentStatus, User, utcnow
from app.operational_schemas import BalanceOut, CommitmentCreate, CommitmentOut, PaymentCreate, PaymentOut, ReasonIn
from app.operations import authorized_total, balance, event, lock_sqlite, order_or_404, serialize_date, valid_paid

router = APIRouter(prefix="/orders/{order_id}", tags=["Cobros P05"])


def summary(db: Session, order_id: int) -> BalanceOut:
    authorized = authorized_total(db, order_id)
    paid = valid_paid(db, order_id)
    remaining = authorized - paid
    state = "sin_importe" if authorized == 0 else "pagado" if remaining == 0 else "parcial" if paid > 0 else "pendiente"
    return BalanceOut(authorized_amount=authorized, valid_payments=paid, balance=remaining, financial_status=state)


@router.get("/balance", response_model=BalanceOut)
def get_balance(order_id: int, _: User = Depends(managers), db: Session = Depends(get_db)):
    order_or_404(db, order_id)
    return summary(db, order_id)


@router.post("/payments", response_model=PaymentOut, status_code=201)
def pay(order_id: int, data: PaymentCreate, actor: User = Depends(managers), db: Session = Depends(get_db)):
    lock_sqlite(db)
    assert_open(db, utcnow())
    order = order_or_404(db, order_id, lock=True)
    existing = db.query(Payment).filter(Payment.idempotency_key == data.idempotency_key).first()
    if existing:
        if existing.order_id == order_id and existing.amount == data.amount and existing.method == data.method and existing.reference == data.reference:
            return existing
        raise HTTPException(409, "La clave de idempotencia ya se usó para otro pago")
    if order.status == OrderStatus.CERRADA_SIN_REPARACION or authorized_total(db, order_id) == 0:
        raise HTTPException(409, "La orden no tiene un importe autorizado cobrable")
    if data.amount > balance(db, order_id):
        raise HTTPException(409, "El abono supera el saldo pendiente")
    payment = Payment(order_id=order_id, amount=data.amount, method=data.method.strip(), reference=data.reference, idempotency_key=data.idempotency_key, created_by=actor.id)
    db.add(payment)
    try:
        db.flush()
        event(db, order, actor, "pago", f"Pago {payment.id}, monto {payment.amount}")
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Clave de idempotencia ya utilizada") from None
    db.refresh(payment)
    return payment


@router.get("/payments", response_model=list[PaymentOut])
def payments(order_id: int, _: User = Depends(managers), db: Session = Depends(get_db)):
    order_or_404(db, order_id)
    return db.query(Payment).filter(Payment.order_id == order_id).order_by(Payment.id).all()


@router.get("/payments/{payment_id}/receipt")
def receipt(order_id: int, payment_id: int, _: User = Depends(managers), db: Session = Depends(get_db)):
    order = order_or_404(db, order_id)
    payment = db.get(Payment, payment_id)
    if not payment or payment.order_id != order.id:
        raise HTTPException(404, "Pago no encontrado")
    return {"receipt_number": f"P-{payment.id:08d}", "order_id": order.id, "payment_id": payment.id, "amount": str(payment.amount), "method": payment.method, "reference": payment.reference, "status": payment.status.value, "received_at": serialize_date(payment.created_at), "voided_at": serialize_date(payment.voided_at), "void_reason": payment.void_reason}


@router.post("/payments/{payment_id}/void", response_model=PaymentOut)
def void_payment(order_id: int, payment_id: int, data: ReasonIn, actor: User = Depends(managers), db: Session = Depends(get_db)):
    lock_sqlite(db)
    assert_open(db, utcnow())
    order = order_or_404(db, order_id, lock=True)
    payment = db.get(Payment, payment_id)
    if not payment or payment.order_id != order_id:
        raise HTTPException(404, "Pago no encontrado")
    if payment.status != PaymentStatus.VALIDO:
        raise HTTPException(409, "El pago ya fue anulado")
    remaining_after = balance(db, order_id) + payment.amount
    if order.delivered_at and remaining_after > 0 and not db.query(PaymentCommitment.id).filter(PaymentCommitment.order_id == order_id, PaymentCommitment.active.is_(True), PaymentCommitment.amount >= remaining_after).first():
        raise HTTPException(409, "Antes de anular un pago de una orden entregada, registre un compromiso que cubra el saldo resultante")
    payment.status, payment.voided_by, payment.voided_at, payment.void_reason = PaymentStatus.ANULADO, actor.id, utcnow(), data.reason
    event(db, order, actor, "anular_pago", f"Pago {payment.id}: {data.reason}")
    db.commit()
    db.refresh(payment)
    return payment


@router.post("/commitments", response_model=CommitmentOut, status_code=201)
def commit_payment(order_id: int, data: CommitmentCreate, actor: User = Depends(managers), db: Session = Depends(get_db)):
    lock_sqlite(db)
    order = order_or_404(db, order_id, lock=True)
    remaining = balance(db, order_id)
    if order.status == OrderStatus.CERRADA_SIN_REPARACION or remaining <= 0:
        raise HTTPException(409, "La orden no tiene saldo pendiente")
    if data.amount < remaining:
        raise HTTPException(422, "El compromiso debe cubrir el saldo pendiente")
    today = datetime.now(get_business_tz()).date()
    if data.due_date < today:
        raise HTTPException(422, "La fecha de compromiso no puede estar vencida")
    current = db.query(PaymentCommitment).filter(PaymentCommitment.order_id == order_id, PaymentCommitment.active.is_(True)).all()
    for item in current:
        item.active = False
    commitment = PaymentCommitment(order_id=order_id, amount=data.amount, due_date=data.due_date, notes=data.notes, approved_by=actor.id)
    db.add(commitment)
    db.flush()
    event(db, order, actor, "compromiso_pago", f"Compromiso {commitment.id}; monto {commitment.amount}; vence {commitment.due_date}")
    db.commit()
    db.refresh(commitment)
    return commitment


@router.get("/commitments", response_model=list[CommitmentOut])
def commitments(order_id: int, _: User = Depends(managers), db: Session = Depends(get_db)):
    order_or_404(db, order_id)
    return db.query(PaymentCommitment).filter(PaymentCommitment.order_id == order_id).order_by(PaymentCommitment.id).all()
