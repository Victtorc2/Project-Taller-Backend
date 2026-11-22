# API — Taller Automotriz Córdova

Implementación de las fases **0–8** del plan backend: base técnica, autenticación/registro interno, P01 atención, P02 citas, P03 órdenes, P04 compras y repuestos, P05 pagos, P06 cierres y reportes, y registro de contingencias. Consulte `FASES_BACKEND_TALLER_CORDOVA.md` para los objetivos de cada fase.

## Preparación

Requisitos: Python 3.11 o posterior y acceso a paquetes de `requirements.txt`. Desde esta carpeta:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

Cambie `JWT_SECRET` en `.env` por un valor aleatorio de al menos 32 caracteres. Ajuste horarios, capacidad, duración de cita, CORS y BD según el taller antes de registrar citas. La configuración inicial abre de lunes a sábado, de 08:00 a 18:00, con citas de 60 minutos y una plaza por franja. Los domingos están cerrados. **Estos horarios son supuestos de desarrollo pendientes de validación con el taller.**

```powershell
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m scripts.bootstrap_manager --username gerente --name "Nombre del gerente"
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

El comando inicial solicita contraseña sin mostrarla y solo funciona si aún no existe ningún usuario. No hay registro público: el gerente entra con `POST /api/v1/auth/login` y registra subgerente y técnicos mediante `POST /api/v1/auth/register` usando su token. El rol de soporte requiere un procedimiento separado y no se asigna desde ese endpoint. No coloque credenciales reales en el repositorio.

Documentación interactiva: `http://localhost:8000/docs`. Salud: `GET /health` y `GET /health/db` para comprobar la conexión. Las operaciones de negocio exigen `Authorization: Bearer <token>`.

## Base de datos MySQL local

El archivo `.env` local ya está configurado para la base `taller` en MySQL mediante PyMySQL; está excluido de Git. El ejemplo `.env.example` usa marcadores en lugar de credenciales. El backend requiere una URL de base de datos explícita y un secreto JWT distinto del marcador de ejemplo.

La base `taller` debe existir antes de ejecutar Alembic. Si todavía no existe, créela en tu administrador de MySQL con el nombre `taller`, juego de caracteres `utf8mb4` y cotejamiento compatible con tu instalación; después ejecuta `python -m alembic upgrade head` desde esta carpeta. Las migraciones crean las tablas, no la base de datos. No uses `Copy-Item .env.example .env` sobre la configuración local ya preparada.

En Windows, instala `tzdata` desde `requirements.txt` para disponer de `America/Lima` en `zoneinfo`. Si todavía no está disponible, la aplicación usa UTC−05:00 exclusivamente para `America/Lima`; otras zonas requieren `tzdata`.

Las pruebas sobrescriben la URL de base de datos con SQLite temporal y no deben crear tablas en MySQL. Para un despliegue posterior, usa un usuario de base de datos propio para la aplicación y cambia la contraseña local de desarrollo.

## API disponible

| Dominio | Operaciones |
| --- | --- |
| Acceso | `POST /auth/login`, `POST /auth/register` (gerente), `GET /auth/me`, `POST /auth/logout`, `POST /auth/change-password`, gestión de usuarios por gerente |
| P01 | `GET/POST /clients`, `GET/PATCH /clients/{id}`, `GET /clients/{id}/vehicles`, `GET/POST /vehicles`, `GET/PATCH /vehicles/{id}`, `GET/POST /requests`, `GET /requests/{id}`, `POST /requests/{id}/direct-reception` |
| P02 | `GET /appointments/availability?day=AAAA-MM-DD`, `GET/POST /appointments`, `GET /appointments/{id}`, `GET /appointments/{id}/history`, `POST /appointments/{id}/reschedule`, `confirm`, `attend`, `cancel`, `no-show` |
| P03 | `GET/POST /orders`, `GET /orders/{id}/history`, `POST /orders/{id}/diagnosis`, `GET/POST /orders/{id}/quotes`, envío, edición de borrador y decisión de presupuesto, asignación, avances, espera/reanudación por repuestos, pruebas, avisos, devolución sin reparación y entrega |
| P04 | `GET/POST /parts`, `GET/POST /parts/suppliers`, solicitud por OT, aprobación/rechazo, asignación desde stock, pedido, recepciones parciales, devolución de unidades recibidas, anulación de solicitud, consumo y movimientos de inventario |
| P05 | `GET /orders/{id}/balance`, `GET/POST /orders/{id}/payments`, constancia por pago, anulación motivada y `GET/POST /orders/{id}/commitments` |
| P06 | `GET /reports`, `GET/POST /periods`, detalle, diferencias, resolución, confirmación, reapertura e historial; `GET/POST /expenses` y anulación motivada |
| Continuidad | `GET/POST /incidents`, resolución; `GET/POST /manual-records` y conciliación con el registro digital correspondiente |
| Adopción | `POST /training/evaluations`, `GET /training/status` y `GET /metrics/repair-updates` para medir la capacitación y las reparaciones activas con avances del día |

Todas las rutas anteriores, salvo `/health`, usan prefijo `/api/v1`. Los listados incluyen `offset` y `limit`; clientes y vehículos admiten búsqueda `q`.

### Reglas P02

