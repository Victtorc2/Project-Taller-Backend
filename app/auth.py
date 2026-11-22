from datetime import timezone

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit import record
from app.core.dependencies import bearer, current_user, require_roles
from app.core.security import decode_token, hash_password, issue_token, verify_password
from app.db import get_db
from app.models import RevokedToken, Role, User
from app.schemas import LoginIn, PasswordChange, TokenOut, UserCreate, UserOut

router = APIRouter(prefix="/auth", tags=["Autenticación"])


@router.post("/login", response_model=TokenOut)
def login(data: LoginIn, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == data.username.strip().lower()).first()
    if not user or not verify_password(data.password, user.password_hash) or not user.active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Credenciales incorrectas")
    token, expires = issue_token(user)
    return TokenOut(access_token=token, expires_at=expires)


@router.post("/register", response_model=UserOut, status_code=201)
def register(data: UserCreate, actor: User = Depends(require_roles(Role.GERENTE)), db: Session = Depends(get_db)):
    # El registro es interno: una cuenta no puede autoasignarse un rol.
    if data.role == Role.SOPORTE:
        raise HTTPException(403, "El acceso de soporte se gestiona por un procedimiento separado")
    user = User(username=data.username.lower(), full_name=data.full_name.strip(), password_hash=hash_password(data.password), role=data.role)
    db.add(user)
    try:
        db.flush()
        record(db, actor, "registrar", "usuario", user.id)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "El nombre de usuario ya existe") from None
    db.refresh(user)
    return user


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(current_user)):
    return user


@router.post("/logout", status_code=204)
def logout(credentials: HTTPAuthorizationCredentials = Depends(bearer), user: User = Depends(current_user), db: Session = Depends(get_db)):
    payload = decode_token(credentials.credentials)
    expiry = payload["exp"]
    from datetime import datetime
    db.add(RevokedToken(jti=payload["jti"], expires_at=datetime.fromtimestamp(expiry, timezone.utc).replace(tzinfo=None)))
    db.commit()


@router.post("/change-password", status_code=204)
def change_password(data: PasswordChange, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if not verify_password(data.current_password, user.password_hash):
        raise HTTPException(400, "Contraseña actual incorrecta")
    user.password_hash = hash_password(data.new_password)
    user.token_version += 1
    record(db, user, "cambiar_contrasena", "usuario", user.id)
    db.commit()


@router.get("/users", response_model=list[UserOut])
def users(_: User = Depends(require_roles(Role.GERENTE)), db: Session = Depends(get_db)):
    return db.query(User).order_by(User.id).all()


@router.patch("/users/{user_id}/active", response_model=UserOut)
def set_active(user_id: int, active: bool, actor: User = Depends(require_roles(Role.GERENTE)), db: Session = Depends(get_db)):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(404, "Usuario no encontrado")
    if user.id == actor.id and not active:
        raise HTTPException(409, "No puede desactivar su propia cuenta")
    user.active = active
    user.token_version += 1
    record(db, actor, "activar" if active else "desactivar", "usuario", user.id)
    db.commit()
    db.refresh(user)
    return user
