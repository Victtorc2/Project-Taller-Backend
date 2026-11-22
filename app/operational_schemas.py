from datetime import date, datetime, timezone
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from app.models import Channel, OrderStatus, PaymentStatus, PurchaseStatus, QuoteKind, QuoteStatus


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


Money = Decimal
PositiveMoney = Field(gt=Decimal("0"), max_digits=12, decimal_places=2)
NonnegativeMoney = Field(ge=Decimal("0"), max_digits=12, decimal_places=2)


class OrderCreate(BaseModel):
    request_id: int
    reception_notes: str = Field(min_length=5, max_length=3000)


class DiagnosisIn(BaseModel):
    diagnosis: str = Field(min_length=5, max_length=5000)
    proposed_work: str = Field(min_length=5, max_length=5000)


class QuoteCreate(BaseModel):
    kind: QuoteKind
    description: str = Field(min_length=5, max_length=5000)
    amount: Decimal = PositiveMoney


class QuoteDecision(BaseModel):
    approved: bool
    channel: Channel
    evidence: str = Field(min_length=5, max_length=3000)


class QuoteSend(BaseModel):
    channel: Channel
    message: str = Field(min_length=5, max_length=3000)


class QuoteEdit(BaseModel):
    description: str = Field(min_length=5, max_length=5000)
    amount: Decimal = PositiveMoney


class AssignIn(BaseModel):
    technician_id: int
    estimated_delivery_at: datetime


class DeliveryEstimateChange(BaseModel):
    estimated_delivery_at: datetime
    reason: str = Field(min_length=5, max_length=3000)
    channel: Channel
    message: str = Field(min_length=5, max_length=3000)


class ProgressIn(BaseModel):
    quote_id: int
    description: str = Field(min_length=5, max_length=5000)
    pending_work: str | None = Field(default=None, max_length=3000)


class TestIn(BaseModel):
    passed: bool
    notes: str = Field(min_length=5, max_length=3000)


class ReasonIn(BaseModel):
    reason: str = Field(min_length=5, max_length=3000)


class OrderOut(ORMModel):
    id: int
    request_id: int
    client_id: int
    vehicle_id: int
    status: OrderStatus
    reception_notes: str
    diagnosis: str | None
    proposed_work: str | None
    assigned_to: int | None
    estimated_delivery_at: datetime | None
    delivered_at: datetime | None
    returned_at: datetime | None
    rejection_reason: str | None
    created_at: datetime

    @field_serializer("estimated_delivery_at", "delivered_at", "returned_at", "created_at")
    def utc_date(self, value: datetime | None) -> str | None:
        return value.replace(tzinfo=timezone.utc).isoformat() if value else None


class QuoteOut(ORMModel):
    id: int
    order_id: int
    kind: QuoteKind
    status: QuoteStatus
    description: str
    amount: Decimal
    sent_channel: Channel | None
    sent_message: str | None
    decision_channel: Channel | None
    decision_evidence: str | None


class NoticeCreate(BaseModel):
    kind: str = Field(pattern=r"^(avance|demora|terminada|entrega|otro)$")
    channel: Channel
    message: str = Field(min_length=5, max_length=3000)


class PartCreate(BaseModel):
    sku: str = Field(min_length=2, max_length=60)
    name: str = Field(min_length=2, max_length=150)


class PartOut(ORMModel):
    id: int
    sku: str
    name: str
    stock: int
    active: bool


class SupplierCreate(BaseModel):
    name: str = Field(min_length=2, max_length=150)
    phone: str | None = Field(default=None, max_length=30)


class SupplierOut(ORMModel):
    id: int
    name: str
    phone: str | None
    active: bool


class PartRequestCreate(BaseModel):
    quote_id: int
    part_id: int
    quantity: int = Field(gt=0)
    estimated_unit_cost: Decimal = NonnegativeMoney


class PartRequestOut(ORMModel):
    id: int
    order_id: int
    quote_id: int
    part_id: int
    quantity: int
    estimated_unit_cost: Decimal
    status: PurchaseStatus
    decision_reason: str | None


class PurchaseCreate(BaseModel):
    supplier_id: int
    unit_cost: Decimal = NonnegativeMoney
    cost_change_quote_id: int | None = None


class ReceiptCreate(BaseModel):
    accepted_quantity: int = Field(ge=0)
    rejected_quantity: int = Field(ge=0)
    actual_unit_cost: Decimal = NonnegativeMoney
    notes: str | None = Field(default=None, max_length=3000)
    cost_change_quote_id: int | None = None


class ConsumptionCreate(BaseModel):
    quantity: int = Field(gt=0)


class ReturnReceived(BaseModel):
    receipt_id: int
    quantity: int = Field(gt=0)
    reason: str = Field(min_length=5, max_length=3000)


class PaymentCreate(BaseModel):
    amount: Decimal = PositiveMoney
    method: str = Field(min_length=2, max_length=40)
    reference: str | None = Field(default=None, max_length=120)
    idempotency_key: str = Field(min_length=8, max_length=80)


class PaymentOut(ORMModel):
    id: int
    order_id: int
    amount: Decimal
    method: str
    reference: str | None
    status: PaymentStatus
    idempotency_key: str
    created_at: datetime
    void_reason: str | None

    @field_serializer("created_at")
    def utc_date(self, value: datetime) -> str:
        return value.replace(tzinfo=timezone.utc).isoformat()


class CommitmentCreate(BaseModel):
    amount: Decimal = PositiveMoney
    due_date: date
    notes: str = Field(min_length=5, max_length=3000)


class CommitmentOut(ORMModel):
    id: int
    order_id: int
    amount: Decimal
    due_date: date
    notes: str
    active: bool
    approved_by: int


class BalanceOut(BaseModel):
    authorized_amount: Decimal
    valid_payments: Decimal
    balance: Decimal
    financial_status: str
