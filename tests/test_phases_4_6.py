import os
from datetime import date, datetime, timedelta

os.environ.setdefault("JWT_SECRET", "test-only-secret-with-at-least-32-characters")
os.environ["DATABASE_URL"] = "sqlite:///./test-import.db"

import pytest
from tests.asgi_client import ASGIClient as TestClient
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.core.config import get_business_tz
from app.db import Base, get_db, make_engine
from app.main import app
from app.models import InventoryMovement, Payment, Role, User


@pytest.fixture
def api(tmp_path):
    engine = make_engine(f"sqlite:///{(tmp_path / 'business.db').as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add_all([
            User(username="gerente", full_name="Gerente", password_hash=hash_password("ManagerPassword123!"), role=Role.GERENTE),
            User(username="tecnico", full_name="Mecánico", password_hash=hash_password("TechnicianPass123!"), role=Role.TECNICO),
        ])
        db.commit()

    def override():
        with Session(engine, autoflush=False, expire_on_commit=False) as db:
            yield db

    app.dependency_overrides[get_db] = override
    with TestClient(app) as client:
        yield client, engine
    app.dependency_overrides.clear()
    engine.dispose()


def auth(client, username, password):
    result = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert result.status_code == 200, result.text
    return {"Authorization": "Bearer " + result.json()["access_token"]}


def send(client, method, url, headers, payload=None, expected=200):
    result = client.request(method, "/api/v1" + url, headers=headers, json=payload)
    assert result.status_code == expected, result.text
    return result.json()


def prepare_order(client, manager, plate="AAA123"):
    customer = send(client, "POST", "/clients", manager, {"full_name": "Cliente Taller", "phone": "999111222"}, 201)
    car = send(client, "POST", "/vehicles", manager, {"client_id": customer["id"], "plate": plate}, 201)
    request = send(client, "POST", "/requests", manager, {"client_id": customer["id"], "vehicle_id": car["id"], "channel": "whatsapp", "reason": "Motor presenta una falla"}, 201)
    send(client, "POST", f"/requests/{request['id']}/direct-reception", manager)
    return send(client, "POST", "/orders", manager, {"request_id": request["id"], "reception_notes": "Vehículo recibido para diagnóstico"}, 201)


def authorize(client, manager, order_id, kind="inicial", amount="300.00"):
    quote = send(client, "POST", f"/orders/{order_id}/quotes", manager, {"kind": kind, "description": "Trabajos y repuestos acordados", "amount": amount}, 201)
    send(client, "POST", f"/orders/{order_id}/quotes/{quote['id']}/send", manager, {"channel": "whatsapp", "message": "Presupuesto enviado al cliente"})
    return send(client, "POST", f"/orders/{order_id}/quotes/{quote['id']}/decision", manager, {"approved": True, "channel": "whatsapp", "evidence": "Cliente autorizó por mensaje registrado"})


def test_rejection_and_no_unauthorized_repair(api):
    client, _ = api
    manager = auth(client, "gerente", "ManagerPassword123!")
    tech = auth(client, "tecnico", "TechnicianPass123!")
    order = prepare_order(client, manager)
    order_id = order["id"]
    assert client.post(f"/api/v1/orders/{order_id}/progress", headers=tech, json={"quote_id": 1, "description": "Trabajo sin autorización"}).status_code == 403
    send(client, "POST", f"/orders/{order_id}/diagnosis", tech, {"diagnosis": "Falla en sistema de frenos", "proposed_work": "Cambio de pastillas y revisión"})
    quote = send(client, "POST", f"/orders/{order_id}/quotes", manager, {"kind": "inicial", "description": "Cambio de pastillas y revisión", "amount": "200.00"}, 201)
    send(client, "POST", f"/orders/{order_id}/quotes/{quote['id']}/send", manager, {"channel": "llamada", "message": "Presupuesto informado al cliente"})
    result = send(client, "POST", f"/orders/{order_id}/quotes/{quote['id']}/decision", manager, {"approved": False, "channel": "llamada", "evidence": "Cliente rechazó el presupuesto"})
    assert result["status"] == "rechazado"
    assert send(client, "GET", f"/orders/{order_id}", manager)["status"] == "cerrada_sin_reparacion"
    assert send(client, "GET", f"/orders/{order_id}", manager)["returned_at"] is None
    assert send(client, "POST", f"/orders/{order_id}/return-unrepaired", manager, {"reason": "Vehículo devuelto al cliente"})["returned_at"] is not None
    assert client.post(f"/api/v1/orders/{order_id}/payments", headers=manager, json={"amount": "10.00", "method": "efectivo", "idempotency_key": "no-payment-allowed"}).status_code == 409


