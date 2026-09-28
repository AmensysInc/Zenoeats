"""Checkout: quote, create order, create PaymentIntent, read order state.

The sequence is Appendix E.1:

  POST /orders                     order committed as PENDING_PAYMENT
  POST /orders/{id}/payment-intent PaymentIntent on the connected account
  (customer confirms in the browser)
  Stripe webhook                   what normally says PAID
  GET  /orders/{id}                client polls for the authoritative state;
                                   if the webhook is overdue, asks Stripe
"""

import logging
from datetime import timedelta
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Header, Response
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import (
    TenantDb, current_restaurant, get_current_user, optional_current_user,
)
from app.config import settings
from app.core import errors, guest_auth, idempotency
from app.core.ratelimit import per_ip, per_user, runtime_redis
from app.core.crypto import decrypt_field
from app.db.base import utcnow
from app.db.session import system_session
from app.models import (
    FulfillmentType, Order, OrderEvent, OrderEventAction, OrderItem, OrderStatus, Payment,
    PaymentStatus, Restaurant, RestaurantPaymentAccount, User, UserKind,
)
from app.schemas.api import (
    AmountsOut, CreateOrderIn, CustomerSessionOut, DriverLocationOut, GuestSessionIn,
    GuestSessionOut, MapPointOut, OrderItemOut, OrderModifierOut, OrderOut, PaymentIntentOut,
    QuoteIn, QuoteOut, TrackingOut, TrackingStepOut,
)
from app.services import (
    clerk_customers, customer_profile, delivery, guest_customers, stripe_service, terms,
    tracking,
)
from app.services.orders import DeliveryDetails, create_pending_order, refund_minor
from app.services.pricing import price_cart
from app.workers.tasks import reconcile_payment_intent

log = logging.getLogger(__name__)
router = APIRouter(tags=["orders"])

# The webhook normally confirms a payment a second or two after the customer
# pays. Once a payment has sat in PROCESSING longer than this, the tracking
# page's poll stops waiting for it and asks Stripe directly.
_WEBHOOK_OVERDUE = timedelta(seconds=10)
# And asks at most this often per payment, however many tabs are polling.
_RECONCILE_EVERY_SECONDS = 10


def _serialize(
    order: Order,
    payment: Payment | None,
    *,
    include_pin: bool,
    tracking_out: TrackingOut | None = None,
) -> OrderOut:
    pin = None
    # Not for a delivery: there is no counter at a doorstep to read it to.
    delivering = order.fulfillment_type == FulfillmentType.DELIVERY.value
    if include_pin and not delivering and order.pickup_pin_encrypted and order.status in {
        OrderStatus.AUTO_ACCEPTED.value,
        OrderStatus.PREPARING.value,
        OrderStatus.READY_FOR_PICKUP.value,
    }:
        pin = decrypt_field(order.pickup_pin_encrypted)

    return OrderOut(
        order_id=order.id,
        order_number=order.order_number,
        status=order.status,
        payment_status=payment.status if payment else PaymentStatus.PENDING.value,
        fulfillment_type=order.fulfillment_type,
        currency=order.currency,
        amounts=AmountsOut(
            subtotal_minor=order.subtotal_minor,
            discount_minor=order.discount_minor,
            delivery_fee_minor=order.delivery_fee_minor,
            tax_minor=order.tax_minor,
            total_minor=order.total_minor,
        ),
        items=[
            OrderItemOut(
                name=i.name_snapshot,
                combo_name=i.combo_name_snapshot,
                combo_group=i.combo_group,
                quantity=i.quantity,
                unit_price_minor=i.unit_price_minor,
                line_total_minor=i.line_total_minor,
                item_note=i.item_note,
                modifiers=[
                    OrderModifierOut(
                        group_name=m.group_name_snapshot,
                        option_name=m.option_name_snapshot,
                        unit_price_delta_minor=m.unit_price_delta_minor,
                        quantity=m.quantity,
                    )
                    for m in i.modifiers
                ],
            )
            for i in order.items
        ],
        pickup_pin=pin,
        delivery_address=order.delivery_address,
        tracking=tracking_out,
        expires_at=order.expires_at,
        created_at=order.created_at,
        cancelled_by_restaurant=(
            order.status == OrderStatus.CANCELLED.value
            and order.cancelled_reason == "CANCELLED_BY_RESTAURANT"
        ),
        refund_minor=refund_minor(payment),
    )


