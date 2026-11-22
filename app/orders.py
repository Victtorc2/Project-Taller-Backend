from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.clients import managers, staff
from app.closing import assert_open
from app.core.audit import record
from app.db import get_db
from app.models import (
    Appointment, AppointmentStatus, CustomerNotice, OrderEvent, OrderStatus, PartRequest, PaymentCommitment,
    PurchaseStatus, Quote, QuoteKind, QuoteStatus, RequestStatus, Role, ServiceRequest,
    User, WorkOrder, WorkProgress, WorkTest, utcnow,
)
from app.operational_schemas import (
    AssignIn, DeliveryEstimateChange, DiagnosisIn, NoticeCreate, OrderCreate, OrderOut,
    ProgressIn, QuoteCreate, QuoteDecision, QuoteEdit, QuoteOut, QuoteSend, ReasonIn, TestIn,
)
from app.operations import assigned_or_owner, balance, event, lock_sqlite, order_or_404, serialize_date, utc_input

router = APIRouter(prefix="/orders", tags=["Órdenes P03"])


@router.post("", response_model=OrderOut, status_code=201)
def receive(data: OrderCreate, actor: User = Depends(staff), db: Session = Depends(get_db)):
    lock_sqlite(db)
    request_query = db.query(ServiceRequest).filter(ServiceRequest.id == data.request_id)
    if db.bind.dialect.name != "sqlite":
        request_query = request_query.with_for_update()
    request = request_query.first()
    if not request:
        raise HTTPException(404, "Solicitud no encontrada")
    if request.status == RequestStatus.CITA:
        appointment = db.query(Appointment).filter(Appointment.request_id == request.id).first()
        if not appointment or appointment.status != AppointmentStatus.ATENDIDA:
            raise HTTPException(409, "Debe marcar la cita como atendida antes de recibir el vehículo")
    elif request.status != RequestStatus.RECEPCION_DIRECTA:
        raise HTTPException(409, "Derive la solicitud a recepción directa o atienda su cita")
    order = WorkOrder(request_id=request.id, client_id=request.client_id, vehicle_id=request.vehicle_id, reception_notes=data.reception_notes, created_by=actor.id)
    db.add(order)
    try:
        db.flush()
        event(db, order, actor, "recepcion", data.reception_notes)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Esta solicitud ya tiene una orden") from None
    db.refresh(order)
    return order


@router.get("", response_model=list[OrderOut])
def list_orders(status: OrderStatus | None = None, offset: int = Query(0, ge=0), limit: int = Query(30, ge=1, le=100), actor: User = Depends(staff), db: Session = Depends(get_db)):
    query = db.query(WorkOrder)
    if actor.role == Role.TECNICO:
        query = query.filter((WorkOrder.assigned_to == actor.id) | (WorkOrder.status == OrderStatus.DIAGNOSTICO))
    if status:
        query = query.filter(WorkOrder.status == status)
    return query.order_by(WorkOrder.id.desc()).offset(offset).limit(limit).all()


@router.get("/{order_id}", response_model=OrderOut)
def get_order(order_id: int, actor: User = Depends(staff), db: Session = Depends(get_db)):
    order = order_or_404(db, order_id)
    if actor.role == Role.TECNICO and order.assigned_to != actor.id and order.status != OrderStatus.DIAGNOSTICO:
        raise HTTPException(403, "Orden no asignada")
    return order


@router.get("/{order_id}/history")
def history(order_id: int, actor: User = Depends(staff), db: Session = Depends(get_db)):
    get_order(order_id, actor, db)
    events = db.query(OrderEvent).filter(OrderEvent.order_id == order_id).order_by(OrderEvent.id).all()
    return [{"action": e.action, "actor_id": e.actor_id, "detail": e.detail, "created_at": serialize_date(e.created_at)} for e in events]


