"""A welcome offer, one per customer, earned by verifying a phone number.

Somebody creates an account, adds a mobile number, proves it is theirs, and
gets a code only they can use.

ONE MORE KIND OF GROUP, not a new mechanism. 0055 established that an offer
can be named once and minted into ordinary coupon rows; a numbered group
mints all of its codes at once, and this mints one at a time, as customers
arrive. Everything after minting is identical, which is why `kind` goes on
coupon_groups rather than a second table being built beside it.

So `uses` becomes nullable: a numbered group knows how many codes it will
ever have, and a sign-up offer does not -- it has as many as there are
customers. The check constraint makes each kind say the right thing.

THE CODE IS BOUND TO ONE PERSON. coupons.customer_user_id is what makes a
welcome code a welcome code rather than a code that leaked: the first
customer to be told it by a friend would otherwise spend it. Refused in
services/coupons.py with the same sentence an unknown code gets, because
"that code belongs to somebody else" tells a stranger the code is real.

THE NUMBER IS PROVED, NOT COLLECTED. An offer given for typing a number is
an offer given for typing anything, so the coupon is issued on verification
rather than on the number being saved. What proves it is a code sent to the
handset, which needs an SMS vendor this deployment does not yet have --
services/sms.py is the seam, and it refuses rather than pretending to send.
Nothing here depends on which vendor it turns out to be.

WHY A TABLE AND NOT A COLUMN for the challenge: a code has an expiry and an
attempt count, both of which are about this attempt rather than about the
customer, and keeping them on the user row makes a second attempt overwrite
the first one's accounting. One row per challenge, and the old ones are
deleted when a new one is asked for.

Revision ID: 0056
Revises: 0055
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0056"
down_revision = "0055"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- a group can now be a sign-up offer -------------------------------
    op.add_column(
        "coupon_groups",
        sa.Column("kind", sa.String(16), nullable=False, server_default="NUMBERED"),
    )
    op.create_check_constraint(
        "ck_coupon_group_kind_value",
        "coupon_groups",
        "kind IN ('NUMBERED', 'SIGNUP')",
    )
    # A numbered group mints a known number of codes. A sign-up offer mints
    # one per customer and cannot know how many that will be.
    op.alter_column("coupon_groups", "uses", nullable=True)
    op.drop_constraint("ck_coupon_group_uses", "coupon_groups", type_="check")
    op.create_check_constraint(
        "ck_coupon_group_uses",
        "coupon_groups",
        """
        (kind = 'NUMBERED' AND uses IS NOT NULL AND uses > 0 AND uses <= 500)
        OR (kind = 'SIGNUP' AND uses IS NULL)
        """,
    )
    # One live welcome offer per restaurant. Two would both fire on the same
    # sign-up and the customer would get two codes for one account.
    op.create_index(
        "uq_coupon_group_signup", "coupon_groups", ["restaurant_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'SIGNUP' AND deleted_at IS NULL"),
    )
    # How long a welcome code lasts once it is issued. Counted from issue
    # rather than set as a date, because every customer gets theirs on a
    # different day: "30 days to use it" is the offer, not "until March".
    op.add_column(
        "coupon_groups", sa.Column("valid_days", sa.Integer, nullable=True)
    )
    op.create_check_constraint(
        "ck_coupon_group_valid_days",
        "coupon_groups",
        "valid_days IS NULL OR (valid_days > 0 AND valid_days <= 3650)",
    )

    # --- what the offer applies to, owned by the group --------------------
    # A numbered group could get away without this: its codes exist from the
    # moment it does, so the scope could be read off any one of them. A
    # sign-up offer cannot -- its first code is minted when its first
    # customer verifies a number, and until then there is nowhere for
    # "burgers only" to live. So the group holds the scope and every minted
    # code gets a copy, which is also the straighter reading of the two.
    op.create_table(
        "coupon_group_item_types",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("restaurant_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("group_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("item_type_id", pg.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(["restaurant_id"], ["restaurants.id"]),
        sa.ForeignKeyConstraint(
            ["group_id"], ["coupon_groups.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["item_type_id"], ["item_types.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint("group_id", "item_type_id", name="uq_coupon_group_item_type"),
    )
    op.create_index(
        "ix_coupon_group_item_types_restaurant",
        "coupon_group_item_types", ["restaurant_id"],
    )
    op.create_index(
        "ix_coupon_group_item_types_group", "coupon_group_item_types", ["group_id"]
    )

    op.execute("ALTER TABLE coupon_group_item_types ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE coupon_group_item_types FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY p_coupon_group_item_types_tenant ON coupon_group_item_types
        FOR ALL TO zenoeats_app
        USING (restaurant_id = current_setting('app.current_tenant', true)::uuid)
        WITH CHECK (restaurant_id = current_setting('app.current_tenant', true)::uuid)
        """
    )
    op.execute(
        """
        CREATE POLICY p_coupon_group_item_types_system ON coupon_group_item_types
        FOR ALL TO zenoeats_system
        USING (true) WITH CHECK (true)
        """
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON coupon_group_item_types "
        "TO zenoeats_app"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON coupon_group_item_types "
        "TO zenoeats_system"
    )

    # --- a coupon that belongs to one customer ----------------------------
    op.add_column(
        "coupons", sa.Column("customer_user_id", pg.UUID(as_uuid=True), nullable=True)
    )
    op.create_foreign_key(
        "fk_coupons_customer", "coupons", "users", ["customer_user_id"], ["id"]
    )
    op.create_index(
        "ix_coupons_customer", "coupons", ["customer_user_id"],
        postgresql_where=sa.text("customer_user_id IS NOT NULL"),
    )
    # One welcome code per customer per offer, ever. Without this, a customer
    # who verified a number, changed it and verified again would collect a
    # second free discount for the same account.
    op.create_index(
        "uq_coupon_group_customer", "coupons", ["group_id", "customer_user_id"],
        unique=True,
        postgresql_where=sa.text(
            "group_id IS NOT NULL AND customer_user_id IS NOT NULL"
        ),
    )

    # A group's codes are numbered or personal, never both and never
    # neither. 0055 could say "numbered" flatly because that was the only
    # kind of member; a welcome code is minted one at a time with a random
    # suffix, so it belongs to a group and has no position in it.
    op.drop_constraint("ck_coupon_group_membership", "coupons", type_="check")
    op.create_check_constraint(
        "ck_coupon_group_membership",
        "coupons",
        """
        (group_id IS NULL AND group_number IS NULL AND customer_user_id IS NULL)
        OR (group_id IS NOT NULL AND group_number IS NOT NULL
            AND customer_user_id IS NULL)
        OR (group_id IS NOT NULL AND group_number IS NULL
            AND customer_user_id IS NOT NULL)
        """,
    )

    # --- a number, and whether it was proved ------------------------------
    op.add_column(
        "users",
        sa.Column("phone_verified_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "phone_verifications",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("restaurant_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", pg.UUID(as_uuid=True), nullable=False),
        # The number being proved, which is not yet the number on the account:
        # it only moves there once a code sent to it comes back.
        sa.Column("phone", sa.String(32), nullable=False),
        # Hashed, never stored in the clear. A six-digit code is a password
        # for the length of its life, and a database read should not hand
        # somebody else's account over.
        sa.Column("code_hash", sa.String(128), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        # Wrong guesses so far. Six digits is a million possibilities and a
        # script does not get tired, so the row stops answering well before
        # that.
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["restaurant_id"], ["restaurants.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.CheckConstraint("attempts >= 0", name="ck_phone_verification_attempts"),
    )
    op.create_index(
        "ix_phone_verifications_restaurant", "phone_verifications", ["restaurant_id"]
    )
    # One live challenge per customer: asking for a new code replaces the old
    # one rather than leaving two valid, which would double the guesses a
    # script gets per minute.
    op.create_index(
        "uq_phone_verification_user", "phone_verifications", ["user_id"], unique=True
    )

    op.execute("ALTER TABLE phone_verifications ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE phone_verifications FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY p_phone_verifications_tenant ON phone_verifications
        FOR ALL TO zenoeats_app
        USING (restaurant_id = current_setting('app.current_tenant', true)::uuid)
        WITH CHECK (restaurant_id = current_setting('app.current_tenant', true)::uuid)
        """
    )
    op.execute(
        """
        CREATE POLICY p_phone_verifications_system ON phone_verifications
        FOR ALL TO zenoeats_system
        USING (true) WITH CHECK (true)
        """
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON phone_verifications TO zenoeats_app"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON phone_verifications TO zenoeats_system"
    )


def downgrade() -> None:
    op.drop_index(
        "ix_phone_verifications_restaurant", table_name="phone_verifications"
    )
    op.drop_index("uq_phone_verification_user", table_name="phone_verifications")
    op.execute(
        "DROP POLICY IF EXISTS p_phone_verifications_system ON phone_verifications"
    )
    op.execute(
        "DROP POLICY IF EXISTS p_phone_verifications_tenant ON phone_verifications"
    )
    op.drop_table("phone_verifications")
    op.drop_column("users", "phone_verified_at")

    # The welcome codes go with the feature: without the offer there is
    # nothing they are a personal copy of.
    op.execute("DELETE FROM coupons WHERE customer_user_id IS NOT NULL")
    op.drop_constraint("ck_coupon_group_membership", "coupons", type_="check")
    op.create_check_constraint(
        "ck_coupon_group_membership",
        "coupons",
        "(group_id IS NULL) = (group_number IS NULL)",
    )
    op.drop_index("uq_coupon_group_customer", table_name="coupons")
    op.drop_index("ix_coupons_customer", table_name="coupons")
    op.drop_constraint("fk_coupons_customer", "coupons", type_="foreignkey")
    op.drop_column("coupons", "customer_user_id")

    op.execute(
        "DROP POLICY IF EXISTS p_coupon_group_item_types_system "
        "ON coupon_group_item_types"
    )
    op.execute(
        "DROP POLICY IF EXISTS p_coupon_group_item_types_tenant "
        "ON coupon_group_item_types"
    )
    op.drop_table("coupon_group_item_types")

    op.execute("DELETE FROM coupon_groups WHERE kind = 'SIGNUP'")
    op.drop_constraint("ck_coupon_group_valid_days", "coupon_groups", type_="check")
    op.drop_column("coupon_groups", "valid_days")
    op.drop_index("uq_coupon_group_signup", table_name="coupon_groups")
    op.drop_constraint("ck_coupon_group_uses", "coupon_groups", type_="check")
    op.alter_column("coupon_groups", "uses", nullable=False)
    op.create_check_constraint(
        "ck_coupon_group_uses", "coupon_groups", "uses > 0 AND uses <= 500"
    )
    op.drop_constraint("ck_coupon_group_kind_value", "coupon_groups", type_="check")
    op.drop_column("coupon_groups", "kind")
