"""The rules a restaurant profile edit must respect, wherever it comes from.

Two portals write the restaurants row: the platform's Super Admin screen and
the restaurant's own Settings screen. They must apply the same rules, because
the dangerous edits are the quiet ones -- a restaurant that clears its street
is a restaurant whose tax silently stops calculating -- and a rule enforced in
only one of two callers is not a rule.

So the normalising and the Stripe Tax gate live here, and both callers go
through them. What differs between the two is which fields they accept at all,
and that stays with each caller's schema.
"""

import json
import re

from sqlalchemy import text

from app.core import errors
from app.services import stripe_tax

# Changing any of these can change what a customer is charged in tax, so they
# are checked together: the gate asks "would the result still calculate?",
# not "did you touch tax or did you touch the address?".
TAX_AND_ADDRESS_FIELDS = (
    "tax_mode", "tax_code", "address_line1", "address_line2", "address_city",
    "address_state", "address_postal_code", "address_country",
)

# The fonts a restaurant's name can be set in on customer pages, when it has
# not uploaded its own lettering. The database holds the same list as a check
# constraint (migration 0035); the web app maps each key to a Google font.
BRAND_NAME_FONTS = ("default", "lora", "playfair", "fraunces", "merriweather", "dm_sans")


def tax_fields(source) -> dict:
    """The tax and address attributes of a Restaurant or a row mapping."""
    get = source.get if isinstance(source, dict) or hasattr(source, "keys") else (
        lambda name: getattr(source, name)
    )
    return {name: get(name) for name in TAX_AND_ADDRESS_FIELDS}


def stripe_tax_blockers(restaurant_view, stripe_account_id: str | None) -> list[str]:
    """Why a restaurant cannot calculate tax through Stripe; empty when it can.

    Called outside any database transaction: it asks Stripe for the connected
    account's tax settings, and a network call must never hold a connection
    from the narrow system pool (rule 6).
    """
    blockers = [f"Pickup address is missing the {part}." for part in
                stripe_tax.address_problems(restaurant_view)]
    if not stripe_account_id:
        blockers.append("No Stripe connected account.")
    else:
        blockers.extend(stripe_tax.settings_problems(stripe_account_id))
    return blockers


PHONE_PATTERN = re.compile(r"^[+0-9()\-.\s]{7,25}$")


def clean_phone(value: str | None) -> str | None:
    """A phone number as a customer would dial it, or None for none.

    Only checks it could be one -- digits, spaces and the punctuation people
    write numbers with, and at least seven digits -- not that it rings.
    """
    if value is None or not value.strip():
        return None
    value = " ".join(value.split())
    if not PHONE_PATTERN.match(value) or sum(c.isdigit() for c in value) < 7:
        raise errors.validation_error(
            "Enter a phone number customers can call, like +1 214 555 0100."
        )
    return value


def normalize(changes: dict) -> dict:
    """Tidy a PATCH's values and refuse the ones that cannot be cleared.

    An empty address line means "there isn't one", so it is stored as NULL --
    otherwise a restaurant can satisfy the Stripe Tax gate with a space bar.
    Tax mode and tax code have no "none": every order needs both.
    """
    if "phone" in changes:
        changes["phone"] = clean_phone(changes["phone"])
    if changes.get("currency"):
        changes["currency"] = changes["currency"].upper()
    if changes.get("address_country"):
        changes["address_country"] = changes["address_country"].upper()
    for field in TAX_AND_ADDRESS_FIELDS:
        if isinstance(changes.get(field), str):
            changes[field] = changes[field].strip() or None
    if "tax_mode" in changes and changes["tax_mode"] is None:
        raise errors.validation_error("Choose a tax mode.")
    if "tax_code" in changes and changes["tax_code"] is None:
        raise errors.validation_error("A tax code is required.")
    return changes


def guard_stripe_tax(current, changes: dict, stripe_account_id: str | None) -> None:
    """Refuse an edit that would leave a Stripe Tax restaurant unable to tax.

    It runs on the *result* of the edit, not on the edit: switching Stripe Tax
    on without an address and deleting the city of a restaurant already on it
    are the same failure, and both are caught here. A flat-rate restaurant is
    never blocked -- its tax needs nothing but a number.
    """
    if not any(field in changes for field in TAX_AND_ADDRESS_FIELDS):
        return

    from types import SimpleNamespace

    prospective = {**tax_fields(current), **{
        field: value for field, value in changes.items() if field in TAX_AND_ADDRESS_FIELDS
    }}
    if prospective["tax_mode"] != "STRIPE_TAX":
        return

    blockers = stripe_tax_blockers(SimpleNamespace(**prospective), stripe_account_id)
    if blockers:
        raise errors.ApiError(409, "STRIPE_TAX_NOT_READY", " ".join(blockers))


ADDRESS_FIELDS = (
    "address_line1", "address_line2", "address_city", "address_state",
    "address_postal_code", "address_country",
)


def apply_changes(restaurant, changes: dict) -> None:
    """Write an edit onto the row, and forget where the restaurant is if the
    address moved.

    Delivery fees are measured from coordinates found by geocoding the pickup
    address. Leaving those coordinates in place after the address changes
    would go on charging every customer for the distance to the old premises,
    and nothing about editing a street says that is what is happening -- so
    the coordinates are dropped and the restaurant is asked to place itself
    again. Delivery stops being offered until it does, which is the safe way
    round: no delivery beats a wrong fee.
    """
    moved = any(
        field in changes and changes[field] != getattr(restaurant, field)
        for field in ADDRESS_FIELDS
    )
    for field, value in changes.items():
        setattr(restaurant, field, value)
    if moved:
        restaurant.latitude = None
        restaurant.longitude = None
        restaurant.geocoded_address = None


def audit(session, actor_user_id, action: str, scope: dict) -> None:
    """Record who changed what.

    The platform audit log takes staff edits too. A restaurant admin changing
    their own tax rate is exactly the kind of thing someone will later need to
    account for, and "the restaurant did it, not us" is only provable if it
    was written down at the time.
    """
    session.execute(
        text(
            """
            INSERT INTO platform_audit_logs
                (id, actor_user_id, action, scope, correlation_id, created_at)
            VALUES (gen_random_uuid(), :actor, :action, CAST(:scope AS jsonb),
                    gen_random_uuid()::text, now())
            """
        ),
        {"actor": str(actor_user_id), "action": action, "scope": json.dumps(scope)},
    )
