"""What the browser is told about whoever is ordering.

One function, in one place, because two routers answer with it: the customer's
own pages (api/v1/customer.py) and the credential endpoints that create and
end a session (api/v1/customer_accounts.py). Registering and signing in both
answer with the same shape the profile page reads, so a second copy of this
would be two definitions of "who is signed in" drifting apart a field at a
time -- and the field that drifts is the one some page quietly stops showing.
"""

from app.models import User, UserKind
from app.schemas.api import CustomerSessionOut
from app.services import clerk_customers, terms


def session_out(user: User) -> CustomerSessionOut:
    """The session as the storefront reads it.

    `email_pending` is a leftover of the Clerk era and stays accurate for the
    rows it was true of: an account created before this codebase held its own
    credentials can still carry a placeholder address that no customer typed.
    An account registered here always has a real one, so it is simply false.
    """
    return CustomerSessionOut(
        email=user.email,
        full_name=user.full_name,
        phone=user.phone,
        address=user.address,
        email_pending=clerk_customers.has_placeholder_email(user),
        is_guest=user.kind == UserKind.GUEST.value,
        terms_accepted=not terms.needs_recording(user),
    )
