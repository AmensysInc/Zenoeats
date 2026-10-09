from fastapi import APIRouter, Depends

from app.api.deps import require_admin_host, require_same_origin
from app.api.v1 import (
    admin, customer, customer_accounts, orders, portal, restaurant, webhooks,
)

api_router = APIRouter(prefix="/api/v1")

# The storefront's public reads carry no credential at all.
api_router.include_router(portal.router)
# Ordering takes a Clerk bearer token -- or a guest's cookie, which a browser
# attaches by itself to a request from any page on a neighbouring storefront
# (same site). JSON-only bodies and SameSite=Lax already stopped a forged
# order; the pin makes the cookie useless from another origin outright.
api_router.include_router(orders.router, dependencies=[Depends(require_same_origin)])
# The customer's own page. Bearer-authenticated like the two above, but a
# guest reaches it with a cookie too, and it writes -- so it is pinned to its
# own origin the way the cookie portals are.
api_router.include_router(customer.router, dependencies=[Depends(require_same_origin)])
# Becoming a customer: register, sign in, sign out, reset a password, change
# an address. Same origin pin as the pages above, and for the same reason --
# every one of them sets or reads the session cookie a browser attaches by
# itself, so a form on another site must not be able to drive them.
api_router.include_router(
    customer_accounts.router, dependencies=[Depends(require_same_origin)]
)

# The two operator portals authenticate with cookies, which a browser attaches
# by itself. Both are pinned to the origin they are served from: the staff API
# to its own restaurant's hostname, the platform API to admin.<root domain>.
api_router.include_router(restaurant.router, dependencies=[Depends(require_same_origin)])
api_router.include_router(
    admin.router, dependencies=[Depends(require_admin_host), Depends(require_same_origin)]
)

# Stripe signs its webhooks and calls them server to server; there is no
# origin, and no session cookie to protect.
api_router.include_router(webhooks.router)
