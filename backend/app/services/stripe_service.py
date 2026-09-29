"""Stripe Connect. Direct charges on the restaurant's connected account.

Section 8: the restaurant is the merchant of record, receives payouts and
owns dispute operations. Zenoeats' own revenue is an application fee on each
charge, set by PLATFORM_FEE_BPS and PLATFORM_FEE_FIXED_MINOR; with both at 0
no fee is sent at all.

Every call here happens outside a database transaction (rule 6).
"""

import logging

import stripe

from app.config import settings
from app.core import errors
from app.models import Order, RestaurantPaymentAccount

log = logging.getLogger(__name__)
stripe.api_key = settings.STRIPE_SECRET_KEY
stripe.max_network_retries = 2

# Accounts v2 lives on the client object rather than the module globals.
# Everything else here stays on v1 on purpose: Stripe accepts a v2 account id
# at v1 endpoints, so AccountLink, Account.retrieve and PaymentIntent all keep
# working unchanged against accounts created below. Verified against the live
# test API rather than assumed.
_client = stripe.StripeClient(settings.STRIPE_SECRET_KEY)


def platform_fee_minor(total_minor: int) -> int:
    """Zenoeats' fee on an order total, in minor units.

    Rounded half up to the nearest cent, and never more than the order is
    worth: Stripe rejects an application fee larger than the charge, and a
    small order must not be refused because a fixed fee outgrew it.
    """
    if total_minor <= 0:
        return 0
    bps = max(0, settings.PLATFORM_FEE_BPS)
    fixed = max(0, settings.PLATFORM_FEE_FIXED_MINOR)
    percentage = (total_minor * bps + 5_000) // 10_000
    return min(total_minor, percentage + fixed)


def refund_order(payment, reason: str | None = None) -> dict:
    """Give a paid order's money back, on the restaurant's own account.

    The whole charge, including Zenoeats' fee: the sale did not happen, and
    a restaurant should not carry a commission on an order it cancelled.
    `refund_application_fee` is what returns the fee from the platform's
    balance rather than leaving it against the restaurant's.

    The idempotency key is the order and the fee flag, so a manager who
    presses twice, or a retry after a timeout nobody saw the answer to,
    produces one refund and not two. Stripe's own reply is returned rather
    than interpreted here; the webhook remains the authority on what was
    refunded in the end.

    Raises an ApiError a manager can read. The common refusal is a connected
    account whose balance has already been paid out, which Stripe answers
    with `balance_insufficient` -- worth saying plainly, because the money
    has to come from somewhere before the refund can go through.
    """
    if not payment.stripe_payment_intent_id or not payment.stripe_account_id:
        raise errors.validation_error("This order has no Stripe payment to refund.")

    # Only where there is one to give back. Stripe refuses the flag outright
    # on a charge with no application fee -- which is every charge while
    # PLATFORM_FEE_BPS and _FIXED_MINOR are 0 -- so sending it
    # unconditionally would make every refund fail on a platform that takes
    # no commission.
    reclaim_fee = _charged_a_fee(payment)
    try:
        refund = stripe.Refund.create(
            payment_intent=payment.stripe_payment_intent_id,
            **({"refund_application_fee": True} if reclaim_fee else {}),
            metadata={
                "order_id": str(payment.order_id),
                **({"reason": reason[:200]} if reason else {}),
            },
            stripe_account=payment.stripe_account_id,
            # The fee flag is part of the key, for the same reason it is part
            # of the intent's: Stripe refuses to replay a key with different
            # parameters, so an attempt that asked for the fee back would
            # poison the retry that must not. Identical attempts still share
            # a key, which is what stops a double press refunding twice.
            idempotency_key=f"order:{payment.order_id}:refund:v1:fee{int(reclaim_fee)}",
        )
    except stripe.error.StripeError as exc:
        code = getattr(exc, "code", None)
        log.warning(
            "refund failed for order %s: %s (%s)", payment.order_id, exc.user_message or exc, code
        )
        raise errors.ApiError(502, "REFUND_FAILED", _refund_problem(code, exc)) from exc

    log.info("refunded order %s: %s", payment.order_id, _value(refund, "id"))
    # A plain dict, not Stripe's object: the caller reads two fields, and a
    # StripeObject is not a dict however much it looks like one -- `.get` on
    # it raises, which is how this was found.
    return {
        "id": _value(refund, "id"),
        "status": _value(refund, "status"),
        "amount": _value(refund, "amount"),
    }


