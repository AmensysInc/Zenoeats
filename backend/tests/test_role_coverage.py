"""Every restaurant endpoint says which staff roles may call it.

A role check is something each endpoint opts into, with a dependency. An
endpoint added without one would still be confined to its own restaurant by
row-level security, but any signed-in member -- a kitchen login -- could call
it. Nothing caught that. This walks the staff router and fails on:

  * an endpoint with no role check that is not one of the few sign-in
    endpoints that run before a role can exist;
  * an endpoint whose roles differ from the table below, so widening one --
    opening cancel to the floor, say -- is a deliberate edit here, next to
    the README's role tables, rather than a one-word change nobody reviews.

No database or server: it reads the dependency tree FastAPI builds.
"""

from fastapi.routing import APIRoute

from app.api import deps

# ALL is the floor: every role that works in the restaurant. DRIVER is not
# one of them -- a driver sees the orders assigned to them and no more of the
# portal -- so it appears only under DELIVERY. IT_SUPPORT is not one either:
# it works on the restaurant rather than in it, and appears only in the four
# lists below it.
ALL = frozenset({"ADMIN", "MANAGER", "KITCHEN", "CASHIER"})
MANAGERS = frozenset({"ADMIN", "MANAGER"})
ADMIN = frozenset({"ADMIN"})
DELIVERY = frozenset({"ADMIN", "MANAGER", "DRIVER"})

# IT support, in four lists. Each is one of the lists above plus IT_SUPPORT,
# and the pairing is the whole point: an endpoint that reads goes in the
# support list, the one that writes the same thing stays on the original, and
# a new endpoint put in the wrong one of a pair is what this file catches.
#
# FLOOR_READ is ALL plus support: the board, its history and the stock list,
# which is how support sees what a restaurant is actually showing. The
# writes next to them -- ready, complete, sold out -- stay on ALL.
FLOOR_READ = ALL | {"IT_SUPPORT"}
# MENU_READ is MANAGERS plus support: the menu builder, read. Every edit in
# it stays on MANAGERS, so support can explain why an item is not on the
# storefront without being able to change its price.
MENU_READ = MANAGERS | {"IT_SUPPORT"}
# STOREFRONT is MANAGERS plus support, read and write both: presentation is
# configuration, it is what support is called about, and the worst a wrong
# one does is look wrong until it is set again. Image upload is here because
# a banner needs one, and an upload on its own attaches nothing.
STOREFRONT = MANAGERS | {"IT_SUPPORT"}
# SETTINGS is ADMIN plus support, read and write both: the trading name, the
# address distances are measured from, the timezone, the tax rate and the
# delivery rings. Audited to the person who changed them, support included.
SETTINGS = ADMIN | {"IT_SUPPORT"}

# Everyone who has a login here, drivers and support included: your own name
# and your own sign-in address belong to you whatever you do at the
# restaurant.
EVERYONE = frozenset(
    {"ADMIN", "MANAGER", "KITCHEN", "CASHIER", "DRIVER", "IT_SUPPORT"}
)