@router.post("/{order_id}/diagnosis", response_model=OrderOut)
def diagnose(order_id: int, data: DiagnosisIn, actor: User = Depends(staff), db: Session = Depends(get_db)):
    lock_sqlite(db)
    order = order_or_404(db, order_id, lock=True)
    assigned_or_owner(order, actor) if order.assigned_to else None
    if order.status != OrderStatus.DIAGNOSTICO:
        raise HTTPException(409, "El diagnóstico ya fue registrado")
    order.diagnosis, order.proposed_work = data.diagnosis, data.proposed_work
    order.status = OrderStatus.ESPERA_AUTORIZACION
    event(db, order, actor, "diagnostico")
    db.commit()
    db.refresh(order)
    return order


@router.post("/{order_id}/quotes", response_model=QuoteOut, status_code=201)
def create_quote(order_id: int, data: QuoteCreate, actor: User = Depends(managers), db: Session = Depends(get_db)):
    lock_sqlite(db)
    order = order_or_404(db, order_id, lock=True)
    if data.kind == QuoteKind.INICIAL:
        if order.status != OrderStatus.ESPERA_AUTORIZACION or db.query(Quote.id).filter(Quote.order_id == order.id, Quote.kind == QuoteKind.INICIAL).first():
            raise HTTPException(409, "La orden ya tiene presupuesto inicial o no está diagnosticada")
    else:
        if order.status not in (OrderStatus.AUTORIZADA, OrderStatus.ESPERA_REPUESTO, OrderStatus.REPARACION, OrderStatus.CORRECCION):
            raise HTTPException(409, "No corresponde presupuesto complementario")
        if db.query(Quote.id).filter(Quote.order_id == order.id, Quote.kind == QuoteKind.COMPLEMENTARIO, Quote.status.in_([QuoteStatus.BORRADOR, QuoteStatus.ENVIADO])).first():
            raise HTTPException(409, "Ya existe un presupuesto complementario pendiente")
    quote = Quote(order_id=order.id, kind=data.kind, description=data.description, amount=data.amount, created_by=actor.id)
    db.add(quote)
    db.flush()
    event(db, order, actor, "presupuesto_creado", f"Presupuesto {quote.id}: {quote.kind.value}")
    db.commit()
    db.refresh(quote)
    return quote


@router.get("/{order_id}/quotes", response_model=list[QuoteOut])
def quotes(order_id: int, actor: User = Depends(staff), db: Session = Depends(get_db)):
    get_order(order_id, actor, db)
    return db.query(Quote).filter(Quote.order_id == order_id).order_by(Quote.id).all()


@router.post("/{order_id}/quotes/{quote_id}/send", response_model=QuoteOut)
def send_quote(order_id: int, quote_id: int, data: QuoteSend, actor: User = Depends(managers), db: Session = Depends(get_db)):
    lock_sqlite(db)
    order = order_or_404(db, order_id, lock=True)
    quote = db.get(Quote, quote_id)
    if not quote or quote.order_id != order.id:
        raise HTTPException(404, "Presupuesto no encontrado")
    if quote.status != QuoteStatus.BORRADOR:
        raise HTTPException(409, "El presupuesto no está en borrador")
    quote.status, quote.sent_at = QuoteStatus.ENVIADO, utcnow()
    quote.sent_channel, quote.sent_message = data.channel, data.message
    event(db, order, actor, "presupuesto_enviado", f"{quote.id}; {data.channel.value}")
    db.commit()
    db.refresh(quote)
    return quote


@router.patch("/{order_id}/quotes/{quote_id}", response_model=QuoteOut)
def edit_quote(order_id: int, quote_id: int, data: QuoteEdit, actor: User = Depends(managers), db: Session = Depends(get_db)):
    lock_sqlite(db)
    order = order_or_404(db, order_id, lock=True)
    quote = db.get(Quote, quote_id)
    if not quote or quote.order_id != order_id:
        raise HTTPException(404, "Presupuesto no encontrado")
    if quote.status != QuoteStatus.BORRADOR:
        raise HTTPException(409, "Solo se edita un presupuesto en borrador")
    quote.description, quote.amount = data.description, data.amount
    event(db, order, actor, "presupuesto_editado", str(quote.id))
    db.commit()
    db.refresh(quote)
    return quote


