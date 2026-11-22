import enum
from datetime import date, datetime, timezone

from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, Enum, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Role(str, enum.Enum):
    GERENTE = "gerente"
    SUBGERENTE = "subgerente"
    TECNICO = "tecnico"
    SOPORTE = "soporte"


class Channel(str, enum.Enum):
    WHATSAPP = "whatsapp"
    LLAMADA = "llamada"
    PRESENCIAL = "presencial"


class RequestStatus(str, enum.Enum):
    PENDIENTE = "pendiente"
    CITA = "cita"
    RECEPCION_DIRECTA = "recepcion_directa"


class AppointmentStatus(str, enum.Enum):
    PROGRAMADA = "programada"
    CONFIRMADA = "confirmada"
    ATENDIDA = "atendida"
    CANCELADA = "cancelada"
    NO_ASISTIO = "no_asistio"


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[Role] = mapped_column(Enum(Role, native_enum=False))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    token_version: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class RevokedToken(Base):
    __tablename__ = "revoked_tokens"
    jti: Mapped[str] = mapped_column(String(36), primary_key=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(80))
    entity: Mapped[str] = mapped_column(String(80))
    entity_id: Mapped[int] = mapped_column(Integer)
    detail: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Client(Base):
    __tablename__ = "clients"
    id: Mapped[int] = mapped_column(primary_key=True)
    full_name: Mapped[str] = mapped_column(String(120))
    document: Mapped[str | None] = mapped_column(String(30), unique=True)
    phone: Mapped[str] = mapped_column(String(30), index=True)
    email: Mapped[str | None] = mapped_column(String(160))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    vehicles: Mapped[list["Vehicle"]] = relationship(back_populates="client")


class Vehicle(Base):
    __tablename__ = "vehicles"
    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"), index=True)
    plate: Mapped[str] = mapped_column(String(15), unique=True, index=True)
    brand: Mapped[str | None] = mapped_column(String(60))
    model: Mapped[str | None] = mapped_column(String(60))
    year: Mapped[int | None] = mapped_column(Integer)
    notes: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    client: Mapped[Client] = relationship(back_populates="vehicles")


class ServiceRequest(Base):
    __tablename__ = "service_requests"
    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"), index=True)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicles.id"), index=True)
    channel: Mapped[Channel] = mapped_column(Enum(Channel, native_enum=False))
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[RequestStatus] = mapped_column(Enum(RequestStatus, native_enum=False), default=RequestStatus.PENDIENTE)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Appointment(Base):
    __tablename__ = "appointments"
    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("service_requests.id"), unique=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime, index=True)  # UTC naive in storage
    status: Mapped[AppointmentStatus] = mapped_column(Enum(AppointmentStatus, native_enum=False), default=AppointmentStatus.PROGRAMADA)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AppointmentSlot(Base):
    __tablename__ = "appointment_slots"
    __table_args__ = (UniqueConstraint("starts_at", "slot_number", name="uq_appointment_slot"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    appointment_id: Mapped[int] = mapped_column(ForeignKey("appointments.id", ondelete="CASCADE"), unique=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    slot_number: Mapped[int] = mapped_column(Integer)


class AppointmentEvent(Base):
    __tablename__ = "appointment_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    appointment_id: Mapped[int] = mapped_column(ForeignKey("appointments.id"), index=True)
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(40))
    old_starts_at: Mapped[datetime | None] = mapped_column(DateTime)
    new_starts_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class OrderStatus(str, enum.Enum):
    DIAGNOSTICO = "diagnostico"
    ESPERA_AUTORIZACION = "espera_autorizacion"
    AUTORIZADA = "autorizada"
    ESPERA_REPUESTO = "espera_repuesto"
    REPARACION = "reparacion"
    CORRECCION = "correccion"
    TERMINADA = "terminada"
    CERRADA_SIN_REPARACION = "cerrada_sin_reparacion"


class QuoteKind(str, enum.Enum):
    INICIAL = "inicial"
    COMPLEMENTARIO = "complementario"


class QuoteStatus(str, enum.Enum):
    BORRADOR = "borrador"
    ENVIADO = "enviado"
    AUTORIZADO = "autorizado"
    RECHAZADO = "rechazado"


class PurchaseStatus(str, enum.Enum):
    SOLICITADO = "solicitado"
    APROBADO = "aprobado"
    RECHAZADO = "rechazado"
    PEDIDO = "pedido"
    PARCIAL = "parcial"
    RECIBIDO = "recibido"
    ANULADO = "anulado"


class PaymentStatus(str, enum.Enum):
    VALIDO = "valido"
    ANULADO = "anulado"


class WorkOrder(Base):
    __tablename__ = "work_orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("service_requests.id"), unique=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"), index=True)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicles.id"), index=True)
    status: Mapped[OrderStatus] = mapped_column(Enum(OrderStatus, native_enum=False), default=OrderStatus.DIAGNOSTICO)
    reception_notes: Mapped[str] = mapped_column(Text)
    diagnosis: Mapped[str | None] = mapped_column(Text)
    proposed_work: Mapped[str | None] = mapped_column(Text)
    assigned_to: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    estimated_delivery_at: Mapped[datetime | None] = mapped_column(DateTime)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime)
    delivered_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    returned_at: Mapped[datetime | None] = mapped_column(DateTime)
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Quote(Base):
    __tablename__ = "quotes"
    __table_args__ = (CheckConstraint("amount > 0", name="ck_quote_amount"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("work_orders.id"), index=True)
    kind: Mapped[QuoteKind] = mapped_column(Enum(QuoteKind, native_enum=False))
    status: Mapped[QuoteStatus] = mapped_column(Enum(QuoteStatus, native_enum=False), default=QuoteStatus.BORRADOR)
    description: Mapped[str] = mapped_column(Text)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime)
    sent_channel: Mapped[Channel | None] = mapped_column(Enum(Channel, native_enum=False))
    sent_message: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime)
    decision_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    decision_channel: Mapped[Channel | None] = mapped_column(Enum(Channel, native_enum=False))
    decision_evidence: Mapped[str | None] = mapped_column(Text)


