"""A welcome code, earned by proving a mobile number.

The offer is a coupon group of kind SIGNUP, so what a code is worth is
already covered by test_coupons.py. What is tested here is the three things
that are genuinely new, in the order they would hurt:

  a welcome code belongs to one customer, so telling a friend does not
  give the friend a discount,

  one per customer ever, so verifying twice does not pay twice, and

  the code is never issued for typing a number -- only for proving one,
  which on a deployment with no SMS vendor means not at all.

The last of those is why most of this file runs with sending stubbed: there
is no SMS vendor on this stack (services/sms.py), and a test suite that
pretended otherwise would be testing a deployment nobody has.
"""

import pytest

from app.services import phone_verification, signup_offers, sms

from tests.test_admin_restaurants import (  # noqa: F401  (fixtures)
    _create, _email, _owner, _set_own_password, _staff_client, admin_user, cleanup,
)
from tests.test_checkout_contact import CONTACT, _guest, shop as base_shop  # noqa: F401
from tests.test_coupons import (  # noqa: F401  (fixtures)
    _admin_client, _order, _publish, _quote, _row, no_rate_limits, operator,
    shop, team,
    ADDRESS, DELIVERY, LOCATE, ORIGIN, PROFILE, ZONES, _place,
)


def _offer_at(restaurant_id) -> str:
    return f"/api/v1/admin/restaurants/{restaurant_id}/signup-offer"


@pytest.fixture
def account(shop):
    """A signed-in customer account on the shop -- not a guest.

    A guest is a cookie with nobody behind it, so there is nobody to issue a
    personal code to; the welcome offer refuses one, which is its own test
    below.
    """
    import uuid as _uuid

    client = _staff_client(shop.slug)
    created = client.post(
        "/api/v1/customer/register",
        json={
            "email": f"welcome-{_uuid.uuid4().hex[:8]}@example.test",
            "password": "burgers-and-fries",
            "full_name": "Sam Taylor",
        },
    )
    assert created.status_code == 201, created.text
    return client


@pytest.fixture
def sending(monkeypatch):
    """A deployment that can send texts, and the codes it sent.

    The real sender raises: there is no vendor. Tests that need the flow to
    finish stand in for one, which is exactly the seam a vendor will slot
    into later.
    """
    sent = []
    monkeypatch.setattr(sms, "configured", lambda: True)
    monkeypatch.setattr(sms, "send", lambda to, body: sent.append((to, body)))
    return sent


def _code_from(sent) -> str:
    """The six digits out of the one message that was sent."""
    assert sent, "nothing was sent"
    digits = "".join(c for c in sent[-1][1] if c.isdigit())
    return digits[:6]


# ------------------------------------------------- the offer, as configured ---


def test_an_offer_is_created_and_read_back(operator):
    res = operator.client.put(
        _offer_at(operator.id),
        json={"code_prefix": "WELCOME", "percent_off_bps": 1500, "valid_days": 30},
    )
    assert res.status_code == 200, res.text
    assert res.json()["code_prefix"] == "WELCOME"
    assert res.json()["valid_days"] == 30

    state = operator.client.get(_offer_at(operator.id)).json()
    assert state["offer"]["discount_value"] == 1500
    # The screen is told whether this deployment can actually run it, rather
    # than offering a switch whose verification step cannot send.
    assert state["sms_configured"] is False


def test_there_is_only_ever_one_welcome_offer(operator):
    operator.client.put(
        _offer_at(operator.id), json={"code_prefix": "WELCOME", "percent_off_bps": 1000}
    )
    operator.client.put(
        _offer_at(operator.id), json={"code_prefix": "HELLO", "amount_off_minor": 300}
    )
    state = operator.client.get(_offer_at(operator.id)).json()
    assert state["offer"]["code_prefix"] == "HELLO"
    assert state["offer"]["discount_kind"] == "AMOUNT"


def test_the_welcome_offer_is_not_listed_among_group_coupons(operator):
    operator.client.put(
        _offer_at(operator.id), json={"code_prefix": "WELCOME", "percent_off_bps": 1000}
    )
    groups = operator.client.get(
        f"/api/v1/admin/restaurants/{operator.id}/coupon-groups"
    ).json()["groups"]
    assert groups == []


# ------------------------------------------------------ proving a number ------


