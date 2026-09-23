"""skema awal: job_runs, signals, signal_updates, subscribers, watchlist, message_deliveries,
provider_health, app_state

Revision ID: 0001_initial
Revises:
Create Date: 2026-09-24
"""

from __future__ import annotations

from alembic import op

from storage.models import Base

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Skema awal identik dengan model deklaratif; revisi berikutnya harus eksplisit per kolom.
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
