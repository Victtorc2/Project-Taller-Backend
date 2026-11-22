"""Create the first manager locally; never exposed over HTTP."""
import argparse
import getpass

from sqlalchemy import select

from app.core.security import hash_password
from app.db import SessionLocal
from app.models import Role, User


def main():
    parser = argparse.ArgumentParser(description="Crear el primer gerente del taller")
    parser.add_argument("--username", required=True)
    parser.add_argument("--name", required=True)
    args = parser.parse_args()
    username = args.username.strip().lower()
    if len(username) < 3 or not all(c.isascii() and (c.islower() or c.isdigit() or c in "._-") for c in username):
        parser.error("Usuario inválido: use letras minúsculas, números, punto, guion o guion bajo")
    password = getpass.getpass("Contraseña del gerente (mínimo 12 caracteres): ")
    confirmation = getpass.getpass("Repetir contraseña: ")
    if len(password) < 12 or password != confirmation:
        parser.error("Las contraseñas no coinciden o son demasiado cortas")
    with SessionLocal.begin() as db:
        if db.scalar(select(User.id).limit(1)) is not None:
            parser.error("Ya existen usuarios; registre los demás desde la cuenta gerente")
        db.add(User(username=username, full_name=args.name.strip(), password_hash=hash_password(password), role=Role.GERENTE))
    print("Gerente inicial creado")


if __name__ == "__main__":
    main()