@router.post("/{order_id}/quotes/{quote_id}/decision", response_model=QuoteOut)
def decide_quote(order_id: int, quote_id: int, data: QuoteDecision, actor: User = Depends(managers), db: Session = Depends(get_db)):
    lock_sqlite(db)
    order = order_or_404(db, order_id, lock=True)
    quote = db.get(Quote, quote_id)
    if not quote or quote.order_id != order.id:
        raise HTTPException(404, "Presupuesto no encontrado")
    if quote.status != QuoteStatus.ENVIADO:
        raise HTTPException(409, "El presupuesto debe haberse enviado y seguir pendiente")
    if data.approved:
        assert_open(db, utcnow())
    if quote.kind == QuoteKind.INICIAL and order.status != OrderStatus.ESPERA_AUTORIZACION:
        raise HTTPException(409, "La orden no espera presupuesto inicial")
    quote.status = QuoteStatus.AUTORIZADO if data.approved else QuoteStatus.RECHAZADO
    quote.decided_at, quote.decision_by = utcnow(), actor.id
    quote.decision_channel, quote.decision_evidence = data.channel, data.evidence
    if quote.kind == QuoteKind.INICIAL:
        if data.approved:
            order.status = OrderStatus.AUTORIZADA
        else:
            order.status, order.rejection_reason = OrderStatus.CERRADA_SIN_REPARACION, data.evidence
    event(db, order, actor, "presupuesto_autorizado" if data.approved else "presupuesto_rechazado", f"{quote.id}; {data.channel.value}; {data.evidence}")
    db.commit()
    db.refresh(quote)
    return quote


@router.post("/{order_id}/return-unrepaired", response_model=OrderOut)
def return_unrepaired(order_id: int, data: ReasonIn, actor: User = Depends(managers), db: Session = Depends(get_db)):
    lock_sqlite(db)
    order = order_or_404(db, order_id, lock=True)
    if order.status != OrderStatus.CERRADA_SIN_REPARACION or order.returned_at:
        raise HTTPException(409, "Solo puede registrarse la devolución de una reparación rechazada aún en el taller")
    order.returned_at = utcnow()
    event(db, order, actor, "devolucion_sin_reparacion", data.reason)
    db.commit()
    db.refresh(order)
    return order


@router.post("/{order_id}/notices", status_code=201)
def notice(order_id: int, data: NoticeCreate, actor: User = Depends(managers), db: Session = Depends(get_db)):
    lock_sqlite(db)
    order = order_or_404(db, order_id, lock=True)
    if data.kind == "terminada" and order.status != OrderStatus.TERMINADA:
        raise HTTPException(409, "La reparación todavía no está terminada")
    item = CustomerNotice(order_id=order.id, kind=data.kind, channel=data.channel, message=data.message, created_by=actor.id)
    db.add(item)
    db.flush()
    event(db, order, actor, "comunicacion_cliente", f"{data.kind}: {data.channel.value}")
    db.commit()
    return {"id": item.id, "created_at": serialize_date(item.created_at)}


@router.get("/{order_id}/notices")
def notices(order_id: int, actor: User = Depends(staff), db: Session = Depends(get_db)):
    get_order(order_id, actor, db)
    items = db.query(CustomerNotice).filter(CustomerNotice.order_id == order_id).order_by(CustomerNotice.id).all()
    return [{"id": item.id, "kind": item.kind, "channel": item.channel.value, "message": item.message, "created_by": item.created_by, "created_at": serialize_date(item.created_at)} for item in items]


@router.post("/{order_id}/assign", response_model=OrderOut)
def assign(order_id: int, data: AssignIn, actor: User = Depends(managers), db: Session = Depends(get_db)):
    lock_sqlite(db)
    order = order_or_404(db, order_id, lock=True)
    if order.status != OrderStatus.AUTORIZADA:
        raise HTTPException(409, "Primero autorice el presupuesto inicial")
    technician = db.get(User, data.technician_id)
    if not technician or not technician.active or technician.role not in (Role.TECNICO, Role.GERENTE, Role.SUBGERENTE):
        raise HTTPException(422, "Responsable no válido")
    estimated = utc_input(data.estimated_delivery_at)
    if estimated <= utcnow():
        raise HTTPException(422, "La entrega estimada debe estar en el futuro")
    order.assigned_to, order.estimated_delivery_at, order.status = technician.id, estimated, OrderStatus.REPARACION
    event(db, order, actor, "asignar", f"Responsable {technician.id}; fecha estimada {serialize_date(estimated)}")
    db.commit()
    db.refresh(order)
    return order