@router.post(
    "/orders/quote",
    response_model=QuoteOut,
    dependencies=[Depends(per_ip("quote", limit=120))],
)
def quote_cart(
    body: QuoteIn,
    restaurant: Restaurant = Depends(current_restaurant),
    db: Session = TenantDb,
):
    """Authoritative pricing preview. Creates nothing.

    For a delivery, the address is checked against the restaurant's rings
    here, so a customer hears "too far" before they reach payment rather than
    after. The same check runs again when the order is created.
    """
    fee = None
    if body.fulfillment_type == "DELIVERY":
        fee = delivery.quote(db, restaurant, body.delivery_address or "")
    cart = price_cart(
        db,
        restaurant,
        [line.model_dump() for line in body.items],
        [combo.model_dump() for combo in body.combos],
        delivery_fee_minor=fee.fee_minor if fee else 0,
    )
    return QuoteOut(
        currency=cart.currency,
        amounts=_amounts(cart),
        delivery_miles=fee.miles if fee else None,
    )


def _amounts(cart) -> AmountsOut:
    return AmountsOut(
        subtotal_minor=cart.subtotal_minor,
        discount_minor=cart.discount_minor,
        delivery_fee_minor=cart.delivery_fee_minor,
        tax_minor=cart.tax_minor,
        total_minor=cart.total_minor,
    )


@router.get("/orders/session", response_model=CustomerSessionOut)
def current_customer(user: User = Depends(get_current_user)):
    """Who is ordering. 401 when nobody is.

    The customer counterpart of /restaurant/me and /admin/me. The storefront
    header and the checkout guard both read it, and it is the only way the
    browser can learn it holds a live guest session at all -- that cookie is
    httpOnly, so no script can see it.
    """
    return CustomerSessionOut(
        email=user.email,
        full_name=user.full_name,
        phone=user.phone,
        address=user.address,
        email_pending=clerk_customers.has_placeholder_email(user),
        is_guest=user.kind == UserKind.GUEST.value,
        terms_accepted=not terms.needs_recording(user),
    )


@router.post(
    "/orders/guest-session",
    response_model=GuestSessionOut,
    status_code=201,
    # Per IP, because there is no user to key on yet -- this endpoint is how
    # one comes into existence. It is also the reason the limit is tighter
    # than the ordering ones: minting identities is the cheap part.
    dependencies=[Depends(per_ip("guest_session", limit=10))],
)
def start_guest_session(
    body: GuestSessionIn,
    response: Response,
    restaurant: Restaurant = Depends(current_restaurant),
):
    """Begin a checkout with no account behind it.

    Creates the users row the order will hang off and hands back the cookie
    that is the only way to reach it. Nothing is verified: the address is
    where the receipt goes, not a claim about who this is.

    Depends on current_restaurant so a guest cannot be minted against a
    restaurant that is not taking orders, and so this endpoint answers the
    same 404 as the rest of the surface on an address with no restaurant.
    """
    guest = guest_customers.create_guest(body.email, body.full_name)

    response.set_cookie(
        key=guest_auth.SESSION_COOKIE,
        value=guest_auth.issue_session(guest.id),
        max_age=settings.GUEST_SESSION_TTL_MINUTES * 60,
        httponly=True,
        samesite="lax",
        secure=settings.ENV == "production",
        path="/",
    )
    return GuestSessionOut(email=guest.email, full_name=guest.full_name)


@router.delete("/orders/session", status_code=204)
def end_guest_session(response: Response):
    """Stop being this guest.

    Guests only: a signed-in customer's session is Clerk's and is ended
    through Clerk. Clearing the cookie is irreversible by design -- nothing
    else can name that guest row -- so the page asks first.
    """
    response.delete_cookie(
        key=guest_auth.SESSION_COOKIE,
        path="/",
        httponly=True,
        samesite="lax",
        secure=settings.ENV == "production",
    )


