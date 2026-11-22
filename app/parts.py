from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.clients import managers, staff
from app.closing import assert_open
from app.core.audit import record
from app.db import get_db
from app.models import (
    InventoryMovement, OrderStatus, Part, PartRequest, PurchaseOrder, PurchaseReceipt,
    PurchaseStatus, Quote, QuoteStatus, Supplier, User, utcnow,
)
from app.operational_schemas import (
    ConsumptionCreate, PartCreate, PartOut, PartRequestCreate, PartRequestOut,
    PurchaseCreate, ReasonIn, ReceiptCreate, ReturnReceived, SupplierCreate, SupplierOut,
)
from app.operations import assigned_or_owner, event, lock_sqlite, order_or_404, serialize_date

router = APIRouter(prefix="/parts", tags=["Repuestos P04"])


def part_request_or_404(db: Session, request_id: int) -> PartRequest:
    item = db.get(PartRequest, request_id)
    if not item:
        raise HTTPException(404, "Solicitud de repuesto no encontrada")
    return item


def authorize_cost_change(db: Session, order_id: int, quote_id: int | None, extra: Decimal) -> int | None:
    if extra <= 0:
        return None
    if quote_id is None:
        raise HTTPException(409, "El sobrecosto requiere presupuesto complementario autorizado")
    from app.models import QuoteKind
    quote = db.get(Quote, quote_id)
    if not quote or quote.order_id != order_id or quote.kind != QuoteKind.COMPLEMENTARIO or quote.status != QuoteStatus.AUTORIZADO:
        raise HTTPException(409, "Presupuesto complementario inválido para este sobrecosto")
    allocated_purchases = db.query(func.coalesce(func.sum(PurchaseOrder.cost_change_amount), 0)).filter(PurchaseOrder.cost_change_quote_id == quote_id).scalar()
    allocated_receipts = db.query(func.coalesce(func.sum(PurchaseReceipt.cost_change_amount), 0)).filter(PurchaseReceipt.cost_change_quote_id == quote_id).scalar()
    if Decimal(allocated_purchases) + Decimal(allocated_receipts) + extra > quote.amount:
        raise HTTPException(409, "El presupuesto complementario no cubre todos los sobrecostos asociados")
    return quote.id


@router.post("", response_model=PartOut, status_code=201)
def create_part(data: PartCreate, actor: User = Depends(managers), db: Session = Depends(get_db)):
    item = Part(sku=data.sku.strip().upper(), name=data.name.strip())
    db.add(item)
    try:
        db.flush()
        record(db, actor, "crear", "repuesto", item.id)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "El código de repuesto ya existe") from None
    db.refresh(item)
    return item


@router.get("", response_model=list[PartOut])
def list_parts(q: str | None = None, offset: int = Query(0, ge=0), limit: int = Query(30, ge=1, le=100), _=Depends(staff), db: Session = Depends(get_db)):
    query = db.query(Part).filter(Part.active.is_(True))
    if q:
        query = query.filter((Part.sku.ilike(f"%{q}%")) | (Part.name.ilike(f"%{q}%")))
    return query.order_by(Part.id).offset(offset).limit(limit).all()


@router.post("/suppliers", response_model=SupplierOut, status_code=201)
def create_supplier(data: SupplierCreate, actor: User = Depends(managers), db: Session = Depends(get_db)):
    supplier = Supplier(name=data.name.strip(), phone=data.phone)
    db.add(supplier)
    try:
        db.flush()
        record(db, actor, "crear", "proveedor", supplier.id)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "El proveedor ya existe") from None
    db.refresh(supplier)
    return supplier


@router.get("/suppliers", response_model=list[SupplierOut])
def suppliers(_: User = Depends(staff), db: Session = Depends(get_db)):
    return db.query(Supplier).filter(Supplier.active.is_(True)).order_by(Supplier.name).all()


