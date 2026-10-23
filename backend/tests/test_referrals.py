"""Who may be referred, and when it pays.

Most of this is about the attempts: referring yourself, referring a regular,
being claimed twice, being paid before the friend has actually bought
anything. The happy path is one test; the rest is the reason the feature can
be switched on.
"""

import uuid

import pytest
from sqlalchemy import text

from app.db.session import system_session, tenant_session
from app.models import (
    LoyaltyProgram, LoyaltyProgramItem, Order, OrderStatus, PurchaseType,
    ReferralStatus,
)
from app.services import referrals

REFERRER = uuid.UUID("66666666-6666-6666-6666-666666666666")
FRIEND = uuid.UUID("77777777-7777-7777-7777-777777777777")
# A second invitee, for the test that one customer may bring several.
THIRD = uuid.UUID("78787878-7878-7878-7878-787878787878")


class _User:
    def __init__(self, uid, kind="CUSTOMER"):
        self.id, self.kind = uid, kind


@pytest.fixture
def shop():
    """A restaurant, a referral program, and two customers."""
    with system_session() as s:
        rid = s.execute(
            text("select id from restaurants where slug='spicehouse'")
        ).scalar_one()
        for uid, email in (
            (REFERRER, "referrer"), (FRIEND, "friend"), (THIRD, "third"),
        ):
            s.execute(
                text(
                    "insert into users (id,kind,email,full_name,is_active) values "
                    "(:u,'CUSTOMER',:e,'Referral Tester',true) on conflict (id) do nothing"
                ),
                {"u": str(uid), "e": f"{email}-referral@example.test"},
            )
        s.commit()

    with tenant_session(rid) as db:
        dish = db.execute(
            text("select id from menu_items where deleted_at is null order by name limit 1")
        ).scalar_one()
        db.commit()

    yield {"restaurant_id": rid, "item_id": dish}

    with tenant_session(rid) as db:
        db.execute(text("delete from referrals"))
        db.execute(text("delete from referral_codes"))
        for table in ("loyalty_rewards", "loyalty_transactions", "customer_loyalty"):
            db.execute(
                text(f"delete from {table} where customer_user_id in (:a,:b,:c)"),
                {"a": str(REFERRER), "b": str(FRIEND), "c": str(THIRD)},
            )
        db.execute(
            text("delete from order_items where order_id in "
                 "(select id from orders where customer_user_id in (:a,:b,:c))"),
            {"a": str(REFERRER), "b": str(FRIEND), "c": str(THIRD)},
        )
        db.execute(
            text("delete from orders where customer_user_id in (:a,:b,:c)"),
            {"a": str(REFERRER), "b": str(FRIEND), "c": str(THIRD)},
        )
        db.execute(
            text("delete from loyalty_program_items where program_id in "
                 "(select id from loyalty_programs where name like 'TEST %')")
        )
        db.execute(text("delete from loyalty_programs where name like 'TEST %'"))
        db.commit()


def _program(db, shop, required=1):
    program = LoyaltyProgram(
        restaurant_id=shop["restaurant_id"],
        name="TEST Bring a friend",
        purchase_type=PurchaseType.REFERRAL.value,
        required_count=required,
    )
    db.add(program)
    db.flush()
    db.add(
        LoyaltyProgramItem(
            restaurant_id=shop["restaurant_id"],
            program_id=program.id,
            item_id=shop["item_id"],
        )
    )
    db.flush()
    return program


def _order(db, shop, who, status=OrderStatus.COMPLETED.value):
    oid = uuid.uuid4()
    db.execute(
        text(
            "insert into orders (id,restaurant_id,order_number,customer_user_id,"
            "fulfillment_type,status,payment_method,currency,subtotal_minor,"
            "tax_minor,total_minor) values (:i,:r,:n,:u,'PICKUP',:s,'CARD','USD',"
            "1000,0,1000)"
        ),
        {
            "i": str(oid), "r": str(shop["restaurant_id"]),
            "n": uuid.uuid4().int % 10**9, "u": str(who), "s": status,
        },
    )
    db.flush()
    return db.get(Order, oid)


