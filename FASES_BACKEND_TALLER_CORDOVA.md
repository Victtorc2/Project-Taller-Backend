# Fases de implementación del backend — Taller Automotriz Córdova

## 1. Objetivo y alcance

Construir la API y la lógica de negocio del sistema web del taller conforme a los seis procesos TO-BE del PETI. La orden de trabajo (OT) es el eje que relaciona solicitud, cliente, vehículo, diagnóstico, presupuesto, reparación, repuestos, pagos y entrega.

**Flujo principal:** solicitud → cita opcional → recepción y OT → diagnóstico → presupuesto y autorización → reparación → prueba → aviso y cobro o acuerdo → entrega → cierre y reportes.

Este documento es un **plan de implementación**, no una descripción de funciones ya construidas. Se basa en `APF2 (1).docx` y los diagramas TO-BE del proyecto. El alcance inicial comprende cuentas para los cuatro trabajadores; clientes y proveedores participan en los procesos, pero no necesitan acceso al sistema en la primera versión.

## 2. Principios de implementación

- Stack propuesto: FastAPI, SQLAlchemy, Alembic y base de datos relacional. SQLite puede servir para desarrollo; antes de producción se debe evaluar la concurrencia y el alojamiento de la BD.
- Separar por dominios (`auth`, `clientes`, `citas`, `ordenes`, `repuestos`, `pagos`, `cierres`, `reportes`). Las rutas reciben solicitudes y los servicios aplican reglas; no aceptar transiciones o saldos arbitrarios del cliente HTTP.
- Usar transacciones para operaciones que cambien varios registros; claves foráneas, restricciones e índices para proteger integridad. Representar dinero con `Decimal`/`Numeric`, nunca con punto flotante.
- Guardar usuario y momento de los cambios relevantes; las aprobaciones, anulaciones, correcciones y reaperturas deben incluir motivo y quedar auditadas.
- Definir una zona horaria de negocio para citas y cierres. Almacenar fechas de forma consistente y expresar claramente el periodo de cada reporte.
- Evitar eliminación física de registros con historial operativo o económico. Usar desactivación, anulación o movimientos compensatorios según corresponda.
- Documentar la API mediante OpenAPI y probar las reglas críticas en el backend, incluidos permisos y solicitudes simultáneas.

## 3. Roles y permisos iniciales

| Rol | Funciones permitidas |
| --- | --- |
| Gerente | Supervisar procesos; aprobar presupuestos y compras; gestionar cobros; validar y reabrir cierres; consultar reportes; administrar usuarios. |
| Subgerente | Registrar atención, citas y OT; coordinar técnicos, presupuestos, compras, recepciones, cobros y comunicaciones; revisar movimientos. Sus aprobaciones se limitan a las facultades que valide el taller. |
| Técnico mecánico | Consultar OT asignadas; registrar diagnóstico, trabajos, avances diarios, repuestos requeridos, pruebas y observaciones. Puede participar en atención y citas si se le concede el permiso operativo. |
| Soporte técnico | Mantenimiento, usuarios y respaldos con acceso limitado y auditable; no autoriza operaciones económicas. Es una función eventual, no un trabajador adicional del taller. |

Los permisos deben comprobarse en cada operación sensible, incluso si la interfaz no muestra el botón correspondiente. Gerente y subgerente también pueden actuar operativamente cuando estén asignados a una reparación.

## 4. Fases

### Fase 0 — Definición funcional y base técnica

**Objetivo:** cerrar las reglas del TO-BE antes de programar los módulos.

**Implementar/definir:**

- Estructura FastAPI, configuración por entorno, conexión a BD, migraciones, errores, paginación y filtros.
- Modelo relacional inicial y diagrama de entidades; catálogos de estados y tabla de transiciones válidas.
- Matriz de permisos por operación, convenciones de auditoría y estrategia de respaldos.
- Acuerdos con el taller sobre capacidad y duración de citas, registro de autorización del cliente, presupuesto, entrega con saldo, alcance de costos y ajustes de periodos cerrados.

**Salida verificable:** esquema inicial migrable y reglas aprobadas para los seis procesos. Una transición inválida tiene un error de negocio definido.

### Fase 1 — Identidad y control de acceso

**Objetivo:** proteger los datos y atribuir cada acción a una persona.

**Entidades:** `Usuario`, `Rol`/permisos, `EventoAuditoria`.

**Funciones:** inicio de sesión, cierre o invalidación de sesión según diseño, cambio seguro de contraseña, activación/desactivación de usuario y autorización por operación. Crear cuentas individuales para gerente, subgerente y dos técnicos; no compartir credenciales.

**Salida verificable:** un técnico no puede aprobar compras, modificar cobros ni cerrar periodos aunque invoque directamente la API. Cada aprobación registra actor y fecha.

### Fase 2 — P01 Atención y registro del cliente

**Objetivo:** centralizar la información que llega por WhatsApp, llamada o atención presencial.

