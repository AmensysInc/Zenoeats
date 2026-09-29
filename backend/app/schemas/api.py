from datetime import datetime, time
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator


# ---------- Portal / menu ------------------------------------------------

class OptionOut(BaseModel):
    id: UUID
    name: str
    price_delta_minor: int
    # What choosing this adds to the item's calories, or null where the
    # restaurant has stated no change. Counted as none either way.
    calories_delta: int | None = None
    is_available: bool
    # Where the browser loads the option's picture, or null when it has none.
    image_url: str | None = None


class ModifierGroupOut(BaseModel):
    id: UUID
    name: str
    selection_type: str
    is_required: bool
    min_select: int
    max_select: int
    options: list[OptionOut]


class ItemOut(BaseModel):
    id: UUID
    name: str
    # Which type, by id. The name is on the section or slot above it, so it is
    # not repeated per item -- and a rename then reaches every item at once
    # rather than needing a menu rebuild.
    item_type_id: UUID
    description: str | None
    # kcal as the restaurant states it, or null where it has not. A combo's
    # total is added up from the items chosen for it.
    calories: int | None = None
    base_price_minor: int
    currency: str
    is_available: bool
    # A URL ready for an img tag, or null. The storage key behind it stays on
    # the server: a customer has no use for it, and handing out keys would
    # tie the storefront to where the files happen to live today.
    image_url: str | None = None
    modifier_groups: list[ModifierGroupOut]
    # What this item comes with: chosen for the customer before they see it,
    # and priced at nothing. The browser pre-selects these and leaves them out
    # of its running total; the server does the same when it prices for real.
    included_option_ids: list[UUID] = []


class SubsectionOut(BaseModel):
    """One subcategory block inside a section: Burgers, under Food.

    The same shape as the section above it, minus the nesting, because there
    is none: two levels is the whole of it.
    """

    item_type_id: UUID
    label: str
    items: list[ItemOut]


class SectionOut(BaseModel):
    """One heading inside a meal period.

    Derived from the types of the items served in that period, not stored.
    There is no such thing as an empty section and nothing can be added to
    one directly -- an item of a new type creates its heading by existing.

    `label` is the restaurant's own word for the type, and the order sections
    appear in is the order it put its types in.

    `items` are the ones filed on the heading itself, and they come first on
    the page: a restaurant that subdivided only half its food still reads
    top to bottom. `groups` are its subcategories, each with its own
    subheading, in the order the restaurant put them in.

    Both can be empty, but never both at once -- a section with nothing under
    it either way is not built. Most menus will send groups as an empty list
    forever, which is the case this was designed around rather than against.
    """

    item_type_id: UUID
    label: str
    items: list[ItemOut]
    groups: list[SubsectionOut] = []


class ComboSlotOut(BaseModel):
    """One required choice inside a combo. Exactly one item, always."""

    id: UUID
    item_type_id: UUID
    label: str
    items: list[ItemOut]


class ComboOut(BaseModel):
    """A combo as a customer needs it: what to choose, and what it saves.

    The discount is sent rather than a finished price because there is no
    finished price until the choices are made. The browser previews a total
    from these numbers; the server prices the real one from the database when
    the order is placed, and only that one is charged.
    """

    id: UUID
    name: str
    description: str | None
    discount_kind: str
    discount_value: int
    # The deal's own photograph. Null means the page borrows one from an item
    # inside it, as every combo did before combos could carry their own.
    image_url: str | None = None
    slots: list[ComboSlotOut]


class MealOut(BaseModel):
    id: UUID
    name: str
    # The hours it is served, or both null where the restaurant has not said.
    # Wall clock in its own day, for a customer to read: nothing is gated on
    # them. An end at or before the start runs into the next day.
    starts_at: time | None = None
    ends_at: time | None = None
    sections: list[SectionOut]
    combos: list[ComboOut] = []

    @field_serializer("starts_at", "ends_at")
    def _hhmm(self, value: time | None) -> str | None:
        """HH:MM, not HH:MM:SS.

        The seconds are always zero -- nothing sets them -- and the browser's
        time input round-trips the short form, so sending the long one would
        have the builder echo back something nobody typed.
        """
        return None if value is None else value.strftime("%H:%M")


