"""Copia consistente de MySQL; ejecutar diariamente con Programador de tareas."""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.engine import make_url

from app.core.config import get_settings


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def backup(output_dir: Path) -> Path:
    url = make_url(get_settings().database_url)
    if url.drivername != "mysql+pymysql" or not url.database:
        raise RuntimeError("Esta utilidad requiere DATABASE_URL de MySQL")
    executable = shutil.which("mysqldump")
    if not executable:
        raise RuntimeError("No se encontró mysqldump en PATH")
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = output_dir / f"taller_{stamp}.sql"
    temporary = output_dir / f"taller_{stamp}.sql.partial"
    env = os.environ.copy()
    env["MYSQL_PWD"] = url.password or ""
    command = [executable, "--single-transaction", "--quick", "--routines", "--triggers", "--default-character-set=utf8mb4", "--host", url.host or "127.0.0.1", "--port", str(url.port or 3306), "--user", url.username or "root", f"--result-file={temporary}", url.database]
    try:
        result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=1800, check=False)
        if result.returncode != 0 or not temporary.exists() or temporary.stat().st_size == 0:
            raise RuntimeError("La copia de seguridad falló; revise la conexión y permisos de MySQL")
        temporary.replace(path)
        manifest = {"file": path.name, "database": url.database, "created_at_utc": stamp, "size_bytes": path.stat().st_size, "sha256": sha256(path)}
        path.with_suffix(".json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return path
    finally:
        temporary.unlink(missing_ok=True)


def verify(manifest_path: Path) -> None:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    file = manifest_path.parent / Path(manifest["file"]).name
    if not file.is_file() or file.stat().st_size != manifest["size_bytes"] or sha256(file) != manifest["sha256"]:
        raise RuntimeError("La copia no coincide con su manifiesto")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=Path("backups"))
    parser.add_argument("--verify", type=Path, help="Ruta del manifiesto JSON a verificar")
    args = parser.parse_args()
    if args.verify:
        verify(args.verify)
        print("Integridad de copia verificada; la restauración se prueba por separado")
    else:
        result = backup(args.output_dir)
        print(f"Copia creada: {result}")


if __name__ == "__main__":
    main()
