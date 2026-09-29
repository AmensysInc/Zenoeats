"""Clerk's customer emails, sent through SendGrid instead of by Clerk.

Clerk sends its own emails -- sign-up codes, password-reset codes and the
rest -- unless "Delivered by Clerk" is switched off for one in its dashboard
(Customization > Emails). Then it sends nothing, and reports each email it
would have sent as an `email.created` webhook instead. This module sends
those, so a customer's every email comes from the same sender and looks the
same.

The two a customer meets most, the verification code and the password-reset
code, are rendered from Zenoeats' own template (customer_code/). Any other
kind is sent in Clerk's own words, exactly as Clerk rendered it, so
switching off a kind this module has no template for still delivers it.

An email Clerk delivered itself (delivered_by_clerk true) is not sent again.

The code is a secret for the minutes it is valid. It is never written to the
database -- the webhook stores a redacted copy of the event -- and it travels
to the worker sealed with the field key, because the broker keeps what it
holds on disk. Its subject is never logged: Clerk's own puts the code in it.
"""

import json
import logging

from app.core import crypto
from app.services import email, email_templates

log = logging.getLogger(__name__)

# Clerk's template slug -> what our code email says it is for.
BRANDED = {
    "verification_code": "verify",
    "reset_password_code": "reset",
}


def wanted(data: dict) -> bool:
    """Whether this email.created is ours to send: Clerk did not deliver it
    itself. Missing counts as delivered, so a payload without the field can
    never produce a second copy."""
    return data.get("delivered_by_clerk", True) is False


def seal(data: dict) -> str:
    """What the worker needs from the event, encrypted for its time in the
    queue: the recipient, the kind, the code, and Clerk's own rendering to
    fall back on."""
    fields = {
        "to": data.get("to_email_address") or "",
        "slug": data.get("slug") or "",
        "code": (data.get("data") or {}).get("otp_code") or "",
        "subject": data.get("subject") or "",
        "body": data.get("body") or "",
        "body_plain": data.get("body_plain") or "",
    }
    return crypto.encrypt_field(json.dumps(fields))


def redacted(event: dict) -> dict:
    """The event as the webhook inbox may keep it: which email, never its
    contents or recipient."""
    data = event.get("data") or {}
    return {
        "type": event.get("type"),
        "data": {
            "id": data.get("id"),
            "slug": data.get("slug"),
            "delivered_by_clerk": data.get("delivered_by_clerk"),
        },
    }


def compose(fields: dict) -> tuple[str, str, str]:
    """(subject, html, text) for one of Clerk's emails."""
    purpose = BRANDED.get(fields["slug"])
    if purpose and fields["code"]:
        out = email_templates.render("customer_code", purpose=purpose, code=fields["code"])
        return out.subject, out.html, out.text
    # A kind we have no template for: Clerk's own words, as Clerk wrote them.
    return fields["subject"], fields["body"], fields["body_plain"] or fields["subject"]


def send(sealed: str, event_id: str) -> bool:
    """Send one of Clerk's emails. True when SendGrid accepted it."""
    fields = json.loads(crypto.decrypt_field(sealed))
    if not fields["to"] or not (fields["body"] or fields["code"]):
        log.warning("clerk email %s (%s) had nothing to send", event_id, fields["slug"])
        return False
    subject, body_html, body_text = compose(fields)
    return email.deliver(email.Email(
        to=fields["to"], subject=subject, html=body_html, text=body_text,
        idempotency_key=f"clerk/{event_id}", sensitive=True,
    )).sent