# Kept in step with "Staff roles" in README.md and with
# web/src/features/restaurant/nav.ts.
EXPECTED = {
    # Presentation, which managers and IT support both own.
    ("GET", "/restaurant/storefront"): STOREFRONT,
    ("PATCH", "/restaurant/storefront/theme"): STOREFRONT,
    ("PUT", "/restaurant/storefront/banners"): STOREFRONT,
    ("PATCH", "/restaurant/item-types/{type_id}/storefront"): STOREFRONT,
    ("PUT", "/restaurant/storefront/collections"): STOREFRONT,
    ("PUT", "/restaurant/storefront/shortcuts"): STOREFRONT,
    ("PATCH", "/restaurant/storefront/map"): STOREFRONT,
    # The floor: the board, handing over with a PIN or without it, cancelling
    # a paid order, and stock. The two exceptions need a reason, kept against
    # whoever gave it.
    ("GET", "/restaurant/orders"): FLOOR_READ,
    ("GET", "/restaurant/orders/history"): FLOOR_READ,
    ("POST", "/restaurant/orders/{order_id}/ready"): ALL,
    ("POST", "/restaurant/orders/{order_id}/complete"): ALL,
    ("POST", "/restaurant/orders/{order_id}/override-complete"): ALL,
    ("POST", "/restaurant/orders/{order_id}/cancel"): ALL,
    ("GET", "/restaurant/stock"): FLOOR_READ,
    ("PATCH", "/restaurant/items/{item_id}/availability"): ALL,
    # Delivery: a driver's own orders, and the two steps of running one.
    ("GET", "/restaurant/deliveries"): DELIVERY,
    ("POST", "/restaurant/orders/{order_id}/picked-up"): DELIVERY,
    ("POST", "/restaurant/orders/{order_id}/delivered"): DELIVERY,
    ("POST", "/restaurant/driver/location"): DELIVERY,
    # Managers: who drives, and money owed on an order already cancelled.
    ("POST", "/restaurant/orders/{order_id}/assign-driver"): MANAGERS,
    ("POST", "/restaurant/orders/{order_id}/unassign-driver"): MANAGERS,
    ("GET", "/restaurant/drivers"): MANAGERS,
    ("POST", "/restaurant/orders/{order_id}/refund"): MANAGERS,
    ("GET", "/restaurant/orders/refunds-due"): MANAGERS,
    # Reports are managers alone: takings are not a support question.
    ("GET", "/restaurant/reports"): MANAGERS,
    ("POST", "/restaurant/images"): STOREFRONT,
    ("GET", "/restaurant/menu"): MENU_READ,
    ("POST", "/restaurant/meals"): MANAGERS,
    ("PATCH", "/restaurant/meals/{meal_id}"): MANAGERS,
    ("DELETE", "/restaurant/meals/{meal_id}"): MANAGERS,
    ("POST", "/restaurant/meals/{meal_id}/items"): MANAGERS,
    ("DELETE", "/restaurant/meals/{meal_id}/items/{item_id}"): MANAGERS,
    ("GET", "/restaurant/combos"): MENU_READ,
    ("POST", "/restaurant/combos"): MANAGERS,
    ("PATCH", "/restaurant/combos/{combo_id}"): MANAGERS,
    ("DELETE", "/restaurant/combos/{combo_id}"): MANAGERS,
    ("GET", "/restaurant/modifier-groups"): MENU_READ,
    ("POST", "/restaurant/modifier-groups"): MANAGERS,
    ("PATCH", "/restaurant/modifier-groups/{group_id}"): MANAGERS,
    ("DELETE", "/restaurant/modifier-groups/{group_id}"): MANAGERS,
    ("POST", "/restaurant/modifier-groups/{group_id}/options"): MANAGERS,
    ("PATCH", "/restaurant/modifier-options/{option_id}"): MANAGERS,
    ("DELETE", "/restaurant/modifier-options/{option_id}"): MANAGERS,
    ("GET", "/restaurant/item-types"): MENU_READ,
    ("POST", "/restaurant/item-types"): MANAGERS,
    ("PATCH", "/restaurant/item-types/{type_id}"): MANAGERS,
    ("DELETE", "/restaurant/item-types/{type_id}"): MANAGERS,
    ("GET", "/restaurant/items"): MENU_READ,
    ("POST", "/restaurant/items"): MANAGERS,
    ("PATCH", "/restaurant/items/{item_id}"): MANAGERS,
    ("DELETE", "/restaurant/items/{item_id}"): MANAGERS,
    # Admins: who is on the team.
    ("GET", "/restaurant/staff"): ADMIN,
    ("POST", "/restaurant/staff"): ADMIN,
    ("DELETE", "/restaurant/staff/{membership_id}"): ADMIN,
    ("PATCH", "/restaurant/staff/{membership_id}"): ADMIN,
    ("POST", "/restaurant/staff/{membership_id}/reset-password"): ADMIN,
    # Resending an invitation can issue a fresh temporary password.
    ("POST", "/restaurant/staff/{membership_id}/resend-invite"): ADMIN,
    # The restaurant's own record, which admin shares with IT support. A
    # manager runs the service; the trading name, the address tax is sourced
    # at and the tax rate itself are the owner's to answer for, and support's
    # to fix when they are wrong.
    ("GET", "/restaurant/profile"): SETTINGS,
    ("PATCH", "/restaurant/profile"): SETTINGS,
    # ...and where it delivers, which decides what customers are charged.
    ("GET", "/restaurant/delivery"): SETTINGS,
    ("PATCH", "/restaurant/delivery"): SETTINGS,
    ("POST", "/restaurant/delivery/locate"): SETTINGS,
    ("PUT", "/restaurant/delivery/zones"): SETTINGS,
    # Your own account, which is nobody's business but yours.
    ("PATCH", "/restaurant/me"): EVERYONE,
    ("POST", "/restaurant/change-email"): EVERYONE,
}

# Before a role exists, each for a stated reason, and each with the identity
# check it does need. None means it needs no session at all.
NO_ROLE = {
    ("POST", "/restaurant/login"): None,  # how a session is obtained
    ("POST", "/restaurant/logout"): None,  # clears the cookie, whoever holds it
    # Ends the caller's own sessions everywhere; a temporary password may too.
    ("POST", "/restaurant/logout-everywhere"): deps.current_staff_user,
    ("GET", "/restaurant/me"): deps.current_staff_user,  # a temporary password must reach it
    ("POST", "/restaurant/change-password"): deps.current_staff_user,  # likewise
    # An invitee is INVITED, not yet a member with a role; this is how they become one.
    ("POST", "/restaurant/staff/accept"): deps.current_staff_user_ready,
}


def _calls(dependant):
    """Every callable in an endpoint's dependency tree."""
    for dep in dependant.dependencies:
        yield dep.call
        yield from _calls(dep)


def _endpoints():
    from app.api.v1.restaurant import router

    for route in router.routes:
        if isinstance(route, APIRoute):
            for method in route.methods:
                yield (method, route.path), list(_calls(route.dependant))


