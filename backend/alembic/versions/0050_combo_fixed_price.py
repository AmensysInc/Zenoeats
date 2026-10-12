"""A combo sold at a set price, with an upcharge for the dearer choices.

Until now a combo was always its parts minus something: a percentage, or an
amount. That is not how a counter prices a meal. "Double Cheese Combo $8.99,
cheese fries a dollar more" names the price the customer pays and what a
swap costs on top of it, and neither number can be expressed as a discount
off parts whose prices differ: the same dollar off makes a cheese-fries meal
$1.80 dearer, not $1.

SO A COMBO MAY BE FIXED_PRICE, and then discount_value is the price itself,
in minor units. What the customer pays is that price, plus the upcharge of
each choice made, plus whatever the chosen options add (a large drink costs
its size difference inside a meal as it does outside one). It is never more
than the same food bought separately: a set price above the parts is a
mistake to absorb, not a charge to pass on, so the pricing caps it.

AND EACH CHOICE MAY CARRY AN UPCHARGE, on combo_slot_items, because it is a
property of that dish in that slot of that combo -- cheese fries are a dollar
more in the burger meal and may be nothing extra in another. Zero by default,
which is every existing choice; ignored by the other kinds, where the price
difference between parts already does the job.

Revision ID: 0050
Revises: 0049
"""

import sqlalchemy as sa
from alembic import op

revision = "0050"
down_revision = "0049"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_combo_discount_kind", "combos", type_="check")
    op.create_check_constraint(
        "ck_combo_discount_kind", "combos",
        "discount_kind IN ('NONE','PERCENT','AMOUNT','FIXED_PRICE')",
    )

    op.add_column(
        "combo_slot_items",
        sa.Column("upcharge_minor", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_check_constraint(
        "ck_combo_slot_item_upcharge_non_negative", "combo_slot_items", "upcharge_minor >= 0"
    )


def downgrade() -> None:
    op.drop_constraint("ck_combo_slot_item_upcharge_non_negative", "combo_slot_items", type_="check")
    op.drop_column("combo_slot_items", "upcharge_minor")

    # A fixed-price combo has no meaning under the old kinds. Selling it with
    # no discount is the honest fallback: never cheaper than intended. FORCE
    # comes off around it because the policies name the app and system roles,
    # so under FORCE this role's UPDATE matches nothing and changes no row
    # (0004 explains it).
    op.execute("ALTER TABLE combos NO FORCE ROW LEVEL SECURITY")
    op.execute(
        "UPDATE combos SET discount_kind = 'NONE', discount_value = 0 "
        "WHERE discount_kind = 'FIXED_PRICE'"
    )
    op.execute("ALTER TABLE combos FORCE ROW LEVEL SECURITY")
    op.drop_constraint("ck_combo_discount_kind", "combos", type_="check")
    op.create_check_constraint(
        "ck_combo_discount_kind", "combos", "discount_kind IN ('NONE','PERCENT','AMOUNT')"
    )
