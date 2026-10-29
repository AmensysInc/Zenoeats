"""Proving a mobile number belongs to whoever typed it.

A code goes to the handset and has to come back. That is the whole idea,
and the reason the welcome offer hangs off it: an offer given for typing a
number is an offer given for typing anything, and the first person to
notice would type ten.

WHAT MAKES IT WORTH ANYTHING, in the order it matters:

  The code is hashed, never stored in the clear. For the ten minutes it
  lives it is a password for an account, and a database read -- a backup, a
  support query, a leak -- must not hand that over.

  Wrong guesses are counted and the row stops answering. Six digits is a
  million possibilities, which a script exhausts in an afternoon if nothing
  is counting.

  Asking for a new code replaces the old one. Two live codes would double
  the guesses per minute for free.

  A code is compared in constant time. The comparison is against a hash, so
  the timing leaks little, but "little" is not a thing to be relaxed about
  in an authentication path.

WHAT IT CANNOT DO HERE is send the message: see services/sms.py. Every
failure to send is surfaced rather than swallowed, because a verification
code that never arrives looks to the customer exactly like a broken site.
"""

import hashlib
import hmac
import logging
import secrets
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.core import errors
from app.db.base import utcnow
from app.models import PhoneVerification, User
from app.services import sms

log = logging.getLogger(__name__)

# Six digits, which is what everybody's muscle memory expects and what an
# SMS autofill on both phone platforms recognises.
CODE_DIGITS = 6


def _hash(code: str) -> str:
    """The stored form of a code.

    Keyed with the session secret rather than a bare digest, for the same
    reason ratelimit keys its buckets: six digits is a million
    possibilities, and a plain sha256 of one is reversible by anybody who
    reads the column and can run a loop. A keyed hash is not, without the
    key -- which is not in the database.

    Not a password KDF, deliberately. This is checked on a request path, it
    lives ten minutes, and the attempt counter beside it is what actually
    bounds guessing; a work factor here would buy little and cost every
    verification.
    """
    key = (settings.SESSION_SECRET or "zenoeats-phone-verification").encode()
    return hmac.new(key, code.encode(), hashlib.sha256).hexdigest()


def normalise(phone: str) -> str:
    """A number as it is stored and compared.

    Digits and a leading +, nothing else: a customer types (580) 226-7925 and
    +1 580 226 7925 and means the same handset both times. Deliberately not a
    full parse -- this is not the place to decide what a valid Nigerian
    number looks like -- it only has to be stable.
    """
    kept = [character for character in phone if character.isdigit()]
    plus = phone.strip().startswith("+")
    return ("+" if plus else "") + "".join(kept)


def looks_like_a_number(phone: str) -> bool:
    """Enough digits to be a phone number somewhere.

    A bound rather than a judgement: E.164 allows fifteen digits and the
    shortest national numbers run to seven, so anything outside that is a
    typo rather than a country this code has not heard of.
    """
    digits = sum(1 for character in phone if character.isdigit())
    return 7 <= digits <= 15


@dataclass(frozen=True)
class Challenge:
    """A code that was just sent, and when it stops working."""

    phone: str
    expires_at: object


def start(db: Session, restaurant_id, user: User, phone: str) -> Challenge:
    """Send a fresh code to `phone`, replacing any challenge in flight.

    The number is not written to the account here. It lives on the challenge
    until a code sent to it comes back, so an unproved number never sits
    where the receipt and the order updates are addressed from.
    """
    number = normalise(phone)
    if not looks_like_a_number(number):
        raise errors.validation_error("Enter a mobile number we can text.")

    if not sms.configured():
        # Said before anything is written. Starting a flow that cannot finish
        # and failing at the last step wastes the customer's time and leaves
        # a dead challenge in the table.
        raise errors.ApiError(
            503, "SMS_UNAVAILABLE",
            "We can't send verification codes just now. This one is ours to "
            "fix rather than yours.",
        )

    code = f"{secrets.randbelow(10 ** CODE_DIGITS):0{CODE_DIGITS}d}"
    expires_at = utcnow() + timedelta(seconds=settings.PHONE_CODE_TTL_SECONDS)

    # One live challenge per customer: a new code replaces the old rather
    # than joining it.
    db.execute(
        PhoneVerification.__table__.delete().where(
            PhoneVerification.user_id == user.id
        )
    )
    db.add(
        PhoneVerification(
            restaurant_id=restaurant_id,
            user_id=user.id,
            phone=number,
            code_hash=_hash(code),
            expires_at=expires_at,
        )
    )
    db.flush()

    # Raises if the vendor refuses, which rolls the challenge back with it:
    # a code nobody received must not be a code the customer is asked for.
    sms.send(number, f"{code} is your ZenoEats verification code.")
    log.info("phone verification sent for user %s", user.id)
    return Challenge(phone=number, expires_at=expires_at)


def check(db: Session, user: User, code: str) -> str:
    """Prove the number, or raise saying why not. Returns the proved number.

    On success the number moves onto the account and the challenge is spent:
    a code that worked once must not work twice.
    """
    challenge = db.execute(
        select(PhoneVerification)
        .where(PhoneVerification.user_id == user.id)
        # Taken for the rest of the transaction, so two attempts arriving at
        # once cannot both read "four attempts used" and both be allowed.
        .with_for_update()
    ).scalar_one_or_none()

    if challenge is None:
        raise errors.ApiError(
            422, "NO_CODE_PENDING", "Ask for a code first, then enter it here."
        )

    if utcnow() >= challenge.expires_at:
        db.delete(challenge)
        raise errors.ApiError(
            422, "CODE_EXPIRED", "That code has expired. Ask for a new one."
        )

    if challenge.attempts >= settings.PHONE_CODE_MAX_ATTEMPTS:
        db.delete(challenge)
        raise errors.ApiError(
            429, "TOO_MANY_ATTEMPTS",
            "Too many wrong codes. Ask for a new one.",
        )

    typed = "".join(character for character in code if character.isdigit())
    if not hmac.compare_digest(_hash(typed), challenge.code_hash):
        challenge.attempts += 1
        db.flush()
        raise errors.ApiError(
            422, "WRONG_CODE", "That code is not right. Check it and try again."
        )

    number = challenge.phone
    user.phone = number
    user.phone_verified_at = utcnow()
    db.delete(challenge)
    db.flush()
    log.info("phone verified for user %s", user.id)
    return number
