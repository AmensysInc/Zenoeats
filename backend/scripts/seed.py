"""Seed the platform: four locations, and a burger menu behind the open one.

    Locations (the platform root at zenoeats.local:8443)
      Jr's Corner    Dallas, TX        OPEN -> the "spicehouse" tenant
      Big Apple      New York, NY      COMING_SOON
      Rainforest     Portland, OR      COMING_SOON
      Sunset Strip   Los Angeles, CA   COMING_SOON

    Item types          Burgers, Fries, Shakes, Drinks
    Meal period         All Day -- one board, every hour they are open

    Items                        type      modifier groups
      Double-Double              Burgers   Preparation, Extras
      Cheeseburger               Burgers   Preparation, Extras
      Hamburger                  Burgers   Preparation, Extras
      Grilled Cheese             Burgers   Preparation, Extras
      French Fries               Fries     Fry style
      Special Fries              Fries     Fry style
      Cheese Fries               Fries     Fry style
      Shake                      Shakes    Flavour (required)
      Root Beer Float            Shakes    --
      Soft Drink                 Drinks    Size (required), Drink (required)
      Coffee, Milk, Hot Cocoa    Drinks    --

Special and Jr's Style stay modifiers rather than items of their own, because
they are how a burger is ordered; as items somebody could order "Special"
with no burger under it.

Every item on the board has a photograph. An item added here without one
shows a card with no picture beside cards that have them, which reads as a
missing image rather than a design -- so ITEM_PHOTOS below should grow with
this list.

Groups are attached per item, not per type, so nothing nonsensical is offered:
a Root Beer Float carries no shake flavour, and the Flying Dutchman -- which
is already a preparation, having no bun to take off -- is offered extras but
not Jr's Style.

    Combos (All Day)    Double-Double Combo, Cheeseburger Combo,
                        Hamburger Combo -- burger, fries and a drink, 5% off

The menu mirrors In-N-Out's: its board items, their prices and their published
calorie counts, plus the preparation names people order by -- renamed to
this counter's own words: Special, Jr's Style, Well Done. That is the menu asked for, and item names,
prices and calories are facts about a menu rather than anything to copy. None
of their branding is here -- no logo, no palette, no trade dress -- and the
restaurant is named Spice House, so nothing in this seed presents itself as
In-N-Out. Swap prices for your own before taking a real order.

The restaurant keeps the slug "spicehouse" and the name "Spice House" while
the location on the front page is "Jr's Corner": a location owns its public
name, which is the reason it is not the same row as its tenant.

Re-running does nothing once the restaurant exists. To rebuild from scratch:

    make fresh          # drops the volume, migrates, seeds

Or by hand:  docker compose exec api python scripts/seed.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select, text  # noqa: E402

from app.db.base import utcnow  # noqa: E402
from app.db.session import system_session, tenant_session  # noqa: E402
from app.models import (  # noqa: E402
    Combo, ComboSlot, ComboSlotItem, DeliveryZone, DiscountKind, Item,
    ItemIncludedOption,
    ItemModifierGroup, ItemType, Location, LocationStatus, Meal, MealItem,
    ModifierGroup, ModifierGroupItemType, ModifierOption, Restaurant,
    RestaurantOrderCounter, RestaurantPaymentAccount, RestaurantStatus,
    RestaurantUser, SelectionType, StaffRole, StaffStatus, User, UserKind,
)
from app.core import staff_auth  # noqa: E402

SLUG = "spicehouse"
OWNER_EMAIL = "owner@spicehouse.local"
# Customers sign in through Clerk, so this row is only reachable with
# AUTH_DEV_BYPASS=true, where the bearer token is read as a Clerk user id.
DEV_CUSTOMER_CLERK_ID = "user_dev_customer"

# The four on the front page. Only the first has a tenant behind it; the rest
# are announcements, which is exactly why a location is not a restaurant row.
LOCATIONS = [
    {
        "slug": "jrs-corner", "name": "Jr's Corner",
        "city": "Ardmore", "region": "Oklahoma",
        "address_line": "1500 Sam Noble Parkway, Ardmore, OK 73401-7154",
        "blurb": "Open daily, 10:30am to 1:00am",
        "status": LocationStatus.OPEN.value, "attach_restaurant": True,
    },
    {
        "slug": "big-apple", "name": "Big Apple",
        "city": "New York", "region": "New York",
        "address_line": None,
        "blurb": "Opening next spring",
        "status": LocationStatus.COMING_SOON.value, "attach_restaurant": False,
    },
    {
        "slug": "rainforest", "name": "Rainforest",
        "city": "Portland", "region": "Oregon",
        "address_line": None,
        "blurb": "We are building the kitchen now",
        "status": LocationStatus.COMING_SOON.value, "attach_restaurant": False,
    },
    {
        "slug": "sunset-strip", "name": "Sunset Strip",
        "city": "Los Angeles", "region": "California",
        "address_line": None,
        "blurb": "Signing the lease",
        "status": LocationStatus.COMING_SOON.value, "attach_restaurant": False,
    },
]

DISH_PREFIX = "Jr's "

# Where Jr's Corner cooks from, in the pieces the restaurant row keeps and as
# the single line delivery compares against. Kept together so the two cannot
# disagree -- a mismatch silently switches delivery off.
STREET = "1500 Sam Noble Parkway"
CITY = "Ardmore"
STATE = "OK"
POSTCODE = "73401-7154"
COUNTRY = "US"
PICKUP_ADDRESS = f"{STREET}, {CITY}, {STATE}, {POSTCODE}, {COUNTRY}"

# What it charges to deliver, by how far. Described by the outer edge of each
# ring: under 2 miles is $2.99, out to 5 is $4.99, and nothing past that.
DELIVERY_ZONES = [(2.0, 299), (5.0, 499)]

ITEM_TYPES = ["Burgers", "Fries", "Shakes", "Drinks"]


def main() -> None:
    owner_password = staff_auth.generate_temp_password()

    with system_session() as session:
        existing = session.execute(
            text("SELECT id FROM restaurants WHERE slug = :s"), {"s": SLUG}
        ).first()
        if existing:
            print(f"Restaurant '{SLUG}' already exists ({existing.id}). Nothing to do.")
            print("To rebuild from scratch, including the locations: make fresh")
            return

        restaurant = Restaurant(
            slug=SLUG,
            name="Spice House",
            status=RestaurantStatus.ACTIVE.value,
            timezone="America/Chicago",
            currency="USD",
            tax_rate_bps=825,  # 8.25% flat. Replace with Stripe Tax for real.
            tagline="Burgers and fries, made to order",
            phone="+1 (580) 226-7925",
            storefront_customization_enabled=True,
            # Where it cooks from. The same street as the Jr's Corner
            # location on the front page -- the location names the place, the
            # restaurant row is what delivers from it.
            address_line1=STREET,
            address_city=CITY,
            address_state=STATE,
            address_postal_code=POSTCODE,
            address_country=COUNTRY,
            # Delivery needs an origin it still believes in:
            # delivery_origin_is_current compares geocoded_address against
            # the address above and refuses if they have drifted. Seeding the
            # coordinates directly, with geocoded_address set to match, is
            # what lets delivery work without a Google Maps key -- the key is
            # still needed to price a *customer's* address, which is a
            # different lookup. Coordinates are Greenville Ave, Dallas.
            delivery_enabled=True,
            latitude=34.18536,
            longitude=-97.10801,
            geocoded_address=PICKUP_ADDRESS,
        )
        session.add(restaurant)
        session.flush()
        rid = restaurant.id

        session.add(
            RestaurantOrderCounter(
                restaurant_id=rid, next_order_number=1001, updated_at=utcnow()
            )
        )

        # The front page. Written here, in the system session, because
        # `locations` is platform data: the app role may read it and only the
        # system role may write it.
        for order, spec in enumerate(LOCATIONS):
            session.add(
                Location(
                    slug=spec["slug"], name=spec["name"],
                    city=spec["city"], region=spec["region"],
                    address_line=spec["address_line"], blurb=spec["blurb"],
                    status=spec["status"],
                    restaurant_id=rid if spec["attach_restaurant"] else None,
                    sort_order=order,
                )
            )

        owner = User(
            kind=UserKind.STAFF.value,
            email=OWNER_EMAIL,
            full_name="Restaurant Owner",
            password_hash=staff_auth.hash_password(owner_password),
            must_change_password=True,
        )
        customer = User(
            kind=UserKind.CUSTOMER.value,
            clerk_user_id=DEV_CUSTOMER_CLERK_ID,
            email="customer@example.com",
            full_name="Sam Customer",
        )
        session.add_all([owner, customer])
        session.flush()
        owner_id = owner.id

    with tenant_session(rid) as session:
        session.add(
            RestaurantUser(
                restaurant_id=rid, user_id=owner_id,
                role_code=StaffRole.ADMIN.value, status=StaffStatus.ACTIVE.value,
                invited_at=utcnow(), accepted_at=utcnow(),
            )
        )
        # Placeholder Connect account. Replace with a real acct_ id from
        # Stripe test mode before running a payment.
        session.add(
            RestaurantPaymentAccount(
                restaurant_id=rid,
                stripe_account_id="acct_REPLACE_WITH_TEST_ACCOUNT",
                charges_enabled=True,
                payouts_enabled=True,
                details_submitted=True,
                onboarding_status="COMPLETE",
            )
        )

        for max_miles, fee in DELIVERY_ZONES:
            session.add(
                DeliveryZone(restaurant_id=rid, max_miles=max_miles, fee_minor=fee)
            )

        # --- The vocabulary ------------------------------------------------
        # An item cannot be created without a type, so these come first. Four
        # headings, in the order the board reads them.
        types = {}
        for order, name in enumerate(ITEM_TYPES):
            item_type = ItemType(restaurant_id=rid, name=name, sort_order=order)
            session.add(item_type)
            types[name] = item_type
        session.flush()

        # One board, all day. The combos below hang off it.
        all_day = Meal(restaurant_id=rid, name="All Day", sort_order=1)
        session.add(all_day)
        session.flush()

        # --- Reusable modifier groups --------------------------------------
        # How the patty is cooked and what it is wrapped in. Multi-select, and
        # max 2, because Special with Jr's Style is a real order.
        preparation = ModifierGroup(
            restaurant_id=rid, name="Preparation",
            selection_type=SelectionType.MULTI.value, is_required=False,
            min_select=0, max_select=2,
        )
        # What goes on it beyond what it comes with.
        extras = ModifierGroup(
            restaurant_id=rid, name="Extras",
            selection_type=SelectionType.MULTI.value, is_required=False,
            min_select=0, max_select=5,
        )
        fry_style = ModifierGroup(
            restaurant_id=rid, name="Fry style",
            selection_type=SelectionType.SINGLE.value, is_required=False,
            min_select=0, max_select=1,
        )
        # Required: a shake has to be a flavour, and there is no sensible
        # default to charge someone for.
        flavour = ModifierGroup(
            restaurant_id=rid, name="Flavour",
            selection_type=SelectionType.SINGLE.value, is_required=True,
            min_select=1, max_select=1,
        )
        drink_size = ModifierGroup(
            restaurant_id=rid, name="Size",
            selection_type=SelectionType.SINGLE.value, is_required=True,
            min_select=1, max_select=1,
        )
        drink_choice = ModifierGroup(
            restaurant_id=rid, name="Drink",
            selection_type=SelectionType.SINGLE.value, is_required=True,
            min_select=1, max_select=1,
        )
        session.add_all([preparation, extras, fry_style, flavour, drink_size, drink_choice])
        session.flush()

        # Which heading each group is offered under. No rows would mean every
        # type, which would put fry styles on a shake.
        for group, type_name in [
            (preparation, "Burgers"), (extras, "Burgers"), (fry_style, "Fries"),
            (flavour, "Shakes"), (drink_size, "Drinks"), (drink_choice, "Drinks"),
        ]:
            session.add(
                ModifierGroupItemType(
                    restaurant_id=rid, group_id=group.id,
                    item_type_id=types[type_name].id,
                )
            )

        def options(group, rows):
            """Add a group's options in board order, keyed by name."""
            made = {}
            for i, (name, delta, cal) in enumerate(rows):
                option = ModifierOption(
                    restaurant_id=rid, group_id=group.id, name=name,
                    price_delta_minor=delta, calories_delta=cal, sort_order=i,
                )
                session.add(option)
                made[name] = option
            return made

        # Special is free and adds the mustard-fried patty, pickles, extra
        # spread and grilled onions. Jr's Style swaps the bun for lettuce,
        # which takes calories off -- hence the negative delta, the documented
        # exception to non-negative money and calories.
        options(preparation, [
            ("Special", 0, 80),
            ("Jr's Style", 0, -150),
        ])
        options(extras, [
            ("Extra spread", 0, 60),
            ("Grilled onions", 0, 10),
            ("Whole grilled onions", 0, 15),
            ("Chopped chillies", 0, 5),
            ("Extra cheese slice", 60, 40),
        ])
        # How they are cooked, only. Cheese Fries and Special Fries are
        # items of their own below rather than options here: the
        # real menu sells them as dishes with their own prices and calories,
        # and having them in both places would let someone order Special
        # Special Fries.
        options(fry_style, [
            ("Well done", 0, 0),
            ("Light", 0, 0),
        ])
        # Published shake calories: chocolate 590, vanilla and strawberry 690.
        # Neapolitan is all three in one cup, so it lands between them.
        flavour_options = options(flavour, [
            ("Chocolate", 0, 0),
            ("Vanilla", 0, 100),
            ("Strawberry", 0, 100),
            ("Neapolitan", 0, 60),
        ])
        size_options = options(drink_size, [
            ("Small", 0, 0),
            ("Medium", 35, 60),
            ("Large", 65, 120),
            ("Extra large", 95, 190),
        ])
        drink_options = options(drink_choice, [
            ("Coca-Cola", 0, 0),
            ("Diet Coke", 0, -190),
            ("Dr Pepper", 0, 10),
            ("7UP", 0, -10),
            ("Root beer", 0, 20),
            ("Iced tea", 0, -190),
            ("Pink lemonade", 0, -20),
            ("Light lemonade", 0, -170),
            # Half iced tea, half lemonade: a drink rather than a dish, so it
            # belongs in this list rather than as an item.
            ("Arnold Palmer", 0, -105),
        ])
        session.flush()

        # --- Items ---------------------------------------------------------
        burgers = types["Burgers"].id
        fries_type = types["Fries"].id
        shakes = types["Shakes"].id
        drinks = types["Drinks"].id

        # Every dish is prefixed with the counter's name. A real menu board
        # says "Double-Double" and lets the sign above the door do the
        # branding, but this is the demo the platform is shown with, and the
        # prefix is what the restaurant asked for. One constant so renaming
        # the counter is one edit, not sixteen.
        double = Item(
            restaurant_id=rid, name=f"{DISH_PREFIX}Double-Double", item_type_id=burgers,
            description="Two beef patties, two slices of American cheese, "
                        "hand-leafed lettuce, tomato and spread.",
            base_price_minor=590, calories=670, currency="USD", sort_order=1,
        )
        cheeseburger = Item(
            restaurant_id=rid, name=f"{DISH_PREFIX}Cheeseburger", item_type_id=burgers,
            description="One patty, American cheese, lettuce, tomato and spread.",
            base_price_minor=360, calories=480, currency="USD", sort_order=2,
        )
        hamburger = Item(
            restaurant_id=rid, name=f"{DISH_PREFIX}Hamburger", item_type_id=burgers,
            description="One patty, lettuce, tomato and spread. No cheese.",
            base_price_minor=310, calories=390, currency="USD", sort_order=3,
        )
        french_fries = Item(
            restaurant_id=rid, name=f"{DISH_PREFIX}French Fries", item_type_id=fries_type,
            description="Whole potatoes, cut and fried to order.",
            base_price_minor=245, calories=395, currency="USD", sort_order=4,
        )
        shake = Item(
            restaurant_id=rid, name=f"{DISH_PREFIX}Shake", item_type_id=shakes,
            description="Made with real ice cream.",
            base_price_minor=340, calories=590, currency="USD", sort_order=5,
        )
        soft_drink = Item(
            restaurant_id=rid, name=f"{DISH_PREFIX}Soft Drink", item_type_id=drinks,
            description="Fountain drink, free refills in the dining room.",
            base_price_minor=210, calories=200, currency="USD", sort_order=6,
        )
        coffee = Item(
            restaurant_id=rid, name=f"{DISH_PREFIX}Coffee", item_type_id=drinks,
            description="Freshly brewed.",
            base_price_minor=140, calories=5, currency="USD", sort_order=7,
        )
        milk = Item(
            restaurant_id=rid, name=f"{DISH_PREFIX}Milk", item_type_id=drinks,
            description="Carton, 1% low fat.",
            base_price_minor=120, calories=130, currency="USD", sort_order=8,
        )
        cocoa = Item(
            restaurant_id=rid, name=f"{DISH_PREFIX}Hot Cocoa", item_type_id=drinks,
            description="With marshmallows.",
            base_price_minor=220, calories=170, currency="USD", sort_order=9,
        )

        # --- The rest of the board -------------------------------------------
        # These were under a "Not-so-secret menu" heading of their own, which
        # is how the real chain prints them. Dropped as a section: the label
        # was the longest on the page and said nothing a customer needed, and
        # a 3x3 is a burger wherever it is filed. The dishes stay, under the
        # heading each one belongs to.
        grilled_cheese = Item(
            restaurant_id=rid, name=f"{DISH_PREFIX}Grilled Cheese", item_type_id=burgers,
            description="Two slices of cheese, lettuce, tomato and spread. "
                        "No meat.",
            base_price_minor=310, calories=380, currency="USD", sort_order=12,
        )
        special_fries = Item(
            restaurant_id=rid, name=f"{DISH_PREFIX}Special Fries", item_type_id=fries_type,
            description="Fries with cheese, grilled onions and spread.",
            base_price_minor=460, calories=750, currency="USD", sort_order=14,
        )
        cheese_fries = Item(
            restaurant_id=rid, name=f"{DISH_PREFIX}Cheese Fries", item_type_id=fries_type,
            description="Fries with two slices of melted cheese.",
            base_price_minor=365, calories=400, currency="USD", sort_order=15,
        )
        root_beer_float = Item(
            restaurant_id=rid, name=f"{DISH_PREFIX}Root Beer Float", item_type_id=shakes,
            description="Root beer over vanilla ice cream.",
            base_price_minor=340, calories=520, currency="USD", sort_order=16,
        )

        session.add_all([
            double, cheeseburger, hamburger, french_fries, shake,
            soft_drink, coffee, milk, cocoa,
            grilled_cheese, special_fries, cheese_fries, root_beer_float,
        ])
        session.flush()

        links = [
            (double, preparation, 0), (double, extras, 1),
            (cheeseburger, preparation, 0), (cheeseburger, extras, 1),
            (hamburger, preparation, 0), (hamburger, extras, 1),
            (french_fries, fry_style, 0),
            (shake, flavour, 0),
            (soft_drink, drink_size, 0), (soft_drink, drink_choice, 1),
            # A Grilled Cheese has no patty to fry Special, but it has a
            # bun to take off, so Preparation still applies.
            (grilled_cheese, preparation, 0), (grilled_cheese, extras, 1),
            (special_fries, fry_style, 0),
            (cheese_fries, fry_style, 0),
            # Root Beer Float: no flavour group. It is not a shake.
        ]
        for item, group, order in links:
            session.add(
                ItemModifierGroup(
                    restaurant_id=rid, item_id=item.id, group_id=group.id,
                    sort_order=order,
                )
            )
        session.flush()

        # --- What each item comes with -------------------------------------
        # Chosen for the customer and charged at nothing. A shake arrives as
        # chocolate unless they say otherwise; a soft drink is a small Coke.
        # The calories above already count these, so the deltas here are what
        # make "Vanilla" read as +100 rather than the flavour's whole count.
        comes_with = [
            (shake, [flavour_options["Chocolate"]]),
            (soft_drink, [size_options["Small"], drink_options["Coca-Cola"]]),
        ]
        for item, chosen in comes_with:
            for option in chosen:
                session.add(
                    ItemIncludedOption(
                        restaurant_id=rid, item_id=item.id, option_id=option.id
                    )
                )

        # --- What the board serves ------------------------------------------
        board = [
            double, cheeseburger, hamburger, french_fries, shake,
            soft_drink, coffee, milk, cocoa,
            grilled_cheese, special_fries, cheese_fries, root_beer_float,
        ]
        for order, item in enumerate(board):
            session.add(
                MealItem(
                    restaurant_id=rid, meal_id=all_day.id, item_id=item.id,
                    sort_order=order,
                )
            )
        session.flush()

        # --- Combos ----------------------------------------------------------
        # A burger, fries and a drink, the way the board sells them. 500 basis
        # points is 5% off what the three would cost separately.
        for order, (burger, label) in enumerate([
            (double, f"{DISH_PREFIX}Double-Double Combo"),
            (cheeseburger, f"{DISH_PREFIX}Cheeseburger Combo"),
            (hamburger, f"{DISH_PREFIX}Hamburger Combo"),
        ]):
            combo = Combo(
                restaurant_id=rid, meal_id=all_day.id, name=label,
                description="With fries and a drink.",
                discount_kind=DiscountKind.PERCENT.value, discount_value=500,
                sort_order=order,
            )
            session.add(combo)
            session.flush()
            # A slot reads a top-level type, so the burger slot is offered the
            # one burger this combo is built around rather than all three.
            for index, (type_id, choices) in enumerate([
                (burgers, [burger]),
                (fries_type, [french_fries]),
                (drinks, [soft_drink, shake, coffee, milk, cocoa]),
            ]):
                slot = ComboSlot(
                    restaurant_id=rid, combo_id=combo.id, item_type_id=type_id,
                    sort_order=index,
                )
                session.add(slot)
                session.flush()
                for position, item in enumerate(choices):
                    session.add(
                        ComboSlotItem(
                            restaurant_id=rid, slot_id=slot.id, item_id=item.id,
                            sort_order=position,
                        )
                    )

        card_photo = seed_storefront(
            session, rid,
            items_by_name={item.name: item for item in board},
            collection_item_ids=[double.id, french_fries.id, shake.id],
        )

    # The picker's card photo, on the platform row that owns it.
    with system_session() as session:
        for location in session.scalars(
            select(Location).where(Location.restaurant_id == rid)
        ):
            location.image_path = card_photo

    print(f"Seeded '{SLUG}' ({rid}) and {len(LOCATIONS)} locations")
    print("  Platform root (locations):  https://zenoeats.local:8443")
    print("  Jr's Corner storefront:     https://spicehouse.zenoeats.local:8443")
    print("  Customer sign-in:  email and password, or guest -- both work now.")
    print("                     No identity provider is configured or needed.")
    print(f"  Owner sign-in:     {OWNER_EMAIL} / {owner_password}  (temporary)")
    print("                     https://spicehouse.zenoeats.local:8443/manage/login")
    print("  Super admin:       from ADMIN_USERS in .env")
    print(f"  curl with AUTH_DEV_BYPASS=true:  -H 'Authorization: Bearer {DEV_CUSTOMER_CLERK_ID}'")
    print()
    print("  Next: replace acct_REPLACE_WITH_TEST_ACCOUNT with a real Stripe")
    print("  test-mode connected account before attempting a payment.")


