"""Training evidence for the four workshop workers.

Revision ID: 0004_training_metrics
Revises: 0003_closures_continuity
"""
from alembic import op

from app.db import Base
import app.models  # noqa: F401

revision = "0004_training_metrics"
down_revision = "0003_closures_continuity"
branch_labels = None
depends_on = None


def upgrade():
    Base.metadata.create_all(bind=op.get_bind(), tables=[Base.metadata.tables["training_evaluations"]])


def downgrade():
    Base.metadata.drop_all(bind=op.get_bind(), tables=[Base.metadata.tables["training_evaluations"]])
