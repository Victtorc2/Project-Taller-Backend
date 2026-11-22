import json
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.core.config import get_business_tz
from app.models import (
    ClosePeriod, CloseStatus, Expense, InventoryMovement, Payment, PurchaseReceipt,
    Quote, QuoteStatus, WorkOrder,
)


def boundaries(start_date: date, end_date: date) -> tuple[datetime, datetime]:
    if end_date < start_date:
        raise HTTPException(422, "El fin del periodo debe ser posterior al inicio")
    tz = get_business_tz()
    start = datetime.combine(start_date, time.min, tzinfo=tz).astimezone(timezone.utc).replace(tzinfo=None)
    end = datetime.combine(end_date + timedelta(days=1), time.min, tzinfo=tz).astimezone(timezone.utc).replace(tzinfo=None)
    return start, end


def assert_open(db: Session, when: datetime) -> None:
    day = when.replace(tzinfo=timezone.utc).astimezone(get_business_tz()).date()
    closed = db.query(ClosePeriod.id).filter(ClosePeriod.status == CloseStatus.CERRADO, ClosePeriod.start_date <= day, ClosePeriod.end_date >= day).first()
    if closed:
        raise HTTPException(409, f"El periodo económico está cerrado (cierre {closed[0]})")


def build_report(db: Session, start_date: date, end_date: date) -> dict:
    start, end = boundaries(start_date, end_date)
    quotes = db.query(Quote).filter(Quote.status == QuoteStatus.AUTORIZADO, Quote.decided_at >= start, Quote.decided_at < end).order_by(Quote.id).all()
    received = db.query(Payment).filter(Payment.created_at >= start, Payment.created_at < end).order_by(Payment.id).all()
    reversals = db.query(Payment).filter(Payment.voided_at >= start, Payment.voided_at < end).order_by(Payment.id).all()
    receipts = db.query(PurchaseReceipt).filter(PurchaseReceipt.created_at >= start, PurchaseReceipt.created_at < end).order_by(PurchaseReceipt.id).all()
    returns = db.query(InventoryMovement).filter(InventoryMovement.reason == "devolucion_proveedor", InventoryMovement.created_at >= start, InventoryMovement.created_at < end).order_by(InventoryMovement.id).all()
    expenses = db.query(Expense).filter(Expense.created_at >= start, Expense.created_at < end).order_by(Expense.id).all()
    expense_reversals = db.query(Expense).filter(Expense.voided_at >= start, Expense.voided_at < end).order_by(Expense.id).all()

    sales = sum((q.amount for q in quotes), Decimal("0.00"))
    cash = sum((p.amount for p in received), Decimal("0.00")) - sum((p.amount for p in reversals), Decimal("0.00"))
    purchase_cost = sum((r.accepted_quantity * r.actual_unit_cost for r in receipts), Decimal("0.00")) + sum((m.quantity_delta * (m.unit_cost or Decimal("0.00")) for m in returns), Decimal("0.00"))
    other_expenses = sum((e.amount for e in expenses), Decimal("0.00")) - sum((e.amount for e in expense_reversals), Decimal("0.00"))

    # Saldo al cierre: autorizaciones y movimientos que existían hasta el último instante del periodo.
    approved_until = db.query(Quote).filter(Quote.status == QuoteStatus.AUTORIZADO, Quote.decided_at < end).all()
    payments_until = db.query(Payment).filter(Payment.created_at < end).all()
    authorized_by_order: dict[int, Decimal] = {}
    paid_by_order: dict[int, Decimal] = {}
    for q in approved_until:
        authorized_by_order[q.order_id] = authorized_by_order.get(q.order_id, Decimal("0.00")) + q.amount
    for p in payments_until:
        if p.voided_at is None or p.voided_at >= end:
            paid_by_order[p.order_id] = paid_by_order.get(p.order_id, Decimal("0.00")) + p.amount
    outstanding = sum((max(Decimal("0.00"), amount - paid_by_order.get(order_id, Decimal("0.00"))) for order_id, amount in authorized_by_order.items()), Decimal("0.00"))

    opened = db.query(WorkOrder.id).filter(WorkOrder.created_at >= start, WorkOrder.created_at < end).count()
    delivered = db.query(WorkOrder.id).filter(WorkOrder.delivered_at >= start, WorkOrder.delivered_at < end).count()
    money = lambda amount: str(amount.quantize(Decimal("0.01")))
    return {
        "start_date": start_date.isoformat(), "end_date": end_date.isoformat(),
        "sales_authorized": money(sales), "cash_collected_net": money(cash),
        "parts_received_net_cost": money(purchase_cost), "other_expenses_net": money(other_expenses),
        "outstanding_at_period_end": money(outstanding),
        "partial_result": money(sales - purchase_cost - other_expenses),
        "partial_result_note": "Resultado parcial: no incluye costos no registrados ni equivale a utilidad neta.",
        "orders_opened": opened, "vehicles_delivered": delivered,
        "movement_ids": {
            "approved_quotes": [q.id for q in quotes],
            "payments_received": [p.id for p in received], "payments_reversed": [p.id for p in reversals],
            "purchase_receipts": [r.id for r in receipts], "supplier_returns": [m.id for m in returns],
            "expenses": [e.id for e in expenses], "expenses_reversed": [e.id for e in expense_reversals],
        },
    }


def snapshot_json(report: dict) -> str:
    return json.dumps(report, ensure_ascii=False, sort_keys=True)
