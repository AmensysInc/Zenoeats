"""The platform root becomes a location picker instead of a dead end.

Until now `zenoeats.local` with no restaurant label answered
RESTAURANT_NOT_FOUND: the only way in was to already know a subdomain. A
customer who types the bare domain is the common case, so the root now lists
the places they can order from.

A location is not a restaurant row. Three of the four we open with have no
tenant behind them yet, and standing up a `restaurants` row for each would
mean four half-built tenants with no menu, no staff and no Stripe account,
which the activation gate, the admin portal and the kitchen board would all
have to learn to skip. A location also owns its public name: the door says
"Jr's Corner", the tenant it opens is "Spice House".

ON RLS. This table carries `restaurant_id`, so the isolation gate in
tests/test_rls_isolation.py requires RLS enabled and forced, and that is
right -- but the policy is deliberately a public read rather than a tenant
match. This list is what an anonymous browser fetches on the platform root,
where there is no tenant in context to scope to; scoping it to
`current_setting('app.current_tenant')` would return zero rows on the only
page that reads it. So:

    app     SELECT, unconditionally. The rows are public by design -- every
            one of them is already on the front page.
    system  SELECT and write. Seeding and, later, the admin portal.

Nothing tenant-owned is reachable through here: the table holds a name, a
city, a status and a foreign key, and the storefront behind an open location
is public anyway.

Revision ID: 0044
Revises: 0043
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0044"
down_revision = "0043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "locations",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("slug", sa.String(80), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("city", sa.String(120), nullable=False),
        sa.Column("region", sa.String(120), nullable=True),
        sa.Column("address_line", sa.String(300), nullable=True),
        sa.Column("blurb", sa.String(300), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="COMING_SOON"),
        sa.Column(
            "restaurant_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("restaurants.id", ondelete="RESTRICT"), nullable=True,
        ),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("image_path", sa.String(500), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "status IN ('OPEN', 'COMING_SOON', 'HIDDEN')",
            name="ck_locations_status",
        ),
        # A location that is OPEN has to open something. Enforced here rather
        # than in the service, because an OPEN row with no restaurant renders
        # a card that goes nowhere and the only way to find out is to click it.
        sa.CheckConstraint(
            "status <> 'OPEN' OR restaurant_id IS NOT NULL",
            name="ck_locations_open_has_restaurant",
        ),
        sa.UniqueConstraint("slug", name="uq_locations_slug"),
    )
    op.create_index("ix_locations_slug", "locations", ["slug"])
    op.create_index("ix_locations_status", "locations", ["status"])
    op.create_index("ix_locations_restaurant_id", "locations", ["restaurant_id"])

    op.execute("ALTER TABLE locations ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE locations FORCE ROW LEVEL SECURITY")

    # Public read. See the module docstring for why this is not a tenant match.
    op.execute(
        """
        CREATE POLICY p_locations_public_read ON locations
        FOR SELECT TO zenoeats_app
        USING (true)
        """
    )
    op.execute(
        """
        CREATE POLICY p_locations_system ON locations
        FOR ALL TO zenoeats_system
        USING (true) WITH CHECK (true)
        """
    )
    op.execute("GRANT SELECT ON locations TO zenoeats_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON locations TO zenoeats_system")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS p_locations_system ON locations")
    op.execute("DROP POLICY IF EXISTS p_locations_public_read ON locations")
    op.drop_index("ix_locations_restaurant_id", table_name="locations")
    op.drop_index("ix_locations_status", table_name="locations")
    op.drop_index("ix_locations_slug", table_name="locations")
    op.drop_table("locations")
