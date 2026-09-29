"""Seed a demo restaurant with a menu worth clicking around.

    Item types (the restaurant's own words, editable in the portal)
      Food, Drinks, Sides, Sauces

    Items                          type
      Smash Burger, Crispy Chicken   Food    -> Veggies, Sauce add-ons
                                       comes with lettuce and onion, free
      Iced Tea, Lemonade             Drinks  -> Ice level (required)
      Fries                          Sides
      Garlic Aioli                   Sauces

    Meal periods
      Lunch    serves all of them
      Dinner   serves the food, the drinks and the fries

    Combo "Burger Meal"   sold during Lunch, 10% off
      a food, a drink and a side, one of each, all required

Iced Tea deliberately appears in both periods as one item, because that is
the arrangement the old shape could not express and the thing most worth
seeing work: one price, one sold-out toggle, two places on the menu.

Run:  docker compose exec api python scripts/seed.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from app.db.base import utcnow  # noqa: E402
from app.db.session import system_session, tenant_session  # noqa: E402
from app.models import (  # noqa: E402
    STARTER_ITEM_TYPES, Combo, ComboSlot, ComboSlotItem, DiscountKind, Item,
    ItemIncludedOption, ItemModifierGroup, ItemType, Meal, MealItem, ModifierGroup,
    ModifierGroupItemType, ModifierOption, Restaurant, RestaurantOrderCounter,
    RestaurantPaymentAccount, RestaurantStatus, RestaurantUser, SelectionType,
    StaffRole, StaffStatus, User, UserKind,
)
from app.core import staff_auth  # noqa: E402

SLUG = "spicehouse"
OWNER_EMAIL = "owner@spicehouse.local"
# Customers sign in through Clerk, so this row is only reachable with
# AUTH_DEV_BYPASS=true, where the bearer token is read as a Clerk user id.
DEV_CUSTOMER_CLERK_ID = "user_dev_customer"


def main() -> None:
    owner_password = staff_auth.generate_temp_password()

    with system_session() as session:
        existing = session.execute(
            text("SELECT id FROM restaurants WHERE slug = :s"), {"s": SLUG}
        ).first()
        if existing:
            print(f"Restaurant '{SLUG}' already exists ({existing.id}). Nothing to do.")
            return

        restaurant = Restaurant(
            slug=SLUG,
            name="Spice House",
            status=RestaurantStatus.ACTIVE.value,
            timezone="America/Chicago",
            currency="USD",
            tax_rate_bps=825,  # 8.25% flat. Replace with Stripe Tax for real.
            tagline="Wood-fired burgers and cold drinks",
            phone="+1 214 555 0100",
            storefront_customization_enabled=True,
        )
        session.add(restaurant)
        session.flush()
        rid = restaurant.id

        session.add(
            RestaurantOrderCounter(
                restaurant_id=rid, next_order_number=1001, updated_at=utcnow()
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

        # The vocabulary first: an item cannot be created without a type.
        types = {}
        for order, name in enumerate(STARTER_ITEM_TYPES):
            item_type = ItemType(restaurant_id=rid, name=name, sort_order=order)
            session.add(item_type)
            types[name] = item_type
        session.flush()

        # One subcategory, so the demo menu shows what nesting looks like
        # without pretending every menu wants it. Burgers sits under Food and
        # Crispy Chicken stays on Food itself, which is the mixed case: the
        # storefront reads Food, then the chicken, then a Burgers subheading.
        burgers = ItemType(
            restaurant_id=rid, name="Burgers",
            parent_id=types["Food"].id, sort_order=0,
        )
        session.add(burgers)
        session.flush()
        types["Burgers"] = burgers

        lunch = Meal(restaurant_id=rid, name="Lunch", sort_order=1)
        dinner = Meal(restaurant_id=rid, name="Dinner", sort_order=2)
        session.add_all([lunch, dinner])
        session.flush()

        # --- Reusable modifier groups -----------------------------------
        veggies = ModifierGroup(
            restaurant_id=rid, name="Veggies",
            selection_type=SelectionType.MULTI.value, is_required=False,
            min_select=0, max_select=5,
        )
        sauce_adds = ModifierGroup(
            restaurant_id=rid, name="Sauce add-ons",
            selection_type=SelectionType.MULTI.value, is_required=False,
            min_select=0, max_select=3,
        )
        ice = ModifierGroup(
            restaurant_id=rid, name="Ice level",
            selection_type=SelectionType.SINGLE.value, is_required=True,
            min_select=1, max_select=1,
        )
        session.add_all([veggies, sauce_adds, ice])
        session.flush()

        # Which types each group is offered for. No rows would mean every
        # type, which is not what these three want.
        for group, type_name in [
            (veggies, "Food"), (sauce_adds, "Food"), (ice, "Drinks"),
        ]:
            session.add(
                ModifierGroupItemType(
                    restaurant_id=rid, group_id=group.id,
                    item_type_id=types[type_name].id,
                )
            )

        veggie_options = {}
        for i, (name, delta) in enumerate([
            ("Lettuce", 0), ("Tomato", 0), ("Red onion", 0),
            ("Pickles", 0), ("Jalapenos", 50),
        ]):
            option = ModifierOption(restaurant_id=rid, group_id=veggies.id,
                                    name=name, price_delta_minor=delta, sort_order=i)
            session.add(option)
            veggie_options[name] = option

        for i, (name, delta) in enumerate([
            ("Garlic aioli", 75), ("Chipotle mayo", 75), ("House hot sauce", 50),
        ]):
            session.add(ModifierOption(restaurant_id=rid, group_id=sauce_adds.id,
                                       name=name, price_delta_minor=delta, sort_order=i))

        ice_options = {}
        for i, name in enumerate(["Light", "Regular", "Heavy"]):
            option = ModifierOption(restaurant_id=rid, group_id=ice.id, name=name,
                                    price_delta_minor=0, sort_order=i)
            session.add(option)
            ice_options[name] = option
        session.flush()

        # --- Items -------------------------------------------------------
        # Defined once, at the restaurant, and put on a period below.
        food, drinks = types["Food"].id, types["Drinks"].id
        sides, sauces = types["Sides"].id, types["Sauces"].id

        smash = Item(restaurant_id=rid, name="Smash Burger", item_type_id=burgers.id,
                     description="Two seared patties, aged cheddar, house sauce.",
                     base_price_minor=1095, currency="USD", sort_order=1)
        crispy = Item(restaurant_id=rid, name="Crispy Chicken", item_type_id=food,
                      description="Buttermilk-brined thigh, slaw, pickles.",
                      base_price_minor=1195, currency="USD", sort_order=2)
        tea = Item(restaurant_id=rid, name="Iced Tea", item_type_id=drinks,
                   description="Brewed hourly. Unsweetened.",
                   base_price_minor=350, currency="USD", sort_order=3)
        lemonade = Item(restaurant_id=rid, name="Lemonade", item_type_id=drinks,
                        description="Fresh-squeezed, not too sweet.",
                        base_price_minor=425, currency="USD", sort_order=4)
        fries = Item(restaurant_id=rid, name="Fries", item_type_id=sides,
                     description="Skin on, salted.",
                     base_price_minor=450, currency="USD", sort_order=5)
        aioli = Item(restaurant_id=rid, name="Garlic Aioli", item_type_id=sauces,
                     description="Two-ounce cup.",
                     base_price_minor=100, currency="USD", sort_order=6)
        session.add_all([smash, crispy, tea, lemonade, fries, aioli])
        session.flush()

        links = [
            (smash, veggies, 0), (smash, sauce_adds, 1),
            (crispy, veggies, 0), (crispy, sauce_adds, 1),
            (tea, ice, 0), (lemonade, ice, 0),
        ]
        for item, group, order in links:
            session.add(ItemModifierGroup(restaurant_id=rid, item_id=item.id,
                                          group_id=group.id, sort_order=order))
        session.flush()

        # --- What each item comes with ------------------------------------
        # Chosen for the customer and charged at nothing. A burger arrives
        # with lettuce and onion on it; jalapenos are still 50c because the
        # burger does not come with those.
        comes_with = [
            (smash, [veggie_options["Lettuce"], veggie_options["Red onion"]]),
            (crispy, [veggie_options["Lettuce"], veggie_options["Pickles"]]),
            (tea, [ice_options["Regular"]]),
            (lemonade, [ice_options["Regular"]]),
        ]
        for item, options in comes_with:
            for option in options:
                session.add(
                    ItemIncludedOption(restaurant_id=rid, item_id=item.id,
                                       option_id=option.id)
                )

        # --- What each period serves -------------------------------------
        served = [
            (lunch, [smash, crispy, tea, lemonade, fries, aioli]),
            (dinner, [smash, crispy, tea, fries]),
        ]
        for meal, items in served:
            for order, item in enumerate(items):
                session.add(MealItem(restaurant_id=rid, meal_id=meal.id,
                                     item_id=item.id, sort_order=order))
        session.flush()

        # --- A combo ------------------------------------------------------
        # Only items Lunch serves may be offered here, which is what the
        # portal enforces too. 1000 basis points is 10% off what the three
        # would cost separately.
        #
        # The food slot asks for Food and takes both the burger, which is
        # filed under Food > Burgers, and the chicken, which sits on Food
        # itself. A slot reads the top-level type, so subdividing the menu
        # left this deal exactly as it was.
        burger_meal = Combo(
            restaurant_id=rid, meal_id=lunch.id, name="Burger Meal",
            description="A burger, a drink and fries.",
            discount_kind=DiscountKind.PERCENT.value, discount_value=1000,
            sort_order=1,
        )
        session.add(burger_meal)
        session.flush()

        for order, (type_id, choices) in enumerate([
            (food, [smash, crispy]),
            (drinks, [tea, lemonade]),
            (sides, [fries]),
        ]):
            slot = ComboSlot(restaurant_id=rid, combo_id=burger_meal.id,
                             item_type_id=type_id, sort_order=order)
            session.add(slot)
            session.flush()
            for index, item in enumerate(choices):
                session.add(ComboSlotItem(restaurant_id=rid, slot_id=slot.id,
                                          item_id=item.id, sort_order=index))

        seed_storefront(session, rid, [smash.id, crispy.id, fries.id])

    print(f"Seeded '{SLUG}' ({rid})")
    print("  Portal:  https://spicehouse.zenoeats.local:8443")
    print("  Customer sign-in:  create an account (through Clerk) at")
    print("                     https://spicehouse.zenoeats.local:8443/account/sign-up")
    print(f"  Owner sign-in:     {OWNER_EMAIL} / {owner_password}  (temporary)")
    print("                     https://spicehouse.zenoeats.local:8443/manage/login")
    print("  Super admin:       from ADMIN_USERS in .env")
    print(f"  curl with AUTH_DEV_BYPASS=true:  -H 'Authorization: Bearer {DEV_CUSTOMER_CLERK_ID}'")
    print()
    print("  Next: replace acct_REPLACE_WITH_TEST_ACCOUNT with a real Stripe")
    print("  test-mode connected account before attempting a payment.")



def seed_storefront(session, restaurant_id, item_ids):
    """Offline demo illustrations, passed through the same image service.

    No remote photograph is needed to seed a fresh database. These simple
    food illustrations make the three slides and category images visible;
    the restaurant replaces them with its own photography in the editor.
    An existing demo is left alone by main(), including its saved branding.
    """
    import io
    from PIL import Image, ImageDraw
    from sqlalchemy import select
    from app.models import StorefrontBanner, StorefrontCollection, StorefrontCollectionItem
    from app.services import images
    from app.services.images import ImageKind

    def picture(index, kind):
        canvas = Image.new("RGB", (2400, 1100), ["#DED7B7", "#DCE4CC", "#E6CEB1"][index % 3])
        draw = ImageDraw.Draw(canvas)
        draw.ellipse((1300, 160, 2220, 1050), fill="#FFF8E4")
        if index % 3 == 0:
            draw.rounded_rectangle((1400, 460, 2080, 800), radius=160, fill="#BD702F")
            draw.rectangle((1410, 590, 2070, 650), fill="#466136")
            draw.rectangle((1410, 660, 2070, 720), fill="#623522")
        elif index % 3 == 1:
            draw.rounded_rectangle((1550, 340, 1980, 900), radius=65, fill="#AC5D2E")
            draw.rectangle((1660, 160, 1690, 590), fill="#FAF8F2")
        else:
            for x in range(1480, 2020, 80):
                draw.rounded_rectangle((x, 340, x + 50, 800), radius=15, fill="#E6AF42")
            draw.polygon([(1450, 600), (2070, 600), (2000, 920), (1520, 920)], fill="#8B3826")
        output = io.BytesIO(); canvas.save(output, "PNG")
        key = images.new_key(restaurant_id, kind)
        images.storage().save(key, images.process(output.getvalue(), kind))
        return images.accept(key, restaurant_id, kind)

    for n, headline in enumerate(("Fresh from our kitchen", "Something refreshing", "Make it a meal")):
        session.add(StorefrontBanner(restaurant_id=restaurant_id, image_path=picture(n, ImageKind.BANNERS), headline=headline, subline="Made to order, ready to enjoy.", cta_label="Explore the menu", cta_target_kind="menu", sort_order=n))
    for n, category in enumerate(session.scalars(select(ItemType).order_by(ItemType.sort_order, ItemType.id))):
        category.image_path = picture(n, ImageKind.CATEGORIES)
    collection = StorefrontCollection(restaurant_id=restaurant_id, title="House favourites", sort_order=0)
    session.add(collection); session.flush()
    for n, ident in enumerate(item_ids):
        session.add(StorefrontCollectionItem(restaurant_id=restaurant_id, collection_id=collection.id, item_id=ident, sort_order=n))


if __name__ == "__main__":
    main()