def _value(obj, name):
    """One field, whether Stripe handed us an object or a test a dict."""
    return obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)


def _charged_a_fee(payment) -> bool:
    """Whether this charge actually carried Zenoeats' fee.

    Asked of Stripe rather than recomputed from the current settings: the fee
    is whatever was taken when the customer paid, and a rate changed since
    then must not decide what comes back now. One extra call, on an operation
    that happens once per cancelled order.

    A failure here is answered with False. Refunding the customer without
    reclaiming our fee is a smaller wrong than refusing the refund, and it is
    a difference the platform can settle afterwards.
    """
    try:
        intent = stripe.PaymentIntent.retrieve(
            payment.stripe_payment_intent_id,
            expand=["latest_charge"],
            stripe_account=payment.stripe_account_id,
        )
    except stripe.error.StripeError:
        log.warning("could not read the fee on order %s; refunding without it", payment.order_id)
        return False
    charge = _value(intent, "latest_charge")
    # Unexpanded, Stripe sends the id alone; the intent still carries what it
    # asked for, which is the same figure.
    if charge is None or isinstance(charge, str):
        return bool(_value(intent, "application_fee_amount"))
    return bool(_value(charge, "application_fee_amount") or _value(charge, "application_fee"))


def _refund_problem(code: str | None, exc: Exception) -> str:
    """Stripe's refusal, in words a manager can act on.

    Its own message names API objects and the connected account, which is
    the restaurant's own but still reads as somebody else's plumbing. Only
    the cases a restaurant can do something about are worded here; the rest
    keep Stripe's message, which is better than a shrug.
    """
    if code == "balance_insufficient":
        return (
            "Stripe refused the refund: this restaurant's Stripe balance is too low. "
            "Add funds in Stripe, or refund it there once the next payout clears."
        )
    if code == "charge_already_refunded":
        return "This order has already been refunded."
    message = getattr(exc, "user_message", None) or "Stripe could not process the refund."
    return str(message)[:200]


def create_payment_intent(
    order: Order, account: RestaurantPaymentAccount
) -> stripe.PaymentIntent:
    """Create a PaymentIntent on the connected account.

    The Stripe idempotency key is derived server side from the order id. It
    is a different concern from our API idempotency key, which is scoped to
    actor plus endpoint. Retrying this call for the same order will always
    return the same intent rather than creating a second one.

    No receipt_email. In live mode Stripe emails a receipt to that address
    whatever the account's settings say, and another for every refund -- on
    top of Zenoeats' own confirmation and refund emails, which carry the
    order and the restaurant's name. One email per event is enough. The
    customer's address still reaches Stripe, for fraud screening, as the
    payment's billing email: the payment page sends it with the card.
    """
    if not account.charges_enabled:
        raise errors.payment_provider_unavailable(
            "This restaurant cannot accept card payments right now."
        )

    fee = platform_fee_minor(order.total_minor)
    extra = {"application_fee_amount": fee} if fee > 0 else {}

    try:
        return stripe.PaymentIntent.create(
            amount=order.total_minor,
            currency=order.currency.lower(),
            automatic_payment_methods={"enabled": True},
            metadata={
                "order_id": str(order.id),
                "restaurant_id": str(order.restaurant_id),
                "order_number": str(order.order_number),
                "platform_fee_minor": str(fee),
                # Stripe's convention for a PaymentIntent whose amount came
                # from a tax calculation. Empty for a flat-rate restaurant.
                **({"tax_calculation": order.tax_calculation_id}
                   if getattr(order, "tax_calculation_id", None) else {}),
            },
            stripe_account=account.stripe_account_id,
            # The fee is part of the key. Stripe refuses to replay an
            # idempotency key with different parameters, so without it a fee
            # change made while a customer sat on the payment page would turn
            # their retry into an error instead of a fresh intent.
            idempotency_key=f"order:{order.id}:pi:v1:fee{fee}",
            **extra,
        )
    except stripe.StripeError as exc:
        log.warning("stripe PaymentIntent failed for order %s: %s", order.id, exc.user_message or exc)
        raise errors.payment_provider_unavailable() from exc


