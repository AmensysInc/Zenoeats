import enum
import uuid
from datetime import datetime

import uuid as _uuid

from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, DateTime, Float, ForeignKey, Integer,
    String, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, uuid_pk


class RestaurantStatus(str, enum.Enum):
    """Frozen RestaurantLifecycle values (Appendix A.1)."""
    DRAFT = "DRAFT"
    INACTIVE = "INACTIVE"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    ARCHIVED = "ARCHIVED"


class TaxMode(str, enum.Enum):
    FLAT = "FLAT"
    STRIPE_TAX = "STRIPE_TAX"


DEFAULT_TAX_CODE = "txcd_40060003"


class Restaurant(Base, TimestampMixin):
    """Tenant root. Request-path access is scoped by id = app.current_tenant."""

    __tablename__ = "restaurants"
    __table_args__ = (
        CheckConstraint(
            "brand_name_font IN ('default', 'lora', 'playfair', 'fraunces', "
            "'merriweather', 'dm_sans')",
            name="ck_restaurants_brand_name_font",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    slug: Mapped[str] = mapped_column(String(80), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True,
                                        default=RestaurantStatus.DRAFT.value)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="America/Chicago")
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")

    # How tax is worked out. FLAT applies tax_rate_bps to every order.
    # STRIPE_TAX calculates on the restaurant's own connected account, for the
    # pickup address below, so the right state and local rates apply and the
    # sales land in the restaurant's Stripe tax reports. See services/tax.py.
    tax_mode: Mapped[str] = mapped_column(String(16), nullable=False, default=TaxMode.FLAT.value)
    tax_rate_bps: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Stripe product tax code. txcd_40060003, "Food for Immediate
    # Consumption", covers prepared food, meals and dispensed drinks.
    tax_code: Mapped[str] = mapped_column(String(32), nullable=False, default=DEFAULT_TAX_CODE)

    # Where orders are picked up: where the sale happens, so where tax is
    # sourced. Required before a restaurant can use STRIPE_TAX.
    address_line1: Mapped[str | None] = mapped_column(String(200), nullable=True)
    address_line2: Mapped[str | None] = mapped_column(String(200), nullable=True)
    address_city: Mapped[str | None] = mapped_column(String(100), nullable=True)
    address_state: Mapped[str | None] = mapped_column(String(100), nullable=True)
    address_postal_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    address_country: Mapped[str | None] = mapped_column(String(2), nullable=True)

    # Presentation is dormant until the platform enables it. Null theme keeps
    # the original customer palette, including its distinct gold action.
    storefront_customization_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    theme: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    logo_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # How the name beside the logo is shown on customer pages: an image of the
    # restaurant's own lettering when there is one, otherwise text in this
    # font. Keys are listed in services/restaurant_profile.BRAND_NAME_FONTS.
    brand_name_image_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    brand_name_font: Mapped[str] = mapped_column(
        String(32), nullable=False, default="default", server_default="default"
    )
    banner_interval_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=5000, server_default="5000")
    # The delivery map on a customer's order page. The style names one the
    # platform configured (null is the platform's own); the pins follow the
    # restaurant's palette unless it prefers the plain ones.
    map_style_key: Mapped[str | None] = mapped_column(String(32), nullable=True)
    map_pins_themed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )

    # Branding
    tagline: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # How a customer reaches the restaurant about an order: on the order page
    # and in every order email. Required to activate (admin.activate).
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    accepting_orders: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # --- Delivery -------------------------------------------------------
    # Off until a restaurant has both a placed address and at least one ring
    # to charge for. A customer is only offered delivery when this is true.
    delivery_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # The pickup address, placed on a map: where every delivery distance is
    # measured from. Null until it has been geocoded.
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    # The address those coordinates came from. Kept so a moved restaurant is
    # detectable: an address edited without re-placing it would otherwise go
    # on charging distances from where it used to be.
    geocoded_address: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Whether a delivery fee is taxed, for restaurants on a flat rate. Varies
    # by state, so it is the restaurant's to answer, like the rate itself.
    # Restaurants on Stripe Tax never read it: Stripe is told the shipping
    # amount and decides taxability for the jurisdiction itself.
    delivery_fee_taxable: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )

    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    @property
    def delivery_origin_is_current(self) -> bool:
        """Whether the coordinates still describe where this restaurant is.

        Compared against the address as it reads now, so moving premises --
        or fixing a typo in the street -- shows up as an origin that needs
        placing again rather than as quietly wrong distances.
        """
        return (
            self.latitude is not None
            and self.longitude is not None
            and self.geocoded_address == self.pickup_address_line
        )

    @property
    def pickup_address_line(self) -> str:
        """The pickup address as one line, in the order a postal service
        expects. Empty when there is no street to build one from."""
        if not self.address_line1:
            return ""
        parts = [
            self.address_line1, self.address_line2, self.address_city,
            self.address_state, self.address_postal_code, self.address_country,
        ]
        return ", ".join(part.strip() for part in parts if part and part.strip())

    @property
    def is_orderable(self) -> bool:
        return (
            self.status == RestaurantStatus.ACTIVE.value
            and self.accepting_orders
            and self.deleted_at is None
        )


class DeliveryZone(Base, TimestampMixin):
    """One ring of a restaurant's delivery area, and what it costs.

    A ring is described by its outer edge alone: zones sorted by max_miles
    make a set of bands, where the first covers everything up to its edge and
    each one after it covers the gap from the previous edge to its own. The
    restaurant types "3 miles, $4" rather than "0 to 3 miles, $4", because the
    inner edge is never a free choice -- a gap between rings would be an
    address the restaurant can neither charge for nor refuse.

    Beyond the outermost ring is not free delivery: it is no delivery.
    """

    __tablename__ = "delivery_zones"
    __table_args__ = (
        CheckConstraint("max_miles > 0", name="ck_delivery_zone_miles"),
        CheckConstraint("fee_minor >= 0", name="ck_delivery_zone_fee"),
        # Two rings with the same edge would make the band between them empty
        # and the cheaper one unreachable.
        UniqueConstraint("restaurant_id", "max_miles", name="uq_delivery_zone_edge"),
    )

    id: Mapped[_uuid.UUID] = uuid_pk()
    restaurant_id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("restaurants.id"), nullable=False, index=True
    )
    # The outer edge, in miles. Two decimal places: restaurants think in
    # halves and quarters of a mile, not in metres.
    max_miles: Mapped[float] = mapped_column(Float, nullable=False)
    # Charged on top of the food. Minor units, like every other amount here.
    fee_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