**Entidades:** `Cliente`, `Vehiculo`, `SolicitudAtencion`.

**Funciones:** buscar antes de crear, registrar o actualizar cliente y vehículo, asociar vehículos al cliente, registrar canal y motivo de consulta, identificar responsable de atención y derivar a cita o recepción directa.

**Reglas:** detectar posibles duplicados por documento, teléfono o placa; conservar historial de servicios; no eliminar clientes o vehículos relacionados con una OT.

**Salida verificable:** desde una solicitud se identifica al cliente y vehículo y se puede seguir su derivación sin duplicar datos.

### Fase 3 — P02 Programación y control de citas

**Objetivo:** sustituir la agenda manual sin cruces de capacidad.

**Entidades:** `Cita`, `HorarioCapacidad`, `HistorialCita`.

**Estados:** `programada`, `confirmada`, `atendida`, `cancelada`, `no_asistio`.

**Funciones:** consultar alternativas, registrar cita, confirmarla, reprogramarla, cancelarla y registrar atención o inasistencia.

**Reglas:** validar disponibilidad **dentro de la operación de guardado**; revalidar al reprogramar; liberar capacidad al cancelar; permitir ingreso sin cita; una cita no define la fecha de entrega del vehículo.

**Salida verificable:** dos solicitudes concurrentes no pueden consumir el último cupo de una franja. Una reprogramación conserva trazabilidad del horario anterior.

### Fase 4 — P03 Gestión de reparación y entrega

**Objetivo:** gestionar cada vehículo recibido mediante una OT trazable.

**Entidades:** `OrdenTrabajo`, `Diagnostico`, `TrabajoPropuesto`, `Presupuesto` y sus versiones, `AutorizacionCliente`, `Asignacion`, `Avance`, `Prueba`, `Entrega`, `HistorialOrden`.

**Secuencia obligatoria:**

1. Registrar recepción y abrir OT; vincular cliente y vehículo.
2. Registrar diagnóstico, trabajos y repuestos necesarios.
3. Preparar y comunicar presupuesto; registrar respuesta del cliente con fecha, canal y evidencia o referencia.
4. Si se rechaza, registrar rechazo y devolución; cerrar **sin reparación**.
5. Si se autoriza, asignar responsable y fecha estimada; tramitar repuestos faltantes por P04.
6. Ejecutar y registrar avances diarios; registrar trabajos adicionales solo tras presupuesto complementario autorizado.
7. Probar y registrar resultado; si no es conforme, devolver a corrección y repetir prueba.
8. Marcar reparación terminada, avisar al cliente, consultar saldo o acuerdo de pago por P05 y registrar entrega y fecha real.

**Estados separados:** estado técnico, estado de entrega y estado financiero. Una reparación terminada puede estar sin entregar; una OT pagada puede continuar en reparación.

**Salida verificable:** no se inicia trabajo no autorizado; se conoce responsable, avance y fecha estimada; una prueba fallida impide marcar el trabajo como conforme; la entrega tiene fecha real y responsable.

### Fase 5 — P04 Compras, recepción y repuestos

**Objetivo:** vincular el costo real y el abastecimiento con cada reparación.

**Entidades:** `Repuesto`, `Proveedor`, `SolicitudRepuesto`, `PedidoCompra`, `RecepcionCompra`, `MovimientoInventario`, `ConsumoRepuesto`.

**Secuencia obligatoria:** solicitud desde OT → revisión de necesidad y costo → aprobación o rechazo motivado → selección de proveedor y pedido → verificación de cantidad y calidad → recepción conforme y costo real → actualización de existencias → consumo en OT.

**Reglas:** pedido y recepción son distintos; admitir recepción parcial; un artículo rechazado no entra al inventario ni se consume; cada consumo descuenta existencias una sola vez; anulaciones y devoluciones crean movimientos trazables. Si cambia el presupuesto al cliente, obtener autorización complementaria antes de ejecutar el trabajo afectado.

**Salida verificable:** desde un repuesto consumido se consulta OT, pedido, proveedor, recepción y costo real. No puede existir stock creado únicamente por emitir un pedido.

### Fase 6 — P05 Servicios, adelantos y cobros

**Objetivo:** conocer importes autorizados, dinero recibido y saldos pendientes por OT.

**Entidades:** `Pago`, `ConstanciaPago`, `CompromisoPago`, `AnulacionPago` o movimiento equivalente.

**Funciones:** registrar adelantos y abonos, verificar medio y referencia, emitir constancia, registrar compromiso de pago y consultar saldo.

**Reglas:** cada pago es un movimiento independiente; `saldo = importe autorizado vigente - suma de pagos válidos`. El saldo lo calcula el backend. Prohibir importes negativos y definir expresamente cómo tratar excedentes. Una corrección requiere anulación motivada y nuevo movimiento; no borrar el pago original. Pagar no cierra reparación ni registra entrega automáticamente.