@router.post("/orders/{order_id}/requests", response_model=PartRequestOut, status_code=201)
def request_part(order_id: int, data: PartRequestCreate, actor: User = Depends(staff), db: Session = Depends(get_db)):
    lock_sqlite(db)
    order = order_or_404(db, order_id, lock=True)
    assigned_or_owner(order, actor)
    if order.status not in (OrderStatus.REPARACION, OrderStatus.CORRECCION, OrderStatus.ESPERA_REPUESTO):
        raise HTTPException(409, "La reparación no está habilitada")
    quote = db.get(Quote, data.quote_id)
    part = db.get(Part, data.part_id)
    if not quote or quote.order_id != order.id or quote.status != QuoteStatus.AUTORIZADO:
        raise HTTPException(409, "El repuesto debe estar asociado a presupuesto autorizado")
    if not part or not part.active:
        raise HTTPException(404, "Repuesto no encontrado")
    item = PartRequest(order_id=order.id, quote_id=quote.id, part_id=part.id, quantity=data.quantity, estimated_unit_cost=data.estimated_unit_cost, created_by=actor.id)
    db.add(item)
    db.flush()
    event(db, order, actor, "repuesto_solicitado", f"Solicitud {item.id}, repuesto {part.id}, cantidad {item.quantity}")
    db.commit()
    db.refresh(item)
    return item


@router.get("/orders/{order_id}/requests", response_model=list[PartRequestOut])
def order_requests(order_id: int, actor: User = Depends(staff), db: Session = Depends(get_db)):
    order = order_or_404(db, order_id)
    assigned_or_owner(order, actor)
    return db.query(PartRequest).filter(PartRequest.order_id == order_id).order_by(PartRequest.id).all()


@router.post("/requests/{request_id}/decision", response_model=PartRequestOut)
def decide_request(request_id: int, approved: bool, data: ReasonIn, actor: User = Depends(managers), db: Session = Depends(get_db)):
    lock_sqlite(db)
    item = part_request_or_404(db, request_id)
    order = order_or_404(db, item.order_id, lock=True)
    if item.status != PurchaseStatus.SOLICITADO:
        raise HTTPException(409, "La solicitud ya fue resuelta")
    item.status = PurchaseStatus.APROBADO if approved else PurchaseStatus.RECHAZADO
    item.decided_by, item.decision_reason = actor.id, data.reason
    event(db, order, actor, "repuesto_aprobado" if approved else "repuesto_rechazado", f"Solicitud {item.id}: {data.reason}")
    db.commit()
    db.refresh(item)
    return item


@router.post("/requests/{request_id}/fulfill-from-stock", response_model=PartRequestOut)
def fulfill_from_stock(request_id: int, actor: User = Depends(managers), db: Session = Depends(get_db)):
    lock_sqlite(db)
    item = part_request_or_404(db, request_id)
    order = order_or_404(db, item.order_id, lock=True)
    if item.status != PurchaseStatus.APROBADO:
        raise HTTPException(409, "Solo puede asignarse stock a una solicitud aprobada")
    part = db.get(Part, item.part_id)
    if part.stock < item.quantity:
        raise HTTPException(409, "Existencias insuficientes")
    # La pieza sigue en stock hasta su consumo: este paso no altera inventario.
    item.status = PurchaseStatus.RECIBIDO
    event(db, order, actor, "repuesto_disponible", f"Solicitud {item.id} desde stock")
    db.commit()
    db.refresh(item)
    return item


@router.post("/requests/{request_id}/cancel", response_model=PartRequestOut)
def cancel_request(request_id: int, data: ReasonIn, actor: User = Depends(managers), db: Session = Depends(get_db)):
    lock_sqlite(db)
    item = part_request_or_404(db, request_id)
    order = order_or_404(db, item.order_id, lock=True)
    if item.status not in (PurchaseStatus.SOLICITADO, PurchaseStatus.APROBADO, PurchaseStatus.PEDIDO, PurchaseStatus.PARCIAL):
        raise HTTPException(409, "La solicitud no puede anularse en su estado actual")
    if db.query(InventoryMovement.id).filter(InventoryMovement.part_request_id == item.id).first():
        raise HTTPException(409, "Hay existencias recibidas o consumidas; registre una devolución antes de anular")
    item.status, item.decision_reason, item.decided_by = PurchaseStatus.ANULADO, data.reason, actor.id
    event(db, order, actor, "repuesto_anulado", f"Solicitud {item.id}: {data.reason}")
    db.commit()
    db.refresh(item)
    return item


