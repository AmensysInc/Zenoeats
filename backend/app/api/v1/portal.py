"""Public portal: branding and menu for the resolved restaurant.

No authentication required. RLS still applies, so an unauthenticated read
cannot reach another tenant's catalog.
"""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext, TenantDb, current_restaurant, resolve_tenant
from app.config import settings
from app.core.ratelimit import per_ip
from app.models import Restaurant, RestaurantPaymentAccount
from app.schemas.api import BrandOut, MenuOut, PortalOut
from app.services import delivery, images, storefront
from app.services.menu import load_menu

router = APIRouter(tags=["portal"])


@router.get(
    "/portal",
    response_model=PortalOut,
    dependencies=[Depends(per_ip("portal", limit=240))],
)
def get_portal(
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