# Stripe's answers that an intent is not there to be read. Anything else going
# wrong is Stripe being unreachable or unwell, which says nothing about the
# payment.
_INTENT_GONE_CODES = {"resource_missing", "account_invalid"}


def retrieve_payment_intent(intent_id: str, stripe_account_id: str) -> dict | None:
    """Read a PaymentIntent straight from Stripe, as a plain dict.

    The payment webhook is how an intent's outcome normally arrives; this is
    the fallback for when it does not (tasks.reconcile_payment_intent). A
    plain dict because that is the shape the webhook handlers read, and
    stripe-python's objects raise on .get().

    Returns None when Stripe answers that the intent does not exist on that
    account, which is a definite answer. Raises when Stripe could not be
    asked, which is not an answer at all: a caller must never read that as
    "unpaid".
    """
    try:
        intent = stripe.PaymentIntent.retrieve(intent_id, stripe_account=stripe_account_id)
    except stripe.StripeError as exc:
        if getattr(exc, "code", None) in _INTENT_GONE_CODES:
            log.warning("stripe has no intent %s on %s: %s", intent_id, stripe_account_id, exc.code)
            return None
        raise errors.payment_provider_unavailable() from exc
    return intent.to_dict() if hasattr(intent, "to_dict") else dict(intent)


def create_account_link(stripe_account_id: str, refresh_url: str, return_url: str) -> str:
    """Stripe-hosted onboarding. Zenoeats never collects KYC data itself."""
    try:
        link = stripe.AccountLink.create(
            account=stripe_account_id,
            refresh_url=refresh_url,
            return_url=return_url,
            type="account_onboarding",
        )
        return link.url
    except stripe.StripeError as exc:
        raise errors.payment_provider_unavailable() from exc


def create_connected_account(
    *,
    email: str,
    display_name: str,
    country: str = "US",
    entity_type: str = "company",
) -> str:
    """Create a connected account and return its id.

    Accounts v2. Stripe rejects `Account.create` (v1) outright for new Connect
    integrations, pointing at /v2/core/accounts instead.

    The v1 `type="standard"` shape maps onto v2 as `dashboard="full"` plus an
    explicit `merchant` configuration: v2 does not infer capabilities from an
    account type, you request them. `responsibilities` both set to "stripe"
    keeps the connected account liable for its own fees and losses, which is
    what direct charges require and what makes the restaurant, not Zenoeats,
    the merchant of record.

    Returns the id rather than the object because the v2 response shape omits
    most fields unless named in `include`, and every caller only wants the id.
    """
    try:
        account = _client.v2.core.accounts.create(
            {
                "contact_email": email,
                "display_name": display_name,
                "dashboard": "full",
                "identity": {"country": country.lower(), "entity_type": entity_type},
                "configuration": {
                    "merchant": {"capabilities": {"card_payments": {"requested": True}}}
                },
                "defaults": {
                    "currency": "usd",
                    "responsibilities": {
                        "fees_collector": "stripe",
                        "losses_collector": "stripe",
                    },
                },
                "include": ["configuration.merchant", "identity", "requirements"],
            }
        )
        return account.id
    except stripe.StripeError as exc:
        # Platform admins are trusted operators, not customers. The generic
        # "try again" told them nothing and was actively wrong for errors that
        # no amount of retrying fixes, such as a disabled capability.
        detail = getattr(exc, "user_message", None) or str(exc)
        log.error("stripe connected account creation failed: %s", detail)
        raise errors.payment_provider_unavailable(
            f"Stripe rejected the account creation: {detail}"
        ) from exc


