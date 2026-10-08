"""Customer accounts with credentials we hold, instead of Clerk's.

One address, one customer account -- enforced by the database rather than by
whichever code path happens to create the row, because two accounts on the
same address means a customer signs in and finds half their order history.

Partial, exactly like uq_users_staff_email in 0023, and for the same reason
read from the other side: it must NOT apply to guests. A guest gets a users
row per checkout session and is never looked up by email, so three guests who
all type sam@example.com are three rows by design (core/guest_auth.py says
why: an address nobody verified must not find another guest's orders). A
global unique index on email would make the second of those guests fail to
check out.

Case-insensitive on the same grounds as the staff index: addresses are handed
out verbally and typed with a stray capital, and Sam@ and sam@ being two
accounts is a support ticket, not a feature.

Existing CUSTOMER rows are Clerk-backed and have no password_hash; they are
untouched and keep working for as long as Clerk is configured. The column
they would fill already exists -- users.password_hash, added for staff -- so
there is no new column here, only the constraint that makes it safe to use.

Revision ID: 0045
Revises: 0044
"""

from alembic import op

revision = "0045"
down_revision = "0044"
branch_labels = None
depends_on = None

INDEX = "uq_users_customer_email"


def upgrade() -> None:
    # Fails loudly on duplicates rather than skipping them, as 0023 did: a
    # duplicate here means somebody cannot sign in, which is worth stopping a
    # deploy for. Clerk itself enforced one account per address, so a database
    # that was only ever written by Clerk has none.
    op.execute(
        f"CREATE UNIQUE INDEX {INDEX} ON users (lower(email)) WHERE kind = 'CUSTOMER'"
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {INDEX}")