from app.schemas.storefront import StorefrontOut


class BrandOut(BaseModel):
    """The restaurant's mark and name as customers see them.

    Set in Settings and shown whether or not storefront customization is on:
    it is the restaurant's identity, not a theme. With no logo the header
    falls back to the initial; with no name image, to the name in `name_font`.
    """

    logo_url: str | None = None
    name_image_url: str | None = None
    name_font: str = "default"


class PortalOut(BaseModel):
    storefront: StorefrontOut | None = None
    brand: BrandOut = BrandOut()
    pickup_address: str | None = None
    # How a customer reaches the restaurant about an order.
    phone: str | None = None
    restaurant_id: UUID
    slug: str
    name: str
    tagline: str | None
    currency: str
    is_orderable: bool
    accepting_orders: bool
    stripe_publishable_key: str
    stripe_account_id: str | None
    # Whether checkout offers delivery at all. Not whether an address can be
    # delivered to -- that needs the address, and is the quote's answer.
    delivery_offered: bool = False
    # The live delivery map. The key is a browser key -- public by design and
    # restricted to this site in Google's console. Null when there is no map.
    maps_browser_key: str | None = None
    # How the map colours itself: one of the styles the storefront can draw,
    # or null for Google's own. See services/maps.py.
    map_style_key: str | None = None
    # Whether the pins on that map take the restaurant's palette. Off leaves
    # them in the platform's colours, which a brand the colour of a road is
    # better served by.
    map_pins_themed: bool = True


class MenuOut(BaseModel):
    meals: list[MealOut]


# ---------- Cart / orders ------------------------------------------------

class CartModifierIn(BaseModel):
    option_id: UUID
    quantity: int = Field(default=1, ge=1, le=20)


class CartLineIn(BaseModel):
    menu_item_id: UUID
    quantity: int = Field(ge=1, le=50)
    note: str | None = Field(default=None, max_length=280)
    modifiers: list[CartModifierIn] = Field(default_factory=list)


class ComboSelectionIn(BaseModel):
    """What fills one slot of one combo, and how it was changed."""

    slot_id: UUID
    menu_item_id: UUID
    modifiers: list[CartModifierIn] = Field(default_factory=list)


class CartComboIn(BaseModel):
    """One combo in the cart, with a choice for every slot.

    No price is sent, here or anywhere else. The client says which combo and
    which choices; what that costs is the server's answer, recomputed on the
    quote and again on the order.
    """

    combo_id: UUID
    quantity: int = Field(ge=1, le=50)
    note: str | None = Field(default=None, max_length=280)
    selections: list[ComboSelectionIn] = Field(default_factory=list)


FulfillmentChoice = Literal["PICKUP", "DELIVERY"]


class QuoteIn(BaseModel):
    """Preview pricing. No order is created and no money moves."""

    # Both default to empty because either alone is a real cart: a customer
    # can order nothing but a meal deal. Refusing an empty cart is pricing's
    # job, once, rather than a rule each field states differently.
    items: list[CartLineIn] = Field(default_factory=list)
    combos: list[CartComboIn] = Field(default_factory=list)
    # A delivery is priced from the address, never from a fee the browser
    # names. Ignored for a collection.
    fulfillment_type: FulfillmentChoice = "PICKUP"
    delivery_address: str | None = Field(default=None, max_length=300)


def _collapse(value: str) -> str:
    return " ".join(value.split())


