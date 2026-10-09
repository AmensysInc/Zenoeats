"""Which map a restaurant's customers open, and in whose colours.

Google colours a map from a Map ID or from a style array, and ignores the
array whenever a Map ID is in use. The tracking map uses the array, so the
choice is one of the styles the storefront knows how to draw -- and the
server's job is to refuse any other, so a stale page cannot store a setting
that does nothing.
"""

import pytest

from app.core import errors
from app.db.session import tenant_session
from app.models import Restaurant
from app.schemas.storefront import MapPatch
from app.services import maps, storefront
from tests.test_storefront import tenants  # noqa: F401

pytestmark = pytest.mark.integration


def _save(db, rid, **changes):
    return storefront.save_map(db, db.get(Restaurant, rid), MapPatch(**changes))


# --- the styles the storefront can draw ----------------------------------

def test_the_keys_are_the_four_the_storefront_draws():
    """web/src/features/storefront/mapStyles.ts holds the same four, and its
    own test asserts the list from that side."""
    assert maps.STYLE_KEYS == ("standard", "light", "dark", "palette")


def test_no_choice_is_a_choice_and_anything_else_is_not():
    assert maps.is_style(None) is True
    assert maps.is_style("palette") is True
    assert maps.is_style("aubergine") is False


# --- what a restaurant chooses -------------------------------------------

def test_a_style_is_stored_and_read_back(tenants):
    rid, _, _ = tenants[0]
    with tenant_session(rid) as db:
        assert _save(db, rid, map_style_key="dark")["map_style_key"] == "dark"
        assert _save(db, rid, map_style_key=None)["map_style_key"] is None


def test_a_style_the_storefront_cannot_draw_is_refused(tenants):
    rid, _, _ = tenants[0]
    with tenant_session(rid) as db:
        with pytest.raises(errors.ApiError):
            _save(db, rid, map_style_key="midnight")


def test_the_pins_follow_the_palette_until_the_restaurant_says_otherwise(tenants):
    rid, _, _ = tenants[0]
    with tenant_session(rid) as db:
        assert db.get(Restaurant, rid).map_pins_themed is True
        assert _save(db, rid, map_pins_themed=False)["map_pins_themed"] is False


def test_choosing_a_style_leaves_the_pin_switch_alone(tenants):
    """Only the fields sent are applied, as everywhere else in this editor."""
    rid, _, _ = tenants[0]
    with tenant_session(rid) as db:
        _save(db, rid, map_pins_themed=False)
        saved = _save(db, rid, map_style_key="light")
        assert saved["map_pins_themed"] is False
        assert saved["map_style_key"] == "light"



def _request(host: str):
    """The least a Request has to be for get_portal to read it.

    platform_url is now derived from the host the browser used rather than
    from a template, so the endpoint needs a request. Starlette's Request
    reads the host out of the raw ASGI scope headers.
    """
    from starlette.requests import Request

    return Request({
        "type": "http",
        "method": "GET",
        "path": "/api/v1/portal",
        "headers": [(b"host", host.encode())],
        "query_string": b"",
        "scheme": "https",
    })

def test_the_customer_is_told_which_style_to_draw(tenants):
    """The portal carries the key, not colours: the storefront holds those,
    and a map drawn from a palette the page already has needs no round trip."""
    from app.api.v1.portal import get_portal
    from app.api.deps import TenantContext

    rid, _, _ = tenants[0]
    with tenant_session(rid) as db:
        _save(db, rid, map_style_key="palette")
        restaurant = db.get(Restaurant, rid)
        restaurant.status = "ACTIVE"
        out = get_portal(
            request=_request(f"{restaurant.slug}.zenoeats.local:8443"),
            tenant=TenantContext(restaurant_id=rid, slug=restaurant.slug),
            restaurant=restaurant,
            db=db,
        )
        assert out.map_style_key == "palette"
        assert out.map_pins_themed is True


def test_the_gate_covers_it_like_the_rest_of_the_storefront(tenants):
    rid, _, _ = tenants[0]
    with tenant_session(rid) as db:
        db.get(Restaurant, rid).storefront_customization_enabled = False
    with tenant_session(rid) as db:
        with pytest.raises(errors.ApiError) as refused:
            _save(db, rid, map_pins_themed=False)
        assert refused.value.status_code == 403