def test_authorized_work_partial_receipts_payments_and_delivery(api):
    client, engine = api
    manager = auth(client, "gerente", "ManagerPassword123!")
    tech = auth(client, "tecnico", "TechnicianPass123!")
    order_id = prepare_order(client, manager)["id"]
    send(client, "POST", f"/orders/{order_id}/diagnosis", tech, {"diagnosis": "Desgaste de componente", "proposed_work": "Sustituir pieza y probar motor"})
    quote = authorize(client, manager, order_id)
    delivery = (datetime.now(get_business_tz()) + timedelta(days=7)).isoformat()
    send(client, "POST", f"/orders/{order_id}/assign", manager, {"technician_id": 2, "estimated_delivery_at": delivery})
    part = send(client, "POST", "/parts", manager, {"sku": "REP-1", "name": "Repuesto principal"}, 201)
    supplier = send(client, "POST", "/parts/suppliers", manager, {"name": "Proveedor Nasca"}, 201)
    request = send(client, "POST", f"/parts/orders/{order_id}/requests", tech, {"quote_id": quote["id"], "part_id": part["id"], "quantity": 2, "estimated_unit_cost": "20.00"}, 201)
    send(client, "POST", f"/parts/requests/{request['id']}/decision?approved=true", manager, {"reason": "Compra necesaria para reparación"})
    purchase = send(client, "POST", f"/parts/requests/{request['id']}/purchase", manager, {"supplier_id": supplier["id"], "unit_cost": "19.00"}, 201)
    receipt1 = send(client, "POST", f"/parts/purchases/{purchase['id']}/receipts", manager, {"accepted_quantity": 1, "rejected_quantity": 1, "actual_unit_cost": "19.00"}, 201)
    assert receipt1["status"] == "parcial"
    receipt2 = send(client, "POST", f"/parts/purchases/{purchase['id']}/receipts", manager, {"accepted_quantity": 1, "rejected_quantity": 0, "actual_unit_cost": "19.00"}, 201)
    assert send(client, "GET", "/parts", tech)[0]["stock"] == 2
    send(client, "POST", f"/parts/purchases/{purchase['id']}/return-received", manager, {"receipt_id": receipt2["id"], "quantity": 1, "reason": "Pieza defectuosa devuelta al proveedor"}, 201)
    assert send(client, "GET", "/parts", tech)[0]["stock"] == 1
    send(client, "POST", f"/parts/purchases/{purchase['id']}/receipts", manager, {"accepted_quantity": 1, "rejected_quantity": 0, "actual_unit_cost": "19.00"}, 201)
    send(client, "POST", f"/parts/requests/{request['id']}/consume", tech, {"quantity": 2}, 201)
    assert send(client, "GET", "/parts", tech)[0]["stock"] == 0
    send(client, "POST", f"/orders/{order_id}/progress", tech, {"quote_id": quote["id"], "description": "Pieza instalada y motor ajustado"}, 201)
    assert send(client, "POST", f"/orders/{order_id}/tests", tech, {"passed": False, "notes": "Se detectó ruido en prueba"})["status"] == "correccion"
    assert client.post(f"/api/v1/orders/{order_id}/tests", headers=tech, json={"passed": True, "notes": "Sin falla en prueba"}).status_code == 409
    send(client, "POST", f"/orders/{order_id}/progress", tech, {"quote_id": quote["id"], "description": "Corrección y ajuste final"}, 201)
    assert send(client, "POST", f"/orders/{order_id}/tests", tech, {"passed": True, "notes": "Prueba conforme"})["status"] == "terminada"
    assert client.post(f"/api/v1/orders/{order_id}/deliver", headers=manager).status_code == 409
    send(client, "POST", f"/orders/{order_id}/notices", manager, {"kind": "terminada", "channel": "whatsapp", "message": "Se avisó que el vehículo está listo"}, 201)
    first = send(client, "POST", f"/orders/{order_id}/payments", manager, {"amount": "100.00", "method": "efectivo", "idempotency_key": "order-payment-1"}, 201)
    replay = send(client, "POST", f"/orders/{order_id}/payments", manager, {"amount": "100.00", "method": "efectivo", "idempotency_key": "order-payment-1"}, 201)
    assert replay["id"] == first["id"]
    assert send(client, "GET", f"/orders/{order_id}/balance", manager)["balance"] == "200.00"
    assert client.post(f"/api/v1/orders/{order_id}/deliver", headers=manager).status_code == 409
    due = (date.today() + timedelta(days=15)).isoformat()
    send(client, "POST", f"/orders/{order_id}/commitments", manager, {"amount": "200.00", "due_date": due, "notes": "Cliente pagará el saldo en fecha acordada"}, 201)
    assert send(client, "POST", f"/orders/{order_id}/deliver", manager)["delivered_at"] is not None
    assert client.post(f"/api/v1/orders/{order_id}/payments/{first['id']}/void", headers=tech, json={"reason": "No autorizado"}).status_code == 403
    assert client.post(f"/api/v1/orders/{order_id}/payments/{first['id']}/void", headers=manager, json={"reason": "Comprobante duplicado"}).status_code == 409
    second = send(client, "POST", f"/orders/{order_id}/payments", manager, {"amount": "200.00", "method": "transferencia", "idempotency_key": "order-payment-2"}, 201)
    assert send(client, "GET", f"/orders/{order_id}/balance", manager)["financial_status"] == "pagado"
    assert send(client, "GET", f"/orders/{order_id}/payments/{second['id']}/receipt", manager)["receipt_number"].startswith("P-")
    with Session(engine) as db:
        assert db.query(InventoryMovement).count() == 5
        assert db.query(Payment).count() == 2


