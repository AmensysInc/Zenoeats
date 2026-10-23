"""A loyalty program a restaurant configures, rather than one we hardcoded.

0052 gave rewards a ladder: reach ten points and these dishes unlock, for
good. That is one loyalty scheme among many, and it was the wrong one -- it
never consumes anything, so a regular eventually eats free forever, and it
cannot express "buy nine burgers, get the tenth". This replaces it with a
configurable engine, and takes the ladder out rather than leaving two
schemes in the database for a reader to choose between.

A STAMP CARD, NOT A THRESHOLD. A qualifying purchase adds a stamp; reaching
the required number issues a reward and takes those stamps back off the
card. The customer starts the next card at zero, the way a paper one works.

WHAT COUNTS IS CONFIGURED, not hardcoded per dish:

  COMPLETED_ORDERS  one stamp per completed order, optionally only when the
                    order was at least minimum_spend_minor. "Nine orders of
                    ten dollars or more, then a free meal."

  MENU_ITEM         one stamp per qualifying item bought, counted by
                    quantity -- three burgers in one order is three stamps.
                    Qualifying means a named dish, or any dish under a named
                    heading: "nine burgers" may mean one burger or the whole
                    Burgers section, and a restaurant means different things
                    by it on different weeks.

WHAT IS WON is a choice, not a fixed dish. A program lists the dishes its
reward may be taken as -- fries, a drink, a burger -- and the customer picks
one when they redeem. No monetary cap, because there is no money in it: the
reward is one of these dishes, free, whatever they cost this month.

FOUR TABLES, and the division between them is the point:

  loyalty_programs      the rule, and the dishes it pays out in
  loyalty_transactions  every stamp ever earned or taken back. The source of
                        truth, and the only place a count is derived from.
  customer_loyalty      how many stamps are on the current card. A summary
                        of the ledger above, kept because every storefront
                        page asks for it and summing a ledger per page view
                        is a query that gets slower forever.
  loyalty_rewards       a reward that has been won: issued, maybe redeemed,
                        maybe expired. Discrete rows rather than a flag,
                        because two cards completed is two free dishes and a
                        boolean cannot hold that.

0052 derived the balance from the orders table and said a counter beside it
would be a second truth. That was right for a threshold and is impossible
here: once earning consumes stamps, the count is orders minus redemptions
and no longer a function of the orders alone. The ledger is the truth; the
counter is a cache of it, and a cache that can be rebuilt by summing the
ledger is not a second truth.

IDEMPOTENCY IS A CONSTRAINT, not a check in Python. One EARN row per program
per order, enforced by a unique index, so a retried webhook or a double
click cannot credit the same order twice. A refund writes a REVERSE row
rather than deleting the EARN: what happened stays in the ledger, and the
count falls out of the sum either way.

Revision ID: 0053
Revises: 0052
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0053"
down_revision = "0052"
branch_labels = None
depends_on = None


def _tenant_rls(table: str) -> None:
    """The policies every tenant-owned table carries, as 0048 set them."""
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


def _uuid(**kw):
    return sa.Column(kw.pop("name"), pg.UUID(as_uuid=True), **kw)


def upgrade() -> None:
    # --- the rule --------------------------------------------------------
    op.create_table(
        "loyalty_programs",
        _uuid(name="id", primary_key=True, server_default=sa.text("gen_random_uuid()")),
        _uuid(name="restaurant_id", nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("purchase_type", sa.String(24), nullable=False),
        sa.Column("required_count", sa.Integer(), nullable=False),
        # Only read by COMPLETED_ORDERS. Zero means every order qualifies.
        sa.Column(
            "minimum_spend_minor", sa.BigInteger(), nullable=False, server_default="0"
        ),
        # Only read by MENU_ITEM, and exactly one of them is set: a named
        # dish, or any dish under a named heading.
        _uuid(name="qualifying_item_id", nullable=True),
        _uuid(name="qualifying_item_type_id", nullable=True),
        # How long a won reward lasts. Null means it does not expire, which
        # is the kinder default and the one a small restaurant wants.
        sa.Column("reward_expires_days", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["restaurant_id"], ["restaurants.id"]),
        sa.ForeignKeyConstraint(["qualifying_item_id"], ["menu_items.id"]),
        sa.ForeignKeyConstraint(["qualifying_item_type_id"], ["item_types.id"]),
        sa.CheckConstraint(
            "purchase_type IN ('COMPLETED_ORDERS','MENU_ITEM')",
            name="ck_loyalty_purchase_type",
        ),
        sa.CheckConstraint("required_count > 0", name="ck_loyalty_required_positive"),
        sa.CheckConstraint("minimum_spend_minor >= 0", name="ck_loyalty_minimum_spend"),
        sa.CheckConstraint(
            "reward_expires_days IS NULL OR reward_expires_days > 0",
            name="ck_loyalty_expiry_positive",
        ),
        # An item program qualifies on exactly one thing; an order program on
        # neither. Enforced rather than documented, because the earning code
        # would otherwise have to guess what a row with both means.
        sa.CheckConstraint(
            """
            (purchase_type = 'MENU_ITEM'
             AND (qualifying_item_id IS NULL) <> (qualifying_item_type_id IS NULL))
            OR
            (purchase_type = 'COMPLETED_ORDERS'
             AND qualifying_item_id IS NULL AND qualifying_item_type_id IS NULL)
            """,
            name="ck_loyalty_qualifier_matches_type",
        ),
    )
    op.create_index("ix_loyalty_programs_restaurant", "loyalty_programs", ["restaurant_id"])
    _tenant_rls("loyalty_programs")

    # --- what the reward may be taken as ---------------------------------
    op.create_table(
        "loyalty_program_items",
        _uuid(name="id", primary_key=True, server_default=sa.text("gen_random_uuid()")),
        _uuid(name="restaurant_id", nullable=False),
        _uuid(name="program_id", nullable=False),
        _uuid(name="item_id", nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["restaurant_id"], ["restaurants.id"]),
        sa.ForeignKeyConstraint(["program_id"], ["loyalty_programs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["item_id"], ["menu_items.id"]),
        sa.UniqueConstraint("program_id", "item_id", name="uq_loyalty_program_item"),
    )
    op.create_index(
        "ix_loyalty_program_items_restaurant", "loyalty_program_items", ["restaurant_id"]
    )
    _tenant_rls("loyalty_program_items")

    # --- every stamp ever earned or taken back ---------------------------
    op.create_table(
        "loyalty_transactions",
        _uuid(name="id", primary_key=True, server_default=sa.text("gen_random_uuid()")),
        _uuid(name="restaurant_id", nullable=False),
        _uuid(name="program_id", nullable=False),
        _uuid(name="customer_user_id", nullable=False),
        _uuid(name="order_id", nullable=False),
        # Positive to earn, negative to take back. Summing the column is the
        # count, which is what makes customer_loyalty a cache rather than a
        # second opinion.
        sa.Column("stamps", sa.Integer(), nullable=False),
        # EARN when the order completed, REVERSE when it was refunded.
        sa.Column("reason", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["restaurant_id"], ["restaurants.id"]),
        sa.ForeignKeyConstraint(["program_id"], ["loyalty_programs.id"]),
        sa.ForeignKeyConstraint(["customer_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"]),
        sa.CheckConstraint("reason IN ('EARN','REVERSE')", name="ck_loyalty_txn_reason"),
        sa.CheckConstraint("stamps <> 0", name="ck_loyalty_txn_stamps_nonzero"),
        # The idempotency the spec asks for, as a constraint rather than a
        # check in Python: a retried webhook or a double click cannot credit
        # or reverse the same order twice on the same program.
        sa.UniqueConstraint(
            "program_id", "order_id", "reason", name="uq_loyalty_txn_once_per_order"
        ),
    )
    op.create_index(
        "ix_loyalty_txn_customer", "loyalty_transactions", ["customer_user_id", "program_id"]
    )
    _tenant_rls("loyalty_transactions")

    # --- how many stamps are on the current card -------------------------
    op.create_table(
        "customer_loyalty",
        _uuid(name="restaurant_id", nullable=False),
        _uuid(name="program_id", primary_key=True),
        _uuid(name="customer_user_id", primary_key=True),
        sa.Column("current_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["restaurant_id"], ["restaurants.id"]),
        sa.ForeignKeyConstraint(["program_id"], ["loyalty_programs.id"]),
        sa.ForeignKeyConstraint(["customer_user_id"], ["users.id"]),
        sa.CheckConstraint("current_count >= 0", name="ck_customer_loyalty_non_negative"),
    )
    op.create_index("ix_customer_loyalty_restaurant", "customer_loyalty", ["restaurant_id"])
    _tenant_rls("customer_loyalty")

    # --- a reward that has been won --------------------------------------
    op.create_table(
        "loyalty_rewards",
        _uuid(name="id", primary_key=True, server_default=sa.text("gen_random_uuid()")),
        _uuid(name="restaurant_id", nullable=False),
        _uuid(name="program_id", nullable=False),
        _uuid(name="customer_user_id", nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="ISSUED"),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        # Null means it does not expire; the program decides when issuing.
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        # Which order spent it, and which dish was taken. Both null until it
        # is redeemed, and both set together.
        _uuid(name="redeemed_order_id", nullable=True),
        _uuid(name="redeemed_item_id", nullable=True),
        sa.Column("redeemed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["restaurant_id"], ["restaurants.id"]),
        sa.ForeignKeyConstraint(["program_id"], ["loyalty_programs.id"]),
        sa.ForeignKeyConstraint(["customer_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["redeemed_order_id"], ["orders.id"]),
        sa.ForeignKeyConstraint(["redeemed_item_id"], ["menu_items.id"]),
        sa.CheckConstraint(
            "status IN ('ISSUED','REDEEMED','EXPIRED')", name="ck_loyalty_reward_status"
        ),
        # A redeemed reward names the order and the dish; an unredeemed one
        # names neither. Half a redemption is a reward nobody can account for.
        sa.CheckConstraint(
            """
            (status = 'REDEEMED'
             AND redeemed_order_id IS NOT NULL
             AND redeemed_item_id IS NOT NULL
             AND redeemed_at IS NOT NULL)
            OR
            (status <> 'REDEEMED'
             AND redeemed_order_id IS NULL
             AND redeemed_item_id IS NULL
             AND redeemed_at IS NULL)
            """,
            name="ck_loyalty_reward_redemption_complete",
        ),
    )
    op.create_index("ix_loyalty_rewards_restaurant", "loyalty_rewards", ["restaurant_id"])
    op.create_index(
        "ix_loyalty_rewards_holder", "loyalty_rewards", ["customer_user_id", "status"]
    )
    # One reward may be spent on one order. Nothing stops two different
    # rewards on one order at the database level -- that rule belongs to
    # pricing, which gives one free dish per order (orders.reward_item_id).
    op.create_index(
        "uq_loyalty_reward_per_order", "loyalty_rewards", ["redeemed_order_id"],
        unique=True, postgresql_where=sa.text("redeemed_order_id IS NOT NULL"),
    )
    _tenant_rls("loyalty_rewards")

    # --- the ladder 0052 introduced, removed -----------------------------
    # Replaced rather than left beside the engine: two loyalty schemes in one
    # database is a question every later reader has to answer, and the answer
    # would always be "the other one". It never shipped to a customer.
    op.execute("DROP POLICY IF EXISTS p_rewards_system ON rewards")
    op.execute("DROP POLICY IF EXISTS p_rewards_tenant ON rewards")
    op.drop_index("uq_reward_item_per_threshold", table_name="rewards")
    op.drop_index("ix_rewards_restaurant_id", table_name="rewards")
    op.drop_table("rewards")


def downgrade() -> None:
    # The ladder, exactly as 0052 left it.
    op.create_table(
        "rewards",
        _uuid(name="id", primary_key=True, server_default=sa.text("gen_random_uuid()")),
        _uuid(name="restaurant_id", nullable=False),
        sa.Column("points_required", sa.Integer(), nullable=False),
        _uuid(name="item_id", nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["restaurant_id"], ["restaurants.id"]),
        sa.ForeignKeyConstraint(["item_id"], ["menu_items.id"]),
        sa.CheckConstraint("points_required > 0", name="ck_reward_points_positive"),
    )
    op.create_index("ix_rewards_restaurant_id", "rewards", ["restaurant_id"])
    op.create_index(
        "uq_reward_item_per_threshold", "rewards",
        ["restaurant_id", "points_required", "item_id"],
        unique=True, postgresql_where=sa.text("deleted_at IS NULL"),
    )
    _tenant_rls("rewards")

    for table in (
        "loyalty_rewards",
        "customer_loyalty",
        "loyalty_transactions",
        "loyalty_program_items",
        "loyalty_programs",
    ):
        op.execute(f"DROP POLICY IF EXISTS p_{table}_system ON {table}")
        op.execute(f"DROP POLICY IF EXISTS p_{table}_tenant ON {table}")
        op.drop_table(table)