def _rewards(db, who) -> int:
    return db.execute(
        text("select count(*) from loyalty_rewards where customer_user_id = :u"),
        {"u": str(who)},
    ).scalar_one()


# ----------------------------------------------------------------- codes


def test_a_customer_keeps_one_code(shop):
    """The link they share is the link they share. A second would split
    their own invitations between two codes."""
    with tenant_session(shop["restaurant_id"]) as db:
        _program(db, shop)
        referrals.invite(db, shop["restaurant_id"], _User(REFERRER))
        first = referrals.code_for(db, shop["restaurant_id"], _User(REFERRER))
        referrals.invite(db, shop["restaurant_id"], _User(REFERRER))
        second = referrals.code_for(db, shop["restaurant_id"], _User(REFERRER))
        assert first == second


def test_two_customers_get_different_codes(shop):
    """A claim has to resolve to exactly one person."""
    with tenant_session(shop["restaurant_id"]) as db:
        _program(db, shop)
        mine = referrals.code_for(db, shop["restaurant_id"], _User(REFERRER))
        theirs = referrals.code_for(db, shop["restaurant_id"], _User(FRIEND))
        assert mine != theirs


def test_a_guest_cannot_invite(shop):
    """There would be nobody to thank: a guest row lives for one checkout."""
    with tenant_session(shop["restaurant_id"]) as db:
        _program(db, shop)
        with pytest.raises(Exception):
            referrals.invite(db, shop["restaurant_id"], _User(REFERRER, "GUEST"))


# --------------------------------------------------------------- claiming


def test_a_friend_can_claim_an_invitation(shop):
    with tenant_session(shop["restaurant_id"]) as db:
        _program(db, shop)
        sent = referrals.invite(db, shop["restaurant_id"], _User(REFERRER))
        code = referrals.code_for(db, shop["restaurant_id"], _User(REFERRER))
        assert referrals.claim(db, shop["restaurant_id"], code, _User(FRIEND)) is True
        db.refresh(sent)
        assert sent.status == ReferralStatus.JOINED.value


def test_nobody_refers_themselves(shop):
    """The obvious first attempt."""
    with tenant_session(shop["restaurant_id"]) as db:
        _program(db, shop)
        sent = referrals.invite(db, shop["restaurant_id"], _User(REFERRER))
        code = referrals.code_for(db, shop["restaurant_id"], _User(REFERRER))
        assert referrals.claim(db, shop["restaurant_id"], code, _User(REFERRER)) is False


def test_somebody_who_has_ordered_here_cannot_be_referred(shop):
    """Otherwise a regular brings a second account of their own and the
    restaurant buys a burger for a customer it already had."""
    with tenant_session(shop["restaurant_id"]) as db:
        _program(db, shop)
        _order(db, shop, FRIEND)
        sent = referrals.invite(db, shop["restaurant_id"], _User(REFERRER))
        code = referrals.code_for(db, shop["restaurant_id"], _User(REFERRER))
        assert referrals.claim(db, shop["restaurant_id"], code, _User(FRIEND)) is False


def test_an_unknown_code_claims_nothing(shop):
    with tenant_session(shop["restaurant_id"]) as db:
        _program(db, shop)
        assert referrals.claim(db, shop["restaurant_id"], "NOPE999", _User(FRIEND)) is False


def test_a_friend_is_claimed_once(shop):
    """Two referrers cannot both be paid for the same person."""
    with tenant_session(shop["restaurant_id"]) as db:
        _program(db, shop)
        sent = referrals.invite(db, shop["restaurant_id"], _User(REFERRER))
        code = referrals.code_for(db, shop["restaurant_id"], _User(REFERRER))
        assert referrals.claim(db, shop["restaurant_id"], code, _User(FRIEND)) is True
        assert referrals.claim(db, shop["restaurant_id"], code, _User(FRIEND)) is False


# -------------------------------------------------------------- paying out


