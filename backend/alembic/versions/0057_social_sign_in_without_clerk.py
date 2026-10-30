"""Sign in with Apple and Facebook directly, and take Clerk out of the schema.

Customers now sign in with a password we hold, or with Google, Apple or
Facebook against those providers themselves (core/social_auth.py), so nothing
reads a Clerk id or a Clerk webhook any more.

apple_subject and facebook_subject follow google_subject (0046), for the same
reason: the address on a provider account can change, and whoever is later given
the old one must not inherit an order history. Partial and unique: no two
accounts may claim one identity, and the many NULLs cost nothing.

An existing account that was created through Clerk keeps everything but the
Clerk id. Its owner signs back in with Google or Apple, which finds the row by
verified address, or asks for a password by email. Rows still carrying a
placeholder address stay as the anchors for their orders.

Revision ID: 0057
Revises: 0056
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0057"
down_revision = "0056"
branch_labels = None
depends_on = None

SUBJECTS = ("apple_subject", "facebook_subject")


def upgrade() -> None:
    for column in SUBJECTS:
        op.add_column("users", sa.Column(column, sa.String(255), nullable=True))
        op.execute(
            f"CREATE UNIQUE INDEX uq_users_{column} ON users ({column}) "
            f"WHERE {column} IS NOT NULL"
        )

    op.drop_column("users", "clerk_user_id")
    op.drop_table("clerk_events")


def downgrade() -> None:
    # The shape comes back, exactly as 0001 and 0002 left it, because their own
    # downgrades expect to find it. The Clerk ids and deliveries do not.
    op.create_table(
        "clerk_events",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("clerk_event_id", sa.String(255), nullable=False, unique=True),
        sa.Column("type", sa.String(128), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="RECEIVED"),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_clerk_events_type", "clerk_events", ["type"])
    op.execute("GRANT SELECT, INSERT, UPDATE ON clerk_events TO zenoeats_system")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON clerk_events TO zenoeats_app")

    op.add_column("users", sa.Column("clerk_user_id", sa.String(255), nullable=True))
    op.create_unique_constraint("users_clerk_user_id_key", "users", ["clerk_user_id"])
    op.create_index("ix_users_clerk", "users", ["clerk_user_id"])
    op.create_check_constraint(
        "ck_users_has_identity",
        "users",
        "clerk_user_id IS NOT NULL OR email IS NOT NULL",
    )

    for column in SUBJECTS:
        op.execute(f"DROP INDEX IF EXISTS uq_users_{column}")
        op.drop_column("users", column)
