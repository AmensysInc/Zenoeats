"""A phone number customers can reach each restaurant on.

The refunds policy sends a customer with a problem to the restaurant first,
and the software held no way to reach one: no phone number anywhere. The
number is shown on the customer's order page and in every email about their
order, and a restaurant cannot be activated without one.

Nullable, because existing restaurants have none until they add it; the
activation gate is what makes it required for going live.

Revision ID: 0043
Revises: 0042
"""

import sqlalchemy as sa
from alembic import op

revision = "0043"
down_revision = "0042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("restaurants", sa.Column("phone", sa.String(32), nullable=True))


def downgrade() -> None:
    op.drop_column("restaurants", "phone")