def test_a_claim_alone_pays_nothing(shop):
    """Clicking a link is not a purchase. A program that paid for clicks
    would pay for a page refresh."""
    with tenant_session(shop["restaurant_id"]) as db:
        _program(db, shop)
        sent = referrals.invite(db, shop["restaurant_id"], _User(REFERRER))
        code = referrals.code_for(db, shop["restaurant_id"], _User(REFERRER))
        referrals.claim(db, shop["restaurant_id"], code, _User(FRIEND))
        assert _rewards(db, REFERRER) == 0


def test_the_friends_first_completed_order_pays_the_referrer(shop):
    with tenant_session(shop["restaurant_id"]) as db:
        _program(db, shop)
        sent = referrals.invite(db, shop["restaurant_id"], _User(REFERRER))
        code = referrals.code_for(db, shop["restaurant_id"], _User(REFERRER))
        referrals.claim(db, shop["restaurant_id"], code, _User(FRIEND))
        referrals.credit_referrer(db, _order(db, shop, FRIEND))
        assert _rewards(db, REFERRER) == 1
        db.refresh(sent)
        assert sent.status == ReferralStatus.QUALIFIED.value


def test_an_unfinished_order_pays_nothing(shop):
    """Paid is not completed, here as everywhere else."""
    with tenant_session(shop["restaurant_id"]) as db:
        _program(db, shop)
        sent = referrals.invite(db, shop["restaurant_id"], _User(REFERRER))
        code = referrals.code_for(db, shop["restaurant_id"], _User(REFERRER))
        referrals.claim(db, shop["restaurant_id"], code, _User(FRIEND))
        referrals.credit_referrer(
            db, _order(db, shop, FRIEND, status=OrderStatus.PREPARING.value)
        )
        assert _rewards(db, REFERRER) == 0


def test_a_referral_pays_once_however_often_the_friend_returns(shop):
    """A referral is worth one reward, not one per visit for ever."""
    with tenant_session(shop["restaurant_id"]) as db:
        _program(db, shop)
        sent = referrals.invite(db, shop["restaurant_id"], _User(REFERRER))
        code = referrals.code_for(db, shop["restaurant_id"], _User(REFERRER))
        referrals.claim(db, shop["restaurant_id"], code, _User(FRIEND))
        referrals.credit_referrer(db, _order(db, shop, FRIEND))
        referrals.credit_referrer(db, _order(db, shop, FRIEND))
        referrals.credit_referrer(db, _order(db, shop, FRIEND))
        assert _rewards(db, REFERRER) == 1


def test_crediting_the_same_order_twice_pays_once(shop):
    """The ledger's unique index, inherited by going through the engine."""
    with tenant_session(shop["restaurant_id"]) as db:
        _program(db, shop)
        sent = referrals.invite(db, shop["restaurant_id"], _User(REFERRER))
        code = referrals.code_for(db, shop["restaurant_id"], _User(REFERRER))
        referrals.claim(db, shop["restaurant_id"], code, _User(FRIEND))
        order = _order(db, shop, FRIEND)
        referrals.credit_referrer(db, order)
        referrals.credit_referrer(db, order)
        assert _rewards(db, REFERRER) == 1


def test_no_program_means_nothing_is_paid(shop):
    """A restaurant that does not run referrals owes nothing for one."""
    with tenant_session(shop["restaurant_id"]) as db:
        # No program at all this time.
        assert referrals.program(db) is None
        referrals.credit_referrer(db, _order(db, shop, FRIEND))
        assert _rewards(db, REFERRER) == 0


def test_several_friends_are_several_rewards(shop):
    """Each invitation is its own row, so bringing two people pays twice."""
    with tenant_session(shop["restaurant_id"]) as db:
        _program(db, shop)
        referrals.invite(db, shop["restaurant_id"], _User(REFERRER))
        code = referrals.code_for(db, shop["restaurant_id"], _User(REFERRER))
        referrals.claim(db, shop["restaurant_id"], code, _User(FRIEND))
        referrals.claim(db, shop["restaurant_id"], code, _User(THIRD))
        referrals.credit_referrer(db, _order(db, shop, FRIEND))
        referrals.credit_referrer(db, _order(db, shop, THIRD))
        assert _rewards(db, REFERRER) == 2