class ContactIn(BaseModel):
    """Who is ordering, as checkout requires it.

    All three are mandatory. The email is not here: it is the account's, or
    the one a guest gave when their session began, and a receipt goes to the
    address that identity holds rather than to whatever a form last said.
    """

    full_name: str = Field(max_length=160)
    phone: str = Field(max_length=32)
    address: str = Field(max_length=300)

    @field_validator("full_name")
    @classmethod
    def _named(cls, value: str) -> str:
        cleaned = _collapse(value)
        if not cleaned:
            raise ValueError("Enter your name.")
        return cleaned

    @field_validator("phone")
    @classmethod
    def _callable(cls, value: str) -> str:
        # Not a full numbering-plan check: a restaurant only needs a number a
        # driver can ring, and people write those with spaces, dashes and
        # brackets. What this refuses is the number nobody could dial.
        cleaned = _collapse(value)
        if any(c not in "0123456789+-(). " for c in cleaned) or "+" in cleaned[1:]:
            raise ValueError("Enter a phone number using digits only.")
        digits = sum(c.isdigit() for c in cleaned)
        if not 7 <= digits <= 15:
            raise ValueError("Enter a phone number a driver could call.")
        return cleaned

    @field_validator("address")
    @classmethod
    def _addressed(cls, value: str) -> str:
        return _collapse(value)


class ProfileContactIn(ContactIn):
    """Saved profile details always include an address for future delivery."""

    @model_validator(mode="after")
    def _has_address(self):
        if len(self.address) < 5:
            raise ValueError("Enter your address.")
        return self


class CreateOrderIn(BaseModel):
    items: list[CartLineIn] = Field(default_factory=list)
    combos: list[CartComboIn] = Field(default_factory=list)
    customer_note: str | None = Field(default=None, max_length=500)
    contact: ContactIn
    # A guest may correct their receipt destination for this order. This never
    # identifies an account, changes its owner, or changes earlier receipts.
    guest_email: str | None = Field(default=None, max_length=320)

    @field_validator("guest_email")
    @classmethod
    def _guest_receipt(cls, value: str | None) -> str | None:
        return GuestSessionIn._plausible_address(value) if value is not None else None

    # A delivery goes to contact.address: one address, typed once. Priced
    # again here from that address, whatever the quote said.
    fulfillment_type: FulfillmentChoice = "PICKUP"
    # Client-computed total, echoed back for a consistency check only. The
    # server total always wins; a mismatch returns PRICE_CHANGED so the
    # customer re-confirms rather than being silently charged a new amount.
    expected_total_minor: int | None = None

    @model_validator(mode="after")
    def _delivery_has_address(self):
        if self.fulfillment_type == "DELIVERY" and len(self.contact.address) < 5:
            raise ValueError("Enter your delivery address.")
        return self


class GuestSessionIn(BaseModel):
    """Ordering without an account. The least we can ask for and still get a
    receipt to a real person and a name the counter can call out."""

    email: str = Field(min_length=3, max_length=320)
    full_name: str | None = Field(default=None, max_length=160)

    @field_validator("email")
    @classmethod
    def _plausible_address(cls, value: str) -> str:
        # Not a full RFC check -- nothing short of sending a message proves an
        # address, and a guest's is never verified anyway. This only catches
        # the typo that would otherwise become a silently undeliverable
        # receipt, and is the same shape Stripe will accept as receipt_email.
        cleaned = value.strip().lower()
        local, _, domain = cleaned.partition("@")
        if (not local or "." not in domain or domain.startswith(".") or domain.endswith(".")
                or cleaned.count("@") != 1 or any(c.isspace() for c in cleaned)):
            raise ValueError("Enter an email address we can send your receipt to.")
        return cleaned


class GuestSessionOut(BaseModel):
    """Deliberately thin. The session itself is the httpOnly cookie set
    alongside this; nothing here is worth a client holding on to."""

    email: str
    full_name: str | None = None


