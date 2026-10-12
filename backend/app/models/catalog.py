"""Menu structure.

    ItemType      "Food", "Drinks", "Tiffins" -- the restaurant's own words
    Item          the sellable thing, owned by the restaurant, not by a meal
      item_type   which of those it is
      <- ItemModifierGroup ->
        ModifierGroup     "Veggies", "Ice level", "Sauce add-ons"
          ModifierOption  "Lettuce" (+$0.00), "Light" / "Regular" / "Heavy"
      <- ItemIncludedOption ->  what this item comes with, at no charge

    Meal          Breakfast / Lunch / Dinner -- a meal period
      <- MealItem ->  the items served during it

    Combo         "Burger Meal", sold during one meal period
      ComboSlot         one per kind: a food, a drink, a side
        ComboSlotItem   the items that may fill that slot

An item is defined once and served in as many meal periods as it is sold in.
Black Coffee on both Breakfast and Lunch is one row with one price and one
sold-out toggle, so a price correction reaches every period at once and the
kitchen cannot mark it sold out in one place and in stock in another.

Categories used to sit between a meal and its items, carrying both a name and
a kind. They are gone. The name was doing nothing a heading could not: a
"Coffee" category holding two coffees is the same list as two coffees typed
as drinks, and it forced an item to be re-created to appear in a second meal
period. The type moved onto the item itself, where it always belonged -- it
describes the thing, not the shelf -- and the storefront groups by it.

The types themselves were four hard-coded words. A restaurant selling tiffins,
thalis and chaat had to file all of them under "Food" and read someone else's
vocabulary back on its own menu. They are rows now, per restaurant, named and
ordered by the people whose menu it is.

A type may name a parent, and that is the whole of the nesting: Food holds
Burgers and Nuggets, Drinks holds Hot Beverages. Two levels and no more, so
there is no tree to walk anywhere. The child is a heading on the storefront
and nothing else -- combos and the modifier-group filter both read the root,
which is what lets a "pick a food" slot offer burgers and nuggets together
instead of splitting into two slots that can never be filled at once.

Leaving the parent blank is the ordinary case. A restaurant with eight items
under Food and no wish to subdivide them writes no subcategories and reads
exactly the menu it read before.

What an item comes with is a property of the item, not of the option. A
burger includes lettuce and onion; a salad built from the same Veggies group
includes something else entirely, and the option itself cannot know which it
is on. So inclusion is a join from the item, and it does two things at once:
the option arrives already chosen, and it costs nothing on that item even
where the same option is charged on another.

Modifier groups are restaurant-level and reusable, attached to items through
the ItemModifierGroup join. A restaurant with twenty drinks defines "Ice
level" once. The item types a group is offered for pre-filter the library so
adding a drink surfaces Ice level rather than Veggies -- and, because it is a
list, a Size group can surface on both drinks and sides.
"""

import enum
import uuid
from datetime import datetime, time

