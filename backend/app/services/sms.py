"""Sending a text message, or saying plainly that we cannot.

THERE IS NO SMS VENDOR ON THIS DEPLOYMENT YET, and this module is the seam
where one goes. Everything above it -- phone verification, the welcome offer
that depends on it -- is written against `send`, so adding a vendor is a
change here and nowhere else.

WHY IT REFUSES RATHER THAN PRETENDS. A verification flow whose code never
arrives is worse than one that is switched off: the customer waits, retries,
and concludes the site is broken, while the server's logs say everything
went fine. So an unconfigured provider raises, the endpoint above answers
"not available right now", and the screen says so.

WHAT A VENDOR WILL NEED, because it is not only an API key. Sending business
SMS to US numbers means A2P 10DLC registration: a brand, a campaign, and
documented opt-in, reviewed before the first message. Unregistered traffic
is filtered by the carriers rather than rejected at the API, which looks
exactly like a working integration whose messages never arrive. Register
first, then set the credentials below.

The same reasoning is why referrals (0054) send nothing and hand the
customer a message to forward themselves.
"""

import logging

from app.config import settings

log = logging.getLogger(__name__)


class SmsUnavailable(RuntimeError):
    """No provider is configured, or the one configured would not send."""


def configured() -> bool:
    """Whether a text message can actually be sent right now.

    Read by the screens that offer something requiring one, so they can say
    it is unavailable instead of starting a flow that cannot finish.
    """
    return bool(
        getattr(settings, "SMS_PROVIDER", "")
        and getattr(settings, "SMS_FROM_NUMBER", "")
    )


def send(to: str, body: str) -> None:
    """Send one message, or raise SmsUnavailable.

    The body is written by the caller and never logged: a verification code
    in a log file is a verification code somebody else can read.
    """
    if not configured():
        log.info("sms not configured; nothing sent to %s", _for_log(to))
        raise SmsUnavailable(
            "Text messaging is not configured on this deployment. Set "
            "SMS_PROVIDER and SMS_FROM_NUMBER, and complete A2P 10DLC "
            "registration, before offering anything that needs it."
        )

    provider = settings.SMS_PROVIDER.strip().lower()
    # One provider when there is one. Written as a branch rather than a
    # registry because a registry with a single entry is a pattern pretending
    # to be a decision.
    raise SmsUnavailable(f"No sender is implemented for SMS_PROVIDER={provider!r}.")


def _for_log(number: str) -> str:
    """A number as a log may record it: enough to trace, not enough to dial.

    The same reasoning as core/logsafe.py applies to an address.
    """
    digits = "".join(character for character in number if character.isdigit())
    return f"***{digits[-4:]}" if len(digits) >= 4 else "***"
