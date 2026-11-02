"""Public portal: branding and menu for the resolved restaurant.

No authentication required. RLS still applies, so an unauthenticated read
cannot reach another tenant's catalog.
"""

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import (
    TenantContext, TenantDb, current_restaurant, get_current_user,
    optional_current_user, resolve_tenant,
)
from app.config import settings
from app.core.tenant import platform_url_for, storefront_url_for
from app.core.ratelimit import per_ip
from app.db.session import system_session
from app.models import (
    Location, LocationStatus, Restaurant, RestaurantPaymentAccount, RestaurantStatus,
    User,
)
from app.schemas.api import (
    BrandOut, InvitationOut, InviteIn, LocationOut, LocationsOut, LoyaltyOut,
    LoyaltyProgramOut, MenuOut, OfferOut, OffersOut, PortalOut, ReferralOut,
    RewardChoiceOut, SpendableRewardOut,
)
from app.services import (
    delivery, images, loyalty, offers as offers_service,
    referrals as referrals_service, storefront,
)
from app.services.menu import load_menu

router = APIRouter(tags=["portal"])


@router.get(
    "/portal",
    response_model=PortalOut,
    dependencies=[Depends(per_ip("portal", limit=240))],
)
def get_portal(
    request: Request,
    tenant: TenantContext = Depends(resolve_tenant),
    restaurant: Restaurant = Depends(current_restaurant),
    db: Session = TenantDb,
):
    account = db.execute(select(RestaurantPaymentAccount)).scalar_one_or_none()
    return PortalOut(
        storefront=storefront.public(db, restaurant),
        brand=BrandOut(
            logo_url=images.image_url(restaurant.logo_path),
            name_image_url=images.image_url(restaurant.brand_name_image_path),
            name_font=restaurant.brand_name_font,
        ),
        restaurant_id=restaurant.id,
        slug=restaurant.slug,
        name=restaurant.name,
        pickup_address=restaurant.pickup_address_line or None,
        phone=restaurant.phone,
        tagline=restaurant.tagline,
        platform_url=platform_url_for(request),
        currency=restaurant.currency,
        is_orderable=restaurant.is_orderable,
        accepting_orders=restaurant.accepting_orders,
        stripe_publishable_key=settings.STRIPE_PUBLISHABLE_KEY,
        stripe_account_id=account.stripe_account_id if account else None,
        delivery_offered=delivery.offered(restaurant),
        maps_browser_key=settings.GOOGLE_MAPS_BROWSER_KEY or None,
        map_style_key=restaurant.map_style_key,
        map_pins_themed=restaurant.map_pins_themed,
    )


@router.get(
    "/menu",
    response_model=MenuOut,
    dependencies=[Depends(per_ip("menu", limit=240))],
)
def get_menu(
    restaurant: Restaurant = Depends(current_restaurant),
    db: Session = TenantDb,
):
    """The sellable menu.

    A meal period serving nothing is dropped: a customer has nothing to do
    with an empty heading. The restaurant portal reads the same tree unpruned
    from /restaurant/menu.
    """
    return load_menu(db, include_empty=False)


@router.get(
    "/locations",
    response_model=LocationsOut,
    dependencies=[Depends(per_ip("locations", limit=240))],
)
def get_locations(request: Request):
    """Every location the platform root offers, open or not.

    Served on any host, including the bare root domain where there is no
    tenant to resolve -- which is the whole point, since this is what the root
    renders instead of RESTAURANT_NOT_FOUND. That is also why it reads through
    the system session rather than the tenant one: a tenant-scoped read would
    match no policy here and come back empty on the only page that wants it.

    HIDDEN rows are dropped. A location pulled before opening keeps its row
    for the audit trail, but it is not something to show a customer.

    So is a location whose restaurant is not live -- a draft still being set
    up, or one suspended. The front page lists the stores that are open for
    business, and one appears the moment the admin activates it (activation
    makes or opens its location; see admin.activate_restaurant). A restaurant
    that is live but has paused orders stays, shown as unavailable: that is
    a closed door, not a store that is not there. Locations with no
    restaurant behind them are announcements and are still listed.
    """
    with system_session() as session:
        rows = session.execute(
            select(Location, Restaurant)
            .outerjoin(Restaurant, Location.restaurant_id == Restaurant.id)
            .where(Location.status != LocationStatus.HIDDEN.value)
            .where(
                Location.restaurant_id.is_(None)
                | (
                    (Restaurant.status == RestaurantStatus.ACTIVE.value)
                    & Restaurant.deleted_at.is_(None)
                )
            )
            .order_by(Location.sort_order, Location.name)
        ).all()

        out: list[LocationOut] = []
        for location, restaurant in rows:
            # Two gates, deliberately separate: the location is open, and the
            # restaurant behind it is live. A suspended tenant reads as
            # unavailable rather than as a card that fails after the click.
            orderable = (
                location.status == LocationStatus.OPEN.value
                and restaurant is not None
                and restaurant.is_orderable
            )
            out.append(
                LocationOut(
                    slug=location.slug,
                    name=location.name,
                    city=location.city,
                    region=location.region,
                    address_line=location.address_line,
                    blurb=location.blurb,
                    status=location.status,
                    is_orderable=orderable,
                    storefront_url=(
                        storefront_url_for(request, restaurant.slug)
                        if orderable and restaurant is not None
                        else None
                    ),
                    image_url=images.image_url(location.image_path),
                )
            )
    return LocationsOut(locations=out)


