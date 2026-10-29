"""One promotion, handed out as numbered codes.

A restaurant wants to give fifty people the same offer and know which of
them used it. One shared code cannot answer the second half -- everybody
types WELCOME10 and the orders are indistinguishable -- and fifty coupons
created by hand is fifty chances to mistype the discount.

So: a group names the offer once, and the codes are minted from it.
"MORE", ten uses, gives MORE1 through MORE10. Each goes to one person.

THE CHILDREN ARE ORDINARY COUPONS, and that is the whole design. A child is
a row in `coupons` with a group_id, which means every guarantee the coupon
path already makes is inherited rather than rebuilt: the row lock that stops
two customers spending the last redemption at once, redemptions counted from
the orders rather than a counter that drifts, the per-customer limit, the
heading scope, the soft delete that keeps a six-month-old receipt readable.
services/coupons.py is untouched by this migration, and the checkout does
not know groups exist.

The alternative -- one row and a pattern match on MORE\\d+ -- was rejected
for exactly that reason. It would have meant a second implementation of
"has this been used?" living beside the first, in the one place in the
system where being wrong charges somebody the wrong amount.

EACH CODE IS GOOD ONCE. That is what the numbering is for: if MORE3 and
MORE7 could each be used freely they would be the same code twice over, and
the group would be a worse version of a single coupon with a redemption
limit. So a child is minted with max_redemptions = 1, and "ten uses" means
ten codes rather than ten goes at one.

Revision ID: 0055
Revises: 0054
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0055"
down_revision = "0054"
branch_labels = None
depends_on = None

# The most codes one group may mint. These are handed to people one at a
# time -- printed, texted, read out -- so a group of thousands is not a
# promotion anybody is running; it is a typo in the uses field, and it would
# write thousands of rows before anybody noticed.
MAX_CODES = 500

# Room for the number on the end. A code is varchar(32) and the longest
# suffix is three digits, so the prefix stops well short of it: what is left
# over is deliberate slack rather than a budget to spend.
MAX_PREFIX = 24


def upgrade() -> None:
    # --- the offer, named once -------------------------------------------
    op.create_table(
        "coupon_groups",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("restaurant_id", pg.UUID(as_uuid=True), nullable=False),
        # What the codes are built from. Stored as it will be compared --
        # upper case, no spaces -- for the same reason a coupon code is.
        sa.Column("code_prefix", sa.String(MAX_PREFIX), nullable=False),
        sa.Column("description", sa.String(160), nullable=True),

        # Read exactly as a coupon's are, by the same helper. A group that
        # priced its discount differently from the codes it mints would be a
        # second answer to a question that already has one.
        sa.Column("discount_kind", sa.String(16), nullable=False),
        sa.Column("discount_value", sa.Integer, nullable=False),
        sa.Column("max_discount_minor", sa.BigInteger, nullable=True),
        sa.Column("min_subtotal_minor", sa.BigInteger, nullable=False,
                  server_default="0"),

        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        # How many codes were minted. Not a limit that is checked at
        # checkout -- the codes themselves are the limit -- but the number
        # the operator typed, kept so the screen can say "7 of 10 used"
        # without counting rows to find out what the denominator was.
        sa.Column("uses", sa.Integer, nullable=False),
        sa.Column("max_per_customer", sa.Integer, nullable=True),

        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["restaurant_id"], ["restaurants.id"]),

        # The same shape of discount a coupon has, enforced the same way.
        sa.CheckConstraint(
            "discount_kind IN ('PERCENT', 'AMOUNT')", name="ck_coupon_group_kind"
        ),
        sa.CheckConstraint("discount_value > 0", name="ck_coupon_group_positive"),
        sa.CheckConstraint(
            "discount_kind <> 'PERCENT' OR discount_value <= 10000",
            name="ck_coupon_group_percent_range",
        ),
        sa.CheckConstraint("min_subtotal_minor >= 0", name="ck_coupon_group_min"),
        sa.CheckConstraint(
            "max_discount_minor IS NULL OR max_discount_minor > 0",
            name="ck_coupon_group_ceiling",
        ),
        sa.CheckConstraint(
            "max_per_customer IS NULL OR max_per_customer > 0",
            name="ck_coupon_group_per_customer",
        ),
        sa.CheckConstraint(
            f"uses > 0 AND uses <= {MAX_CODES}", name="ck_coupon_group_uses"
        ),
        sa.CheckConstraint(
            "starts_at IS NULL OR ends_at IS NULL OR ends_at > starts_at",
            name="ck_coupon_group_window",
        ),
    )
    op.create_index(
        "ix_coupon_groups_restaurant", "coupon_groups", ["restaurant_id"]
    )
    # One live group per prefix per tenant, so MORE1 can only ever have come
    # from one group. Partial, like the coupon index it mirrors: a retired
    # prefix may be used again next season.
    op.create_index(
        "uq_coupon_group_prefix", "coupon_groups",
        ["restaurant_id", "code_prefix"],
        unique=True, postgresql_where=sa.text("deleted_at IS NULL"),
    )

    op.execute("ALTER TABLE coupon_groups ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE coupon_groups FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY p_coupon_groups_tenant ON coupon_groups
        FOR ALL TO zenoeats_app
        USING (restaurant_id = current_setting('app.current_tenant', true)::uuid)
        WITH CHECK (restaurant_id = current_setting('app.current_tenant', true)::uuid)
        """
    )
    op.execute(
        """
        CREATE POLICY p_coupon_groups_system ON coupon_groups
        FOR ALL TO zenoeats_system
        USING (true) WITH CHECK (true)
        """
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON coupon_groups TO zenoeats_app")
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON coupon_groups TO zenoeats_system"
    )

    # --- which group a code came from, and which number it got -----------
    op.add_column(
        "coupons", sa.Column("group_id", pg.UUID(as_uuid=True), nullable=True)
    )
    op.add_column("coupons", sa.Column("group_number", sa.Integer, nullable=True))
    op.create_foreign_key(
        "fk_coupons_group", "coupons", "coupon_groups", ["group_id"], ["id"]
    )
    # Both or neither. A code with a number and no group has nothing to be
    # the third of, and a group member with no number cannot be told from
    # its siblings -- which is the one thing the numbering is for.
    op.create_check_constraint(
        "ck_coupon_group_membership",
        "coupons",
        "(group_id IS NULL) = (group_number IS NULL)",
    )
    # MORE3 exists once. The code index already says so by code; this says
    # so by position, which is what the minting loop relies on when it is
    # asked to add more codes to a group that already has some.
    op.create_index(
        "uq_coupon_group_number", "coupons", ["group_id", "group_number"],
        unique=True, postgresql_where=sa.text("group_id IS NOT NULL"),
    )
    op.create_index(
        "ix_coupons_group", "coupons", ["group_id"],
        postgresql_where=sa.text("group_id IS NOT NULL"),
    )


def downgrade() -> None:
    # The children go first: they are ordinary coupons that happen to point
    # at a group, and a group cannot be dropped while they do. Deleting them
    # rather than orphaning them is the honest reading -- without the group
    # there is no offer they are numbered copies of.
    op.execute("DELETE FROM coupons WHERE group_id IS NOT NULL")
    op.drop_index("ix_coupons_group", table_name="coupons")
    op.drop_index("uq_coupon_group_number", table_name="coupons")
    op.drop_constraint("ck_coupon_group_membership", "coupons", type_="check")
    op.drop_constraint("fk_coupons_group", "coupons", type_="foreignkey")
    op.drop_column("coupons", "group_number")
    op.drop_column("coupons", "group_id")

    op.execute("DROP POLICY IF EXISTS p_coupon_groups_system ON coupon_groups")
    op.execute("DROP POLICY IF EXISTS p_coupon_groups_tenant ON coupon_groups")
    op.drop_index("uq_coupon_group_prefix", table_name="coupon_groups")
    op.drop_index("ix_coupon_groups_restaurant", table_name="coupon_groups")
    op.drop_table("coupon_groups")