def retrieve_account_status(stripe_account_id: str) -> dict:
    """Read a connected account's current state straight from Stripe.

    The account.updated webhook is the normal path, but it is not the only
    one that has to work. Stripe's own guidance for hosted onboarding is that
    the return_url carries no state, so the platform should retrieve the
    account and check its requirements -- a webhook that was missed, arrived
    while the worker was down, or was never configured would otherwise leave
    the portal permanently disagreeing with Stripe.

    Returns the flags plus why charges are disabled, if they are. Without the
    reason, "onboarding incomplete" is a dead end for whoever is trying to
    get a restaurant live.
    """
    try:
        account = stripe.Account.retrieve(stripe_account_id)
    except stripe.StripeError as exc:
        detail = getattr(exc, "user_message", None) or str(exc)
        log.error("could not retrieve connected account %s: %s", stripe_account_id, detail)
        raise errors.payment_provider_unavailable(
            f"Stripe could not be reached for this account: {detail}"
        ) from exc

    requirements = account.requirements
    currently_due = list(requirements.currently_due or []) if requirements else []
    past_due = list(requirements.past_due or []) if requirements else []
    disabled_reason = requirements.disabled_reason if requirements else None

    return {
        "charges_enabled": bool(account.charges_enabled),
        "payouts_enabled": bool(account.payouts_enabled),
        "details_submitted": bool(account.details_submitted),
        "disabled_reason": disabled_reason,
        "currently_due": currently_due,
        "past_due": past_due,
    }


def storefront_domain(slug: str) -> str:
    return f"{slug}.{settings.ROOT_DOMAIN}"


def ensure_wallet_domain(stripe_account_id: str, slug: str) -> dict | None:
    """Register the restaurant's storefront for Apple Pay, Google Pay and Link.

    Stripe only shows wallet buttons in the Payment Element on a registered
    domain, and with direct charges the registration belongs to the account
    running the charge: each restaurant's own connected account, for its own
    subdomain. Unregistered, checkout still takes cards, so nothing fails --
    the wallets are simply never offered, which is easy to miss for months.

    Idempotent: finds an existing registration and re-enables or re-validates
    it, creating one only if there is none. Never raises. A wallet problem
    must not stop a restaurant going live, so the outcome is returned for the
    portal to show instead. None where there is nothing to register: a
    development domain Stripe would refuse.
    """
    domain = storefront_domain(slug)
    if settings.ROOT_DOMAIN.endswith(".local") or not settings.STRIPE_SECRET_KEY:
        return None

    try:
        found = stripe.PaymentMethodDomain.list(
            domain_name=domain, limit=1, stripe_account=stripe_account_id
        ).data
        if not found:
            record = stripe.PaymentMethodDomain.create(
                domain_name=domain, stripe_account=stripe_account_id
            )
        else:
            record = found[0]
            if not record.enabled:
                record = stripe.PaymentMethodDomain.modify(
                    record.id, enabled=True, stripe_account=stripe_account_id
                )
            if record.apple_pay.status != "active":
                record = stripe.PaymentMethodDomain.validate(
                    record.id, stripe_account=stripe_account_id
                )
    except stripe.StripeError as exc:
        detail = getattr(exc, "user_message", None) or str(exc)
        log.warning("could not register %s for wallets on %s: %s", domain, stripe_account_id, detail)
        return {"domain": domain, "apple_pay": "unknown", "google_pay": "unknown", "problem": detail}

    apple = record.apple_pay
    problem = None
    if apple.status != "active" and getattr(apple, "status_details", None):
        problem = apple.status_details.error_message
    return {
        "domain": domain,
        "apple_pay": apple.status,
        "google_pay": record.google_pay.status,
        "problem": problem,
    }


def construct_connect_event(payload: bytes, signature: str) -> stripe.Event:
    """Verify the Connect webhook signature. Raises on tampering."""
    return stripe.Webhook.construct_event(
        payload, signature, settings.STRIPE_CONNECT_WEBHOOK_SECRET
    )