# Which photograph each part of the demo menu gets. Keyed by the names used
# above, so adding an item without a photo is a missing entry here rather than
# a silent fallback to something that looks like the wrong dish.
ITEM_PHOTOS = {
    f"{DISH_PREFIX}Double-Double": "item-double-double",
    f"{DISH_PREFIX}Cheeseburger": "item-cheeseburger",
    f"{DISH_PREFIX}Hamburger": "item-hamburger",
    f"{DISH_PREFIX}French Fries": "item-fries",
    f"{DISH_PREFIX}Shake": "item-shake",
    f"{DISH_PREFIX}Soft Drink": "item-soft-drink",
    f"{DISH_PREFIX}Coffee": "item-coffee",
    f"{DISH_PREFIX}Milk": "item-milk",
    f"{DISH_PREFIX}Hot Cocoa": "item-cocoa",
    f"{DISH_PREFIX}Grilled Cheese": "item-grilled-cheese",
    f"{DISH_PREFIX}Special Fries": "item-special-fries",
    f"{DISH_PREFIX}Cheese Fries": "item-cheese-fries",
    f"{DISH_PREFIX}Root Beer Float": "item-root-beer-float",
}

CATEGORY_PHOTOS = {
    "Burgers": "cat-burgers",
    "Fries": "cat-fries",
    "Shakes": "cat-shakes",
    "Drinks": "cat-drinks",
}

