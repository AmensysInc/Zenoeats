"""Public portal: branding and menu for the resolved restaurant.

No authentication required. RLS still applies, so an unauthenticated read
cannot reach another tenant's catalog.
"""

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext, TenantDb, current_restaurant, resolve_tenant
from app.config import settings
from app.core import google_oauth
from app.core.tenant import platform_url_for, storefront_url_for
from app.core.ratelimit import per_ip
from app.db.session import system_session
from app.models import Location, LocationStatus, Restaurant, RestaurantPaymentAccount
from app.schemas.api import BrandOut, LocationOut, LocationsOut, MenuOut, PortalOut
from app.services import delivery, images, storefront
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
        google_sign_in=google_oauth.configured(),
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
    """
    with system_session() as session:
        rows = session.execute(
            select(Location, Restaurant)
            .outerjoin(Restaurant, Location.restaurant_id == Restaurant.id)
            .where(Location.status != LocationStatus.HIDDEN.value)
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
