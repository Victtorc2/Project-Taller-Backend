import os
import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

os.environ["DATABASE_URL"] = "sqlite:///./test-import.db"
os.environ.setdefault("JWT_SECRET", "test-only-secret-with-at-least-32-characters")

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app import continuity, reports
from app.closing import assert_open, build_report
from app.core.config import get_business_tz
from app.db import Base, make_engine
from app.models import (
    Channel, Client, CloseFrequency, Expense, Part, PartRequest, Payment,
    PurchaseOrder, PurchaseReceipt, Quote, QuoteKind, QuoteStatus, Role,
    ServiceRequest, Supplier, User, Vehicle, WorkOrder,
)
from scripts.backup_mysql import sha256, verify


@pytest.fixture
def context(tmp_path):
    engine = make_engine(f"sqlite:///{(tmp_path / 'report.db').as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine, autoflush=False, expire_on_commit=False) as db:
        manager = User(username="gerente", full_name="Gerente", password_hash="test", role=Role.GERENTE)
        db.add(manager)
        db.flush()
        yield db, manager
        db.rollback()
    engine.dispose()


def month_before_today():
    first_this_month = date.today().replace(day=1)
    end = first_this_month - timedelta(days=1)
    return end.replace(day=1), end


def local_midday(day):
    return datetime(day.year, day.month, day.day, 12, tzinfo=get_business_tz()).astimezone(timezone.utc).replace(tzinfo=None)


def seed_ledger(db, manager, day):
    customer = Client(full_name="Cliente", phone="999111222")
    db.add(customer)
    db.flush()
    car = Vehicle(client_id=customer.id, plate="ABC123")
    db.add(car)
    db.flush()
    request = ServiceRequest(client_id=customer.id, vehicle_id=car.id, channel=Channel.LLAMADA, reason="Motor", created_by=manager.id)
    db.add(request)
    db.flush()
    order = WorkOrder(request_id=request.id, client_id=customer.id, vehicle_id=car.id, reception_notes="Recibido", created_by=manager.id, created_at=local_midday(day))
    db.add(order)
    db.flush()
    quote = Quote(order_id=order.id, kind=QuoteKind.INICIAL, status=QuoteStatus.AUTORIZADO, description="Servicio", amount=Decimal("300.00"), created_by=manager.id, decided_at=local_midday(day))
    payment = Payment(order_id=order.id, amount=Decimal("100.00"), method="efectivo", idempotency_key="seed-payment", created_by=manager.id, created_at=local_midday(day))
    part = Part(sku="REP-1", name="Repuesto")
    supplier = Supplier(name="Proveedor")
    db.add_all([quote, payment, part, supplier])
    db.flush()
    need = PartRequest(order_id=order.id, quote_id=quote.id, part_id=part.id, quantity=1, estimated_unit_cost=Decimal("20.00"), created_by=manager.id)
    db.add(need)
    db.flush()
    purchase = PurchaseOrder(part_request_id=need.id, supplier_id=supplier.id, quantity=1, unit_cost=Decimal("20.00"), created_by=manager.id)
    db.add(purchase)
    db.flush()
    db.add(PurchaseReceipt(purchase_order_id=purchase.id, accepted_quantity=1, rejected_quantity=0, actual_unit_cost=Decimal("20.00"), created_by=manager.id, created_at=local_midday(day)))
    db.add(Expense(category="otros", amount=Decimal("10.00"), description="Gasto registrado", evidence="Comprobante físico", created_by=manager.id, created_at=local_midday(day)))
    db.commit()
    return request


def test_monthly_close_snapshot_and_reopening(context):
    db, manager = context
    start, end = month_before_today()
    seed_ledger(db, manager, start)
    report = build_report(db, start, end)
    assert report["sales_authorized"] == "300.00"
    assert report["cash_collected_net"] == "100.00"
    assert report["parts_received_net_cost"] == "20.00"
    assert report["other_expenses_net"] == "10.00"
    assert report["outstanding_at_period_end"] == "200.00"
    assert report["partial_result"] == "270.00"
    period = reports.create_period(reports.PeriodCreate(frequency=CloseFrequency.MENSUAL, start_date=start, end_date=end), manager, db)
    issue = reports.add_issue(period["id"], reports.IssueCreate(category="cobros", description="Diferencia en comprobantes", evidence="Libro físico sin cotejar"), manager, db)
    with pytest.raises(HTTPException) as error:
        reports.confirm_period(period["id"], manager, db)
    assert error.value.status_code == 409
    db.rollback()  # Cada petición HTTP usa una sesión nueva y revierte en caso de error.
    reports.resolve_issue(period["id"], issue["id"], reports.IssueResolve(resolution="Comprobante cotejado con pago existente", evidence="Referencia del pago conciliada"), manager, db)
    closed = reports.confirm_period(period["id"], manager, db)
    assert closed["status"] == "cerrado"
    assert closed["report"] == report
    with pytest.raises(HTTPException) as error:
        assert_open(db, local_midday(start))
    assert error.value.status_code == 409
    assert reports.get_period(period["id"], manager, db)["report"] == report
    assert reports.reopen_period(period["id"], reports.Reason(reason="Revisión gerencial de cierre"), manager, db)["status"] == "borrador"
    assert reports.confirm_period(period["id"], manager, db)["status"] == "cerrado"
    assert [item["action"] for item in reports.period_history(period["id"], manager, db)].count("confirmar") == 2


def test_incident_and_manual_reconciliation(context):
    db, manager = context
    start, _ = month_before_today()
    request = seed_ledger(db, manager, start)
    incident = continuity.report_incident(continuity.IncidentCreate(process_code="P03", severity="alta", description="Sistema temporalmente inaccesible", impact="Se suspendieron registros digitales"), manager, db)
    assert incident["status"] == "abierto"
    assert continuity.resolve_incident(incident["id"], continuity.IncidentResolve(action_taken="Servicio restablecido y datos cotejados"), manager, db)["status"] == "resuelto"
    occurred = datetime.now(timezone.utc) - timedelta(hours=1)
    first = continuity.capture_manual(continuity.ManualRecordCreate(temp_code="PAPEL-001", process_code="P01", description="Solicitud recibida durante caída", occurred_at=occurred), manager, db)
    assert first["reconciled_at"] is None
    linked = continuity.reconcile_manual("PAPEL-001", continuity.ReconcileIn(entity="solicitud", entity_id=request.id, evidence="Ficha física cotejada con solicitud"), manager, db)
    assert linked["reconciled_id"] == request.id
    continuity.capture_manual(continuity.ManualRecordCreate(temp_code="PAPEL-002", process_code="P01", description="Segunda ficha por verificar", occurred_at=occurred), manager, db)
    with pytest.raises(HTTPException) as error:
        continuity.reconcile_manual("PAPEL-002", continuity.ReconcileIn(entity="solicitud", entity_id=request.id, evidence="Misma solicitud ya conciliada"), manager, db)
    assert error.value.status_code == 409


def test_backup_manifest_integrity(tmp_path):
    dump = tmp_path / "backup.sql"
    dump.write_text("CREATE TABLE sample (id INT);", encoding="utf-8")
    manifest = tmp_path / "backup.json"
    manifest.write_text(json.dumps({"file": dump.name, "size_bytes": dump.stat().st_size, "sha256": sha256(dump)}), encoding="utf-8")
    verify(manifest)
    dump.write_text("alterado", encoding="utf-8")
    with pytest.raises(RuntimeError):
        verify(manifest)