- `starts_at` debe incluir zona horaria, por ejemplo `2030-05-10T09:00:00-05:00`; se almacena en UTC y se devuelve como `+00:00`.
- Consultar disponibilidad es informativo; la reserva se verifica nuevamente en la misma transacción que guarda la cita. Una restricción única protege cada cupo.
- Estados: `programada → confirmada → atendida` o `no_asistio`; `programada`/`confirmada → cancelada`. La reprogramación devuelve a `programada`; también permite volver a programar una cita cancelada sin duplicar la solicitud.
- Cancelar libera el cupo. La recepción directa no necesita cita. La fecha de una cita no es fecha de entrega.
- Un conflicto de cupo o de transición responde `409`; un horario inválido responde `422`.

### Reglas P03–P05

- La recepción abre una OT solo desde solicitud de recepción directa o cita marcada `atendida`. Diagnóstico, presupuesto inicial enviado, respuesta registrada, responsable y fecha estimada preceden a la reparación.
- Cada presupuesto registra importe, descripción, canal y mensaje al enviar, y evidencia y canal de la respuesta del cliente. El rechazo inicial cierra sin reparación; la devolución física del vehículo es otra operación. Trabajos adicionales se vinculan a presupuestos complementarios autorizados.
- Los avances y solicitudes de repuesto requieren un presupuesto autorizado. Una prueba fallida exige registrar corrección antes de repetirla. Para entregar, la prueba debe ser conforme, debe constar aviso al cliente y el saldo debe ser cero o tener compromiso activo suficiente.
- Pedido ≠ recepción: solo unidades aceptadas aumentan existencias. El consumo descuenta stock dentro de una transacción y se vincula a solicitud y OT. Una devolución de unidades recibidas descuenta existencias y abre la posibilidad de recibir un reemplazo. Si un costo supera lo estimado o aprobado, se requiere presupuesto complementario autorizado identificado por `cost_change_quote_id`.
- Cada abono tiene `idempotency_key` para evitar cobros duplicados por reintentos. El saldo procede de importes autorizados menos pagos válidos; una anulación conserva el movimiento y motivo. Los compromisos no cuentan como dinero cobrado. La constancia `P-XXXXXXXX` es un registro interno; no equivale a comprobante tributario.
- Fechas de entrada incluyen zona horaria. Importes monetarios son decimales con dos cifras. Una OT terminada, una entrega y un pago son estados independientes.

### Cierre semanal y mensual

- Una semana va de lunes a domingo; un mes comprende el mes calendario. Solo se puede cerrar un periodo finalizado. La revisión registra diferencias y su resolución con evidencia; diferencias abiertas impiden confirmar.
- El gerente confirma o reabre con motivo. Cada confirmación guarda una copia de resultados y los IDs de los movimientos; una reapertura conserva la copia anterior en el historial.
- Se muestran por separado importe autorizado, dinero neto cobrado, costo neto de repuestos recepcionados, otros egresos y saldo al final del periodo. Las anulaciones de pagos y egresos aparecen como reversos en el periodo en que se registraron. El resultado se etiqueta **parcial** porque no equivale a utilidad neta.
- Los egresos no vinculados a compras se registran en `/expenses` con evidencia. No se permite registrar movimientos económicos nuevos dentro de un periodo cerrado; las operaciones actuales no pueden indicar una fecha económica anterior arbitraria.

### Contingencia y respaldo

- Durante una caída se usan formatos físicos con código temporal. Al restablecerse el sistema, el personal registra el incidente y la ficha temporal; gerente o subgerente vincula esa ficha a la operación digital. Un mismo registro digital no puede conciliarse con dos códigos temporales. Este mecanismo **no crea automáticamente** una cita, OT o pago: primero se ingresan y verifican por sus módulos.
- Para respaldar MySQL, instale `mysqldump` y programe diariamente `python -m scripts.backup_mysql` en el Programador de tareas de Windows. Los archivos quedan en `backups/`, fuera de Git; el manifiesto contiene SHA-256 y tamaño. Compruebe su integridad con `python -m scripts.backup_mysql --verify backups/NOMBRE.json` y pruebe periódicamente una **restauración en una base aislada**. La verificación de SHA-256 por sí sola no demuestra que la base pueda restaurarse.
- Proteja el directorio de respaldos y conserve una copia separada de la máquina principal. Las metas RTO/RPO del PETI requieren validación operativa y un calendario de copias configurado por el responsable del taller.
- El gerente registra los resultados de la evaluación práctica por trabajador. La meta es al menos 80 % de tareas correctas; el indicador de avances diarios muestra reparaciones en ejecución, espera de repuesto o corrección con un avance registrado hoy. Cuando no hay reparaciones activas, el porcentaje es `null`, no 100 %.

## Pruebas

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

La suite cubre inicio de sesión, registro controlado, permisos, revocación, relación cliente/vehículo/solicitud, duplicados, cupos y transiciones; OT, presupuestos, compras, inventario, cobros, entrega, cierres, incidentes, conciliación y verificación de respaldos. Usa una BD SQLite temporal; no modifica la base MySQL `taller`. En entornos que impiden sockets locales, el cliente ASGI de pruebas ejecuta peticiones en memoria.

## Notas de operación

Las tablas se crean mediante Alembic, no automáticamente al iniciar la API. Haga respaldos de la BD y compruebe su restauración antes del uso real. La lista de tokens revocados se almacena en BD; programar limpieza de tokens vencidos durante mantenimiento. Para un despliegue con varios trabajadores, validar una BD y un alojamiento apropiados para concurrencia antes de producción.