BANNERS = [
    ("banner-burger", "Fresh from our kitchen", "Never frozen, cooked when you order."),
    ("banner-fries", "Cut here, every day", "Whole potatoes, into the fryer, onto your tray."),
    ("banner-shake", "Made with real ice cream", "Chocolate, vanilla, strawberry — or all three."),
]


def seed_storefront(session, restaurant_id, items_by_name, collection_item_ids):
    """Give the demo restaurant real photography.

    These are stock photographs committed under app/seed_assets (see the
    README there for provenance and why they are not fetched at seed time).
    Each one goes through the same image service a restaurant's own upload
    does -- new_key, process, accept -- so there is one code path producing
    what the browser gets, and a seeded storefront is indistinguishable in
    shape from a real one.
    """
    from sqlalchemy import select

    from app.models import StorefrontBanner, StorefrontCollection, StorefrontCollectionItem
    from app.services import images
    from app.services.images import ImageKind

    assets = Path(__file__).resolve().parents[1] / "app" / "seed_assets"

    def stored(name: str, kind: ImageKind) -> str:
        """One asset, through the ordinary image pipeline."""
        data = (assets / f"{name}.webp").read_bytes()
        key = images.new_key(restaurant_id, kind)
        images.storage().save(key, images.process(data, kind))
        return images.accept(key, restaurant_id, kind)

    for order, (asset, headline, subline) in enumerate(BANNERS):
        session.add(
            StorefrontBanner(
                restaurant_id=restaurant_id,
                image_path=stored(asset, ImageKind.BANNERS),
                headline=headline, subline=subline,
                cta_label="Explore the menu", cta_target_kind="menu",
                sort_order=order,
            )
        )

    for category in session.scalars(select(ItemType).order_by(ItemType.sort_order, ItemType.id)):
        asset = CATEGORY_PHOTOS.get(category.name)
        if asset:
            category.image_path = stored(asset, ImageKind.CATEGORIES)

    # Dish photographs. Only the items a photograph was chosen for: a burger
    # picture on a carton of milk is worse than no picture at all, and the
    # menu card is built to read well without one.
    for name, asset in ITEM_PHOTOS.items():
        item = items_by_name.get(name)
        if item is not None:
            item.image_path = stored(asset, ImageKind.ITEMS)

    # The open location's card photo on the platform picker. Stored here,
    # where the image service and its tenant key are in scope, and written on
    # to the Location row by the caller: locations are platform rows and live
    # in the system session, not this one.
    card_photo = stored("hero-counter", ImageKind.BANNERS)

    collection = StorefrontCollection(
        restaurant_id=restaurant_id, title="House favourites", sort_order=0
    )
    session.add(collection)
    session.flush()
    for order, ident in enumerate(collection_item_ids):
        session.add(
            StorefrontCollectionItem(
                restaurant_id=restaurant_id, collection_id=collection.id,
                item_id=ident, sort_order=order,
            )
        )

    return card_photo


if __name__ == "__main__":
    main()