class WorkProgress(Base):
    __tablename__ = "work_progress"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("work_orders.id"), index=True)
    quote_id: Mapped[int] = mapped_column(ForeignKey("quotes.id"))
    description: Mapped[str] = mapped_column(Text)
    pending_work: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class WorkTest(Base):
    __tablename__ = "work_tests"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("work_orders.id"), index=True)
    passed: Mapped[bool] = mapped_column(Boolean)
    notes: Mapped[str] = mapped_column(Text)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class OrderEvent(Base):
    __tablename__ = "order_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("work_orders.id"), index=True)
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(60))
    detail: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class CustomerNotice(Base):
    __tablename__ = "customer_notices"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("work_orders.id"), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    channel: Mapped[Channel] = mapped_column(Enum(Channel, native_enum=False))
    message: Mapped[str] = mapped_column(Text)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Part(Base):
    __tablename__ = "parts"
    __table_args__ = (CheckConstraint("stock >= 0", name="ck_part_stock"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    sku: Mapped[str] = mapped_column(String(60), unique=True)
    name: Mapped[str] = mapped_column(String(150))
    stock: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Supplier(Base):
    __tablename__ = "suppliers"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(150), unique=True)
    phone: Mapped[str | None] = mapped_column(String(30))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class PartRequest(Base):
    __tablename__ = "part_requests"
    __table_args__ = (CheckConstraint("quantity > 0", name="ck_part_request_qty"), CheckConstraint("estimated_unit_cost >= 0", name="ck_part_request_cost"))
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("work_orders.id"), index=True)
    quote_id: Mapped[int] = mapped_column(ForeignKey("quotes.id"))
    part_id: Mapped[int] = mapped_column(ForeignKey("parts.id"))
    quantity: Mapped[int] = mapped_column(Integer)
    estimated_unit_cost: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    status: Mapped[PurchaseStatus] = mapped_column(Enum(PurchaseStatus, native_enum=False), default=PurchaseStatus.SOLICITADO)
    decision_reason: Mapped[str | None] = mapped_column(Text)
    decided_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"
    __table_args__ = (CheckConstraint("quantity > 0", name="ck_purchase_qty"), CheckConstraint("unit_cost >= 0", name="ck_purchase_cost"))
    id: Mapped[int] = mapped_column(primary_key=True)
    part_request_id: Mapped[int] = mapped_column(ForeignKey("part_requests.id"), unique=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"))
    quantity: Mapped[int] = mapped_column(Integer)
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    cost_change_quote_id: Mapped[int | None] = mapped_column(ForeignKey("quotes.id"))
    cost_change_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"))
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class PurchaseReceipt(Base):
    __tablename__ = "purchase_receipts"
    __table_args__ = (CheckConstraint("accepted_quantity >= 0 AND rejected_quantity >= 0 AND accepted_quantity + rejected_quantity > 0", name="ck_receipt_qty"), CheckConstraint("actual_unit_cost >= 0", name="ck_receipt_cost"))
    id: Mapped[int] = mapped_column(primary_key=True)
    purchase_order_id: Mapped[int] = mapped_column(ForeignKey("purchase_orders.id"), index=True)
    accepted_quantity: Mapped[int] = mapped_column(Integer)
    rejected_quantity: Mapped[int] = mapped_column(Integer)
    actual_unit_cost: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    cost_change_quote_id: Mapped[int | None] = mapped_column(ForeignKey("quotes.id"))
    cost_change_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"))
    notes: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class InventoryMovement(Base):
    __tablename__ = "inventory_movements"
    __table_args__ = (CheckConstraint("quantity_delta != 0", name="ck_movement_delta"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    part_id: Mapped[int] = mapped_column(ForeignKey("parts.id"), index=True)
    order_id: Mapped[int | None] = mapped_column(ForeignKey("work_orders.id"))
    receipt_id: Mapped[int | None] = mapped_column(ForeignKey("purchase_receipts.id"))
    part_request_id: Mapped[int | None] = mapped_column(ForeignKey("part_requests.id"))
    quantity_delta: Mapped[int] = mapped_column(Integer)
    unit_cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    reason: Mapped[str] = mapped_column(String(60))
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Payment(Base):
    __tablename__ = "payments"
    __table_args__ = (CheckConstraint("amount > 0", name="ck_payment_amount"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("work_orders.id"), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(80), unique=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    method: Mapped[str] = mapped_column(String(40))
    reference: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[PaymentStatus] = mapped_column(Enum(PaymentStatus, native_enum=False), default=PaymentStatus.VALIDO)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    voided_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    voided_at: Mapped[datetime | None] = mapped_column(DateTime)
    void_reason: Mapped[str | None] = mapped_column(Text)


class PaymentCommitment(Base):
    __tablename__ = "payment_commitments"
    __table_args__ = (CheckConstraint("amount > 0", name="ck_commitment_amount"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("work_orders.id"), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    due_date: Mapped[date] = mapped_column(Date)
    notes: Mapped[str] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    approved_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class CloseFrequency(str, enum.Enum):
    SEMANAL = "semanal"
    MENSUAL = "mensual"


class CloseStatus(str, enum.Enum):
    BORRADOR = "borrador"
    CERRADO = "cerrado"


class ClosePeriod(Base):
    __tablename__ = "close_periods"
    __table_args__ = (UniqueConstraint("frequency", "start_date", "end_date", name="uq_close_period"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    frequency: Mapped[CloseFrequency] = mapped_column(Enum(CloseFrequency, native_enum=False))
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    status: Mapped[CloseStatus] = mapped_column(Enum(CloseStatus, native_enum=False), default=CloseStatus.BORRADOR)
    snapshot: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    confirmed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class CloseEvent(Base):
    __tablename__ = "close_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    period_id: Mapped[int] = mapped_column(ForeignKey("close_periods.id"), index=True)
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(40))
    detail: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ReconciliationIssue(Base):
    __tablename__ = "reconciliation_issues"
    id: Mapped[int] = mapped_column(primary_key=True)
    period_id: Mapped[int] = mapped_column(ForeignKey("close_periods.id"), index=True)
    category: Mapped[str] = mapped_column(String(40))
    description: Mapped[str] = mapped_column(Text)
    evidence: Mapped[str] = mapped_column(Text)
    resolution: Mapped[str | None] = mapped_column(Text)
    resolved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Expense(Base):
    __tablename__ = "expenses"
    __table_args__ = (CheckConstraint("amount > 0", name="ck_expense_amount"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    category: Mapped[str] = mapped_column(String(60))
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    description: Mapped[str] = mapped_column(Text)
    evidence: Mapped[str] = mapped_column(Text)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    voided_at: Mapped[datetime | None] = mapped_column(DateTime)
    voided_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    void_reason: Mapped[str | None] = mapped_column(Text)


class IncidentStatus(str, enum.Enum):
    ABIERTO = "abierto"
    RESUELTO = "resuelto"


class Incident(Base):
    __tablename__ = "incidents"
    id: Mapped[int] = mapped_column(primary_key=True)
    process_code: Mapped[str] = mapped_column(String(3))
    severity: Mapped[str] = mapped_column(String(12))
    description: Mapped[str] = mapped_column(Text)
    impact: Mapped[str] = mapped_column(Text)
    action_taken: Mapped[str | None] = mapped_column(Text)
    status: Mapped[IncidentStatus] = mapped_column(Enum(IncidentStatus, native_enum=False), default=IncidentStatus.ABIERTO)
    reported_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    resolved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime)


class ManualRecord(Base):
    __tablename__ = "manual_records"
    temp_code: Mapped[str] = mapped_column(String(80), primary_key=True)
    process_code: Mapped[str] = mapped_column(String(3))
    description: Mapped[str] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(DateTime)
    recorded_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    captured_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    reconciled_entity: Mapped[str | None] = mapped_column(String(40))
    reconciled_id: Mapped[int | None] = mapped_column(Integer)
    reconciled_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    reconciled_at: Mapped[datetime | None] = mapped_column(DateTime)
    evidence: Mapped[str | None] = mapped_column(Text)


class TrainingEvaluation(Base):
    __tablename__ = "training_evaluations"
    __table_args__ = (CheckConstraint("tasks_total > 0 AND tasks_correct >= 0 AND tasks_correct <= tasks_total", name="ck_training_tasks"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    tasks_correct: Mapped[int] = mapped_column(Integer)
    tasks_total: Mapped[int] = mapped_column(Integer)
    notes: Mapped[str | None] = mapped_column(Text)
    evaluated_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