@router.post(
    "/orders",
    response_model=OrderOut,
    status_code=201,
    dependencies=[Depends(per_user("create_order", limit=20))],
)
def create_order(
    body: CreateOrderIn,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    user: User = Depends(get_current_user),
    restaurant: Restaurant = Depends(current_restaurant),
    db: Session = TenantDb,
):
    """Create a PENDING_PAYMENT order. No charge is attempted here.

    Rule 16: the order row always exists before Stripe is allowed to move
    money. If the customer closes the tab now, a Celery sweep expires the
    order after the TTL and nothing was ever charged.
    """
    payload = body.model_dump(mode="json")
    request_hash = idempotency.hash_request(payload)
    endpoint = "POST /api/v1/orders"

    replay = idempotency.lookup(
        db, key=idempotency_key, actor_id=user.id, endpoint=endpoint,
        request_hash=request_hash,
    )
    if replay is not None:
        return OrderOut.model_validate(replay)

    # Name and phone are required by the body itself, with an address required
    # for delivery; the email is the identity's. A signed-in customer whose
    # address Clerk has not supplied yet has none a receipt or a restaurant
    # could use, and every later request asks Clerk again, so this clears
    # itself in a moment.
    if clerk_customers.has_placeholder_email(user):
        raise errors.ApiError(
            503, "EMAIL_PENDING",
            "We could not confirm your email address just now. Try again in a moment.",
        )

    # Priced again from the address, whatever the quote said: the fee is
    # never the browser's to name.
    fee = None
    if body.fulfillment_type == "DELIVERY":
        fee = delivery.quote(db, restaurant, body.contact.address)
    cart = price_cart(
        db,
        restaurant,
        [line.model_dump() for line in body.items],
        [combo.model_dump() for combo in body.combos],
        delivery_fee_minor=fee.fee_minor if fee else 0,
    )

    if body.expected_total_minor is not None and body.expected_total_minor != cart.total_minor:
        # The menu changed between the quote and the confirm. Never silently
        # charge a different amount than the customer agreed to.
        raise errors.price_changed()

    order = create_pending_order(
        db,
        restaurant=restaurant,
        customer_user_id=user.id,
        cart=cart,
        customer_note=body.customer_note,
        contact_name=body.contact.full_name,
        contact_phone=body.contact.phone,
        delivery=(
            DeliveryDetails(address=body.contact.address, miles=fee.miles) if fee else None
        ),
    )
    order.contact_email = (
        body.guest_email if user.kind == UserKind.GUEST.value and body.guest_email
        else clerk_customers.receipt_address(user)
    )
    # New pickup forms send an empty address, while older clients may still
    # provide one. Preserve a supplied snapshot without requiring it.
    order.contact_address = body.contact.address or None
    db.flush()
    db.refresh(order)
    # An order-specific address must not overwrite a registered profile.
    if user.kind == UserKind.GUEST.value:
        customer_profile.save_contact(user.id, body.contact)

    # The checkout page says, above the button, that continuing means agreeing
    # to the terms. This is what remains of that afterwards. Here rather than
    # at sign-up because a guest never signs up, and it does nothing at all
    # once the customer's record is already current.
    terms.record(user)

    payment = db.execute(select(Payment).where(Payment.order_id == order.id)).scalar_one()
    response = _serialize(order, payment, include_pin=False)

    idempotency.store(
        db, key=idempotency_key, actor_id=user.id, endpoint=endpoint,
        request_hash=request_hash, status=201, body=response.model_dump(mode="json"),
    )
    return response


@router.post(
    "/orders/{order_id}/payment-intent",
    response_model=PaymentIntentOut,
    dependencies=[Depends(per_user("payment_intent", limit=20))],
)
def create_payment_intent(
    order_id: UUID,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    user: User = Depends(get_current_user),
    restaurant: Restaurant = Depends(current_restaurant),
    db: Session = TenantDb,
):
    """Create or return the PaymentIntent for an existing order.

    Stripe is called outside any long-running transaction (rule 6). If Stripe
    is unreachable, the order stays PENDING_PAYMENT and no charge is
    attempted; the customer can retry until the TTL expires it.
    """
    endpoint = "POST /api/v1/orders/{id}/payment-intent"
    request_hash = idempotency.hash_request({"order_id": str(order_id)})

    replay = idempotency.lookup(
        db, key=idempotency_key, actor_id=user.id, endpoint=endpoint,
        request_hash=request_hash,
    )
    if replay is not None:
        return PaymentIntentOut.model_validate(replay)

    order = db.get(Order, order_id)
    if order is None or order.customer_user_id != user.id:
        raise errors.order_not_found()
    if order.status != OrderStatus.PENDING_PAYMENT.value:
        raise errors.order_state_conflict("This order is no longer awaiting payment.")
    if order.expires_at and order.expires_at <= utcnow():
        raise errors.order_state_conflict("This order expired. Start a new one.")

    payment = db.execute(
        select(Payment).where(Payment.order_id == order.id).with_for_update()
    ).scalar_one()

    account = db.execute(select(RestaurantPaymentAccount)).scalar_one_or_none()
    if account is None:
        raise errors.payment_provider_unavailable("This restaurant is not set up for card payments.")

    # Stripe's own idempotency key is derived from the order id, so a retry
    # returns the same intent rather than creating a second one.
    intent = stripe_service.create_payment_intent(
        order, account, receipt_email=order.contact_email or clerk_customers.receipt_address(user)
    )

    payment.stripe_payment_intent_id = intent.id
    payment.stripe_client_secret = intent.client_secret
    payment.stripe_account_id = account.stripe_account_id
    payment.status = PaymentStatus.PROCESSING.value
    db.flush()

    response = PaymentIntentOut(
        order_id=order.id,
        payment_id=payment.id,
        client_secret=intent.client_secret,
        stripe_account_id=account.stripe_account_id,
        publishable_key=settings.STRIPE_PUBLISHABLE_KEY,
        payment_status=payment.status,
    )
    idempotency.store(
        db, key=idempotency_key, actor_id=user.id, endpoint=endpoint,
        request_hash=request_hash, status=200, body=response.model_dump(mode="json"),
    )
    return response


