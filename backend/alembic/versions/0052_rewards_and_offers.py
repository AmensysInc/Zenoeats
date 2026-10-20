"""A reward a customer earns by coming back, and an offer they are told about.

Two things a restaurant wants that the menu cannot say. Both belong to the
platform rather than the restaurant, for the reason coupons do (0048): what a
promotion costs is the platform's business as much as the kitchen's.

REWARDS are a loyalty program of the kind a counter runs: one point per
order, and at ten points some dishes are free. The points themselves are not
stored. A balance is the number of orders the customer has completed at that
restaurant, which the orders table already knows -- a counter beside it would
be a second truth about the same fact, and the two would part company the
first time an order was cancelled (the same reason coupon redemptions are
counted rather than kept).

A reward row is therefore only "this dish, free, from this many points". The
thresholds are whatever numbers the rows use -- ten, twenty, thirty-five --
rather than a table of tiers, because a tier with no dish in it is not a tier
a customer can see, and the rows already say which numbers matter.

Reaching a threshold unlocks it for good; nothing is deducted. That is the
generous reading of a points program and it is the one chosen here, so the
cap is per order instead: orders.reward_item_id is one dish, so a customer
who has unlocked everything still takes one free thing per visit rather than
a free meal every time.

OFFERS are what the storefront tells a customer between visits: a title, a
few words, a picture and a window to show it in. They carry no discount and
change no price. A promotion that actually takes money off is a coupon, which
already exists and already prices itself; this is the poster in the window,
and keeping it separate means a poster can be written by someone who is not
allowed to move a price.

Revision ID: 0052
Revises: 0051

"""

import sqlalchemy as sa
from alembic import op

revision = "0052"
down_revision = "0051"
branch_labels = None
depends_on = None


def _tenant_rls(table: str) -> None:
    """The policies every tenant-owned table carries, as 0048 set them.

    The app role sees one restaurant, the one in app.current_tenant. The
    system role sees all of them and uses it for exactly one thing: the
    permanent purge walks every tenant-owned table (admin.py _PURGE_ORDER).
    """
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY p_{table}_tenant ON {table}
        FOR ALL TO zenoeats_app
        USING (restaurant_id = current_setting('app.current_tenant', true)::uuid)
        WITH CHECK (restaurant_id = current_setting('app.current_tenant', true)::uuid)
        """
    )
    op.execute(
        f"""
        CREATE POLICY p_{table}_system ON {table}
        FOR ALL TO zenoeats_system
        USING (true) WITH CHECK (true)
        """
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO zenoeats_app")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO zenoeats_system")


def upgrade() -> None:
    # --- what a customer may have free, and from how many points ----------
    op.create_table(
        "rewards",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("restaurant_id", sa.dialects.postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("restaurants.id"), nullable=False),
        # The threshold this dish becomes free at. Ten, twenty, thirty-five:
        # the numbers are the restaurant's, not a fixed ladder.
        sa.Column("points_required", sa.Integer(), nullable=False),
        sa.Column("item_id", sa.dialects.postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("menu_items.id"), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        # Off without being deleted, so a reward withdrawn for a month keeps
        # its place in the ladder.
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.CheckConstraint("points_required > 0", name="ck_reward_points_positive"),
    )
    op.create_index("ix_rewards_restaurant_id", "rewards", ["restaurant_id"])
    # The same dish twice at one threshold is a mistake, not two rewards. A
    # dish may appear at several thresholds, which is how "free fries at ten"
    # and "free fries at twenty" can both be true while only one is reached.
    op.create_index(
        "uq_reward_item_per_threshold", "rewards",
        ["restaurant_id", "points_required", "item_id"],
        unique=True, postgresql_where=sa.text("deleted_at IS NULL"),
    )
    _tenant_rls("rewards")

    # --- what the storefront tells a customer -----------------------------
    op.create_table(
        "offers",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("restaurant_id", sa.dialects.postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("restaurants.id"), nullable=False),
        sa.Column("title", sa.String(120), nullable=False),
        # The few words under the title. Not required: a title and a picture
        # are a poster on their own.
        sa.Column("body", sa.String(500), nullable=True),
        # A key from POST /images, the same shape every other picture uses.
        sa.Column("image_path", sa.Text(), nullable=True),
        # The window to show it in. Both open-ended by default: an offer with
        # no dates is simply on until switched off.
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        # A window that closes before it opens can never show, and is always
        # a typo rather than an intention (the same rule coupons follow).
        sa.CheckConstraint(
            "starts_at IS NULL OR ends_at IS NULL OR ends_at > starts_at",
            name="ck_offer_window",
        ),
    )
    op.create_index("ix_offers_restaurant_id", "offers", ["restaurant_id"])
    _tenant_rls("offers")

    # --- what an order remembers about the reward it claimed --------------
    # One dish, so one free thing per order however many points the customer
    # has. Points at menu_items rather than at rewards: the row says which
    # dish was given away, and it stays true after the reward is withdrawn
    # from the ladder. What the line cost is on the order line itself, which
    # is where every other price on a receipt lives.
    op.add_column(
        "orders",
        sa.Column("reward_item_id", sa.dialects.postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("menu_items.id"), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("orders", "reward_item_id")

    for table in ("offers", "rewards"):
        op.execute(f"DROP POLICY IF EXISTS p_{table}_system ON {table}")
        op.execute(f"DROP POLICY IF EXISTS p_{table}_tenant ON {table}")
    op.drop_index("ix_offers_restaurant_id", table_name="offers")
    op.drop_table("offers")
    op.drop_index("uq_reward_item_per_threshold", table_name="rewards")
    op.drop_index("ix_rewards_restaurant_id", table_name="rewards")
    op.drop_table("rewards")
