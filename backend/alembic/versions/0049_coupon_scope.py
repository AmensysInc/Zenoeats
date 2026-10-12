"""What a coupon applies to, when it does not apply to everything.

Until now a code came off the whole cart. That is the common case and stays
the default -- a launch offer is meant to be spent on anything -- but it is
not the only one anybody runs. "20% off burgers" and "a dollar off any shake"
are the promotions a restaurant actually asks for, and they are a promotion
about part of a menu rather than about an order.

SO A COUPON MAY NAME HEADINGS. Zero rows here means the whole cart, which is
why this is a join table rather than a nullable column: the overwhelming
majority of coupons will have no rows at all, and "applies to everything" is
better said by absence than by a sentinel.

HEADINGS RATHER THAN DISHES, and that is a judgement. A coupon scoped to
individual items goes stale the moment the menu is edited -- the new burger
is not on the offer and nobody notices until a customer asks -- while one
scoped to "Burgers" keeps meaning what it meant. It is also the vocabulary
the restaurant already chose for its own menu (0007), so an operator setting
a promotion up picks from words the restaurant uses.

ON DELETE CASCADE from both sides, which is unusual here and right. A row in
this table is not a fact about anything: it is one line of a coupon's
definition. If the coupon goes, its scope is meaningless; if a heading is
deleted, a coupon that named it has lost that part of its scope and the
remaining headings are still what it applies to. Neither case is history
anybody can read later, unlike an order's snapshot of what it was charged.

Revision ID: 0049
Revises: 0048
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0049"
down_revision = "0048"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "coupon_item_types",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        # Carried so row-level security can scope this table the way it
        # scopes every other tenant-owned one, rather than through a join.
        sa.Column(
            "restaurant_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("restaurants.id"), nullable=False,
        ),
        sa.Column(
            "coupon_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("coupons.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "item_type_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("item_types.id", ondelete="CASCADE"), nullable=False,
        ),
        # One heading per coupon, once. A duplicate would double nothing --
        # the scope is a set -- but it would make the dashboard list a
        # heading twice.
        sa.UniqueConstraint("coupon_id", "item_type_id", name="uq_coupon_item_type"),
    )
    op.create_index(
        "ix_coupon_item_types_restaurant_id", "coupon_item_types", ["restaurant_id"]
    )
    op.create_index("ix_coupon_item_types_coupon", "coupon_item_types", ["coupon_id"])

    op.execute("ALTER TABLE coupon_item_types ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE coupon_item_types FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY p_coupon_item_types_tenant ON coupon_item_types
        FOR ALL TO zenoeats_app
        USING (restaurant_id = current_setting('app.current_tenant', true)::uuid)
        WITH CHECK (restaurant_id = current_setting('app.current_tenant', true)::uuid)
        """
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON coupon_item_types TO zenoeats_app"
    )

    # The system role is deliberately absent. Coupons are written through a
    # tenant session -- see api/v1/coupons.py, which took that route precisely
    # so that RLS scopes a platform operator's writes rather than an explicit
    # predicate being the only thing between one restaurant's promotion and
    # another's. The only cross-tenant job left on this table is the
    # permanent purge, and that runs as the tenant too (admin.py).


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS p_coupon_item_types_tenant ON coupon_item_types")
    op.drop_index("ix_coupon_item_types_coupon", table_name="coupon_item_types")
    op.drop_index("ix_coupon_item_types_restaurant_id", table_name="coupon_item_types")
    op.drop_table("coupon_item_types")