@router.get("/orders/{order_id}", response_model=OrderOut)
def get_order(
    order_id: UUID,
    background: BackgroundTasks,
    x_order_token: str | None = Header(default=None, alias="X-Order-Token"),
    user: User | None = Depends(optional_current_user),
    restaurant: Restaurant = Depends(current_restaurant),
    db: Session = TenantDb,
):
    """Authoritative order state. This is what the tracking page polls.

    The client never decides that a payment succeeded. It reads what the
    webhook wrote -- or, when the webhook is overdue, what Stripe itself says,
    checked after this response has gone (see _reconcile_payment) and so
    visible to the next poll.

    Two ways in. Ordinarily the caller owns the order -- a Clerk session or
    the guest cookie that created it. X-Order-Token is the other: the token
    in a guest's confirmation email, which is how someone who ordered without
    an account reaches their pickup PIN from a phone that is not the one they
    ordered on. It is checked against this order id, so it opens nothing else.

    Only ever a header. It used to be ?t=, which put a seven-day key to a
    pickup PIN into every access log the request passed through -- uvicorn's
    and nginx's both record the query string. The email link now carries it
    in the fragment, which no server sees, and the page moves it into this
    header; a ?t= is ignored, so no client can put it back in a URL.
    """
    t = x_order_token
    # Decided before the order is read: a caller with neither a session nor a
    # token has nothing to be told apart from a 404, and asking the database
    # first would only turn "sign in" into "no such order".
    by_token = bool(t) and guest_auth.order_token_grants(t or "", order_id)
    if not by_token and user is None:
        raise errors.ApiError(
            401, "UNAUTHENTICATED", "Sign in or continue as a guest to order."
        )

    order = db.execute(
        select(Order)
        .where(Order.id == order_id)
        .options(selectinload(Order.items).selectinload(OrderItem.modifiers))
    ).scalar_one_or_none()

    if order is None:
        raise errors.order_not_found()
    if not by_token and order.customer_user_id != user.id:
        raise errors.order_not_found()

    payment = db.execute(select(Payment).where(Payment.order_id == order.id)).scalar_one_or_none()
    if _webhook_is_overdue(order, payment):
        background.add_task(
            _reconcile_payment,
            payment.id, payment.stripe_payment_intent_id, payment.stripe_account_id,
        )
    return _serialize(
        order, payment, include_pin=True,
        tracking_out=_tracking(db, order, restaurant, background),
    )


# The order a tracking timeline reads events in. Any of them can be missing:
# a driver assigned before the kitchen finished, or never recorded at all on
# an order from before events were kept.
_STEP_FOR_EVENT = {
    OrderEventAction.ASSIGNED_DRIVER.value: "DRIVER_ASSIGNED",
    OrderEventAction.MARKED_READY.value: "READY",
    OrderEventAction.PICKED_UP.value: "PICKED_UP",
    OrderEventAction.DELIVERED.value: "DELIVERED",
}
_STEP_ORDER = ["PAID", "DRIVER_ASSIGNED", "READY", "PICKED_UP", "DELIVERED"]