**Salida verificable:** varios pagos parciales, una anulación y un pago posterior producen el saldo correcto y conservan historial completo. Un compromiso no cuenta como cobro recibido.

### Fase 7 — P06 Cierre de cuentas y reportes

**Objetivo:** revisar, confirmar y consultar resultados semanales y mensuales.

**Entidades:** `PeriodoCierre`, `Cierre`, `Ajuste`/corrección con evidencia, `HistorialCierre`.

**Secuencia obligatoria:** seleccionar periodo → consolidar movimientos → revisar registros y comprobantes → corregir diferencias con evidencia → recalcular → gerente valida y confirma → guardar cierre, responsable y reportes.

**Reportes:** separar servicios e importes autorizados, cobros efectivos, saldos pendientes, compras, egresos y estados de reparación. No equiparar ventas con cobros. Presentar resultados parciales como tales si no se registran todos los costos.

**Reglas:** impedir ediciones ordinarias sobre periodos cerrados; la reapertura requiere autorización del gerente, motivo y auditoría. El cálculo debe basarse en movimientos válidos y fechas del periodo definido.

**Salida verificable:** cada total puede explicarse por sus movimientos; una reapertura y posterior corrección quedan visibles con actor y motivo.

### Fase 8 — Comunicaciones, continuidad y piloto

**Objetivo:** probar el flujo real y sostener la operación ante incidentes.

**Funciones:** registrar confirmaciones y avisos sobre cita, presupuesto, avances, retrasos y entrega. En la primera versión, preparar y registrar comunicaciones hechas por WhatsApp o llamada sin depender de una integración automática. Programar respaldos, verificar restauración y admitir carga controlada de registros temporales en papel después de una caída, evitando duplicados.

**Piloto:** ejecutar casos completos con los cuatro trabajadores, corregir observaciones, capacitar por rol y medir adopción.

**Salida verificable:** restauración comprobada; flujo P01–P06 ejecutado de extremo a extremo; registros de contingencia incorporados sin duplicación.

## 5. Escenarios mínimos de prueba de negocio

1. Solicitud → cita → OT → presupuesto autorizado → reparación → prueba conforme → pago → entrega → cierre.
2. Atención sin cita y presupuesto rechazado: devolución y OT cerrada sin reparación.
3. Dos reservas simultáneas del último cupo; reprogramación hacia horario ocupado.
4. Trabajo adicional sin autorización: operación rechazada; con autorización complementaria: importe y saldo actualizados.
5. Repuesto pedido, recibido parcialmente y otro rechazado: existencias y consumo correctos.
6. Reparación detenida por repuesto: registrar retraso, nueva fecha estimada y comunicación al cliente.
7. Prueba no conforme: volver a corrección y repetir prueba antes de terminar.
8. Adelanto, abonos, anulación motivada y compromiso: saldo y cobros efectivos correctos.
9. Reparación terminada con saldo pendiente y acuerdo autorizado: entrega registrada independientemente.
10. Cierre confirmado: modificación ordinaria bloqueada; reapertura autorizada y auditada.
11. Acceso directo a endpoints con rol insuficiente: respuesta denegada sin alterar datos.
12. Recuperación desde respaldo e ingreso de operaciones en papel sin registros duplicados.

## 6. Hitos de aceptación del proyecto

| Hito | Meta del PETI | Evidencia desde el backend |
| --- | --- | --- |
| Procesos formalizados | 6 de 6 durante los primeros tres meses del proyecto | Reglas, estados, responsables y procedimientos documentados. |
| Seguimiento de reparaciones | Al menos 90 % de reparaciones en curso actualizadas diariamente desde el tercer mes de funcionamiento | Consulta de OT activas y fecha del último avance. |
| OT digital | 100 % de reparaciones registradas con OT al finalizar el primer año | Toda reparación referenciada a una OT. |
| Operaciones digitales | Al menos 90 % de operaciones y 95 % de operaciones económicas registradas al finalizar el primer año | Métricas de registros por proceso; contrastar con el total real del taller. |
| Reportes | Reportes semanales y mensuales antes de finalizar el primer año | Periodos generados con trazabilidad a movimientos. |
| Capacitación | Cuatro trabajadores durante el primer mes de uso; al menos 80 % de tareas correctas por persona | Evaluación práctica por rol, registrada fuera o dentro del sistema. |

Las metas de cobertura necesitan comparar registros digitales con el **total real** de operaciones; contar únicamente lo que ya existe en la base de datos daría un porcentaje engañoso.

## 7. Orden de entrega

`Fase 0 → 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8`.

La primera versión operativa requiere, como mínimo, accesos, atención, citas, OT, compras vinculadas y pagos. Construir reportes después de fijar esas reglas evita consolidar cifras incompletas. Cada fase termina con migración, endpoints documentados, permisos aplicados y pruebas de sus reglas de negocio antes de avanzar.
