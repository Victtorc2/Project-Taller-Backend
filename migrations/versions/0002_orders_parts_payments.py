"""P03/P04/P05 operational schema.

Revision ID: 0002_orders_parts_payments
Revises: 0001_initial
"""
from alembic import op

from app.db import Base
import app.models  # noqa: F401

revision = "0002_orders_parts_payments"
down_revision = "0001_initial"
branch_labels = None
depends_on = None

TABLES = (
    "work_orders", "quotes", "work_progress", "work_tests", "order_events", "customer_notices",
    "parts", "suppliers", "part_requests", "purchase_orders", "purchase_receipts",
    "inventory_movements", "payments", "payment_commitments",
)


def upgrade():
    Base.metadata.create_all(bind=op.get_bind(), tables=[Base.metadata.tables[name] for name in TABLES])


def downgrade():
    Base.metadata.drop_all(bind=op.get_bind(), tables=[Base.metadata.tables[name] for name in TABLES])