def test_every_endpoint_has_exactly_the_roles_it_is_meant_to():
    unchecked, wrong = [], []

    for key, calls in _endpoints():
        roles = [c.staff_roles for c in calls if hasattr(c, "staff_roles")]
        if key in NO_ROLE:
            if roles:
                wrong.append(f"{key} is listed as needing no role but checks {roles}")
            continue
        if not roles:
            unchecked.append(key)
        elif key not in EXPECTED:
            wrong.append(f"{key} is new: add it to EXPECTED with the roles {sorted(roles[0])}")
        elif set(roles) != {EXPECTED[key]}:
            wrong.append(
                f"{key} admits {[sorted(r) for r in roles]}, expected {sorted(EXPECTED[key])}"
            )

    if unchecked:
        wrong.insert(0, (
            f"Endpoints with no role check: {unchecked}. Depend on MANAGE, ANY_STAFF or "
            "STAFF_ADMIN, or add the endpoint to NO_ROLE with the reason it needs none."
        ))
    # One assertion, so a single run lists every problem rather than the first.
    assert not wrong, "\n".join(wrong)


def test_the_endpoints_without_a_role_still_check_who_is_calling():
    found = dict(_endpoints())

    for key, identity in NO_ROLE.items():
        assert key in found, f"{key} no longer exists; remove it from NO_ROLE"
        if identity is not None:
            assert identity in found[key], f"{key} no longer requires {identity.__name__}"


def test_the_tables_name_only_real_endpoints():
    """A stale entry would let the table drift from the router unnoticed."""
    found = {key for key, _ in _endpoints()}
    assert not set(EXPECTED) - found, f"Not endpoints any more: {set(EXPECTED) - found}"


# The writes IT support may make. Everything else the role can reach is a
# read, and that is the promise the role is worth having for: a support login
# can work out what is wrong with a restaurant without being able to change
# what it sells, act on a live order, read its takings or touch its team.
#
# This is derived from the router rather than from EXPECTED above, so the two
# have to agree. Adding an entry here is the deliberate act that widening the
# role should be; the assertion below is what makes it one.
IT_SUPPORT_WRITES = {
    # Presentation. The worst a wrong one does is look wrong until it is set
    # again, and it is most of what support is called about.
    ("PATCH", "/restaurant/storefront/theme"),
    ("PUT", "/restaurant/storefront/banners"),
    ("PATCH", "/restaurant/item-types/{type_id}/storefront"),
    ("PUT", "/restaurant/storefront/collections"),
    ("PUT", "/restaurant/storefront/shortcuts"),
    ("PATCH", "/restaurant/storefront/map"),
    # A banner needs a picture. An upload attaches nothing on its own, so
    # this is not a way into the menu.
    ("POST", "/restaurant/images"),
    # The restaurant's record and where it delivers, both audited to whoever
    # changed them.
    ("PATCH", "/restaurant/profile"),
    ("PATCH", "/restaurant/delivery"),
    ("POST", "/restaurant/delivery/locate"),
    ("PUT", "/restaurant/delivery/zones"),
    # Your own name and your own sign-in address, which every role may change.
    ("PATCH", "/restaurant/me"),
    ("POST", "/restaurant/change-email"),
}


def test_it_support_changes_nothing_outside_its_own_configuration():
    unexpected = [
        key
        for key, calls in _endpoints()
        if key[0] not in ("GET", "HEAD")
        and key not in IT_SUPPORT_WRITES
        and any("IT_SUPPORT" in c.staff_roles for c in calls if hasattr(c, "staff_roles"))
    ]
    assert not unexpected, (
        f"IT support can now write {unexpected}. If that is intended, add each to "
        "IT_SUPPORT_WRITES and say in README.md's role tables what it lets support do."
    )


def test_it_support_cannot_reach_the_money_or_the_team():
    """The four refusals that make the role safe to hand out.

    Stated as endpoints rather than as role lists, because this is the
    question someone reviewing the role actually asks: could a support login
    cancel a paid order, change a price, read what we took, or invite itself
    an admin?
    """
    off_limits = {
        ("POST", "/restaurant/orders/{order_id}/cancel"),
        # Money going back is money moving: the same answer as cancelling.
        ("POST", "/restaurant/orders/{order_id}/refund"),
        ("POST", "/restaurant/orders/{order_id}/override-complete"),
        ("PATCH", "/restaurant/items/{item_id}"),
        ("PATCH", "/restaurant/items/{item_id}/availability"),
        ("GET", "/restaurant/reports"),
        ("GET", "/restaurant/staff"),
        ("POST", "/restaurant/staff"),
        ("PATCH", "/restaurant/staff/{membership_id}"),
        ("POST", "/restaurant/staff/{membership_id}/reset-password"),
        ("POST", "/restaurant/staff/{membership_id}/resend-invite"),
    }
    found = dict(_endpoints())
    reachable = []
    for key in off_limits:
        assert key in found, f"{key} no longer exists; this test is now blind to it"
        roles = [c.staff_roles for c in found[key] if hasattr(c, "staff_roles")]
        assert roles, f"{key} has no role check at all"
        if any("IT_SUPPORT" in r for r in roles):
            reachable.append(key)
    assert not reachable, f"IT support can reach {reachable}"