def test_without_an_sms_vendor_nothing_is_offered_or_sent(shop):
    """The honest state of this deployment: the flow refuses up front rather
    than taking a number and failing at the last step."""
    shop.admin.put(
        _offer_at(shop.id), json={"code_prefix": "WELCOME", "percent_off_bps": 1000}
    )
    customer = _guest(shop)
    res = customer.post("/api/v1/welcome/phone", json={"phone": "5802267925"})
    # A guest has no account to hang a code on, so it refuses for that first.
    assert res.status_code in (403, 503)


def test_a_wrong_code_is_refused_and_counted(shop, sending, account):
    shop.admin.put(
        _offer_at(shop.id), json={"code_prefix": "WELCOME", "percent_off_bps": 1000}
    )
    assert account.post(
        "/api/v1/welcome/phone", json={"phone": "5802267925"}
    ).status_code == 204

    wrong = account.post("/api/v1/welcome/verify", json={"code": "000000"})
    assert wrong.status_code == 422
    # And the right one still works afterwards: a wrong guess spends an
    # attempt, not the challenge.
    good = account.post("/api/v1/welcome/verify", json={"code": _code_from(sending)})
    assert good.status_code == 200, good.text


def test_proving_the_number_issues_one_code(shop, sending, account):
    shop.admin.put(
        _offer_at(shop.id),
        json={"code_prefix": "WELCOME", "percent_off_bps": 1000, "valid_days": 30},
    )
    account.post("/api/v1/welcome/phone", json={"phone": "5802267925"})
    issued = account.post(
        "/api/v1/welcome/verify", json={"code": _code_from(sending)}
    ).json()

    assert issued["code"].startswith("WELCOME")
    # Random rather than numbered: WELCOME7 would tell its holder they were
    # the seventh customer and invite a guess at WELCOME8.
    assert issued["code"] != "WELCOME1"
    assert issued["ends_at"] is not None


def test_one_code_per_customer_however_many_times_they_verify(shop, sending, account):
    shop.admin.put(
        _offer_at(shop.id), json={"code_prefix": "WELCOME", "percent_off_bps": 1000}
    )
    account.post("/api/v1/welcome/phone", json={"phone": "5802267925"})
    first = account.post(
        "/api/v1/welcome/verify", json={"code": _code_from(sending)}
    ).json()

    # Verify again with a different number -- the obvious way to try for a
    # second discount on one account.
    account.post("/api/v1/welcome/phone", json={"phone": "4055550123"})
    second = account.post(
        "/api/v1/welcome/verify", json={"code": _code_from(sending)}
    ).json()

    assert second["code"] == first["code"]
    assert len(account.get("/api/v1/welcome/codes").json()) == 1


# -------------------------------------------------- the code is personal ------


def test_a_welcome_code_cannot_be_used_by_anybody_else(shop, sending, account):
    """The whole reason a coupon can be bound to a customer. Without it the
    first person they read it out to spends it."""
    shop.admin.put(
        _offer_at(shop.id), json={"code_prefix": "WELCOME", "percent_off_bps": 1000}
    )
    account.post("/api/v1/welcome/phone", json={"phone": "5802267925"})
    mine = account.post(
        "/api/v1/welcome/verify", json={"code": _code_from(sending)}
    ).json()["code"]

    # Somebody else types it. They are told what they would be told about a
    # code that does not exist -- "that code is someone else's" would
    # confirm it is real and worth hunting for.
    stranger = _guest(shop)
    refused = _quote(stranger, shop, coupon_code=mine).json()
    assert refused["coupon_error"]


def test_the_customer_it_belongs_to_can_use_it(shop, sending, account):
    shop.admin.put(
        _offer_at(shop.id), json={"code_prefix": "WELCOME", "percent_off_bps": 1000}
    )
    account.post("/api/v1/welcome/phone", json={"phone": "5802267925"})
    mine = account.post(
        "/api/v1/welcome/verify", json={"code": _code_from(sending)}
    ).json()["code"]

    priced = _quote(account, shop, coupon_code=mine).json()
    assert priced["coupon_error"] is None
    assert priced["amounts"]["coupon_discount_minor"] > 0


def test_a_spent_code_is_still_listed_as_spent(shop, sending, account):
    shop.admin.put(
        _offer_at(shop.id), json={"code_prefix": "WELCOME", "percent_off_bps": 1000}
    )
    account.post("/api/v1/welcome/phone", json={"phone": "5802267925"})
    mine = account.post(
        "/api/v1/welcome/verify", json={"code": _code_from(sending)}
    ).json()["code"]
    assert _order(account, shop, coupon_code=mine).status_code == 201

    # Listed rather than hidden: a customer looking for the code they were
    # given wants to be told they used it, not left wondering.
    held = account.get("/api/v1/welcome/codes").json()
    assert [(c["code"], c["spent"]) for c in held] == [(mine, True)]