def test_cost_change_requires_approved_supplement(api):
    client, _ = api
    manager = auth(client, "gerente", "ManagerPassword123!")
    tech = auth(client, "tecnico", "TechnicianPass123!")
    order_id = prepare_order(client, manager)["id"]
    send(client, "POST", f"/orders/{order_id}/diagnosis", tech, {"diagnosis": "Falla en alternador", "proposed_work": "Reparación y prueba"})
    quote = authorize(client, manager, order_id)
    delivery = (datetime.now(get_business_tz()) + timedelta(days=7)).isoformat()
    send(client, "POST", f"/orders/{order_id}/assign", manager, {"technician_id": 2, "estimated_delivery_at": delivery})
    part = send(client, "POST", "/parts", manager, {"sku": "ALT-1", "name": "Alternador"}, 201)
    supplier = send(client, "POST", "/parts/suppliers", manager, {"name": "Proveedor Ica"}, 201)
    request = send(client, "POST", f"/parts/orders/{order_id}/requests", tech, {"quote_id": quote["id"], "part_id": part["id"], "quantity": 1, "estimated_unit_cost": "20.00"}, 201)
    send(client, "POST", f"/parts/requests/{request['id']}/decision?approved=true", manager, {"reason": "Repuesto requerido"})
    assert client.post(f"/api/v1/parts/requests/{request['id']}/purchase", headers=manager, json={"supplier_id": supplier["id"], "unit_cost": "30.00"}).status_code == 409
    supplement = authorize(client, manager, order_id, kind="complementario", amount="10.00")
    purchase = send(client, "POST", f"/parts/requests/{request['id']}/purchase", manager, {"supplier_id": supplier["id"], "unit_cost": "30.00", "cost_change_quote_id": supplement["id"]}, 201)
    assert purchase["quantity"] == 1
    assert client.post(f"/api/v1/parts/purchases/{purchase['id']}/receipts", headers=manager, json={"accepted_quantity": 1, "rejected_quantity": 0, "actual_unit_cost": "31.00", "cost_change_quote_id": supplement["id"]}).status_code == 409
    assert send(client, "GET", f"/orders/{order_id}/balance", manager)["authorized_amount"] == "310.00"