class CustomerSessionOut(BaseModel):
    """Who is ordering, for the storefront header and the checkout guard.

    is_guest is the part the UI acts on: a guest is offered an account, and
    is warned that this browser is the only thing holding their order.

    phone and address are what checkout saved last time, so a returning
    customer is not asked twice. email_pending is a signed-in customer whose
    address Clerk has not told us yet: checkout cannot proceed without one.
    """

    email: str
    full_name: str | None = None
    phone: str | None = None
    address: str | None = None
    email_pending: bool = False
    is_guest: bool
    # Whether this customer has agreed to the terms in force. False for an
    # account that reached us through a social provider without passing a
    # consent step -- Clerk finishes those itself when it has everything it
    # needs, and asks us for nothing. The app puts the consent form in front
    # of them rather than assuming the sign-up page was where they came from.
    terms_accepted: bool = False


class AmountsOut(BaseModel):
    subtotal_minor: int
    discount_minor: int
    # Zero on a collection. Defaulted so an idempotent replay stored before
    # delivery existed still reads back.
    delivery_fee_minor: int = 0
    tax_minor: int
    total_minor: int


class QuoteOut(BaseModel):
    currency: str
    amounts: AmountsOut
    # How far, for a delivery. Null on a collection.
    delivery_miles: float | None = None


class OrderModifierOut(BaseModel):
    group_name: str
    option_name: str
    unit_price_delta_minor: int
    quantity: int


class OrderItemOut(BaseModel):
    """One line of an order. Lines that came from a combo carry its name and
    a group number, so a receipt and a kitchen ticket can show a meal deal as
    one thing rather than as unrelated food that happened to be cheap."""

    name: str
    combo_name: str | None = None
    combo_group: int | None = None
    quantity: int
    unit_price_minor: int
    line_total_minor: int
    item_note: str | None
    modifiers: list[OrderModifierOut]


class MapPointOut(BaseModel):
    latitude: float
    longitude: float


class DriverLocationOut(MapPointOut):
    heading: float | None = None
    recorded_at: datetime


class TrackingStepOut(BaseModel):
    # PAID, DRIVER_ASSIGNED, READY, PICKED_UP, DELIVERED -- in that order,
    # each present once it has happened.
    step: str
    at: datetime


class TrackingOut(BaseModel):
    """A delivery, as its customer follows it.

    The driver's position is only ever here while the order is on the road,
    and only the driver's first name is. Coordinates are borrowed, not kept:
    the restaurant's are its own, the customer's come from the geocoding
    cache, the driver's from a few minutes of Redis.
    """

    steps: list[TrackingStepOut]
    driver_name: str | None = None
    restaurant: MapPointOut | None = None
    destination: MapPointOut | None = None
    driver_location: DriverLocationOut | None = None
    eta_seconds: int | None = None
    eta_computed_at: datetime | None = None


class OrderOut(BaseModel):
    order_id: UUID
    order_number: int
    status: str
    payment_status: str
    fulfillment_type: str
    currency: str
    amounts: AmountsOut
    items: list[OrderItemOut]
    # Only ever returned to the owning customer or authorized staff, and only
    # once the order is paid.
    pickup_pin: str | None = None
    delivery_address: str | None = None
    # Delivery orders once paid; null for a collection.
    tracking: TrackingOut | None = None
    expires_at: datetime | None = None
    created_at: datetime
    # True when the restaurant cancelled a paid order, as opposed to a payment
    # that was never completed.
    cancelled_by_restaurant: bool = False
    # What is on its way back to the customer's card, once a refund has been
    # issued. Null while nothing has been refunded.
    refund_minor: int | None = None


class PaymentIntentOut(BaseModel):
    order_id: UUID
    payment_id: UUID
    client_secret: str
    stripe_account_id: str
    publishable_key: str
    payment_status: str
    # The customer's email, for the payment's billing details. Null when
    # there is no real address yet.
    customer_email: str | None = None


# ---------- Admin --------------------------------------------------------