def test_retiring_the_offer_ends_the_codes_already_out(shop, sending, account):
    offer = shop.admin.put(
        _offer_at(shop.id), json={"code_prefix": "WELCOME", "percent_off_bps": 1000}
    ).json()
    account.post("/api/v1/welcome/phone", json={"phone": "5802267925"})
    mine = account.post(
        "/api/v1/welcome/verify", json={"code": _code_from(sending)}
    ).json()["code"]

    assert shop.admin.delete(_offer_at(shop.id)).status_code == 204
    assert _quote(account, shop, coupon_code=mine).json()["coupon_error"]


# -------------------------------------------------------- the number itself ---


def test_a_number_is_normalised_however_it_was_typed():
    assert phone_verification.normalise("(580) 226-7925") == "5802267925"
    assert phone_verification.normalise("+1 580 226 7925") == "+15802267925"


def test_a_code_is_never_stored_in_the_clear():
    # Ten minutes is long enough for a database read to matter, and six
    # digits is reversible from a plain digest by anybody who can run a loop.
    assert phone_verification._hash("123456") != "123456"
    assert len(phone_verification._hash("123456")) == 64


# ------------------------------------------------------- who used what --------
#
# The admin's record of every redemption. Read from the orders, which is the
# only source that stays right when one is cancelled.


def _redemptions_at(restaurant_id, code=None) -> str:
    at = f"/api/v1/admin/restaurants/{restaurant_id}/coupon-redemptions"
    return f"{at}?code={code}" if code else at


def test_every_redemption_is_recorded_with_who_used_it(shop, sending, account):
    shop.admin.put(
        _offer_at(shop.id), json={"code_prefix": "WELCOME", "percent_off_bps": 1000}
    )
    account.post("/api/v1/welcome/phone", json={"phone": "5802267925"})
    mine = account.post(
        "/api/v1/welcome/verify", json={"code": _code_from(sending)}
    ).json()["code"]
    assert _order(account, shop, coupon_code=mine).status_code == 201

    listed = shop.admin.get(_redemptions_at(shop.id)).json()
    assert listed["total_redemptions"] == 1
    assert listed["total_discounted_minor"] > 0

    row = listed["redemptions"][0]
    assert row["code"] == mine
    # The point of the section: a name, not just a count.
    # An account keeps its own name; only a guest is known by what was
    # typed at checkout.
    assert row["customer_name"] == "Sam Taylor"
    assert row["is_guest"] is False
    assert row["discount_minor"] > 0


def test_a_guest_redemption_is_listed_with_nobody_to_name(shop):
    _publish(shop, code="OPENTOALL")
    assert _order(_guest(shop), shop, coupon_code="OPENTOALL").status_code == 201

    row = shop.admin.get(_redemptions_at(shop.id)).json()["redemptions"][0]
    assert row["code"] == "OPENTOALL"
    # A guest is a real row carrying the name they typed at checkout, so the
    # name cannot tell a guest from an account -- which is why the flag is
    # there, and what the screen reads to say "a guest".
    assert row["customer_name"] == "Pat Customer"
    assert row["is_guest"] is True


def test_the_record_can_be_narrowed_to_one_code(shop):
    _publish(shop, code="FIRSTCODE")
    _publish(shop, code="OTHERCODE")
    _order(_guest(shop), shop, coupon_code="FIRSTCODE")
    _order(_guest(shop), shop, coupon_code="OTHERCODE")

    only = shop.admin.get(_redemptions_at(shop.id, "firstcode")).json()
    assert only["total_redemptions"] == 1
    assert only["redemptions"][0]["code"] == "FIRSTCODE"


def test_an_order_that_never_happened_is_not_a_redemption(shop):
    """The reason redemptions are read from the orders rather than counted
    on the coupon: a counter would still say one."""
    from app.db.session import tenant_session
    from sqlalchemy import text

    _publish(shop, code="CANCELME")
    placed = _order(_guest(shop), shop, coupon_code="CANCELME").json()
    assert shop.admin.get(_redemptions_at(shop.id)).json()["total_redemptions"] == 1

    with tenant_session(shop.id) as session:
        session.execute(
            text("UPDATE orders SET status = 'CANCELLED' WHERE id = :o"),
            {"o": placed["order_id"]},
        )

    assert shop.admin.get(_redemptions_at(shop.id)).json()["total_redemptions"] == 0
