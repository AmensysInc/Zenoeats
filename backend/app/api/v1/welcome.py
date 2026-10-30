"""The customer side of the welcome offer: prove a number, get a code.

Three endpoints and one idea. A restaurant's welcome offer gives one code
to each new customer; what earns it is proving a mobile number, because an
offer given for typing a number is an offer given for typing anything.

WHY THE CODE IS ISSUED ON VERIFICATION rather than at sign-up: sign-up asks
for an email and a password and nothing else, and adding a required phone
field to it would cost every customer who does not want to give one. The
offer is the reason to give one, and the account screen says so.

GUESTS ARE OUT. A guest session is an httpOnly cookie with no account
behind it and no way back to the same person tomorrow, so there is nobody
to issue a personal code to.
"""

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import TenantDb, current_restaurant, get_current_user
from app.core import errors
from app.core.ratelimit import per_user
from app.models import Restaurant, User, UserKind
from app.services import phone_verification, signup_offers, sms

log = logging.getLogger(__name__)
router = APIRouter(tags=["welcome"])


class StartIn(BaseModel):
    phone: str = Field(min_length=5, max_length=32)


class VerifyIn(BaseModel):
    code: str = Field(min_length=4, max_length=12)


class OfferOut(BaseModel):
    """What this customer is being offered, and what stands between them and it.

    Written as "what to do next" rather than as a set of flags, because the
    screen has exactly one sentence to show and working out which from four
    booleans is how two screens end up disagreeing.
    """

    # None where the restaurant runs no welcome offer, or this customer has
    # already had theirs.
    headline: str | None
    # Whether to show the "add your number" prompt at all.
    can_earn: bool
    # False when this deployment cannot send a text, which the screen says
    # plainly rather than offering a button that cannot work.
    sms_available: bool
    phone_verified: bool


class CodeOut(BaseModel):
    """A personal code the customer holds."""

    code: str
    description: str | None
    discount_kind: str
    discount_value: int
    min_subtotal_minor: int
    ends_at: str | None
    spent: bool


def _customer(user: User) -> User:
    """An account, not a guest.

    A guest is a cookie with nobody behind it: there is no one to issue a
    personal code to and no way to recognise them tomorrow.
    """
    if user.kind != UserKind.CUSTOMER.value:
        raise errors.ApiError(
            403, "ACCOUNT_REQUIRED",
            "Create an account to claim this offer.",
        )
    return user


@router.get("/welcome/offer", response_model=OfferOut)
def read_offer(
    user: User = Depends(get_current_user),
    restaurant: Restaurant = Depends(current_restaurant),
    db: Session = TenantDb,
):
    """What this customer can still earn here."""
    offer = signup_offers.offer(db)
    verified = user.phone_verified_at is not None
    if offer is None or user.kind != UserKind.CUSTOMER.value:
        return OfferOut(
            headline=None, can_earn=False,
            sms_available=sms.configured(), phone_verified=verified,
        )

    had = signup_offers.already_issued(db, offer.id, user.id) is not None
    return OfferOut(
        headline=offer.description or "A welcome offer on your first order",
        can_earn=not had,
        sms_available=sms.configured(),
        phone_verified=verified,
    )


@router.post(
    "/welcome/phone",
    status_code=204,
    # Per account rather than per address: the cost here is a text message
    # somebody pays for, and one account asking for ten is the shape of
    # both an accident and an attack.
    dependencies=[Depends(per_user("welcome_phone_start", limit=5, window_seconds=900))],
)
def start_verification(
    body: StartIn,
    user: User = Depends(get_current_user),
    restaurant: Restaurant = Depends(current_restaurant),
    db: Session = TenantDb,
):
    """Text a code to the number given."""
    customer = _customer(user)
    try:
        phone_verification.start(db, restaurant.id, customer, body.phone)
    except sms.SmsUnavailable as unavailable:
        # The reason is for whoever runs the deployment, not the customer:
        # an A2P registration they have never heard of is not theirs to fix.
        log.error("welcome verification could not send: %s", unavailable)
        raise errors.ApiError(
            503, "SMS_UNAVAILABLE",
            "We can't send verification codes just now. This one is ours to "
            "fix rather than yours.",
        ) from None


@router.post(
    "/welcome/verify",
    response_model=CodeOut | None,
    dependencies=[Depends(per_user("welcome_phone_verify", limit=20, window_seconds=900))],
)
def verify(
    body: VerifyIn,
    user: User = Depends(get_current_user),
    restaurant: Restaurant = Depends(current_restaurant),
    db: Session = TenantDb,
):
    """Prove the number, and be given the code if one is going.

    The verification stands whether or not there is an offer to earn: a
    proved number is worth having on the account regardless, and a customer
    who verified during a week when no offer was running has still verified.
    """
    customer = _customer(user)
    phone_verification.check(db, customer, body.code)

    offer = signup_offers.offer(db)
    if offer is None:
        return None
    coupon = signup_offers.issue(db, offer, customer)
    return CodeOut(
        code=coupon.code,
        description=coupon.description,
        discount_kind=coupon.discount_kind,
        discount_value=coupon.discount_value,
        min_subtotal_minor=coupon.min_subtotal_minor,
        ends_at=coupon.ends_at.isoformat() if coupon.ends_at else None,
        spent=False,
    )


@router.get("/welcome/codes", response_model=list[CodeOut])
def my_codes(
    user: User = Depends(get_current_user),
    restaurant: Restaurant = Depends(current_restaurant),
    db: Session = TenantDb,
):
    """Every personal code this customer holds at this restaurant.

    Spent codes are listed rather than hidden, marked as spent: a customer
    looking for the code they were given wants to be told they used it, not
    left wondering where it went.
    """
    from app.services import coupons as coupon_service

    held = signup_offers.held_by(db, user.id)
    return [
        CodeOut(
            code=coupon.code,
            description=coupon.description,
            discount_kind=coupon.discount_kind,
            discount_value=coupon.discount_value,
            min_subtotal_minor=coupon.min_subtotal_minor,
            ends_at=coupon.ends_at.isoformat() if coupon.ends_at else None,
            spent=coupon_service.redemptions(db, coupon.id) > 0,
        )
        for coupon in held
    ]
