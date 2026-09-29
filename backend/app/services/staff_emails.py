"""The emails a restaurant's team gets, beyond the invitation.

    staff_welcome         to someone who has just accepted an invitation
    staff_joined          to whoever invited them, that they have joined
    staff_role_changed    to a team member whose role an admin changed
    staff_removed         to a team member an admin took off the team
    staff_password_reset  to a team member given a new temporary password
    refund_failed         to the restaurant's admins and managers, when a
                          cancellation went through but its refund did not

Each is sent once (notifications.send_once), keyed on the change it is about
and when it happened, so doing the same thing again later is a new email and
a task run twice is not.

Queued by the endpoint after its transaction commits and sent by the worker,
like every other email: a slow provider must never hold up the portal.

A temporary password travels to the worker sealed with the field key, as the
invitation's does, because the broker keeps what it holds on disk.
"""

import logging
from uuid import UUID

from sqlalchemy import select

from app.core import crypto
from app.db.session import system_session, tenant_session
from app.models import Order, Payment, Restaurant, RestaurantUser, StaffRole, StaffStatus, User
from app.services import email, email_templates, notifications

log = logging.getLogger(__name__)

KINDS = (
    "staff_welcome", "staff_joined", "staff_role_changed", "staff_removed",
    "staff_password_reset", "refund_failed",
)


def queue(kind: str, restaurant_id: UUID, **args) -> None:
    """Hand one of these emails to the worker, after the response is sent.

    Best effort: the change has been made, and a broker outage costs the
    email, not the change. A `temporary_password` is sealed before queueing.
    """
    from app.workers.tasks import send_staff_email

    if kind not in KINDS:
        raise ValueError(f"no such staff email: {kind}")
    password = args.pop("temporary_password", None)
    if password:
        args["sealed_password"] = crypto.encrypt_field(password)
    args = {k: str(v) if isinstance(v, UUID) else v for k, v in args.items()}
    try:
        send_staff_email.delay(kind, str(restaurant_id), args)
    except Exception:
        log.warning("could not queue the %s email", kind, exc_info=True)


def role_words(role_code: str) -> str:
    return notifications.ROLE_WORDS.get(role_code, role_code.lower())


def compose(kind: str, **context) -> tuple[str, str, str]:
    """(subject, html, text). The context is what the template names; see the
    comment at the top of each email.html."""
    if kind not in KINDS:
        raise ValueError(f"no such staff email: {kind}")
    out = email_templates.render(kind, **context)
    return out.subject, out.html, out.text


def _person(user_id) -> tuple[str, str | None] | None:
    """(address, first name) of a staff login, or None if there is none."""
    with system_session() as session:
        user = session.get(User, user_id)
        if user is None or not user.email:
            return None
        first = (user.full_name or "").split(" ")[0] or None
        return user.email, first


def _send(restaurant_id: UUID, key: str, to: str, kind: str, **context) -> bool:
    subject, body_html, body_text = compose(kind, **context)
    outcome = notifications.send_once(restaurant_id, key, email.Email(
        to=to, subject=subject, html=body_html, text=body_text, idempotency_key=key,
    ))
    return bool(outcome and outcome.sent)


