"""A flat-rate coupon may be worth nothing, or the whole order.

Two ends of the same range, and only one of them needed changing.

THE WHOLE ORDER ALREADY WORKED. services/coupons.py ends discount_for with
min(amount, base_minor), so a flat $500 off a $20 order has always been $20
off rather than a refund of the difference -- the order simply becomes free.
No upper bound is added here because none was ever in the way.

ZERO DID NOT. "discount_value > 0" refused it in the database, the schema
refused it in the request, and the checkout refused it on the cart. A flat
rate of nothing is a real thing to want -- a code set up before its value is
decided, or one that exists to be counted rather than to discount -- and
none of those three refusals were about the restaurant's intention.

PERCENT KEEPS ITS FLOOR. Nothing is expressible as 0% that is not better
expressed as a flat zero, and a 0% coupon is the kind of row that gets
created by a form bug rather than by a decision. So the constraint narrows
to the kind it is about rather than being dropped.

Revision ID: 0058
Revises: 0057
"""

from alembic import op

revision = "0058"
down_revision = "0057"
branch_labels = None
depends_on = None

# A flat amount may be zero; a percentage may not. Written once and applied
# to both tables, because a group mints coupons and the two must agree about
# what a discount is allowed to be.
_RULE = """
    (discount_kind = 'AMOUNT' AND discount_value >= 0)
    OR (discount_kind = 'PERCENT' AND discount_value > 0)
"""


def upgrade() -> None:
    op.drop_constraint("ck_coupon_discount_positive", "coupons", type_="check")
    op.create_check_constraint("ck_coupon_discount_positive", "coupons", _RULE)

    op.drop_constraint("ck_coupon_group_positive", "coupon_groups", type_="check")
    op.create_check_constraint("ck_coupon_group_positive", "coupon_groups", _RULE)


def downgrade() -> None:
    # Any zero-value coupon becomes one cent rather than blocking the
    # rollback: the alternative is a migration that cannot run on a database
    # where somebody used the feature, which is the worst kind.
    op.execute("UPDATE coupons SET discount_value = 1 WHERE discount_value = 0")
    op.execute("UPDATE coupon_groups SET discount_value = 1 WHERE discount_value = 0")

    op.drop_constraint("ck_coupon_discount_positive", "coupons", type_="check")
    op.create_check_constraint(
        "ck_coupon_discount_positive", "coupons", "discount_value > 0"
    )
    op.drop_constraint("ck_coupon_group_positive", "coupon_groups", type_="check")
    op.create_check_constraint(
        "ck_coupon_group_positive", "coupon_groups", "discount_value > 0"
    )
