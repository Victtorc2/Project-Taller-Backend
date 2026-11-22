"""P06 closes and phase 8 incident/contingency records.

Revision ID: 0003_closures_continuity
Revises: 0002_orders_parts_payments
"""
from alembic import op

from app.db import Base
import app.models  # noqa: F401

revision = "0003_closures_continuity"
down_revision = "0002_orders_parts_payments"
branch_labels = None
depends_on = None

TABLES = ("close_periods", "close_events", "reconciliation_issues", "expenses", "incidents", "manual_records")


def upgrade():
    Base.metadata.create_all(bind=op.get_bind(), tables=[Base.metadata.tables[name] for name in TABLES])


def downgrade():
    Base.metadata.drop_all(bind=op.get_bind(), tables=[Base.metadata.tables[name] for name in TABLES])
