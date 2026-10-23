"""A course a customer may leave out, and what leaving it out takes off.

Until now every slot of a combo was required: pick a food, pick a drink, or
there is no deal. That is true of a meal deal and false of a customer, who
regularly wants the burger and the fries and nothing to drink. The old answer
was to build a second combo without a drink slot, which works and which the
restaurant then has to keep in step with the first -- same dishes, same
photograph, same price changes, twice.

SO A SLOT MAY BE OPTIONAL, and then the storefront offers "no drink" beside
the drinks and the pricing accepts a combo that names no choice for it. A
required slot behaves exactly as before: a combo missing one of those is
still an incomplete meal deal sold at meal-deal price, and still refused.

AND AN OPTIONAL SLOT MAY CARRY A CREDIT, because what skipping a course is
worth depends on how the combo is priced and only one of the kinds can work
it out alone:

  PERCENT, AMOUNT   The drink's own price simply is not in the subtotal, so
                    the bill falls by it and the credit is ignored. Nothing
                    to state: the parts already differ in price.

  FIXED_PRICE       The set price is a number, not a sum of parts, so there
                    is nothing to subtract and no way to infer it. The
                    restaurant states what the course is worth -- "no drink,
                    a dollar off" -- and that comes off the set price the
                    same way an upcharge goes on it.

Zero by default, which is every existing slot, and constrained to zero on a
required slot: a credit for not taking a course you must take is a number
nobody can ever earn, and leaving it settable would invite a restaurant to
set it and wonder why no bill moved.

Revision ID: 0051
Revises: 0050
"""

import sqlalchemy as sa
from alembic import op

revision = "0051"
down_revision = "0050"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "combo_slots",
        sa.Column("is_optional", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "combo_slots",
        sa.Column("skip_credit_minor", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_check_constraint(
        "ck_combo_slot_skip_credit_non_negative", "combo_slots", "skip_credit_minor >= 0"
    )
    # A credit only means something where the course can actually be left
    # out. Enforced rather than documented, so the two fields cannot drift
    # into a combination the pricing would have to guess about.
    op.create_check_constraint(
        "ck_combo_slot_credit_needs_optional",
        "combo_slots",
        "is_optional OR skip_credit_minor = 0",
    )


def downgrade() -> None:
    # Every slot becomes required again, which is what the older pricing
    # means by a slot. Nothing has to be rewritten for that: orders keep
    # their own lines and their own figures, and a combo that was sold
    # without a drink stays sold as it was. Only what may be built from now
    # on changes, and it changes back to the stricter rule.
    op.drop_constraint("ck_combo_slot_credit_needs_optional", "combo_slots", type_="check")
    op.drop_constraint("ck_combo_slot_skip_credit_non_negative", "combo_slots", type_="check")
    op.drop_column("combo_slots", "skip_credit_minor")
    op.drop_column("combo_slots", "is_optional")
