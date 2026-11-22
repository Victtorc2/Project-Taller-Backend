"""Initial P01/P02 and identity schema.

Revision ID: 0001_initial
Revises:
"""
from alembic import op

from app.db import Base
import app.models  # noqa: F401

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    initial_tables = ("users", "revoked_tokens", "audit_events", "clients", "vehicles", "service_requests", "appointments", "appointment_slots", "appointment_events")
    Base.metadata.create_all(bind=op.get_bind(), tables=[Base.metadata.tables[name] for name in initial_tables])


def downgrade():
    initial_tables = ("users", "revoked_tokens", "audit_events", "clients", "vehicles", "service_requests", "appointments", "appointment_slots", "appointment_events")
    Base.metadata.drop_all(bind=op.get_bind(), tables=[Base.metadata.tables[name] for name in initial_tables])
