"""Sign in with Google, against Google rather than through a broker.

Stores the Google subject claim -- the `sub` of the ID token -- rather than
matching on email alone.

Email is how an existing account is *found* the first time (Google has
verified it, which is what makes that safe), but it is not an identity: people
change the address on a Google account, and matching on it forever would mean
someone who changed theirs becomes a stranger, while whoever is given their
old address inherits their order history. The subject never changes and is
never reissued, so once the link is made it is what the lookup uses.

Nullable and unique: almost every row has none, and no two accounts may claim
the same Google identity. Partial, so the many NULLs cost nothing and only
real subjects are indexed.

Revision ID: 0046
Revises: 0045
"""

import sqlalchemy as sa
from alembic import op

revision = "0046"
down_revision = "0045"
branch_labels = None
depends_on = None

INDEX = "uq_users_google_subject"


def upgrade() -> None:
    op.add_column("users", sa.Column("google_subject", sa.String(255), nullable=True))
    op.execute(
        f"CREATE UNIQUE INDEX {INDEX} ON users (google_subject) "
        "WHERE google_subject IS NOT NULL"
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {INDEX}")
    op.drop_column("users", "google_subject")