from sqlalchemy import (
    Boolean, CheckConstraint, DateTime, ForeignKey, Integer,
    String, Text, Time, UniqueConstraint, false, func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, uuid_pk


# The vocabulary a new restaurant starts with, in the order it reads. Not a
# constraint and not a default anything falls back to: four rows are created
# with the restaurant, and from that moment they are the restaurant's to
# rename, reorder or delete like any other.
STARTER_ITEM_TYPES = ["Food", "Drinks", "Sides", "Sauces"]


class ItemType(Base, TimestampMixin):
    """What sort of thing an item is, in the restaurant's own words.

    Decides the heading an item appears under on the storefront, which
    modifier groups the builder offers for it, and which slot of a combo it
    can fill.

    A row rather than an enum because "Food, Drinks, Sides, Sauces" is one
    restaurant's menu, not a rule about menus. A tiffin house wants Tiffins
    and Thalis; a coffee bar wants Espresso and Filter. Reading a stranger's
    vocabulary back off your own menu is the kind of small wrongness nobody
    reports and everybody notices.

    Ordering is the restaurant's too. sort_order is what the storefront reads
    down the page, so "Drinks before Food" is a decision the menu can express
    rather than one baked into a dictionary in the service layer. Among
    children it orders them inside their parent, not across the whole menu.

    parent_id makes it two levels: Food holding Burgers and Nuggets. A child
    is a subheading and nothing more. Everything structural -- which combo
    slot an item can fill, which modifier groups the builder offers -- reads
    the root through root_type_id below, so subdividing a menu never splits
    a combo or duplicates a modifier group.

    Depth is capped at two, and by the database rather than by a promise: see
    the composite foreign key in migration 0009. A parent that could itself
    have a parent would make every one of those reads a recursive walk, and
    "Food > Burgers > Smash" is not a thing a menu heading needs to say.
    """

    __tablename__ = "item_types"

    id: Mapped[uuid.UUID] = uuid_pk()
    restaurant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("restaurants.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(60), nullable=False)
    # Null for a top-level type, which is most of them. Names stay unique
    # across the whole restaurant rather than within a parent: the item form
    # offers one flat list, and two entries reading "Regular" under different
    # parents would be a menu nobody can file against.
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("item_types.id"), nullable=True, index=True
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    image_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    show_in_shortcuts: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")

    parent: Mapped["ItemType | None"] = relationship(remote_side=[id])

    @property
    def root_id(self) -> uuid.UUID:
        """This type if it is top-level, otherwise its parent.

        The one place the two levels collapse back into one. Depth is capped
        at two, so this is a single hop and never a loop.
        """
        return self.parent_id or self.id


class SelectionType(str, enum.Enum):
    SINGLE = "SINGLE"   # radio. Ice level: light / regular / heavy.
    MULTI = "MULTI"     # checkbox. Veggies: lettuce, tomato, onion.


class DiscountKind(str, enum.Enum):
    """How a combo is cheaper than its parts.

    The value that goes with it is read differently by each, which is the
    reason they are one column and a kind rather than two nullable columns
    that could both be set:

      NONE     value ignored
      PERCENT  value is basis points, 1250 = 12.5%, as tax_rate_bps is
      AMOUNT   value is minor units off the total
      FIXED_PRICE  value is what the combo costs, in minor units, before
               each choice's upcharge and the options chosen (0050)

    Basis points rather than whole percent because that is already the
    vocabulary for a rate here, and it is the difference between being able
    to express 12.5% and not.
    """

    NONE = "NONE"
    PERCENT = "PERCENT"
    AMOUNT = "AMOUNT"
    FIXED_PRICE = "FIXED_PRICE"


# Ordering, and why every menu relationship below carries a tiebreaker.
#
# Rows are ordered by sort_order, and nothing sets it yet, so every row in a
# menu carries 0. A sort with nothing but ties has no defined result: Postgres
# returns tied rows in scan order, and an UPDATE rewrites the row at the end of
# the heap. Toggling one item sold out therefore moved it to the bottom of its
# list, on the storefront as much as in the builder.
#
# created_at breaks the tie into creation order, which is what a menu builder
# means by "the order I added them". Rows written in one transaction share a
# timestamp, because now() is the transaction clock rather than the statement
# clock, so id settles the remainder: arbitrary but fixed, which is the
# property that actually matters.


class Meal(Base, TimestampMixin):
    """A meal period. It holds no items of its own; it points at them.

    It may also say the hours it is served, for a customer to read. Those are
    wall-clock times in the restaurant's own day and nothing enforces them:
    ordering is never gated on the clock, because no restaurant here carries
    a timezone to judge the clock against. See migration 0010.
    """

    __tablename__ = "meals"
    __table_args__ = (
        # Half a range says less than no range at all.
        CheckConstraint(
            "(starts_at IS NULL) = (ends_at IS NULL)",
            name="ck_meal_hours_both_or_neither",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    restaurant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("restaurants.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Unset on both means the restaurant has not said. An end at or before the
    # start runs into the next day, which is what late night is.
    starts_at: Mapped[time | None] = mapped_column(Time, nullable=True)
    ends_at: Mapped[time | None] = mapped_column(Time, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    item_links: Mapped[list["MealItem"]] = relationship(
        back_populates="meal",
        # created_at on the link is when the item joined this period, which is
        # what a builder means by the order they put things on breakfast.
        order_by="MealItem.sort_order, MealItem.created_at, MealItem.id",
        cascade="all, delete-orphan",
    )


class ModifierGroupItemType(Base):
    """Offers one modifier group for one item type.

    A join table rather than a list of ids on the group, so a type that is
    deleted cannot leave a group pointing at something that no longer exists.
    No rows at all means the group is offered for every type, which is the
    same "empty means everything" rule the array it replaced had.

    Top-level types only. A group named against Burgers rather than Food
    would have to be named again against Nuggets and again against every
    subcategory added later, which is the duplication subcategories exist to
    avoid. The builder resolves an item's type to its root before matching,
    so a burger is offered exactly what Food is offered.
    """

    __tablename__ = "modifier_group_item_types"
    __table_args__ = (
        UniqueConstraint("group_id", "item_type_id", name="uq_group_item_type"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    restaurant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("restaurants.id"), nullable=False, index=True
    )
    group_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("modifier_groups.id"), nullable=False, index=True
    )
    item_type_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("item_types.id"), nullable=False, index=True
    )

    item_type: Mapped["ItemType"] = relationship()


class Item(Base, TimestampMixin):
    __tablename__ = "menu_items"
    __table_args__ = (
        CheckConstraint("base_price_minor >= 0", name="ck_item_price_non_negative"),
        CheckConstraint(
            "calories IS NULL OR calories BETWEEN 0 AND 20000", name="ck_item_calories_sane"
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    restaurant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("restaurants.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(180), nullable=False)
    item_type_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("item_types.id"), nullable=False, index=True
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Energy per item as the restaurant states it, kcal. Null means it has
    # not said -- never zero, which would claim the dish has none.
    calories: Mapped[int | None] = mapped_column(Integer, nullable=True)
    base_price_minor: Mapped[int] = mapped_column(nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    is_available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Charged no sales tax: its share of an order is left out of the flat-rate
    # base, and sent to Stripe Tax as non-taxable. Which items qualify is the
    # restaurant's call, the same as its rate.
    tax_exempt: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    # A storage key like restaurants/<id>/items/<random>.webp, never a URL, so
    # the images can move to object storage without rewriting a row. The API
    # turns it into a URL on the way out. See services/images.
    image_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    item_type: Mapped["ItemType"] = relationship()
    included_links: Mapped[list["ItemIncludedOption"]] = relationship(
        back_populates="item", cascade="all, delete-orphan"
    )
    modifier_links: Mapped[list["ItemModifierGroup"]] = relationship(
        back_populates="item",
        order_by="ItemModifierGroup.sort_order, ItemModifierGroup.id",
        cascade="all, delete-orphan",
    )
    meal_links: Mapped[list["MealItem"]] = relationship(
        back_populates="item", cascade="all, delete-orphan"
    )


class MealItem(Base, TimestampMixin):
    """Serves one item during one meal period.

    A plain link row, deleted outright rather than stamped. Nothing outside
    the menu reads it -- an order records the item, never the period it was
    ordered from -- so taking Black Coffee off Breakfast leaves no history to
    protect and no reason for the row to linger.
    """

    __tablename__ = "meal_items"
    __table_args__ = (
        UniqueConstraint("meal_id", "item_id", name="uq_meal_item"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    restaurant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("restaurants.id"), nullable=False, index=True
    )
    meal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("meals.id"), nullable=False, index=True
    )
    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("menu_items.id"), nullable=False, index=True
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    meal: Mapped["Meal"] = relationship(back_populates="item_links")
    item: Mapped["Item"] = relationship(back_populates="meal_links")


class ItemIncludedOption(Base):
    """One option this item comes with, at no charge.

    Two effects, and they are the same decision rather than two settings: the
    option is chosen for the customer before they see the item, and it adds
    nothing to the price on this item even where the same option is charged
    on another. A burger that includes lettuce is a burger with lettuce on
    it, not a burger with a free topping ticked.

    Taking an included option off does not make the item cheaper. It is part
    of what the item is, so "no onion" is a burger without onion at the same
    price, not a discount.

    The option must belong to a group this item actually offers, which the
    API checks. Nothing else would be orderable: pricing only accepts options
    from the item's own groups, so an inclusion outside them could never be
    selected and would be a silent lie about what the item comes with.
    """

    __tablename__ = "item_included_options"
    __table_args__ = (
        UniqueConstraint("item_id", "option_id", name="uq_item_included_option"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    restaurant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("restaurants.id"), nullable=False, index=True
    )
    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("menu_items.id"), nullable=False, index=True
    )
    option_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("modifier_options.id"), nullable=False, index=True
    )

    item: Mapped["Item"] = relationship(back_populates="included_links")


class ModifierGroup(Base, TimestampMixin):
    __tablename__ = "modifier_groups"
    __table_args__ = (
        CheckConstraint("min_select >= 0", name="ck_group_min_non_negative"),
        CheckConstraint("max_select >= min_select", name="ck_group_max_gte_min"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    restaurant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("restaurants.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(180), nullable=False)
    selection_type: Mapped[str] = mapped_column(
        String(16), nullable=False, default=SelectionType.MULTI.value
    )
    is_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    min_select: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_select: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    options: Mapped[list["ModifierOption"]] = relationship(
        back_populates="group",
        order_by="ModifierOption.sort_order, ModifierOption.created_at, ModifierOption.id",
    )
    # Menu-builder filter hint: the item types this group is offered for. No
    # rows means every type -- "applies to nothing" is not a state a group can
    # usefully be in, so there is no second way to say "everything".
    type_links: Mapped[list["ModifierGroupItemType"]] = relationship(
        cascade="all, delete-orphan"
    )


class ModifierOption(Base, TimestampMixin):
    __tablename__ = "modifier_options"
    __table_args__ = (
        CheckConstraint(
            "calories_delta IS NULL OR calories_delta BETWEEN -20000 AND 20000",
            name="ck_option_calories_delta_sane",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    restaurant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("restaurants.id"), nullable=False, index=True
    )
    group_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("modifier_groups.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(180), nullable=False)
    # May be negative: "no cheese -$0.50" is a legitimate decrement.
    # This is the documented exception to the non-negative money constraint.
    price_delta_minor: Mapped[int] = mapped_column(nullable=False, default=0)
    # What this choice adds to the item's calories, kcal. Negative for a
    # choice that takes something off. Null is "no change stated", counted as
    # none -- an item's null means the figure itself is unknown, which is a
    # different claim and is why these two are read differently.
    calories_delta: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # A storage key, never a URL, the same as an item's. See services/images.
    image_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    group: Mapped["ModifierGroup"] = relationship(back_populates="options")


class ItemModifierGroup(Base):
    """Attaches a reusable modifier group to an item."""

    __tablename__ = "item_modifier_groups"
    __table_args__ = (
        UniqueConstraint("item_id", "group_id", name="uq_item_modifier_group"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    restaurant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("restaurants.id"), nullable=False, index=True
    )
    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("menu_items.id"), nullable=False, index=True
    )
    group_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("modifier_groups.id"), nullable=False, index=True
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    item: Mapped["Item"] = relationship(back_populates="modifier_links")
    group: Mapped["ModifierGroup"] = relationship()


class Combo(Base, TimestampMixin):
    """A meal deal: one item from each of several kinds, priced together.

    Belongs to exactly one meal period. A breakfast combo and a lunch combo
    are different deals even when they offer the same food, because what is
    on the menu at nine is not what is on it at one -- and a combo whose
    choices came from another period would offer items that period does not
    serve.

    The discount is the whole point of a combo and lives here rather than on
    the items: the items keep their own prices, and buying them together is
    what is cheaper. So nothing about an item changes when it joins a combo,
    and taking a combo down leaves its prices exactly as they were.
    """

    __tablename__ = "combos"
    __table_args__ = (
        CheckConstraint(
            "discount_kind IN ('NONE','PERCENT','AMOUNT','FIXED_PRICE')",
            name="ck_combo_discount_kind",
        ),
        CheckConstraint("discount_value >= 0", name="ck_combo_discount_non_negative"),
        # A percentage over 100 is not a discount, it is the restaurant paying
        # the customer. Only meaningful for PERCENT, so the other kinds are
        # let through untouched.
        CheckConstraint(
            "discount_kind <> 'PERCENT' OR discount_value <= 10000",
            name="ck_combo_percent_within_range",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    restaurant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("restaurants.id"), nullable=False, index=True
    )
    meal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("meals.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(180), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    discount_kind: Mapped[str] = mapped_column(
        String(16), nullable=False, default=DiscountKind.NONE.value
    )
    # Basis points when PERCENT, minor units when AMOUNT, the combo's own price
    # when FIXED_PRICE. See DiscountKind.
    discount_value: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # The deal's own photograph, kind "items". Null falls back to a photo of
    # something inside it, which is what every combo showed before.
    image_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    meal: Mapped["Meal"] = relationship()
    slots: Mapped[list["ComboSlot"]] = relationship(
        back_populates="combo",
        order_by="ComboSlot.sort_order, ComboSlot.created_at, ComboSlot.id",
        cascade="all, delete-orphan",
    )


class ComboSlot(Base, TimestampMixin):
    """One required choice inside a combo: pick a food, pick a drink.

    Always exactly one, always required. A combo that lets you skip the drink
    is two combos, and a slot that takes two drinks is a different product
    with a different price -- neither is what "meal deal" means, and both
    would need pricing rules this does not have.

    One slot per item type, enforced by the unique constraint, because that is
    how the builder reads: the types are the rows, and ticking items into a
    type is what creates its slot.

    Top-level types only, and that is the point of subcategories being a
    display idea. A slot asks for a food; burgers and nuggets are both foods,
    so both may fill it. Were the slot allowed to ask for Burgers, a menu
    that subdivided its food would turn one meal deal into several, each
    offering a narrower choice than the deal it replaced.
    """

    __tablename__ = "combo_slots"
    __table_args__ = (
        UniqueConstraint("combo_id", "item_type_id", name="uq_combo_slot_type"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    restaurant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("restaurants.id"), nullable=False, index=True
    )
    combo_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("combos.id"), nullable=False, index=True
    )
    item_type_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("item_types.id"), nullable=False, index=True
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    combo: Mapped["Combo"] = relationship(back_populates="slots")
    item_type: Mapped["ItemType"] = relationship()
    choices: Mapped[list["ComboSlotItem"]] = relationship(
        back_populates="slot",
        order_by="ComboSlotItem.sort_order, ComboSlotItem.created_at, ComboSlotItem.id",
        cascade="all, delete-orphan",
    )


class ComboSlotItem(Base, TimestampMixin):
    """One item a slot may be filled with.

    Explicit rather than "every item of this kind the period serves", so a
    value meal can leave the ribeye out, and so adding an item to the menu
    does not silently join every combo on it.
    """

    __tablename__ = "combo_slot_items"
    __table_args__ = (
        UniqueConstraint("slot_id", "item_id", name="uq_combo_slot_item"),
        CheckConstraint("upcharge_minor >= 0", name="ck_combo_slot_item_upcharge_non_negative"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    restaurant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("restaurants.id"), nullable=False, index=True
    )
    slot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("combo_slots.id"), nullable=False, index=True
    )
    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("menu_items.id"), nullable=False, index=True
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # What choosing this dish adds to a FIXED_PRICE combo: cheese fries a
    # dollar more in the burger meal. Ignored by the other kinds, where the
    # price difference between the dishes already does it.
    upcharge_minor: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )

    slot: Mapped["ComboSlot"] = relationship(back_populates="choices")
    item: Mapped["Item"] = relationship()


class CustomerFavourite(Base):
    """An item a customer saved to order again, at this restaurant.

    A pointer, not a snapshot: a favourite should show today's name, price
    and sold-out state. Items are soft deleted, so the link stays valid after
    one leaves the menu and the list simply stops showing it. Customers with
    an account only; see migration 0029.
    """

    __tablename__ = "customer_favourites"
    __table_args__ = (
        UniqueConstraint("restaurant_id", "user_id", "item_id", name="uq_customer_favourite"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    restaurant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("restaurants.id"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("menu_items.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
