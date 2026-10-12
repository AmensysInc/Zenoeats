"""Codes a customer types at checkout to pay less.

A restaurant could already make an order cheaper, but only by building a
combo: a discount welded to a particular set of dishes, with no code, no
window, and no way to give it to one customer and not another. A coupon is
the other shape of the same idea -- the discount is the thing, and what it
applies to is the cart.

WHAT A COUPON IS ALLOWED TO BE is almost entirely constraints, because every
one of them is a rule that would otherwise live in whichever endpoint
happened to write the row. A percentage over 100%, a window that closes
before it opens, a limit of zero redemptions: all of them are typos that the
database refuses rather than promotions anybody meant.

The code is unique per restaurant and only among the live ones (a partial
index on deleted_at IS NULL), so two restaurants may both run WELCOME10 and
neither has to know, and a seasonal promotion can be retired in the autumn
and created again in the spring under the same code.

ON COUNTING REDEMPTIONS. There is no counter column and no redemptions
table. How many times a coupon has been used is a question the orders
already answer -- they carry coupon_id -- and a counter beside them would be
a second truth that parts company with the first the moment an order is
cancelled or expires unpaid. So the orders gain the pointer and an index to
count it by, and services/coupons.py does the counting.

ON THE ORDER'S THREE COLUMNS. coupon_id is for that counting.
coupon_code and coupon_discount_minor are snapshots, for the same reason
every other money field on an order is one (rule 4: later edits never rewrite
order history): a receipt has to keep saying STUDENT10 and -$2.40 after the
coupon has been retired, renamed, or had its percentage changed for next
month's promotion. coupon_discount_minor is the coupon's share of the
discount_minor that was already there, so a receipt can show what the meal
deals took off and what the code took off as separate lines.

Revision ID: 0048
Revises: 0047
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0048"
down_revision = "0047"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "coupons",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "restaurant_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("restaurants.id"), nullable=False,
        ),
        # Stored as it is compared: upper case, no spaces. A customer typing
        # "welcome10" on a phone has typed this coupon, and the service
        # normalises before it looks, so the column never holds a variant.
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("description", sa.String(160), nullable=True),
        # Read exactly as a combo's discount is (DiscountKind): PERCENT is
        # basis points, AMOUNT is minor units. Same two words a manager
        # already knows from the combo builder, same helper prices both.
        sa.Column("discount_kind", sa.String(16), nullable=False),
        sa.Column("discount_value", sa.Integer(), nullable=False),
        # The ceiling a combo has no use for. "20% off" with no ceiling is a
        # different offer on a $10 order and a $400 office lunch.
        sa.Column("max_discount_minor", sa.BigInteger(), nullable=True),
        sa.Column(
            "min_subtotal_minor", sa.BigInteger(), nullable=False, server_default="0"
        ),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("max_redemptions", sa.Integer(), nullable=True),
        sa.Column("max_per_customer", sa.Integer(), nullable=True),
        # Off without being deleted: a promotion paused at lunchtime and
        # resumed on Friday keeps its code, its limits and its history.
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        # NONE is a valid DiscountKind for a combo and meaningless here: a
        # coupon that takes nothing off is not a coupon.
        sa.CheckConstraint(
            "discount_kind IN ('PERCENT', 'AMOUNT')", name="ck_coupon_discount_kind"
        ),
        sa.CheckConstraint("discount_value > 0", name="ck_coupon_discount_positive"),
        sa.CheckConstraint(
            "discount_kind <> 'PERCENT' OR discount_value <= 10000",
            name="ck_coupon_percent_within_range",
        ),
        sa.CheckConstraint("min_subtotal_minor >= 0", name="ck_coupon_min_subtotal"),
        sa.CheckConstraint(
            "max_discount_minor IS NULL OR max_discount_minor > 0",
            name="ck_coupon_max_discount",
        ),
        sa.CheckConstraint(
            "max_redemptions IS NULL OR max_redemptions > 0",
            name="ck_coupon_max_redemptions",
        ),
        sa.CheckConstraint(
            "max_per_customer IS NULL OR max_per_customer > 0",
            name="ck_coupon_max_per_customer",
        ),
        sa.CheckConstraint(
            "starts_at IS NULL OR ends_at IS NULL OR ends_at > starts_at",
            name="ck_coupon_window",
        ),
    )
    op.create_index("ix_coupons_restaurant_id", "coupons", ["restaurant_id"])
    op.create_index(
        "uq_coupon_code_per_restaurant", "coupons", ["restaurant_id", "code"],
        unique=True, postgresql_where=sa.text("deleted_at IS NULL"),
    )

    op.execute("ALTER TABLE coupons ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE coupons FORCE ROW LEVEL SECURITY")
    # The ordinary tenant policy. A coupon is read by the storefront under
    # the same tenant context an order is priced in, and written by the
    # dashboard; neither is a different restaurant's business.
    op.execute(
        """
        CREATE POLICY p_coupons_tenant ON coupons
        FOR ALL TO zenoeats_app
        USING (restaurant_id = current_setting('app.current_tenant', true)::uuid)
        WITH CHECK (restaurant_id = current_setting('app.current_tenant', true)::uuid)
        """
    )
    # The system role writes nothing here and reads for one reason: the
    # permanent purge walks every tenant-owned table (admin.py _PURGE_ORDER).
    op.execute(
        """
        CREATE POLICY p_coupons_system ON coupons
        FOR ALL TO zenoeats_system
        USING (true) WITH CHECK (true)
        """
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON coupons TO zenoeats_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON coupons TO zenoeats_system")

    # --- what an order remembers about the coupon it used -----------------
    op.add_column(
        "orders",
        sa.Column(
            "coupon_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("coupons.id"), nullable=True,
        ),
    )
    op.add_column("orders", sa.Column("coupon_code", sa.String(32), nullable=True))
    op.add_column(
        "orders",
        sa.Column(
            "coupon_discount_minor", sa.BigInteger(), nullable=False, server_default="0"
        ),
    )
    # What redemptions are counted by. Partial: the overwhelming majority of
    # orders carry no coupon, and this index exists for the one question
    # "how many orders used this coupon".
    op.create_index(
        "ix_orders_coupon", "orders", ["coupon_id"],
        postgresql_where=sa.text("coupon_id IS NOT NULL"),
    )
    op.create_check_constraint(
        "ck_order_coupon_discount", "orders", "coupon_discount_minor >= 0"
    )
    # The coupon's share cannot exceed the discount it is part of. Every
    # order already here has 0 for both, so this holds over existing rows.
    op.create_check_constraint(
        "ck_order_coupon_within_discount", "orders",
        "coupon_discount_minor <= discount_minor",
    )


def downgrade() -> None:
    op.drop_constraint("ck_order_coupon_within_discount", "orders", type_="check")
    op.drop_constraint("ck_order_coupon_discount", "orders", type_="check")
    op.drop_index("ix_orders_coupon", table_name="orders")
    op.drop_column("orders", "coupon_discount_minor")
    op.drop_column("orders", "coupon_code")
    op.drop_column("orders", "coupon_id")

    op.execute("DROP POLICY IF EXISTS p_coupons_system ON coupons")
    op.execute("DROP POLICY IF EXISTS p_coupons_tenant ON coupons")
    op.drop_index("uq_coupon_code_per_restaurant", table_name="coupons")
    op.drop_index("ix_coupons_restaurant_id", table_name="coupons")
    op.drop_table("coupons")