@router.get(
    "/rewards",
    response_model=LoyaltyOut,
    dependencies=[Depends(per_ip("rewards", limit=240))],
)
def get_rewards(
    user: User | None = Depends(optional_current_user),
    db: Session = TenantDb,
):
    """This restaurant's loyalty programs, and the caller's cards.

    Answers for an anonymous caller rather than refusing one: the programs
    are what persuade somebody to make an account, so they have to be
    readable before they have one. Such a caller gets every card at zero and
    `collecting: false`, which the storefront renders as an invitation
    rather than as progress.

    A guest is in the same position, and structurally rather than by policy:
    a guest users row is made per checkout and reachable only by the cookie
    that made it, so there is no card to add a stamp to.
    """
    collecting = loyalty.collects(user)
    cards = loyalty.progress(db, user)
    held = loyalty.spendable(db, user)

    # The dishes each program pays out in, read once per program rather than
    # once per card: a customer holding two rewards on one program is offered
    # the same list twice.
    choices: dict = {}
    for card in cards:
        choices[card.program_id] = [
            RewardChoiceOut(
                item_id=choice.item_id,
                item_name=choice.item_name,
                image_url=images.image_url(choice.image_path),
                is_available=choice.is_available,
            )
            for choice in loyalty.choices_for(db, card.program_id)
        ]

    return LoyaltyOut(
        collecting=collecting,
        programs=[
            LoyaltyProgramOut(
                program_id=card.program_id,
                name=card.name,
                required_count=card.required_count,
                current_count=card.current_count,
                rewards_ready=card.rewards_ready,
                choices=choices.get(card.program_id, []),
            )
            for card in cards
        ],
        rewards=[
            SpendableRewardOut(
                id=reward.id,
                program_id=reward.program_id,
                expires_at=reward.expires_at,
                choices=choices.get(reward.program_id, []),
            )
            for reward in held
        ],
    )


@router.get(
    "/offers",
    response_model=OffersOut,
    dependencies=[Depends(per_ip("offers", limit=240))],
)
def get_offers(db: Session = TenantDb):
    """What this restaurant is advertising right now.

    Public and unauthenticated, like the menu: an offer is a poster in the
    window and the whole point is that a passer-by reads it. Only the ones
    inside their window are returned, so the storefront never has to know
    the rules about dates.
    """
    return OffersOut(
        offers=[
            OfferOut(
                id=offer.id,
                title=offer.title,
                body=offer.body,
                image_url=images.image_url(offer.image_path),
                starts_at=offer.starts_at,
                ends_at=offer.ends_at,
            )
            for offer in offers_service.live(db)
        ]
    )


@router.get(
    "/referrals",
    response_model=ReferralOut,
    dependencies=[Depends(per_ip("referrals", limit=120))],
)
def get_referrals(
    request: Request,
    restaurant: Restaurant = Depends(current_restaurant),
    user: User | None = Depends(optional_current_user),
    db: Session = TenantDb,
):
    """The customer's referral link, and how their invitations have gone.

    The link is made on first read rather than by a separate step: a page
    that asks you to press a button before it will show you your own link
    has added a step for nothing.

    Answers for an anonymous caller with `collecting: false` and no link, so
    the page can explain the offer to somebody deciding whether to make an
    account.
    """
    offer = referrals_service.program(db)
    if offer is None:
        return ReferralOut(running=False, collecting=False)

    collecting = loyalty.collects(user)
    reward = ", ".join(c.item_name for c in loyalty.choices_for(db, offer.id)) or None
    if not collecting:
        return ReferralOut(running=True, collecting=False, reward_name=reward)

    code = referrals_service.code_for(db, restaurant.id, user)
    return ReferralOut(
        running=True,
        collecting=True,
        code=code,
        # Built from the request so it is the host this browser actually
        # reached, the same reason the location picker stopped using a
        # template -- see core/tenant.storefront_url_for.
        share_url=f"{storefront_url_for(request, restaurant.slug)}/?ref={code}",
        reward_name=reward,
        invitations=[
            InvitationOut(
                id=i.id,
                invitee_name=i.invitee_name,
                status=i.status,
                qualified_at=i.qualified_at,
            )
            for i in referrals_service.invitations(db, user)
        ],
    )


@router.post(
    "/referrals/invite",
    response_model=ReferralOut,
    status_code=201,
    dependencies=[Depends(per_ip("referral-invite", limit=60))],
)
def record_invite(
    request: Request,
    body: InviteIn,
    restaurant: Restaurant = Depends(current_restaurant),
    user: User = Depends(get_current_user),
    db: Session = TenantDb,
):
    """Note down a friend the customer is inviting.

    Writes a row and sends nothing. The storefront then hands the customer a
    prefilled message for their own phone to send, which is the whole of the
    delivery mechanism -- see services/referrals.py.
    """
    referrals_service.invite(db, restaurant.id, user, name=body.name, phone=body.phone)
    return get_referrals(request, restaurant, user, db)


@router.post(
    "/referrals/claim",
    dependencies=[Depends(per_ip("referral-claim", limit=60))],
)
def claim_referral(
    code: str,
    restaurant: Restaurant = Depends(current_restaurant),
    user: User = Depends(get_current_user),
    db: Session = TenantDb,
):
    """Attach an arriving customer to whoever invited them.

    Answers the same way whether or not the claim stuck. Whether somebody
    has ordered here before is their business and not the caller's, and
    spelling out why a claim failed would describe the rules to the one
    person trying to get round them.
    """
    referrals_service.claim(db, restaurant.id, code, user)
    return {"ok": True}