def send(kind: str, restaurant_id: UUID, args: dict) -> int:
    """Send one of these emails. Returns how many went out now."""
    with tenant_session(restaurant_id) as session:
        restaurant = session.get(Restaurant, restaurant_id)
        if restaurant is None:
            return 0
        restaurant_name, slug = restaurant.name, restaurant.slug
    portal_url = notifications.storefront_url(slug, "/manage")
    sign_in_url = notifications.storefront_url(slug, "/manage/login")
    base = dict(restaurant_name=restaurant_name, portal_url=portal_url, sign_in_url=sign_in_url)

    if kind == "refund_failed":
        return _send_refund_failed(restaurant_id, args, base)

    if kind == "staff_password_reset":
        person = _person(UUID(args["user_id"]))
        if person is None:
            return 0
        sealed = args.get("sealed_password")
        return int(_send(
            restaurant_id, f"staff-password/{args['user_id']}/{args['at']}", person[0], kind,
            **base, name=person[1],
            temporary_password=crypto.decrypt_field(sealed) if sealed else None,
        ))

    # The rest are about one membership.
    with tenant_session(restaurant_id) as session:
        member = session.get(RestaurantUser, UUID(args["membership_id"]))
        if member is None:
            return 0
        status, role, user_id = member.status, member.role_code, member.user_id
        accepted_at, inviter_id = member.accepted_at, member.invited_by_user_id
        inviter_is_on_team = inviter_id is not None and inviter_id != user_id and (
            session.execute(
                select(RestaurantUser.id).where(
                    RestaurantUser.user_id == inviter_id,
                    RestaurantUser.status == StaffStatus.ACTIVE.value,
                )
            ).first() is not None
        )
    person = _person(user_id)
    if person is None:
        return 0
    to, name = person
    membership_id = args["membership_id"]

    if kind == "staff_welcome":
        if status != StaffStatus.ACTIVE.value or accepted_at is None:
            return 0
        return int(_send(
            restaurant_id, f"staff-welcome/{membership_id}/{accepted_at.isoformat()}", to, kind,
            **base, name=name, role=role_words(role),
        ))

    if kind == "staff_joined":
        # Only to someone still on this team: the super admin who set up an
        # owner is not, and neither is a manager who has since left.
        if status != StaffStatus.ACTIVE.value or accepted_at is None or not inviter_is_on_team:
            return 0
        inviter = _person(inviter_id)
        if inviter is None:
            return 0
        return int(_send(
            restaurant_id, f"staff-joined/{membership_id}/{accepted_at.isoformat()}",
            inviter[0], kind,
            **base, name=inviter[1], member=name or to, role=role_words(role),
        ))

    if kind == "staff_role_changed":
        # Someone who has not joined yet is not told: the role an invitation
        # offers is what they will see when they accept it.
        if status != StaffStatus.ACTIVE.value or args["new_role"] != role:
            return 0
        return int(_send(
            restaurant_id,
            f"staff-role/{membership_id}/{args['new_role']}/{args['at']}", to, kind,
            **base, name=name, role=role_words(args["new_role"]),
            old_role=role_words(args["old_role"]),
        ))

    if kind == "staff_removed":
        if status != StaffStatus.REVOKED.value:
            return 0
        return int(_send(
            restaurant_id, f"staff-removed/{membership_id}/{args['at']}", to, kind,
            **base, name=name,
        ))

    raise ValueError(f"no such staff email: {kind}")


def _send_refund_failed(restaurant_id: UUID, args: dict, base: dict) -> int:
    """Tell the people who can issue refunds that one did not go through.

    The manager who cancelled saw it on the screen, and may have moved on in
    the middle of service. Every admin and manager hears, so the customer is
    not left waiting on a refund nobody remembers.
    """
    order_id = UUID(args["order_id"])
    with tenant_session(restaurant_id) as session:
        order = session.get(Order, order_id)
        payment = session.execute(
            select(Payment).where(Payment.order_id == order_id)
        ).scalar_one_or_none()
        if order is None or payment is None:
            return 0
        order_number, currency, amount = order.order_number, order.currency, payment.amount_minor
        people = session.execute(
            select(RestaurantUser.user_id).where(
                RestaurantUser.status == StaffStatus.ACTIVE.value,
                RestaurantUser.role_code.in_([StaffRole.ADMIN.value, StaffRole.MANAGER.value]),
            )
        ).scalars().all()

    sent = 0
    for user_id in people:
        person = _person(user_id)
        if person is None:
            continue
        sent += _send(
            restaurant_id, f"refund-failed/{order_id}/{args['at']}/{user_id}", person[0],
            "refund_failed",
            **base, name=person[1], order_number=order_number,
            amount=notifications.money(amount, currency), problem=args.get("problem"),
        )
    return sent
