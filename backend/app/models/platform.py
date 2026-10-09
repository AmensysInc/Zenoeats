"""Platform-level rows: the things that exist above any one restaurant.

A location is what a customer picks on the platform root before a storefront
exists for them. It is deliberately NOT the same row as a restaurant:

  * A location can be announced before it is a tenant at all. Three of the
    four we list have no restaurant behind them yet, and a `restaurants` row
    with no menu, no staff and no Stripe account would be a half-built tenant
    that the admin portal, the activation gate and the kitchen board all have
    to special-case.
  * A location's public name is its own. "Jr's Corner" is the name on the
    door; the tenant it opens is "Spice House". Collapsing the two would make
    renaming one rename the other.

So a location carries its own copy of what the picker shows, and points at a
restaurant only once there is one to point at.
"""

import enum
import uuid

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, uuid_pk


class LocationStatus(str, enum.Enum):
    """Whether a customer can order from this location today.

    Only OPEN is orderable, and only then if the restaurant behind it is
    itself ACTIVE -- the two are separate gates on purpose. A location stays
    OPEN while its restaurant is suspended for a billing problem, and the
    picker reports it as unavailable without the platform forgetting that the
    location exists.
    """

    OPEN = "OPEN"
    COMING_SOON = "COMING_SOON"
    # Announced, then pulled before opening. Kept rather than deleted so the
    # audit trail and any printed link survive.
    HIDDEN = "HIDDEN"


class Location(Base, TimestampMixin):
    """One row per place on the platform's front page.

    RLS is enabled and forced like every other table carrying a
    `restaurant_id`, but the policy here is deliberately public: this list is
    what an anonymous browser reads on the platform root, so there is no
    tenant in context to scope it to. Writes are the system role's alone. The
    migration says the same thing next to the policy.
    """

    __tablename__ = "locations"
    __table_args__ = (
        UniqueConstraint("slug", name="uq_locations_slug"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()

    # The URL key for this location on the platform root (/l/<slug>), which is
    # not the restaurant's subdomain and does not change when one is attached.
    slug: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)

    # Where it is, in the two lines the picker shows under the name.
    city: Mapped[str] = mapped_column(String(120), nullable=False)
    region: Mapped[str | None] = mapped_column(String(120), nullable=True)
    address_line: Mapped[str | None] = mapped_column(String(300), nullable=True)

    # One line of copy on the card. For a location that is not open yet this
    # is where "opening this spring" belongs.
    blurb: Mapped[str | None] = mapped_column(String(300), nullable=True)

    status: Mapped[str] = mapped_column(
        String(32), nullable=False, index=True, default=LocationStatus.COMING_SOON.value
    )

    # The tenant this location opens, once there is one. Null for a location
    # that has been announced but not built. RESTRICT rather than CASCADE: a
    # restaurant that is being deleted should not silently take the location
    # off the front page without someone deciding that.
    restaurant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("restaurants.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )

    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Card artwork, stored the way every other uploaded image is: a key into
    # the image service, not a URL.
    image_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