def _known_timezone(value: str | None) -> str | None:
    """An IANA timezone name, like America/Chicago, or a refusal.

    Stored unchecked, a typo only surfaced as a restaurant whose reports could
    not say what "today" was.
    """
    if value is None:
        return value
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError(f"{value} is not a timezone. Use a name like America/Chicago.")
    return value


# The restaurant portal validates its own timezone field with the same rule,
# and a shared rule should not be reached for through a private name.
known_timezone = _known_timezone


class CreateRestaurantIn(BaseModel):
    slug: str = Field(min_length=2, max_length=80, pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str = Field(min_length=1, max_length=160)
    timezone: str = "America/Chicago"
    # Bounded as the columns are, so a bad value is a 422 naming the field
    # rather than a 500 from the database after the request got that far.
    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")
    tax_rate_bps: int = Field(default=0, ge=0, le=3000)
    tagline: str | None = Field(default=None, max_length=200)
    admin_email: str | None = Field(default=None, max_length=320)

    _timezone = field_validator("timezone")(_known_timezone)


class StaffLoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=256)


class ChangePasswordIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    # 12 characters is the floor for an account that can read a restaurant's
    # revenue and mark orders collected.
    new_password: str = Field(min_length=12, max_length=256)


class StaffMeOut(BaseModel):
    storefront_customization_enabled: bool = False
    user_id: UUID
    email: str
    full_name: str | None
    role_code: str
    must_change_password: bool
    # Which restaurant this session is scoped to. The portal puts it in the
    # header, where the page name used to sit: the name told an operator
    # nothing the highlighted tab was not already saying, and on a phone with
    # two restaurants open it is the one thing worth knowing at a glance.
    restaurant_name: str
    # ACTIVE, or INVITED for someone who has signed in but not yet accepted.
    # An invited account can reach exactly two things -- this, and accepting --
    # so the portal shows the invitation instead of a wall of refusals.
    membership_status: str


class StaffPasswordResetOut(BaseModel):
    id: UUID
    email: str
    # Shown once to the restaurant admin to pass on; only its hash is kept.
    temporary_password: str
    # Whether it is also on its way to them by email.
    email_configured: bool = False


class StaffInviteOut(BaseModel):
    id: UUID
    email: str
    status: str
    # Set only when the invite created the person's login. Shown once to the
    # restaurant admin to pass on, like an owner's; never retrievable again.
    # Null means the address already has a staff login, which they keep using.
    temporary_password: str | None = None
    # Whether an invitation email is on its way. False when no email provider
    # is configured, so the portal can say so and the admin passes the
    # sign-in link on themselves, instead of being told it was emailed.
    email_configured: bool = False


class CreateOwnerIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=320)
    full_name: str | None = Field(default=None, max_length=160)


class CreateOwnerOut(BaseModel):
    user_id: UUID
    email: str
    # Returned once, when one is issued, and never retrievable again -- only
    # its argon2 hash is stored. The super admin passes it to the owner, who
    # is forced to replace it at first sign-in. None when the address already
    # has a login in use: that person keeps their own password.
    temporary_password: str | None
    # ACTIVE, or INVITED for an existing login that has to accept first.
    status: str = "ACTIVE"
    # Whether an invitation email is on its way, for an existing login that
    # was invited rather than issued a password. False when no email provider
    # is configured, so the portal does not claim one was sent.
    email_configured: bool = False