@router.post("/requests/{request_id}/purchase", status_code=201)
def place_order(request_id: int, data: PurchaseCreate, actor: User = Depends(managers), db: Session = Depends(get_db)):
    lock_sqlite(db)
    item = part_request_or_404(db, request_id)
    order = order_or_404(db, item.order_id, lock=True)
    supplier = db.get(Supplier, data.supplier_id)
    if item.status != PurchaseStatus.APROBADO or not supplier or not supplier.active:
        raise HTTPException(409, "Solicitud no aprobada o proveedor inválido")
    extra = max(Decimal("0.00"), (data.unit_cost - item.estimated_unit_cost) * item.quantity)
    quote_id = authorize_cost_change(db, order.id, data.cost_change_quote_id, extra)
    purchase = PurchaseOrder(part_request_id=item.id, supplier_id=supplier.id, quantity=item.quantity, unit_cost=data.unit_cost, cost_change_quote_id=quote_id, cost_change_amount=extra, created_by=actor.id)
    db.add(purchase)
    db.flush()
    item.status = PurchaseStatus.PEDIDO
    event(db, order, actor, "pedido", f"Pedido {purchase.id}; proveedor {supplier.id}")
    db.commit()
    return {"id": purchase.id, "request_id": item.id, "quantity": purchase.quantity, "unit_cost": str(purchase.unit_cost)}


@router.post("/purchases/{purchase_id}/receipts", status_code=201)
def receive_purchase(purchase_id: int, data: ReceiptCreate, actor: User = Depends(managers), db: Session = Depends(get_db)):
    if data.accepted_quantity + data.rejected_quantity == 0:
        raise HTTPException(422, "Registre al menos una unidad recibida")
    lock_sqlite(db)
    assert_open(db, utcnow())
    purchase = db.get(PurchaseOrder, purchase_id)
    if not purchase:
        raise HTTPException(404, "Pedido no encontrado")
    item = part_request_or_404(db, purchase.part_request_id)
    order = order_or_404(db, item.order_id, lock=True)
    if item.status not in (PurchaseStatus.PEDIDO, PurchaseStatus.PARCIAL):
        raise HTTPException(409, "El pedido no admite recepciones")
    accepted = db.query(func.coalesce(func.sum(PurchaseReceipt.accepted_quantity), 0)).filter(PurchaseReceipt.purchase_order_id == purchase.id).scalar()
    returned = -db.query(func.coalesce(func.sum(InventoryMovement.quantity_delta), 0)).filter(InventoryMovement.part_request_id == item.id, InventoryMovement.reason == "devolucion_proveedor").scalar()
    accepted -= returned
    remaining = purchase.quantity - accepted
    if data.accepted_quantity + data.rejected_quantity > remaining:
        raise HTTPException(409, "La recepción supera las unidades pendientes del pedido")
    extra = max(Decimal("0.00"), (data.actual_unit_cost - purchase.unit_cost) * data.accepted_quantity)
    quote_id = authorize_cost_change(db, order.id, data.cost_change_quote_id, extra)
    receipt = PurchaseReceipt(purchase_order_id=purchase.id, accepted_quantity=data.accepted_quantity, rejected_quantity=data.rejected_quantity, actual_unit_cost=data.actual_unit_cost, cost_change_quote_id=quote_id, cost_change_amount=extra, notes=data.notes, created_by=actor.id)
    db.add(receipt)
    db.flush()
    if data.accepted_quantity:
        db.execute(update(Part).where(Part.id == item.part_id).values(stock=Part.stock + data.accepted_quantity))
        db.add(InventoryMovement(part_id=item.part_id, order_id=order.id, receipt_id=receipt.id, part_request_id=item.id, quantity_delta=data.accepted_quantity, unit_cost=data.actual_unit_cost, reason="recepcion", created_by=actor.id))
    item.status = PurchaseStatus.RECIBIDO if accepted + data.accepted_quantity == purchase.quantity else PurchaseStatus.PARCIAL
    event(db, order, actor, "recepcion_repuesto", f"Pedido {purchase.id}; aceptadas {data.accepted_quantity}; rechazadas {data.rejected_quantity}")
    db.commit()
    return {"id": receipt.id, "status": item.status.value, "accepted_quantity": receipt.accepted_quantity, "rejected_quantity": receipt.rejected_quantity}


