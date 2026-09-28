"""Outgoing email through SendGrid.

One POST per message. What this module decides is what happens when that
POST does not simply succeed:

  * no SENDGRID_API_KEY   nothing is sent; the attempt is logged, without the
                          message body or the recipient's full address
  * 429 or 5xx, or no     RetryableEmailError, so the Celery task retries
    connection at all
  * any other 4xx         logged and dropped: an unverified sender or a
                          malformed address will not fix itself on retry
  * no answer after the   logged and dropped, not retried -- see below
    message went out

SendGrid has no idempotency key. A retry after it accepted a message but
before we heard back would deliver the message twice, so the one case where
that can happen -- the request was sent and the reply never came -- is not
retried. A request that never reached SendGrid is always safe to retry, and
is. The key each message carries still goes along, as a custom argument, so
a message can be found in SendGrid's activity feed by it.

Click and open tracking are turned off on every message. Click tracking
rewrites each link to pass through SendGrid, which would put a guest's order
link -- whose token sits after "#" precisely so no server sees it -- into
SendGrid's redirect URL and logs.
"""

import logging
from dataclasses import dataclass
from email.utils import parseaddr

import httpx

from app.config import settings
from app.core.logsafe import email_for_log

log = logging.getLogger(__name__)

SENDGRID_URL = "https://api.sendgrid.com/v3/mail/send"

# Failures that happen before a byte of the message reaches SendGrid, so a
# retry cannot deliver it twice. Anything else after sending is ambiguous.
_NEVER_SENT = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout, httpx.WriteError,
               httpx.WriteTimeout)


class RetryableEmailError(Exception):
    """The provider could not take the message now; try again later."""


@dataclass(frozen=True)
class Outcome:
    """What became of one attempt, for a caller that has to tell a person.

    `status` is SENT, FAILED or NOT_CONFIGURED. `problem` is written for a
    restaurant admin -- never the provider's raw reply, which can name the
    platform's own accounts; that goes to the log instead.
    """

    status: str
    problem: str | None = None

    @property
    def sent(self) -> bool:
        return self.status == "SENT"


def _problem(status_code: int, reply: str) -> str:
    """The provider's refusal, in words fit for the admin who sent the invite."""
    lowered = reply.lower()
    if "sender identity" in lowered or "from address does not match" in lowered:
        return (
            "Not delivered: the sending address is not verified with the email provider yet."
        )
    if status_code == 401 or "authorization grant" in lowered or "api key" in lowered:
        return "Not delivered: the email provider did not accept Zenoeats' credentials."
    if "valid address" in lowered or ("invalid" in lowered and "email" in lowered):
        return "Not delivered: the email provider says this address is not valid."
    return f"Not delivered: the email provider refused it (HTTP {status_code})."


@dataclass(frozen=True)
class Email:
    to: str
    subject: str
    html: str
    text: str
    idempotency_key: str


def configured() -> bool:
    """Whether anything will actually be sent.

    For a caller that has to tell a person whether an email is on its way.
    The send itself happens later, on the worker, so this cannot promise
    delivery -- but "no provider is configured" is certain, and saying an
    email went when none can is how a restaurant ended up waiting on
    invitations that were never sent.
    """
    return bool(settings.SENDGRID_API_KEY)


def _address(value: str) -> dict:
    """"Name <addr@x>" or a bare address, as SendGrid's {email, name}."""
    name, addr = parseaddr(value)
    return {"email": addr, **({"name": name} if name else {})}


def _body(email: "Email") -> dict:
    body = {
        "personalizations": [{"to": [{"email": email.to}]}],
        "from": _address(settings.EMAIL_FROM),
        "subject": email.subject,
        # SendGrid requires the plain text first when both are sent.
        "content": [
            {"type": "text/plain", "value": email.text},
            {"type": "text/html", "value": email.html},
        ],
        "custom_args": {"idempotency_key": email.idempotency_key[:256]},
        "tracking_settings": {
            "click_tracking": {"enable": False, "enable_text": False},
            "open_tracking": {"enable": False},
        },
    }
    if settings.EMAIL_REPLY_TO:
        body["reply_to"] = _address(settings.EMAIL_REPLY_TO)
    return body


def send(email: Email) -> bool:
    """Deliver one message. True when the provider accepted it."""
    return deliver(email).sent


def deliver(email: Email) -> Outcome:
    """Deliver one message, and say what happened in a form a person can read.

    Raises RetryableEmailError for the cases worth trying again; everything
    else is an answer.
    """
    if not configured():
        log.info(
            "SENDGRID_API_KEY is not set; not sending %r to %s",
            email.subject, email_for_log(email.to),
        )
        return Outcome("NOT_CONFIGURED", "Not sent: email is not set up on this server.")

    try:
        res = httpx.post(
            SENDGRID_URL,
            json=_body(email),
            headers={"Authorization": f"Bearer {settings.SENDGRID_API_KEY}"},
            timeout=10.0,
        )
    except _NEVER_SENT as exc:
        raise RetryableEmailError(f"email provider unreachable: {type(exc).__name__}") from exc
    except httpx.HTTPError as exc:
        # The message went out and no answer came back. SendGrid may well
        # have taken it; retrying could deliver it twice.
        log.warning(
            "email %r to %s: no answer from the provider (%s); not retrying",
            email.subject, email_for_log(email.to), type(exc).__name__,
        )
        return Outcome(
            "FAILED",
            "Not confirmed: the email provider did not answer in time. It may still arrive.",
        )

    if res.status_code == 429 or res.status_code >= 500:
        raise RetryableEmailError(f"email provider answered {res.status_code}")
    if res.status_code >= 400:
        # The body names the problem (an unverified sender, say) and carries
        # no secret, so it is worth the log line. The recipient is masked.
        log.error(
            "email %r to %s rejected (%s): %s",
            email.subject, email_for_log(email.to), res.status_code, res.text[:300],
        )
        return Outcome("FAILED", _problem(res.status_code, res.text))

    log.info(
        "sent %r to %s (%s)",
        email.subject, email_for_log(email.to), res.headers.get("x-message-id", "no id"),
    )
    return Outcome("SENT")
