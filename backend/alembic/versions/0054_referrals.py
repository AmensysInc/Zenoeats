"""Bring a friend, get something free.

A customer shares a link; somebody who has never ordered here uses it and
buys something; the customer who shared it wins a free dish.

IT IS A LOYALTY PROGRAM, not a parallel scheme. The thing won is the same
thing a filled stamp card wins -- a reward row, issued, spendable once,
expiring if the program says so, redeemed at checkout by the same picker.
So REFERRAL joins COMPLETED_ORDERS and MENU_ITEM as a third purchase type
and everything downstream of winning is already built: the ledger, the
idempotency, the locking, the expiry, the free-dish pricing. What is new is
only what counts as the qualifying purchase.

required_count works unchanged: one is "a free dish per friend", three is
"bring three friends". The engine was already counting something and this
counts friends.

THE INVITE IS SENT BY THE CUSTOMER, not by us. There is no SMS anywhere in
this stack -- email is the only channel -- and sending business SMS in the
US means a vendor, per-message cost and A2P 10DLC brand registration with
documented opt-in before the first message. None of that buys anything here:
a link forwarded by a friend from their own phone arrives from a number the
recipient knows, which is both likelier to be read and not a cold message to
a stranger's handset. So the referrals row records who was invited, the
storefront hands the customer a prefilled message to send, and the server
sends nothing to anybody who has not asked it to.

WHAT STOPS IT BEING FREE MONEY:

  A referral qualifies on the invitee's first completed order, not on the
  click. Clicking a link is not a purchase and a program that paid for
  clicks would pay for a browser refresh.

  The invitee must be somebody who has never ordered here. Otherwise a
  regular "refers" themselves a second account and the restaurant buys a
  burger for a customer it already had.

  One claim per invitee, ever, enforced by a unique index rather than a
  check: two referrers cannot both be paid for the same person, and the same
  person cannot be claimed again next month.

  A referrer cannot claim themselves, which is the obvious first attempt.

Revision ID: 0054
Revises: 0053
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0054"
down_revision = "0053"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- a third kind of qualifying purchase ------------------------------
    op.drop_constraint("ck_loyalty_purchase_type", "loyalty_programs", type_="check")
    op.create_check_constraint(
        "ck_loyalty_purchase_type",
        "loyalty_programs",
        "purchase_type IN ('COMPLETED_ORDERS','MENU_ITEM','REFERRAL')",
    )
    # A referral program names no dish and no heading, the same as an order
    # program: what it counts is people, and they are not on the menu.
    op.drop_constraint(
        "ck_loyalty_qualifier_matches_type", "loyalty_programs", type_="check"
    )
    op.create_check_constraint(
        "ck_loyalty_qualifier_matches_type",
        "loyalty_programs",
        """
        (purchase_type = 'MENU_ITEM'
         AND (qualifying_item_id IS NULL) <> (qualifying_item_type_id IS NULL))
        OR
        (purchase_type IN ('COMPLETED_ORDERS','REFERRAL')
         AND qualifying_item_id IS NULL AND qualifying_item_type_id IS NULL)
        """,
    )

    # --- who invited whom -------------------------------------------------
    op.create_table(
        "referrals",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("restaurant_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("referrer_user_id", pg.UUID(as_uuid=True), nullable=False),
        # Who they said they were inviting. Both optional: the customer may
        # simply copy the link and send it to somebody they have not named,
        # and a referral works either way. Kept so the customer can see who
        # they have invited and whether it worked.
        sa.Column("invitee_name", sa.String(160), nullable=True),
        sa.Column("invitee_phone", sa.String(32), nullable=True),
        # Filled when somebody actually arrives with the code and is a
        # customer we can name; null while the invite is only sent.
        sa.Column("invitee_user_id", pg.UUID(as_uuid=True), nullable=True),
        # SENT -> JOINED (claimed by a customer) -> QUALIFIED (they ordered).
        sa.Column("status", sa.String(16), nullable=False, server_default="SENT"),
        sa.Column("qualifying_order_id", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("qualified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["restaurant_id"], ["restaurants.id"]),
        sa.ForeignKeyConstraint(["referrer_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["invitee_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["qualifying_order_id"], ["orders.id"]),
        sa.CheckConstraint(
            "status IN ('SENT','JOINED','QUALIFIED')", name="ck_referral_status"
        ),
        # Nobody refers themselves. The obvious first attempt, refused by the
        # database rather than by a check somebody could forget to call.
        sa.CheckConstraint(
            "invitee_user_id IS NULL OR invitee_user_id <> referrer_user_id",
            name="ck_referral_not_self",
        ),
        # A qualified referral names the order that qualified it; an
        # unqualified one names none. Half a qualification is a reward
        # nobody can account for.
        sa.CheckConstraint(
            "(status = 'QUALIFIED' AND qualifying_order_id IS NOT NULL"
            " AND qualified_at IS NOT NULL)"
            " OR (status <> 'QUALIFIED' AND qualifying_order_id IS NULL"
            " AND qualified_at IS NULL)",
            name="ck_referral_qualified_complete",
        ),
    )
    op.create_index("ix_referrals_restaurant", "referrals", ["restaurant_id"])
    op.create_index("ix_referrals_referrer", "referrals", ["referrer_user_id"])
    # One claim per invitee, ever. Two referrers cannot both be paid for the
    # same person, and the same person cannot be claimed again next month.
    op.create_index(
        "uq_referral_invitee", "referrals", ["restaurant_id", "invitee_user_id"],
        unique=True, postgresql_where=sa.text("invitee_user_id IS NOT NULL"),
    )

    op.execute("ALTER TABLE referrals ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE referrals FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY p_referrals_tenant ON referrals
        FOR ALL TO zenoeats_app
        USING (restaurant_id = current_setting('app.current_tenant', true)::uuid)
        WITH CHECK (restaurant_id = current_setting('app.current_tenant', true)::uuid)
        """
    )
    op.execute(
        """
        CREATE POLICY p_referrals_system ON referrals
        FOR ALL TO zenoeats_system
        USING (true) WITH CHECK (true)
        """
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON referrals TO zenoeats_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON referrals TO zenoeats_system")

    # --- the link a customer shares --------------------------------------
    # A table of its own, because a code identifies a person and an
    # invitation is an event. Putting the code on every invitation made one
    # customer's second invitation collide with their first, which is a
    # thing the model should not have been able to express.
    op.create_table(
        "referral_codes",
        sa.Column("restaurant_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("code", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["restaurant_id"], ["restaurants.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
    )
    op.create_index(
        "ix_referral_codes_restaurant", "referral_codes", ["restaurant_id"]
    )
    # Nobody else at this restaurant holds this code, so a claim resolves to
    # exactly one customer.
    op.create_index(
        "uq_referral_code", "referral_codes", ["restaurant_id", "code"], unique=True
    )
    op.execute("ALTER TABLE referral_codes ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE referral_codes FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY p_referral_codes_tenant ON referral_codes
        FOR ALL TO zenoeats_app
        USING (restaurant_id = current_setting('app.current_tenant', true)::uuid)
        WITH CHECK (restaurant_id = current_setting('app.current_tenant', true)::uuid)
        """
    )
    op.execute(
        """
        CREATE POLICY p_referral_codes_system ON referral_codes
        FOR ALL TO zenoeats_system
        USING (true) WITH CHECK (true)
        """
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON referral_codes TO zenoeats_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON referral_codes TO zenoeats_system")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS p_referral_codes_system ON referral_codes")
    op.execute("DROP POLICY IF EXISTS p_referral_codes_tenant ON referral_codes")
    op.drop_table("referral_codes")
    op.execute("DROP POLICY IF EXISTS p_referrals_system ON referrals")
    op.execute("DROP POLICY IF EXISTS p_referrals_tenant ON referrals")
    op.drop_table("referrals")

    # A referral program has no meaning under the older kinds. Switching it
    # off is the honest fallback: nothing is paid out that was not already
    # won, and the rewards already issued stay spendable.
    op.execute("ALTER TABLE loyalty_programs NO FORCE ROW LEVEL SECURITY")
    op.execute(
        "UPDATE loyalty_programs SET is_active = false, deleted_at = now() "
        "WHERE purchase_type = 'REFERRAL'"
    )
    op.execute("ALTER TABLE loyalty_programs FORCE ROW LEVEL SECURITY")
    op.execute(
        "DELETE FROM loyalty_programs WHERE purchase_type = 'REFERRAL'"
    )

    op.drop_constraint(
        "ck_loyalty_qualifier_matches_type", "loyalty_programs", type_="check"
    )
    op.create_check_constraint(
        "ck_loyalty_qualifier_matches_type",
        "loyalty_programs",
        """
        (purchase_type = 'MENU_ITEM'
         AND (qualifying_item_id IS NULL) <> (qualifying_item_type_id IS NULL))
        OR
        (purchase_type = 'COMPLETED_ORDERS'
         AND qualifying_item_id IS NULL AND qualifying_item_type_id IS NULL)
        """,
    )
    op.drop_constraint("ck_loyalty_purchase_type", "loyalty_programs", type_="check")
    op.create_check_constraint(
        "ck_loyalty_purchase_type",
        "loyalty_programs",
        "purchase_type IN ('COMPLETED_ORDERS','MENU_ITEM')",
    )