@router.post("/{order_id}/progress", status_code=201)
def add_progress(order_id: int, data: ProgressIn, actor: User = Depends(staff), db: Session = Depends(get_db)):
    lock_sqlite(db)
    order = order_or_404(db, order_id, lock=True)
    assigned_or_owner(order, actor)
    if order.status not in (OrderStatus.REPARACION, OrderStatus.CORRECCION):
        raise HTTPException(409, "La reparación no está en ejecución")
    quote = db.get(Quote, data.quote_id)
    if not quote or quote.order_id != order.id or quote.status != QuoteStatus.AUTORIZADO:
        raise HTTPException(409, "El avance debe corresponder a un presupuesto autorizado de esta orden")
    progress = WorkProgress(order_id=order.id, quote_id=quote.id, description=data.description, pending_work=data.pending_work, created_by=actor.id)
    db.add(progress)
    db.flush()
    event(db, order, actor, "avance", f"Avance {progress.id}; presupuesto {quote.id}")
    db.commit()
    return {"id": progress.id, "created_at": serialize_date(progress.created_at), "status": order.status.value}


@router.post("/{order_id}/estimated-delivery", response_model=OrderOut)
def change_estimate(order_id: int, data: DeliveryEstimateChange, actor: User = Depends(managers), db: Session = Depends(get_db)):
    lock_sqlite(db)
    order = order_or_404(db, order_id, lock=True)
    if order.status not in (OrderStatus.REPARACION, OrderStatus.CORRECCION, OrderStatus.ESPERA_REPUESTO) or order.delivered_at:
        raise HTTPException(409, "La orden no admite cambio de fecha estimada")
    estimate = utc_input(data.estimated_delivery_at)
    if estimate <= utcnow():
        raise HTTPException(422, "La fecha estimada debe estar en el futuro")
    old = order.estimated_delivery_at
    order.estimated_delivery_at = estimate
    db.add(CustomerNotice(order_id=order.id, kind="demora", channel=data.channel, message=data.message, created_by=actor.id))
    event(db, order, actor, "cambio_fecha_estimada", f"{serialize_date(old)} → {serialize_date(estimate)}; {data.reason}")
    db.commit()
    db.refresh(order)
    return order


@router.get("/{order_id}/progress")
def progress_list(order_id: int, actor: User = Depends(staff), db: Session = Depends(get_db)):
    get_order(order_id, actor, db)
    records = db.query(WorkProgress).filter(WorkProgress.order_id == order_id).order_by(WorkProgress.id).all()
    return [{"id": p.id, "quote_id": p.quote_id, "description": p.description, "pending_work": p.pending_work, "created_by": p.created_by, "created_at": serialize_date(p.created_at)} for p in records]


@router.post("/{order_id}/waiting-parts", response_model=OrderOut)
def waiting_parts(order_id: int, actor: User = Depends(staff), db: Session = Depends(get_db)):
    lock_sqlite(db)
    order = order_or_404(db, order_id, lock=True)
    assigned_or_owner(order, actor)
    if order.status != OrderStatus.REPARACION or not db.query(PartRequest.id).filter(PartRequest.order_id == order.id, PartRequest.status.in_([PurchaseStatus.SOLICITADO, PurchaseStatus.APROBADO, PurchaseStatus.PEDIDO, PurchaseStatus.PARCIAL])).first():
        raise HTTPException(409, "Registre un repuesto pendiente para suspender la reparación")
    order.status = OrderStatus.ESPERA_REPUESTO
    event(db, order, actor, "espera_repuesto")
    db.commit()
    db.refresh(order)
    return order