def _tracking(
    db: Session, order: Order, restaurant: Restaurant, background: BackgroundTasks
) -> TrackingOut | None:
    """What a delivery's customer can follow: the steps so far, who is
    bringing it, and while it is on the road, where they are.

    Nothing here calls out of the building. The customer's coordinates come
    from the geocoding cache, the driver's from Redis, the arrival time from
    an answer a background task asked Google for after an earlier poll.
    """
    if order.fulfillment_type != FulfillmentType.DELIVERY.value or order.paid_at is None:
        return None

    # The latest time each step happened. A driver assigned, unassigned and
    # assigned again is assigned as of the last time.
    at: dict[str, object] = {"PAID": order.paid_at}
    for event in db.execute(
        select(OrderEvent)
        .where(OrderEvent.order_id == order.id)
        .order_by(OrderEvent.created_at)
    ).scalars():
        step = _STEP_FOR_EVENT.get(event.action)
        if step:
            at[step] = event.created_at
        elif event.action == OrderEventAction.UNASSIGNED_DRIVER.value:
            at.pop("DRIVER_ASSIGNED", None)
    if order.driver_user_id is None:
        at.pop("DRIVER_ASSIGNED", None)
    if order.status == OrderStatus.COMPLETED.value and "DELIVERED" not in at and order.completed_at:
        at["DELIVERED"] = order.completed_at

    driver_name = None
    if order.driver_user_id:
        # users has no tenant policy, so it is read with the system role. The
        # first name only: a customer needs to know who to look out for, not
        # who they are.
        with system_session() as sys_db:
            driver = sys_db.get(User, order.driver_user_id)
            if driver is not None and driver.full_name:
                driver_name = driver.full_name.split(" ")[0]

    has_origin = restaurant.latitude is not None and restaurant.longitude is not None
    target = tracking.destination(order.delivery_address)

    location = eta = None
    if order.status == OrderStatus.OUT_FOR_DELIVERY.value and order.driver_user_id:
        location = tracking.current_location(restaurant.id, order.driver_user_id)
        if location is not None and target is not None and tracking.eta_configured():
            background.add_task(
                tracking.refresh_eta,
                order.id,
                tracking.Point(location.latitude, location.longitude),
                target,
            )
        eta = tracking.current_eta(order.id) if location is not None else None

    return TrackingOut(
        steps=[TrackingStepOut(step=s, at=at[s]) for s in _STEP_ORDER if s in at],
        driver_name=driver_name,
        restaurant=(
            MapPointOut(latitude=restaurant.latitude, longitude=restaurant.longitude)
            if has_origin else None
        ),
        destination=(
            MapPointOut(latitude=target.latitude, longitude=target.longitude) if target else None
        ),
        driver_location=(
            DriverLocationOut(
                latitude=location.latitude,
                longitude=location.longitude,
                heading=location.heading,
                recorded_at=location.recorded_at,
            )
            if location else None
        ),
        eta_seconds=eta.seconds if eta else None,
        eta_computed_at=eta.computed_at if eta else None,
    )


def _webhook_is_overdue(order: Order, payment: Payment | None) -> bool:
    """A charge has been attempted and nothing has told us how it went."""
    return (
        payment is not None
        and order.status == OrderStatus.PENDING_PAYMENT.value
        and payment.status == PaymentStatus.PROCESSING.value
        and bool(payment.stripe_payment_intent_id and payment.stripe_account_id)
        and payment.updated_at <= utcnow() - _WEBHOOK_OVERDUE
    )


def _reconcile_payment(payment_id: UUID, intent_id: str, account_id: str) -> None:
    """Settle a payment whose webhook has not come, from Stripe's own record.

    A background task, so it runs once the response has been sent and the
    request's transaction has closed: Stripe is never called inside one
    (rule 6), and a slow Stripe never slows the poll. It runs in the API
    process, not Celery, because a worker that is down is one of the things
    this exists to survive.

    Throttled per payment in Redis, and fails open like the rate limiter: with
    Redis gone, an open tracking page asks Stripe on every poll, which is
    still well inside Stripe's limits and better than never asking.
    """
    try:
        claimed = runtime_redis().set(
            f"reconcile:payment:{payment_id}", "1", nx=True, ex=_RECONCILE_EVERY_SECONDS
        )
    except RedisError:
        claimed = True
    if not claimed:
        return

    try:
        status = reconcile_payment_intent(intent_id, account_id)
    except Exception:
        # Nothing to tell anyone: the response has gone, the page keeps
        # polling, and the next overdue poll tries again.
        log.warning("could not reconcile payment %s with Stripe", payment_id, exc_info=True)
        return
    log.info("reconciled payment %s from stripe: intent is %s", payment_id, status)


@router.get("/orders", response_model=list[OrderOut])
def list_my_orders(
    user: User = Depends(get_current_user),
    restaurant: Restaurant = Depends(current_restaurant),
    db: Session = TenantDb,
):
    """Order history, scoped to this restaurant by RLS and to this customer
    by ownership. Section 2.3: each restaurant sees only its own relationship
    with a customer."""
    orders = db.execute(
        select(Order)
        .where(Order.customer_user_id == user.id)
        .order_by(Order.created_at.desc())
        .limit(50)
        .options(selectinload(Order.items).selectinload(OrderItem.modifiers))
    ).scalars().all()

    payments = {
        p.order_id: p
        for p in db.execute(
            select(Payment).where(Payment.order_id.in_([o.id for o in orders]))
        ).scalars().all()
    } if orders else {}

    return [_serialize(o, payments.get(o.id), include_pin=True) for o in orders]
