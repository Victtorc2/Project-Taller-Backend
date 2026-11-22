from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit import record
from app.core.dependencies import require_roles
from app.db import get_db
from app.models import Client, Role, ServiceRequest, Vehicle
from app.schemas import ClientCreate, ClientOut, ClientUpdate, VehicleCreate, VehicleOut, VehicleUpdate

router = APIRouter(tags=["Clientes y vehículos"])
staff = require_roles(Role.GERENTE, Role.SUBGERENTE, Role.TECNICO)
managers = require_roles(Role.GERENTE, Role.SUBGERENTE)


@router.get("/clients", response_model=list[ClientOut])
def list_clients(q: str | None = None, offset: int = Query(0, ge=0), limit: int = Query(30, ge=1, le=100), _=Depends(staff), db: Session = Depends(get_db)):
    query = db.query(Client).filter(Client.active.is_(True))
    if q:
        query = query.filter(or_(Client.full_name.ilike(f"%{q}%"), Client.document.ilike(f"%{q}%"), Client.phone.ilike(f"%{q}%")))
    return query.order_by(Client.id.desc()).offset(offset).limit(limit).all()


@router.post("/clients", response_model=ClientOut, status_code=201)
def create_client(data: ClientCreate, actor=Depends(staff), db: Session = Depends(get_db)):
    client = Client(**data.model_dump())
    db.add(client)
    try:
        db.flush()
        record(db, actor, "crear", "cliente", client.id)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Ya existe un cliente con ese documento") from None
    db.refresh(client)
    return client


@router.get("/clients/{client_id}", response_model=ClientOut)
def get_client(client_id: int, _=Depends(staff), db: Session = Depends(get_db)):
    client = db.get(Client, client_id)
    if not client or not client.active:
        raise HTTPException(404, "Cliente no encontrado")
    return client


@router.patch("/clients/{client_id}", response_model=ClientOut)
def update_client(client_id: int, data: ClientUpdate, actor=Depends(staff), db: Session = Depends(get_db)):
    client = get_client(client_id, actor, db)
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(client, key, value)
    record(db, actor, "actualizar", "cliente", client.id)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Ya existe un cliente con ese documento") from None
    db.refresh(client)
    return client


@router.get("/clients/{client_id}/vehicles", response_model=list[VehicleOut])
def client_vehicles(client_id: int, _=Depends(staff), db: Session = Depends(get_db)):
    get_client(client_id, _, db)
    return db.query(Vehicle).filter(Vehicle.client_id == client_id, Vehicle.active.is_(True)).order_by(Vehicle.id).all()


@router.get("/vehicles", response_model=list[VehicleOut])
def list_vehicles(q: str | None = None, offset: int = Query(0, ge=0), limit: int = Query(30, ge=1, le=100), _=Depends(staff), db: Session = Depends(get_db)):
    query = db.query(Vehicle).filter(Vehicle.active.is_(True))
    if q:
        query = query.filter(Vehicle.plate.ilike(f"%{q}%"))
    return query.order_by(Vehicle.id.desc()).offset(offset).limit(limit).all()


@router.post("/vehicles", response_model=VehicleOut, status_code=201)
def create_vehicle(data: VehicleCreate, actor=Depends(staff), db: Session = Depends(get_db)):
    get_client(data.client_id, actor, db)
    vehicle = Vehicle(**data.model_dump())
    db.add(vehicle)
    try:
        db.flush()
        record(db, actor, "crear", "vehiculo", vehicle.id)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Ya existe un vehículo con esa placa") from None
    db.refresh(vehicle)
    return vehicle


@router.get("/vehicles/{vehicle_id}", response_model=VehicleOut)
def get_vehicle(vehicle_id: int, _=Depends(staff), db: Session = Depends(get_db)):
    vehicle = db.get(Vehicle, vehicle_id)
    if not vehicle or not vehicle.active:
        raise HTTPException(404, "Vehículo no encontrado")
    return vehicle


@router.patch("/vehicles/{vehicle_id}", response_model=VehicleOut)
def update_vehicle(vehicle_id: int, data: VehicleUpdate, actor=Depends(managers), db: Session = Depends(get_db)):
    vehicle = get_vehicle(vehicle_id, actor, db)
    values = data.model_dump(exclude_unset=True)
    if "client_id" in values:
        get_client(values["client_id"], actor, db)
        if values["client_id"] != vehicle.client_id and db.query(ServiceRequest.id).filter(ServiceRequest.vehicle_id == vehicle.id).first():
            raise HTTPException(409, "No se puede cambiar el propietario de un vehículo con solicitudes registradas")
    for key, value in values.items():
        setattr(vehicle, key, value)
    record(db, actor, "actualizar", "vehiculo", vehicle.id)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Ya existe un vehículo con esa placa") from None
    db.refresh(vehicle)
    return vehicle