@router.post("/{order_id}/resume", response_model=OrderOut)
def resume(order_id: int, actor: User = Depends(staff), db: Session = Depends(get_db)):
    lock_sqlite(db)
    order = order_or_404(db, order_id, lock=True)
    assigned_or_owner(order, actor)
    if order.status != OrderStatus.ESPERA_REPUESTO:
        raise HTTPException(409, "La orden no espera repuestos")
    outstanding = db.query(PartRequest.id).filter(PartRequest.order_id == order.id, PartRequest.status.in_([PurchaseStatus.SOLICITADO, PurchaseStatus.APROBADO, PurchaseStatus.PEDIDO, PurchaseStatus.PARCIAL])).first()
    if outstanding:
        raise HTTPException(409, "Existen solicitudes de repuesto pendientes")
    order.status = OrderStatus.REPARACION
    event(db, order, actor, "reanudar")
    db.commit()
    db.refresh(order)
    return order


@router.post("/{order_id}/tests", response_model=OrderOut)
def record_test(order_id: int, data: TestIn, actor: User = Depends(staff), db: Session = Depends(get_db)):
    lock_sqlite(db)
    order = order_or_404(db, order_id, lock=True)
    assigned_or_owner(order, actor)
    if order.status not in (OrderStatus.REPARACION, OrderStatus.CORRECCION):
        raise HTTPException(409, "La orden no está lista para pruebas")
    if not db.query(WorkProgress.id).filter(WorkProgress.order_id == order.id).first():
        raise HTTPException(409, "Registre los trabajos realizados antes de probar")
    last_test = db.query(WorkTest).filter(WorkTest.order_id == order.id).order_by(WorkTest.id.desc()).first()
    if last_test and not db.query(WorkProgress.id).filter(WorkProgress.order_id == order.id, WorkProgress.created_at > last_test.created_at).first():
        raise HTTPException(409, "Registre la corrección realizada antes de repetir la prueba")
    if db.query(Quote.id).filter(Quote.order_id == order.id, Quote.status.in_([QuoteStatus.BORRADOR, QuoteStatus.ENVIADO])).first():
        raise HTTPException(409, "Resuelva los presupuestos pendientes antes de terminar")
    db.add(WorkTest(order_id=order.id, passed=data.passed, notes=data.notes, created_by=actor.id))
    order.status = OrderStatus.TERMINADA if data.passed else OrderStatus.CORRECCION
    event(db, order, actor, "prueba_conforme" if data.passed else "prueba_no_conforme", data.notes)
    db.commit()
    db.refresh(order)
    return order


@router.post("/{order_id}/deliver", response_model=OrderOut)
def deliver(order_id: int, actor: User = Depends(managers), db: Session = Depends(get_db)):
    lock_sqlite(db)
    order = order_or_404(db, order_id, lock=True)
    if order.status != OrderStatus.TERMINADA or order.delivered_at:
        raise HTTPException(409, "Solo se entrega una reparación terminada y aún no entregada")
    passed_test = db.query(WorkTest).filter(WorkTest.order_id == order.id, WorkTest.passed.is_(True)).order_by(WorkTest.id.desc()).first()
    if not passed_test or not db.query(CustomerNotice.id).filter(CustomerNotice.order_id == order.id, CustomerNotice.kind == "terminada", CustomerNotice.created_at >= passed_test.created_at).first():
        raise HTTPException(409, "Registre el aviso de reparación terminada al cliente antes de entregar")
    remaining = balance(db, order.id)
    if remaining > 0 and not db.query(PaymentCommitment.id).filter(PaymentCommitment.order_id == order.id, PaymentCommitment.active.is_(True), PaymentCommitment.amount >= remaining).first():
        raise HTTPException(409, "Hay saldo pendiente sin compromiso de pago suficiente")
    order.delivered_at, order.delivered_by = utcnow(), actor.id
    event(db, order, actor, "entrega", f"Saldo al entregar: {remaining}")
    db.commit()
    db.refresh(order)
    return order