class UpdateRestaurantIn(BaseModel):
    """Every field optional: absent means "leave alone", which is what lets a
    caller clear the tagline by sending null without also blanking the rest.

    slug is deliberately absent. It is the tenant's public address -- it is in
    QR codes on tables, in printed menus and in customers' bookmarks -- so it
    is not an editable attribute. Moving a restaurant to a new subdomain is a
    migration, not a text edit.

    status is absent too: activate and suspend own that transition, and they
    enforce the readiness gate that a plain field write would bypass.
    """

    # Reject unknown fields rather than ignoring them. Without this, sending
    # slug or a mistyped key silently changes nothing and reports "No fields
    # to update", which reads like a client bug rather than a rejected field.
    model_config = ConfigDict(extra="forbid")

    storefront_customization_enabled: bool = False
    name: str | None = Field(default=None, min_length=1, max_length=160)
    tagline: str | None = Field(default=None, max_length=200)
    # Customers reach the restaurant on it; required before activation.
    phone: str | None = Field(default=None, max_length=32)
    timezone: str | None = Field(default=None, min_length=1, max_length=64)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    _timezone = field_validator("timezone")(_known_timezone)
    tax_rate_bps: int | None = Field(default=None, ge=0, le=3000)
    # FLAT applies tax_rate_bps; STRIPE_TAX calculates per order on the
    # restaurant's connected account, and needs the full pickup address.
    tax_mode: Literal["FLAT", "STRIPE_TAX"] | None = None
    tax_code: str | None = Field(default=None, pattern=r"^txcd_\d{8}$")
    address_line1: str | None = Field(default=None, max_length=200)
    address_line2: str | None = Field(default=None, max_length=200)
    address_city: str | None = Field(default=None, max_length=100)
    address_state: str | None = Field(default=None, max_length=100)
    address_postal_code: str | None = Field(default=None, max_length=20)
    address_country: str | None = Field(default=None, pattern=r"^[A-Za-z]{2}$")
    accepting_orders: bool | None = None


class RestaurantOut(BaseModel):
    storefront_customization_enabled: bool = False
    id: UUID
    slug: str
    name: str
    status: str
    currency: str
    tax_rate_bps: int
    accepting_orders: bool
    stripe_account_id: str | None
    charges_enabled: bool
    created_at: datetime
    # Needed so the admin edit form can show current values rather than
    # making the operator retype them.
    tagline: str | None = None
    phone: str | None = None
    timezone: str | None = None
    deleted_at: datetime | None = None
    tax_mode: str = "FLAT"
    tax_code: str | None = None
    address_line1: str | None = None
    address_line2: str | None = None
    address_city: str | None = None
    address_state: str | None = None
    address_postal_code: str | None = None
    address_country: str | None = None


class StripeSyncOut(BaseModel):
    """Connected account state after re-reading it from Stripe.

    disabled_reason and the outstanding requirement lists are included so the
    portal can say what is missing rather than only that something is.
    """

    stripe_account_id: str
    charges_enabled: bool
    payouts_enabled: bool
    details_submitted: bool
    onboarding_status: str
    disabled_reason: str | None = None
    currently_due: list[str] = []
    past_due: list[str] = []
    changed: bool = False
    # Apple Pay / Google Pay registration of the storefront's domain on this
    # account: {domain, apple_pay, google_pay, problem}. None when the account
    # cannot charge yet, or the domain is a development one.
    wallet_domain: dict | None = None


class AdminOrderOut(BaseModel):
    """One order as the platform is allowed to see it.

    Deliberately narrower than the restaurant's own view. zenoeats_system holds
    column-level SELECT grants on orders, and customer_note, pickup_pin_encrypted
    and customer_user_id are not among them -- so a platform operator answering a
    billing question cannot read what a customer wrote or the PIN that hands
    their food over. The database enforces that, not this class.
    """

    order_id: UUID
    order_number: int
    status: str
    total_minor: int
    tax_minor: int
    currency: str
    created_at: datetime
    paid_at: datetime | None
    expires_at: datetime | None
    payment_status: str | None
    stripe_payment_intent_id: str | None


class AdminOrderPageOut(BaseModel):
    restaurant_id: UUID
    slug: str
    total: int
    orders: list[AdminOrderOut]


class RestaurantReportOut(BaseModel):
    restaurant_id: UUID
    slug: str
    name: str
    status: str
    currency: str
    orders_paid: int
    gross_revenue_minor: int
    tax_collected_minor: int
    average_order_value_minor: int
    orders_pending_payment: int
    orders_expired: int
    last_order_at: datetime | None