@router.post("/purchases/{purchase_id}/return-received", status_code=201)
def return_received(purchase_id: int, data: ReturnReceived, actor: User = Depends(managers), db: Session = Depends(get_db)):
    lock_sqlite(db)
    assert_open(db, utcnow())
    purchase = db.get(PurchaseOrder, purchase_id)
    receipt = db.get(PurchaseReceipt, data.receipt_id)
    if not purchase or not receipt or receipt.purchase_order_id != purchase.id:
        raise HTTPException(404, "Recepción no encontrada para este pedido")
    item = part_request_or_404(db, purchase.part_request_id)
    order = order_or_404(db, item.order_id, lock=True)
    already_returned = -db.query(func.coalesce(func.sum(InventoryMovement.quantity_delta), 0)).filter(InventoryMovement.receipt_id == receipt.id, InventoryMovement.reason == "devolucion_proveedor").scalar()
    if data.quantity > receipt.accepted_quantity - already_returned:
        raise HTTPException(409, "La devolución supera las unidades aceptadas de esta recepción")
    result = db.execute(update(Part).where(Part.id == item.part_id, Part.stock >= data.quantity).values(stock=Part.stock - data.quantity))
    if result.rowcount != 1:
        raise HTTPException(409, "No hay existencias disponibles para devolver")
    db.add(InventoryMovement(part_id=item.part_id, order_id=order.id, receipt_id=receipt.id, part_request_id=item.id, quantity_delta=-data.quantity, unit_cost=receipt.actual_unit_cost, reason="devolucion_proveedor", created_by=actor.id))
    item.status = PurchaseStatus.PARCIAL
    event(db, order, actor, "devolucion_repuesto", f"Recepción {receipt.id}; cantidad {data.quantity}; motivo {data.reason}")
    db.commit()
    return {"receipt_id": receipt.id, "quantity": data.quantity, "status": item.status.value}


@router.post("/requests/{request_id}/consume", status_code=201)
def consume(request_id: int, data: ConsumptionCreate, actor: User = Depends(staff), db: Session = Depends(get_db)):
    lock_sqlite(db)
    item = part_request_or_404(db, request_id)
    order = order_or_404(db, item.order_id, lock=True)
    assigned_or_owner(order, actor)
    if order.status not in (OrderStatus.REPARACION, OrderStatus.CORRECCION) or item.status not in (PurchaseStatus.APROBADO, PurchaseStatus.PARCIAL, PurchaseStatus.RECIBIDO):
        raise HTTPException(409, "Repuesto o reparación no habilitados para consumo")
    consumed = -db.query(func.coalesce(func.sum(InventoryMovement.quantity_delta), 0)).filter(InventoryMovement.part_request_id == item.id, InventoryMovement.reason == "consumo").scalar()
    if consumed + data.quantity > item.quantity:
        raise HTTPException(409, "El consumo supera lo aprobado para la orden")
    updated = db.execute(update(Part).where(Part.id == item.part_id, Part.stock >= data.quantity).values(stock=Part.stock - data.quantity))
    if updated.rowcount != 1:
        raise HTTPException(409, "Existencias insuficientes")
    db.add(InventoryMovement(part_id=item.part_id, order_id=order.id, part_request_id=item.id, quantity_delta=-data.quantity, reason="consumo", created_by=actor.id))
    event(db, order, actor, "consumo_repuesto", f"Solicitud {item.id}; cantidad {data.quantity}")
    db.commit()
    return {"request_id": item.id, "quantity": data.quantity}


@router.get("/{part_id}/movements")
def movements(part_id: int, _: User = Depends(staff), db: Session = Depends(get_db)):
    if not db.get(Part, part_id):
        raise HTTPException(404, "Repuesto no encontrado")
    records = db.query(InventoryMovement).filter(InventoryMovement.part_id == part_id).order_by(InventoryMovement.id).all()
    return [{"id": m.id, "order_id": m.order_id, "receipt_id": m.receipt_id, "part_request_id": m.part_request_id, "quantity_delta": m.quantity_delta, "unit_cost": str(m.unit_cost) if m.unit_cost is not None else None, "reason": m.reason, "created_by": m.created_by, "created_at": serialize_date(m.created_at)} for m in records]
