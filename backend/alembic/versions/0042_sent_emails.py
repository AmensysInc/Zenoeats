"""Emails sent once: a record of each, by what it was about.

SendGrid, unlike the provider before it, has no idempotency key. An email
about an order -- ready to collect, cancelled, refunded -- is therefore
claimed here under a key naming its subject before it goes out, and a
second attempt at the same key sends nothing. That covers a task run twice,
a Stripe webhook delivered again, and a refund the board issued that Stripe
then reports back.

Only the key and how the attempt went are kept: no address, no content.

Revision ID: 0042
Revises: 0041
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sent_emails",
        sa.Column("restaurant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("restaurants.id"),
                  primary_key=True),
        sa.Column("key", sa.String(200), primary_key=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.execute("ALTER TABLE sent_emails ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE sent_emails FORCE ROW LEVEL SECURITY")
    op.execute("""CREATE POLICY p_sent_emails_tenant ON sent_emails FOR ALL TO zenoeats_app
        USING (restaurant_id = current_setting('app.current_tenant', true)::uuid)
        WITH CHECK (restaurant_id = current_setting('app.current_tenant', true)::uuid)""")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON sent_emails TO zenoeats_app")


def downgrade() -> None:
    op.drop_table("sent_emails")
