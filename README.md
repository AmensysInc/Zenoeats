# Zenoeats — online ordering for restaurants, from menu to handover

A multi-restaurant ordering platform built on the v3.0 architecture
baseline. Each restaurant gets its own subdomain storefront, with an Item →
Modifier menu served by meal periods. Customers sign in with Clerk on our
own pages, or check out as a guest, for pickup or delivery. Payment is a
Stripe Connect direct charge, confirmed by webhook, and the paid order lands
on the restaurant's kitchen board. There it is handed over with a pickup
PIN, or cancelled and refunded from the same screen.

**Status (29 September 2026):** the application is feature-complete for
launch, and **`v1.3.1` is the release to deploy** (see
[`CHANGELOG.md`](CHANGELOG.md)). It includes the customer, staff and account
emails, sent through SendGrid from editable templates (see [Email](#email)).
End-to-end passes against the running stack have found no bugs in the
ordering path. They covered:

- guest checkout with a Stripe test-card payment
- the kitchen board, the PIN handover, and cancel and refund
- the admin portal
- the security checks

What remains before real money is accounts, the domain, the server and a
legal review, tracked in
[`docs/operations/STEPS_BEFORE_PRODUCTION.md`](docs/operations/STEPS_BEFORE_PRODUCTION.md). See
[Production](#production), and the
[Production setup guide](#production-setup-guide) for Stripe, Clerk, Google
Maps and every other account step by step.

## What is in this build

| Area | Included |
|---|---|
| Tenancy | Wildcard subdomain resolution, PostgreSQL RLS, three-role DB model |
| Identity | Customers: Clerk (email and password, plus Google, Apple and Facebook when enabled in Clerk) behind our own `/account` pages, or **guest checkout** with just an email. Staff: platform-issued passwords. Admins: `ADMIN_USERS` |
| Customer profile | `/profile`: name, phone and address, a change-email link beside the email, order history at this restaurant, favourite items saved from a heart on the menu, and **Close my account**, which removes the sign-in and personal details and keeps paid orders. Accounts only; a guest sees their orders |
| Menu | Item types the restaurant names itself, items, meal periods that serve them, combos with their own photo, reusable modifier groups, and **calories** per item and per size or option, summed for combos. One card size for every item and combo |
| Brand | Each restaurant uploads its logo and chooses how its name is shown — typed in one of six fonts, or its own lettering as an image — in Settings; shown in every customer header and on the sign-in pages, with or without storefront customization |
| Delivery map | Each restaurant chooses how its tracking map is coloured -- Google standard, light, dark, or built from its own palette -- and whether the pins take its colours |
| Storefront | Each restaurant sets its own palette, font pairing, rotating banners with their own framing, category shortcuts and collections — behind a platform switch |
| Cart and checkout | A **cart page** (`/cart`) before anything is asked. Then checkout: pickup or delivery, contact details, server-authoritative repricing, TaxService (flat rate or Stripe Tax), idempotent order creation |
| Email | 15 emails through SendGrid: the order confirmation and every step after it (ready, on the way, delivered, cancelled with its refund, refunded), staff invitations and notices, and a customer's welcome and account closure. Each is a template file, and each is sent once |
| Payments | Stripe Connect direct charges, a durable webhook inbox, an account-match guard, a Stripe read-back fallback when a webhook is late, and **Apple Pay / Google Pay** domains registered per restaurant |
| Ops | Kitchen board, pickup PIN, **cancel and refund**, menu builder, stock, staff invitations, reports, deliveries the restaurant runs itself with live driver tracking, self-service settings, storefront editor |
| Admin | Super admin portal: onboarding, activation, Stripe refresh, platform reports, CSV |
| Policies | Privacy, terms, refunds and data deletion as files outside React, linked from sign-up, checkout and every customer page; agreement recorded per customer |
| Infra | Docker Compose (development, plus a production override), Nginx, two Redis instances, Celery, Alembic, GitHub Actions publishing images to GHCR |
| Operations | `/health`, `/health/ready`, `/health/operations`, nightly encrypted off-site backups with a restore drill, Sentry, a production `.env` generator |

## What is deliberately not here

Route planning, a native driver app, WebSockets, cash payments, reconciliation, promotions,
reviews, SMS, push, PITR. All of it stays in the v3.0 baseline for later
releases. What delivery does and does not do yet is under "Delivery" at the
bottom.

Checkout requires a name, phone number and address on every order, plus the
email the customer signed in or started their guest session with. A customer
of a restaurant that delivers chooses Pickup or Delivery there: the address is
priced against the restaurant's rings on the quote and again when the order is
created, and the fee is charged with the food. A manager still assigns the
driver. Name and phone are kept on the order as a snapshot and shown on the
kitchen ticket and the driver's card; the customer's row keeps the latest copy
to fill in next time.

## The payment sequence

This is the part worth reading before changing anything.

```
1. POST /api/v1/orders
   Server reprices the cart from the database. The browser's numbers are
   ignored. Order committed as PENDING_PAYMENT with immutable snapshots.
   No money has moved.

2. POST /api/v1/orders/{id}/payment-intent
   PaymentIntent created on the RESTAURANT's connected account. Stripe's
   idempotency key is derived from the order id, so retries return the same
   intent instead of creating a second one.

3. Customer confirms in the browser via Stripe Elements.
   Stripe returning "succeeded" here does NOT mark the order paid.

4. POST /api/v1/webhooks/stripe/connect
   Signature verified. Event persisted to stripe_events with a UNIQUE event
   id. Celery enqueued by row id. 200 returned immediately.

5. Celery worker
   Loads the event as zenoeats_system. Resolves order → restaurant and the
   restaurant's configured stripe_account_id. Proves the event's account
   matches. Opens a SEPARATE zenoeats_app transaction with SET LOCAL
   app.current_tenant. Marks payment PAID and moves the order
   PENDING_PAYMENT → AUTO_ACCEPTED → PREPARING.

6. The customer's page polls GET /orders/{id} and sees the real state.
   If the payment has sat in PROCESSING for 10 seconds with no webhook, the
   poll also reads the PaymentIntent back from Stripe (after responding, at
   most every 10 seconds per payment) and applies it through the same
   handlers as step 5. The 5-minute expiry sweep does the same before it
   expires anything, so a lost webhook cannot expire a charged order.
```

Three rules hold this together. The order row exists before any charge. Only
Stripe can say PAID: a verified webhook, or the intent read back from Stripe
with the platform key, never the browser. A partial unique index on
`payments (order_id) WHERE succeeded_at IS NOT NULL` means a second
successful payment on one order is impossible at the database level, even
after a refund.

## The three portals

All three are the same React app, built by Vite. Which one you get depends
on the URL.

| URL | Who | What |
|---|---|---|
| `https://spicehouse.zenoeats.local:8443/` | Customers | Menu, cart, checkout, order tracking |
| `https://spicehouse.zenoeats.local:8443/manage` | Restaurant staff | Kitchen board, deliveries, menu builder, staff, reports |
| `https://admin.zenoeats.local:8443/admin` | Platform | Create and activate restaurants, platform reports |

The restaurant screens must be opened **on that restaurant's subdomain**. The
tenant is resolved from the `Host` header and nothing else, so
`localhost:3000/manage` will not work. Always browse through nginx at
`https://<slug>.zenoeats.local:8443`. Development is HTTPS only: there is no
plain-HTTP address.

`admin` is a reserved slug, so it never resolves as a tenant. The super admin
endpoints do not use tenant context at all; they run through the audited
system read surface.

### Restaurant screens

- **Kitchen** polls every five seconds. Unpaid orders never appear here.
  A new order chimes (once sound is switched on with a tap, which browsers
  require), is marked "new" and counts in the tab title. Tickets show
  quantity, modifiers and notes, and time from payment, turning red past
  fifteen minutes. "Done today" lists today's handed-over and cancelled
  orders, searchable by number, with who did it and any reason given. "Collect with PIN" needs the customer's six digits;
  five wrong attempts locks that order. Anyone on the floor -- kitchen and
  cashier included -- can hand an order over without the PIN or cancel a
  paid one, each with a reason kept against their name. **Cancel and
  refund** sends the whole amount back to the customer's card, Zenoeats' fee
  included. Untick it for a no-show the restaurant is still charging for. A
  cancelled order not yet refunded waits under **Refunds to issue**, one
  click from refunding for a manager. Partial refunds, and refunds after collection, are
  made in the restaurant's own Stripe Dashboard, and a ticket refunded there
  is marked "refunded" too. The customer is told either way, by email and on
  their order page, with the amount and the 10 to 14 business days a refund
  takes. If Stripe refuses a refund, every admin and manager is emailed.
- **Deliveries** is for the orders a restaurant runs out itself. A delivery
  starts one of two ways. A customer chooses Delivery at checkout, which a
  restaurant offers once it has set its delivery rings. Or a manager sends a
  paid collection out and types the address taken by phone. Either way a
  manager assigns one of the restaurant's drivers. The kitchen's "ready" then
  means ready for the driver,
  and the driver marks it picked up, then delivered -- no PIN at a doorstep,
  so the driver saying so is what completes it, recorded against them. A
  driver sees their own deliveries and nothing else; a manager sees them all
  and can press the same buttons for a driver whose hands are full. A manager
  can also hand the order to a different driver, or take it back to being a
  collection -- until the driver has it, at which point the choices are let
  them deliver it or cancel.
- **Stock** is the sold-out toggle for everyone on the floor: sold-out items
  first, a search box, one button per item.
- **Menu** has four tabs. *Items* is everything the restaurant sells, each
  with a type and the meal periods that serve it, plus the sold-out toggle.
  *Meal periods* adds a period and chooses what it serves, pulling from that
  item list. Item types are managed above the item list, on the Items tab. *Combos* bundles a period's items into meal deals with a
  discount. *Modifier library* creates reusable groups, each shown on any
  number of item types. Options are entered a row at a time, name beside
  price change; a blank price means no change and a negative one is allowed,
  like `-0.50` for no cheese.
- **Staff** sends invitations. An invited person shows as "waiting to accept"
  and has no access until they sign in to this restaurant and accept. An admin
  can change a member's role, which applies on their next click, and reset a
  forgotten password: the person is signed out everywhere and gets a
  temporary password, shown once and emailed to them. Joining, a role change
  and being removed are emailed to the person too, and whoever sent an
  invitation hears when it is accepted. You cannot remove yourself, change your own
  role, or leave the restaurant without an active admin. Resets are refused
  for another admin, and for a login that also works at another Zenoeats
  restaurant; Zenoeats support resets those.
- **Storefront** is available to admins and managers when the platform enables
  Storefront customisation. At `/manage/storefront`, save banners, category
  shortcut photos and visibility, featured collections, and brand settings
  independently. Uploads are drafts until saved; the live preview shows unsaved
  choices. Category order stays on Menu. Collections reference existing items
  and always use today's menu prices and availability. Turning the module off
  restores the original customer appearance without discarding saved settings.
- **Settings** is the admin's own screen, in six parts. *Your account* is
  your display name and the address you sign in with -- the name saves on its
  own, the address asks for your password, since it is a credential and a name
  is not. *The restaurant* is the trading name, tagline, the **phone number**
  customers see on their order page and in every order email (a restaurant
  cannot be activated without one), and whether you are taking orders. *Where you are* is the pickup address, which is also the
  address your sales tax is worked out for, and your timezone, which decides
  which day an order counts on in reports. *Delivery* is below. *Tax* is a flat
  rate or Stripe Tax, which needs a connected account that has finished its own
  tax setup -- until it has, the option says so rather than offering a switch
  that would be refused. Your subdomain, status and currency are shown but not
  editable, under *Set by Zenoeats*: the first is printed on your tables, the
  second has its own readiness checks, and the third is what your existing
  orders are counted in.
- **Delivery**, inside Settings, is three things in the only order that works.
  Place the restaurant on the map, which geocodes the pickup address and is
  what every distance is then measured from. Draw the rings: each is how far it
  reaches and what it costs, typed as "3 miles, $4" -- the inner edge is the
  previous ring's outer one, so a gap is impossible, and past the last ring is
  no delivery rather than free delivery. Then switch delivery on, which is
  refused until the first two exist. Editing the address afterwards drops the
  coordinates and pauses delivery until the restaurant is placed again, because
  measuring from where a restaurant used to be would charge every customer the
  wrong fee and nothing about editing a street says so. Under a flat rate you
  also say whether your state taxes the fee; under Stripe Tax you do not,
  because Stripe is handed the amount and decides for the jurisdiction.
- **Reports** covers today, yesterday, the last 7 days, this month or chosen
  dates -- the restaurant's own days, in its timezone, with each order counted
  on the day it was paid there. It shows net sales (gross less refunds, whether
  made from the board or the Stripe Dashboard), paid orders, average order, tax net of refunds,
  combo discounts, cancelled orders, a by-day breakdown, top items (leaving out
  cancelled and fully refunded orders), and checkouts that expired unpaid.
  Deliveries are counted apart from collections and per driver -- the same
  sales, split, not added -- since they cost the restaurant someone's time in
  a car.

### Staff roles

Every member of a restaurant's team has one of six roles. The same person
can hold a different role at another restaurant.

| Role | Kitchen | Deliveries | Stock | Menu | Staff | Reports | Storefront* | Settings |
|---|---|---|---|---|---|---|---|---|
| Admin | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Manager | ✓ | ✓ | ✓ | ✓ | | ✓ | ✓ | |
| Kitchen | ✓ | | ✓ | | | | | |
| Cashier | ✓ | | ✓ | | | | | |
| Driver | | ✓ | | | | | | |
| IT support | read | | read | read | | | ✓ | ✓ |

*Storefront requires the platform's per-restaurant switch. Only a super admin
can change that switch, from the restaurant edit form; the change is audited.

A driver is not floor staff with an extra screen. Deliveries is the whole
portal to them, and it shows only the orders assigned to them.

IT support is the other role that is not floor staff: it works on the
restaurant rather than in it. Three of its screens open read-only, and the
portal shows them that way -- the board loses its two buttons, the stock list
its toggles, and the menu builder keeps only its preview tab. What the role
does own is the setup a restaurant rings support about: the storefront's
presentation, the trading name and address, the timezone, the tax rate and
the delivery rings. Every one of those changes is audited to the person who
made it.

What it deliberately cannot do is change what is sold, act on a live order,
read the takings or touch the team. Those are the four ways a support login
would become a way to move money or take over the restaurant, and not having
them is what makes the role safe to give to someone outside it.

The actions split further:

| Action | Admin | Manager | Kitchen | Cashier | Driver | IT support |
|---|---|---|---|---|---|---|
| See the board and today's history | ✓ | ✓ | ✓ | ✓ | | ✓ |
| See what is sold out | ✓ | ✓ | ✓ | ✓ | | ✓ |
| Read the menu | ✓ | ✓ | | | | ✓ |
| Mark ready, collect with PIN | ✓ | ✓ | ✓ | ✓ | | |
| Mark items sold out or back in stock | ✓ | ✓ | ✓ | ✓ | | |
| Hand over without the PIN (with a reason) | ✓ | ✓ | ✓ | ✓ | | |
| Cancel a paid order, refunding or not (with a reason) | ✓ | ✓ | ✓ | ✓ | | |
| Refund an order already cancelled | ✓ | ✓ | | | | |
| Edit storefront presentation (when enabled) | ✓ | ✓ | | | | ✓ |
| Edit the menu | ✓ | ✓ | | | | |
| Read reports | ✓ | ✓ | | | | |
| Send an order out with a driver | ✓ | ✓ | | | | |
| Pick up and deliver | ✓ | ✓ | | | ✓ | |
| Invite and remove staff, change roles, reset passwords | ✓ | | | | | |
| Edit the restaurant's name, address, timezone and tax | ✓ | | | | | ✓ |
| Set the delivery area, its fees and whether they are taxed | ✓ | | | | | ✓ |
| Change your own display name and sign-in address | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |

The last row is not an oversight. Your own name and your own login belong to
you whatever you do at the restaurant, so a driver may change theirs exactly as
an owner may. Only the Settings screen offers it today, which admins and IT
support can open, so a screen for the rest of the team is a route away rather
than a rewrite.

### How roles are enforced

The API decides; the portal only follows. Every restaurant endpoint runs
these checks in order, and any one of them refuses the request:

1. **Who you are.** The staff session cookie is a signed token naming a
   person, never a restaurant. It is refused if expired, if it was issued
   before a password change or reset (`users.sessions_valid_after`), or if
   the account is inactive. An account still holding a temporary password can
   only ask who it is, sign out, and change that password. "Sign out" is
   this device only, because restaurants share logins across tablets; "Sign
   out all devices" ends every session the account holds.
2. **Which restaurant.** The tenant comes from the `Host` header and nothing
   else, so a request cannot name a restaurant it is not on.
3. **Your role there.** `require_staff(...)` in `app/api/deps.py` reads your
   `restaurant_users` row for that restaurant, under row-level security, and
   requires it to be `ACTIVE` with a role in the endpoint's list. It is read
   on every request, so removing someone or changing their role takes effect
   on their next click. The lists live at the top of the staff API in
   `app/api/v1/restaurant.py`: `MANAGE` (Admin, Manager), `ANY_STAFF` (the
   four who work the floor, deliberately not Driver), `STAFF_ADMIN` (Admin
   alone), `DELIVERY` (Admin, Manager, Driver) and `OWN_ACCOUNT` (everyone,
   for the two endpoints that are about you rather than about the
   restaurant). IT support adds four more, each one of those plus
   `IT_SUPPORT`: `FLOOR_VIEW` and `MENU_VIEW` are the read halves of
   `ANY_STAFF` and `MANAGE`, with every write beside them left on the
   original, while `STOREFRONT` and `SETTINGS` are read and write both.
   Keeping the reads and the writes on separate lists is what makes the role
   read-only where it is meant to be, rather than a promise in a comment.
4. **Row-level security.** The query itself runs with
   `app.current_tenant` set, so even a wrong role check could not read or
   write another restaurant's rows.
5. **Origin pinning.** A request whose `Origin` is another hostname is
   refused, so a page on a neighbouring subdomain cannot use a signed-in
   operator's cookie.

The portal mirrors the role lists in `web/src/features/restaurant/nav.ts` to
decide which tabs and buttons to show, and a page opened outside your role
says so instead of loading. That is a convenience: removing it would change
what people see, not what they can do.

`tests/test_role_coverage.py` holds the whole map of endpoint to roles. It
fails if an endpoint is added without a role check, or if an endpoint's roles
change without the map changing with it. So adding an endpoint, or widening
one, means editing that table on purpose. When you do, update the tables above
and `nav.ts` to match. Two further tests in that file are about IT support
alone: one fails if the role gains a write outside its own configuration, and
one names the endpoints it must never reach -- cancel, override, item edits,
reports and the whole staff screen -- so a widening that reaches one of them
is caught even if the map was edited to allow it.

### Super admin screen

Create a restaurant (starts in draft), create its owner, connect its Stripe
account through hosted onboarding, then activate. Activation is gated on
payments only: it refuses unless the connected account has charges enabled
(and, for a Stripe Tax restaurant, working tax settings). The menu is not
part of the gate, so a restaurant can go live and fill its menu afterwards.

Activating also registers the storefront's domain for **Apple Pay and Google
Pay** on the restaurant's connected account. Without that registration Stripe
shows only the card form. **Refresh Stripe** re-reads the account from
Stripe, and does the same registration for a restaurant already live. The
row then shows "Apple Pay active · Google Pay active", or Stripe's reason if
not. A wallet problem never blocks activation, because cards work
regardless.
Every read on this page writes to `platform_audit_logs` with your user, the
scope requested, and a correlation id.

## Running it

### 1. Local DNS

Wildcard subdomains need to resolve. Add to `/etc/hosts`
(`C:\Windows\System32\drivers\etc\hosts` on Windows, edited as administrator):

```
127.0.0.1  zenoeats.local
127.0.0.1  spicehouse.zenoeats.local
127.0.0.1  admin.zenoeats.local
```

On macOS and Linux you can also use `dnsmasq` to wildcard `*.zenoeats.local`
instead of listing each slug.

### 2. Configure

```bash
make setup                     # copies .env.example to .env
make key                       # prints a Fernet key -> FIELD_ENCRYPTION_KEY
cp web/.env.example web/.env   # the frontend's own small file
```

Then fill in `.env`. Only `FIELD_ENCRYPTION_KEY` and `SESSION_SECRET` are
required; every other setting is optional, and the file says what you lose
without it. What follows is the **development** setup, with test keys.

`.env.example` is the development template only. Production has its own,
[`.env.production.example`](.env.production.example), which
`scripts/make_prod_env.py` fills in on the server (see [Production](#production)).
Production accounts (Stripe live, a Clerk production instance, Google keys for
the real domain) are in the [Production setup guide](#production-setup-guide).

**Sessions.** Set `SESSION_SECRET` (`openssl rand -base64 32`). It signs the
admin, staff and guest session cookies.

**Email (optional).** Without a SendGrid key nothing is emailed and the app
works otherwise. To see emails arrive, follow [Email](#email): a key, one
verified sender address in `EMAIL_FROM`, and `make worker` running.

**Clerk (customers).** Create one application. Enable *Email address* +
*Password*, and *Google* under social connections if you want the button
(development instances use Clerk's shared Google credentials, so there is
nothing to set up at Google). Copy the publishable key into
`VITE_CLERK_PUBLISHABLE_KEY`, and the secret key, JWKS URL and issuer into the
`CLERK_*` settings. For production, set the primary domain to the parent
(`zenoeats.com`) so one sign-in covers every restaurant subdomain, and add a
webhook endpoint at `https://yourdomain/api/v1/webhooks/clerk` for
`user.created`, `user.updated` and `user.deleted`.

**Google Maps (delivery only).** Attach a billing account to the Google Cloud
project and enable **Maps JavaScript API**, **Places API (Legacy)**,
**Geocoding API**, and **Routes API**. API activation itself is not billed;
Google charges for usage after the applicable free allowances.

Create two separate credentials and put them in the root `.env`:

```dotenv
# Private backend credential. Never expose this value to browser code.
GOOGLE_MAPS_API_KEY=replace_with_server_key

# Public browser credential. Its website and API restrictions protect it.
GOOGLE_MAPS_BROWSER_KEY=replace_with_browser_key
```

Configure the **server key** with API restrictions for **Geocoding API** and
**Routes API**. In production, add an IP-address application restriction for
the API server's fixed outbound IP. An HTTP-referrer restriction will break
this key because calls originate from the backend.

Configure the **browser key** with the **Websites** application restriction,
allow `https://*.zenoeats.local:8443/*` for local development, and add
each deployed storefront origin before release. Restrict this key to **Maps
JavaScript API** and **Places API (Legacy)**. Do not reuse the server key as
the browser key.

The server key geocodes delivery addresses and calculates arrival estimates.
The browser key provides the checkout suggestion list and customer tracking
map. Delivery cannot be quoted when server geocoding is unavailable; checkout
continues to accept manual addresses if browser suggestions fail. Coordinates
are cached in Redis for 30 days to reduce requests.

After changing these settings, restart the API so the public portal response
contains the browser configuration. For the normal native development setup,
stop the terminal running `make api` and start it again:

```bash
make api
```

When rehearsing the containerized application instead, run:

```bash
docker compose --profile app restart api
```

Open the checkout page, choose **Delivery**, and type part of an address. A
Google suggestion list should appear. Select a suggestion and confirm that a
delivery quote replaces the address-checking message. If Google reports
`REQUEST_DENIED`, verify billing, key restrictions, enabled APIs, and allowed
website referrers; Google configuration changes can take several minutes to
propagate.

**Stripe.** Enable Connect in test mode. Copy the secret and publishable
keys. Create a webhook endpoint **on the Connect tab** (not the account tab)
pointing at `/api/v1/webhooks/stripe/connect`, subscribed to
`payment_intent.succeeded`, `payment_intent.payment_failed`,
`payment_intent.canceled`, `charge.refunded` and `account.updated`. Copy that
endpoint's signing secret into `STRIPE_CONNECT_WEBHOOK_SECRET`. It is a
different secret from the platform endpoint's.

### 3. HTTPS certificate

Development runs over HTTPS only, at **https://spicehouse.zenoeats.local:8443**.
There is no plain-HTTP port, and nginx will not start without a certificate.
Production HTTPS is `docker-compose.prod.yml` and `infra/nginx/production/`,
not this.

1. **Make the certificate** (once; run again yearly to renew):

   ```powershell
   bash scripts/local_https_cert.sh
   ```

   It creates a local certificate authority in
   `%USERPROFILE%\zenoeats-secrets\local-ca\` (outside the repository). The
   authority is name-constrained to `zenoeats.local` and can sign nothing
   else. It then writes a certificate for `zenoeats.local` and
   `*.zenoeats.local` to `infra/certs-local/` (git-ignored). `make infra` and
   `make up-all` refuse to start without it.
2. **Trust the local authority**, once, so browsers show the padlock.
   Windows asks you to confirm:

   ```powershell
   Import-Certificate -FilePath "$env:USERPROFILE\zenoeats-secrets\local-ca\ca.crt" -CertStoreLocation Cert:\CurrentUser\Root
   ```

   Chrome, Edge and Brave use this immediately; restart them if they were
   open. Firefox keeps its own store: set `security.enterprise_roots.enabled`
   to `true` in `about:config`. A phone used to test driver mode needs the
   same `ca.crt` installed and trusted.

   To remove the trust later:

   ```powershell
   Get-ChildItem Cert:\CurrentUser\Root | Where-Object Subject -eq "CN=Zenoeats local development CA" | Remove-Item
   ```

An address typed as `http://...:8443` is redirected to `https://`. There is
no HSTS, on purpose: a browser that saw it would refuse plain HTTP to every
`zenoeats.local` port for a year.

### 4. Start

Two ways, and only one at a time (`scripts/dev_preflight.py` refuses a
second):

```bash
# Everything in Docker -- closest to production, slowest to rebuild
make up-all                # = docker compose --profile app up -d --build
make seed                  # the demo restaurant and menu, once

# Or infrastructure in Docker, the app native -- fastest for daily work
make infra                 # postgres, both Redis, nginx
make migrate && make seed
make api                   # each in its own terminal
make web
make worker                # only needed for payments and emails
```

Open `https://spicehouse.zenoeats.local:8443`. `make fresh` wipes the
database and starts again from migrations and the seed.

### 5. Connect a real test-mode restaurant account

The seed inserts a placeholder account id. Replace it before paying:

```bash
# Create a test connected account through the API, or in the Stripe dashboard
docker compose exec postgres psql -U postgres -d zenoeats -c \
  "UPDATE restaurant_payment_accounts SET stripe_account_id = 'acct_YOUR_TEST_ID';"
```

### 6. Forward webhooks to localhost

```bash
stripe listen --forward-connect-to localhost:8000/api/v1/webhooks/stripe/connect
```

Copy the `whsec_` it prints into `STRIPE_CONNECT_WEBHOOK_SECRET` and restart
the API (`make api`, or `docker compose --profile app up -d --force-recreate api`
for the container).

The CLI listens on whichever Stripe account it is logged in to, which is not
necessarily the one `STRIPE_SECRET_KEY` belongs to. If they differ, nothing is
forwarded and every order sits on "Confirming your payment" even though Stripe
took the money. Either `stripe login` to the same account, or pass the key:
`stripe listen --api-key "$STRIPE_SECRET_KEY" --forward-connect-to ...`. The
secret it prints depends on the account, so copy it again after switching.

Keep `make worker` running too. The webhook only stores the event; the worker
is what marks the order paid. Without either, the order page still gets there
by asking Stripe after about 10 seconds, but that is the fallback, so a slow
confirmation locally usually means one of the two is not running.

### 7. Pay

Add something to the cart, open the cart and **Go to checkout**. Sign in, or
use **Continue as guest** with any email. Pay with card
`4242 4242 4242 4242`, any future expiry, any CVC and ZIP. The order page
will sit on "Confirming your payment" for a second or two and then flip to
"Being made now", with the pickup PIN, when the webhook lands. That pause is
the system working correctly, not a bug. If `stripe listen` is not running,
the page gets there anyway after about 10 seconds by asking Stripe itself.

Then, signed in as staff at `/manage`: the order is on the board. **Mark
ready for pickup**, then **Collect with PIN** with the customer's six digits.
Or **cancel order** → **Cancel and refund** to see a test-mode refund.

With SendGrid set up and the worker running, the customer's emails arrive
along the way: the confirmation, "ready to collect", and the cancellation
with its refund.

## Verifying tenant isolation

```bash
make rls
```

Seven gates run. The important one creates two restaurants, writes a menu into
tenant B, then queries for it from a tenant A session with no application
filter at all. It must return zero rows. The others assert that no runtime
role has `BYPASSRLS`, that no runtime role owns a table, and that
`zenoeats_system` cannot UPDATE orders or INSERT menu items.

If you change the migration, run these before merging. They are the only
thing standing between you and a cross-tenant data leak.

## Customer sign-in

The pages are ours; the identity is Clerk's. `/account/sign-in`,
`/account/sign-up` and `/account/forgot-password` are plain HTML entries
styled like the rest of the storefront, and they call Clerk's JavaScript SDK
directly -- no Clerk component is rendered.

* **Sign-up** sends a 6-digit code, entered on the same page.
* **Forgot password** sends a 6-digit code, entered with the new password.
* **Google, Apple and Facebook** each get a button when enabled in the Clerk
  dashboard. They go through Clerk and return to `/account/sso-callback`,
  which finishes the sign-in (or sign-up) and continues to checkout. A
  provider that did not supply everything, such as a Facebook account with
  no email, lands on a step that asks for exactly what is missing.
* **Two-step verification** is handled on the sign-in page: authenticator
  app, text, email or a backup code.
* **Guests** skip all of it. **Continue as guest** on the sign-in page takes
  an email (and optionally a name) and signs the browser in to a guest
  session. The confirmation email carries a private link that reopens the
  order and its pickup PIN, and that browser keeps them for 30 days.
* **The API** verifies Clerk's session token on every order request, checks it
  was minted for one of our own hosts, and keeps one `users` row per Clerk
  user. A new customer's email and name are read from Clerk's Backend API the
  first time they appear, so receipts do not wait for the webhook.

The SDK is loaded from the Clerk instance at runtime (about 80 KB), not
bundled, and only on customer pages.

## Policies and consent

Four documents, served as real files by nginx rather than as routes in the
app: `/legal/privacy`, `/legal/terms`, `/legal/refunds` and
`/legal/data-deletion`. They render with no JavaScript and answer on the root
domain as well as every restaurant subdomain, which is what Google, Apple and
Facebook need — a reviewer is given one canonical URL and it cannot be a
tenant's address. Google and Apple require a reachable privacy policy before
they will approve sign-in; Facebook requires the deletion page too.

They are **drafts**. Each opens with a banner saying so and ends with the
questions a lawyer has to answer for that page. `docs/operations/STEPS_BEFORE_PRODUCTION.md`
§9 tracks what is left.

Agreement is asked for in three places and recorded in one:

* **Sign-up** will not submit without the checkbox, and the provider buttons
  are held behind it too. Clerk finishes a social sign-up by itself whenever
  the provider supplied everything, and returns nobody to a consent step — so
  the checkbox has to be passed before the browser leaves for Google.
* **The customer guard** shows a consent form in place of checkout for an
  account that still has nothing on record, which covers anyone who reached
  an account another way. Guests are not gated.
* **Checkout** says above the button that continuing means agreeing, and
  placing the order writes `users.terms_accepted_at` and `terms_version`.
  Guests included, since a guest never signs up.

Bump `CURRENT_VERSION` in `app/services/terms.py` when the wording changes
materially; every customer re-agrees on their next order.

## Email

Two senders, for two different kinds of email:

| Email | Sent by | Set up in |
|---|---|---|
| Customer verification and password-reset codes | Clerk, or Zenoeats through SendGrid once switched over (below) | Clerk's dashboard |
| Everything else, listed below | Zenoeats, through SendGrid | `.env`; the words are templates in the repository |

Clerk cannot send Zenoeats' own: staff are not Clerk users (they sign in with
passwords the platform issues), and Clerk sends no business email.

Zenoeats sends 15 emails:

| To | When |
|---|---|
| Customer | **Order confirmed** when the payment succeeds; **ready to collect** when the kitchen marks a pick-up order ready; **on its way** when the driver picks a delivery up; **delivered**; **cancelled**, with the refund and its 10 to 14 business days when one was issued; **refund on its way** for a refund made later, from the board or Stripe's dashboard |
| Customer account | **Welcome**, from the restaurant whose storefront a brand-new account first opens; **account closed**, once, however it was closed |
| Restaurant staff | **Invitation**; **welcome** on accepting it, and **has joined** to whoever invited them; **role changed**; **removed**; **password reset**, with the temporary password |
| Admins and managers | **A refund didn't go through**, when a cancellation went through but Stripe refused its refund |

The worker sends them all, so `make worker` (or the worker container) must be
running. Each is recorded in the `sent_emails` table before it goes, so none
is sent twice, however often a task or webhook repeats. A pickup PIN is never
in an email; the order page shows it.

SendGrid is optional. With `SENDGRID_API_KEY` empty nothing is sent: the
worker logs each skipped email, and the portal says when an invitation or a
reset password did not go, so the admin passes it on by hand. Order pages
still show everything the emails would have said.

Stripe sends **no** receipt of its own. A `receipt_email` on the payment
would make it email one in live mode whatever the account's settings say,
and another for every refund, duplicating Zenoeats' confirmation and refund
emails. The customer's address still goes to Stripe as the payment's billing
email, which its fraud screening uses.

**Turning it on.** Put a key from SendGrid › Settings › API Keys (with *Mail
Send* access; it starts `SG.`) in `.env` as `SENDGRID_API_KEY`. SendGrid only
sends from an address it has verified; anything else is refused, and the
worker logs the refusal. For development, verify one address under Settings ›
Sender Authentication › *Single Sender Verification* and put it in
`EMAIL_FROM`. Then restart what reads it. A running process keeps the settings
it started with: restart `make api` and `make worker`, or recreate the
containers:

```
docker compose --profile app up -d --force-recreate api worker
```

For real staff and customers, authenticate your whole domain instead (step 5
of the production guide below) and set:

```
EMAIL_FROM=Zenoeats <orders@yourdomain.com>
EMAIL_REPLY_TO=an-inbox-you-read@yourdomain.com
STOREFRONT_URL_TEMPLATE=https://{slug}.{root_domain}
```

`STOREFRONT_URL_TEMPLATE` is where links inside emails point. Development is
`https://{slug}.{root_domain}:8443`, through nginx; getting it wrong sends
people a link that goes nowhere.

**Checking it.** Each attempt is in the worker's log, sent or not:

```
docker compose --profile app logs worker | Select-String "sent|rejected|SENDGRID"
```

**Clerk's emails through SendGrid (optional).** Clerk can hand its own
customer emails (sign-up verification codes, password-reset codes and the
rest) to Zenoeats instead of sending them itself, so every email a customer
gets comes from the same sender. The verification and reset codes then use
Zenoeats' own template (`customer_code/`); any other kind is sent in Clerk's
words. The code is never stored or logged.

**Do this only where Clerk's webhook reaches the app**, which means
production, or a laptop behind a public tunnel. Once Clerk stops sending an
email itself, the webhook is the only way it arrives. If Clerk can't reach
the app, nobody can sign up or reset a password until you switch it back.

1. Clerk › **Webhooks** › your endpoint (`https://<domain>/api/v1/webhooks/clerk`)
   › subscribe it to **`email.created`** as well as the `user.*` events.
2. SendGrid set up as above, and the worker running.
3. Clerk › **Customization › Emails**. For **Verification code** and
   **Reset password code**, turn off **Delivered by Clerk**. Do one, test it,
   then the other.
4. Sign up with a new address and check the code arrives from `EMAIL_FROM`.

To undo it, turn **Delivered by Clerk** back on; nothing else changes.

**Editing an email.** The words and layout of every email are templates in
[`backend/app/templates/email/`](backend/app/templates/email/), one folder per
email, with a README saying how to edit them. To see a change without sending
anything, run `python scripts/preview_emails.py` from `backend/` and open the
`index.html` it prints.

## Development without Clerk

For backend work you can skip Clerk entirely:

```
AUTH_DEV_BYPASS=true
```

The `Authorization: Bearer` header is then read as a bare Clerk user id. The
seed creates `user_dev_customer`.

```bash
curl -H "Authorization: Bearer user_dev_customer" \
     -H "X-Zenoeats-Restaurant: spicehouse" \
     -H "Idempotency-Key: $(uuidgen)" \
     -H "Content-Type: application/json" \
     -d '{"items":[{"menu_item_id":"...","quantity":1,"modifiers":[]}]}' \
     http://localhost:8000/api/v1/orders
```

The app refuses to boot with `AUTH_DEV_BYPASS=true` and `ENV=production`.

## Menu model

```
ItemType                "Food"  "Drinks"  "Sides"  "Sauces"   <- the restaurant's own
  ItemType                "Burgers"  parent=Food                <- optional, one level

Item  type=Burgers      "Smash Burger"
  ModifierGroup           "Veggies"      MULTI,  0-5, optional
    ModifierOption          "Lettuce"    +$0.00
    ModifierOption          "Jalapenos"  +$0.50
Item  type=Drinks       "Iced Tea"
  ModifierGroup           "Ice level"    SINGLE, 1-1, required
    ModifierOption          "Light" / "Regular" / "Heavy"
Item  type=Sides        "Fries"
Item  type=Sauces       "Garlic Aioli"

Meal                    "Lunch"     serves all four
Meal                    "Dinner"    serves the burger, the tea and the fries

Combo "Burger Meal"     sold during Lunch, 10% off
  slot Food               Smash Burger
  slot Drinks             Iced Tea
  slot Sides              Fries
```

Item types are rows, not an enum. Four hard-coded words meant a tiffin house
filed tiffins, thalis and chaat under "Food" and read a stranger's vocabulary
back on its own menu. A restaurant is created with Food, Drinks, Sides and
Sauces as a starting point and renames, reorders, adds to or deletes them from
the portal. `item_types.sort_order` is the order headings read down a
storefront. Deleting a type still on items is refused rather than cascading,
because the alternative is taking real menu items with it.

A type may name a parent, which makes it a subcategory: Food holding Burgers
and Nuggets, Drinks holding Hot Beverages. Two levels and no more, capped by a
composite foreign key rather than by a rule the code has to remember, so
nothing walks a tree. Leaving the parent blank is the ordinary case and the
one most menus stay in.

The nesting is a heading on the storefront and nothing else. Combos and the
modifier-group filter read the top-level type, so a slot asking for a food
offers burgers and nuggets together and a group offered for Food reaches every
burger. Making Burgers and Nuggets top-level types instead would have split
that one slot into two, each offering half the choice, and forced the group to
be named against both. `sort_order` on a subcategory orders it among its
siblings, not across the menu. A heading with subcategories under it cannot be
deleted or filed under a third type while they are there.

An item belongs to the restaurant, not to a meal period, and a period serves
it through `meal_items`. So one item can be on breakfast and lunch alike, with
one price and one sold-out toggle. The headings a customer reads inside a
period are derived from the types of the items served, not stored. A
subcategory becomes a block inside its parent's heading rather than a heading
of its own, and what is filed on the heading itself reads before it.

A combo is one item from each of several item types, sold together for less. It
belongs to one meal period and may only offer items that period serves. Each
slot takes exactly one item of one type and is always required. The discount is a
percentage in basis points or a flat amount in minor units, and it is capped
at what the chosen items cost.

A combo is not an order line. It becomes one line per slot at each item's own
price, tagged with `combo_id`, `combo_name_snapshot` and `combo_group`, and
the saving lands in `orders.discount_minor`. So the subtotal is still what the
food costs, the saving is a figure a receipt can show, and the kitchen sees
the items it has to plate.

Modifier groups belong to the restaurant, not to a single item, and attach
through `item_modifier_groups`. Define "Ice level" once and reuse it on every
drink. The item types a group names filter the library in the menu builder, so
adding a drink surfaces Ice level rather than Veggies. It is a list, so a
"Size" group can be offered on drinks and sides at once; naming none means
every type.

`modifier_options.price_delta_minor` is the one money column without a
non-negative constraint, because "no cheese −$0.50" is legitimate. Everything
else is `BIGINT` minor units with `CHECK >= 0`.

## Project layout

```
backend/
  app/core/         auth, tenant resolution, money, crypto, idempotency,
                    stall-watch (says which line blocked the event loop)
  app/db/           engines and the SET LOCAL tenant session; every wait bounded
  app/models/       SQLAlchemy models, frozen enums, transition matrix
  app/services/     pricing, orders, tax, Stripe, storefront, images, terms,
                    ops_health (what /health/operations checks), and the
                    emails: order_emails, staff_emails, account_emails
  app/templates/email/  every email's words and layout, one folder each
  app/api/v1/       portal, orders, customer, restaurant, admin, webhooks
  app/workers/      Celery app and tasks
  alembic/          schema, RLS policies, role grants
  tests/            unit and integration tests plus the RLS gates
  scripts/          seed.py (demo data), hash_password.py (ADMIN_USERS entries),
                    preview_emails.py (every email rendered, nothing sent)
web/
  src/routes/             the route table, and one lazy area per portal
  src/pages/storefront/   customer: menu, checkout, order tracking
  src/pages/manage/       restaurant: kitchen, menu builder, staff, reports,
                          storefront editor
  src/pages/admin/        platform: restaurants, onboarding, reports
  src/features/           cart and session state, one RTK Query API per portal
  src/services/           HTTP client and the RTK Query base query
  src/components/         modifier sheet, cart bar, operator shell, guards
  login/                  the sign-in pages, deliberately outside React
  legal/                  privacy, terms, refunds, deletion -- files, no React
  qa/, tests/             browser regression checks against fixtures
  nginx.conf              static serving: SPA fallback, real files for
                          /login and /legal
infra/
  postgres/         role creation, runs on first boot; passwords from the
                    environment in production
  nginx/            development edge; production/ is the HTTPS edge
  systemd/          the nightly backup timer for the production server
scripts/
  dev_preflight.py  refuses to run the app natively and in Docker at once
  check_storefront.py  read-only latency check through the edge
  make_prod_env.py  writes a production .env with fresh secrets; --check
  backup.sh         nightly encrypted backup of the database and images
  restore_drill.sh  restores a backup into a scratch database and proves it
  local_https_cert.sh  the development certificate; nginx needs it (:8443)
docs/               everything written beyond the code; index in docs/README.md
  operations/       docs/operations/STEPS_BEFORE_PRODUCTION.md: go-live list, setup, deploy
  security/         the audit report, test matrix, deployment checklist
  development/      docs/development/URLS.txt: every local address
  design/           the storefront redesign brief and its records
.github/
  workflows/ci.yml  every check a pull request must pass
  CONTRIBUTING.md   how to set up, change, test and release
  SECURITY.md       how to report a vulnerability
  CODEOWNERS        who reviews which paths
.env.example             development settings, copied to .env
.env.production.example  production settings, filled in by make_prod_env.py
docker-compose.yml       development: infrastructure and the HTTPS edge on
                         :8443, plus the app behind --profile app
docker-compose.prod.yml  production override: GHCR images, passwords, no
                         internal ports, HTTPS edge
CHANGELOG.md             what changed in each release
```

### What a customer downloads

One app, three portals, but not one bundle. The page a QR code opens is a
menu on a phone, and it used to carry the kitchen board, the menu builder and
the platform admin screens — a third of its weight, none of it openable by
the person holding the phone.

```
entry      ~70 kB   the shell and the storefront
vendor    ~330 kB   React, the router, Redux -- cached across deploys
manage    ~150 kB   fetched when someone opens /manage
admin      ~24 kB   fetched when someone opens /admin
```

The boundary is the import graph, not a config list: each portal owns its own
sub-routes *and* its own guard behind one lazy import, because listing a page
in the main route table is what drags it back into the shell. `Guards.tsx` is
split for the same reason — the operator guards import both portal APIs and
the manage shell, so they live behind the boundary rather than beside it.

CI asserts it, since one static import undoes the whole thing and leaves no
other trace.

## Tests

| Suite | Run | What |
|---|---|---|
| Backend | `make test` (native), or in a container, below | 1,000 tests: pricing, orders, payments and webhooks, refunds, tax, roles, every email and its send-once record, the seven RLS gates, health, startup checks |
| Tenant isolation | `make rls` | The RLS gates alone (see above) |
| Web | `node --test tests/*.test.mjs` in `web/` | 35 tests: storefront presentation, calories, maps, profile |
| Web checks | `npm run lint`, `npx tsc --noEmit`, `npm run build` in `web/` | |

With the app running in Docker, the backend suite runs in a one-off
container against the same Postgres and Redis:

```bash
docker compose --profile app run --rm --no-deps   -e ROOT_DOMAIN=zenoeats.local -e IMAGES_DIR=/tmp/images   -e REDIS_RUNTIME_URL=redis://redis-runtime:6379/0   -e CELERY_BROKER_URL=redis://redis-broker:6379/0   -v "$PWD/backend:/srv" migrate sh -c "mkdir -p /tmp/images && python -m pytest tests -q"
```

CI (`.github/workflows/ci.yml`) runs all of it on every push and pull request,
plus a dependency audit, the reversible-migration check and a bundle-size
guard. On `main` and on version tags it publishes the `api` and `web` images
to GHCR.

## Health checks

| Path | Answers | Use |
|---|---|---|
| `/health` | always 200 while the process runs | container liveness |
| `/health/ready` | 503 unless the database answers as the app role | uptime monitor |
| `/health/operations` | 503 naming what is failing: `worker_heartbeat`, `webhooks_stuck`, `webhooks_failed`, `stale_checkouts`, `redis_runtime`, `redis_broker`, `database`, `disk` | uptime monitor |

`/health/operations` watches the parts that fail without an error:
- a stopped worker leaves payment webhooks unprocessed
- a stopped beat leaves abandoned checkouts unexpired
- a Redis outage turns the rate limiter off
- a full disk stops Postgres

Beat schedules a heartbeat task every minute, and the worker records it, so
a stale heartbeat means either has stopped. The endpoint gives names only,
never counts, because it is public. All three paths answer on every host.

## Production

The target is a single Linux VM (2 vCPU / 4 GB is a sensible floor) running
the images CI publishes. Nothing is built on the server. Its `.env` is
generated from [`.env.production.example`](.env.production.example), which
explains every setting:

```bash
python3 scripts/make_prod_env.py --domain <domain> --release v1.3.1   # once
python3 scripts/make_prod_env.py --check .env                         # until clean
export COMPOSE_FILE=docker-compose.yml:docker-compose.prod.yml
docker compose --profile app pull && docker compose --profile app up -d
```

What the production shape guarantees:

- **Configuration.** The API refuses to start with an unsafe configuration:
  a short session secret, missing Clerk or Stripe keys, **test keys**, a dev
  domain, or the owner's database URL. `docker-compose.prod.yml` refuses to
  start without real database and Redis passwords.
- **Network.** Only nginx listens, on 80/443 with HTTPS and HSTS. Postgres,
  Redis, the API and web publish no port. The API trusts forwarding headers
  from nginx's fixed address alone, and the edge takes the visitor's IP from
  Cloudflare only on Cloudflare's ranges.
- **Migrations.** They run as a one-off job with the owner's credentials,
  before the API starts. Runtime processes never hold them.
- **Backups.** `scripts/backup.sh`, nightly by systemd, takes the database
  and the images together, encrypted with age to a key the server does not
  hold, off-site with rclone. `scripts/restore_drill.sh` proves a backup
  restores whole.

[`docs/operations/STEPS_BEFORE_PRODUCTION.md`](docs/operations/STEPS_BEFORE_PRODUCTION.md) is the launch
checklist, with what is done and what is open: accounts, the domain, backups,
monitoring, legal, a staging rehearsal. Its §11 is the first deploy, command
by command. Keep it current rather than a list here. Every account and
setting it needs is explained step by step in the
[Production setup guide](#production-setup-guide) below.

## Production setup guide

Every external account and setting that production needs: what to create,
where to click, and which setting in `.env` each value fills. Do them in the
order below; later sections assume earlier ones. The day-by-day checklist,
with progress ticks, is the go-live list at the top of
[`docs/operations/STEPS_BEFORE_PRODUCTION.md`](docs/operations/STEPS_BEFORE_PRODUCTION.md).

Throughout, `<domain>` is your root domain (for example `zenoeats.com`).
Restaurants live at `<slug>.<domain>` and the platform portal at
`admin.<domain>`. Create every production account **separately** from the
development ones; never reuse development keys.

| # | Service | Needed for | Settings it fills |
|---|---|---|---|
| 1 | Cloudflare (domain, DNS, TLS) | Everything else | `ROOT_DOMAIN`, `infra/certs/` |
| 2 | Stripe (live, Connect) | Taking payments | `STRIPE_*`, `PLATFORM_FEE_*` |
| 3 | Clerk (production instance) | Customer sign-in | `CLERK_*`, `VITE_CLERK_PUBLISHABLE_KEY` |
| 4 | Google Maps Platform | Delivery: addresses, fees, tracking map | `GOOGLE_MAPS_API_KEY`, `GOOGLE_MAPS_BROWSER_KEY` |
| 5 | SendGrid | Order and staff emails | `SENDGRID_API_KEY`, `EMAIL_FROM`, `EMAIL_REPLY_TO` |
| 6 | Sentry | Error tracking | `SENTRY_DSN` |
| 7 | Cloudflare R2, healthchecks.io | Off-site backups | `/etc/zenoeats/backup.env` |
| 8 | UptimeRobot (or similar) | Downtime alerts | — |
| 9 | Platform administrators | The super admin portal | `ADMIN_USERS` |

Generate the `.env` first, on the server, and fill it in as you go:
`python3 scripts/make_prod_env.py --domain <domain> --release v1.3.1`. It
creates every secret and password itself, and marks each value that has to
come from one of the accounts below with `# FILL IN:`. Run
`python3 scripts/make_prod_env.py --check .env` at any point to see what is
still missing.

### 1. Domain, DNS and HTTPS (Cloudflare)

1. **Buy the domain** from any registrar. Cloudflare Registrar sells at cost.
2. **Add it to Cloudflare** (Free plan): *Add a site*, then replace the
   nameservers at the registrar with the two Cloudflare shows. Wait until
   the site is *Active*.
3. **DNS → Records.** Point all three at the server's public IPv4 address,
   **Proxied** (orange cloud):

   | Type | Name | Content |
   |---|---|---|
   | A | `@` | server IP |
   | A | `*` | server IP |
   | A | `admin` | server IP |

   Records that Clerk (§3) and SendGrid (§5) ask for later must be **DNS only**
   (grey cloud).
4. **SSL/TLS → Overview:** mode **Full (strict)**. **Edge Certificates:**
   *Always Use HTTPS* on, *Minimum TLS Version* 1.2.
5. **SSL/TLS → Origin Server → Create Certificate:** RSA, hostnames
   `<domain>` and `*.<domain>`, validity 15 years. On the server, save the
   certificate as `infra/certs/fullchain.pem` and the private key as
   `infra/certs/privkey.pem`, then `chmod 600` the key. The folder is
   git-ignored.
6. In `.env`: `ROOT_DOMAIN=<domain>` (set by `make_prod_env.py --domain`).

Visitors see Cloudflare's publicly trusted certificate, so there is nothing
to install on any device. The origin certificate only secures the hop from
Cloudflare to the server.

### 2. Stripe (live payments, Connect)

Zenoeats is a Stripe **Connect platform** using **direct charges**. Each
restaurant has its own connected Stripe account: it is the merchant of
record, is paid out directly, and handles its own refunds and disputes.
Zenoeats' optional commission is an application fee on each charge.

**2.1 Activate the platform account.** Create a Stripe account for the
business (or use your existing one), then complete *Activate payments*:
- legal entity and address
- representative and identity verification
- bank account for payouts
- statement descriptor

Stripe's review can take **1–3 business days**, so start this first.

**2.2 Set up Connect.** Dashboard → **Connect** → complete the platform
onboarding and **platform profile**. The app creates each connected account
through the API with its own full Stripe Dashboard, liable for its own fees
and losses, and charges directly on that account. Choose the options that
match (a platform whose sellers are businesses with their own Dashboard, and
direct charges). Under **Connect → Settings → Branding**, add the name, icon
and colour restaurants will see during onboarding.

**2.3 Live API keys.** Dashboard in **live mode** → **Developers → API
keys**:
- Publishable key `pk_live_…` → `STRIPE_PUBLISHABLE_KEY`
- Secret key `sk_live_…` → `STRIPE_SECRET_KEY`. Store it only in the
  server's `.env`.

The API refuses to start in production with test keys, or with one test key
and one live key.

**2.4 The Connect webhook.** **Developers → Webhooks → Add endpoint**
(called a destination in newer dashboards):
- **Events from:** *Connected accounts*. This is not *Your account*:
  payments happen on the restaurants' accounts.
- **Endpoint URL:** `https://<domain>/api/v1/webhooks/stripe/connect`
- **Events:** `payment_intent.succeeded`, `payment_intent.payment_failed`,
  `payment_intent.canceled`, `charge.refunded`, `account.updated`

Copy the endpoint's **signing secret** (`whsec_…`) into
`STRIPE_CONNECT_WEBHOOK_SECRET`. The worker processes the events: keep at
least one `worker` running. If a webhook is ever late, the order page still
confirms the payment by asking Stripe directly after about ten seconds.

**2.5 Your commission.** `PLATFORM_FEE_BPS` (basis points: 250 = 2.5%) and
`PLATFORM_FEE_FIXED_MINOR` (cents: 30 = $0.30). Both `0` means no fee.
Changes apply to orders paid afterwards.

**2.6 Each restaurant** is connected from the super admin portal, never in
the Stripe Dashboard by hand:
1. Create the restaurant.
2. Create its owner. The connected account is contactable at the owner's
   email, so do this first.
3. **Connect Stripe** hands the owner to Stripe-hosted onboarding.
4. **Refresh Stripe** reads the result back.
5. **Activate**, which is refused until the account can take charges.

Activation and Refresh Stripe also register `<slug>.<domain>` for **Apple
Pay and Google Pay** on that restaurant's account. The row then shows
"Apple Pay active · Google Pay active". There is no domain-association file
to host.

**2.7 Stripe Tax (optional, per restaurant).** For automatic sales tax, the
restaurant, **in its own Stripe Dashboard → Tax**:
- sets its head-office address and preset tax code
- **adds a tax registration** for each state it collects in

Without a registration Stripe calculates zero tax, silently. Then, in the
super admin portal, fill in the pickup address, set the tax mode to *Stripe
Tax* (the save checks Stripe), and activate. The product tax code is
`txcd_40060003` (food for immediate consumption). Stripe bills per
calculation.

**2.8 Before real money.**
- Stripe's own **email receipts** are not used: Zenoeats emails the
  confirmation and every refund itself, and never gives Stripe a
  `receipt_email`, so leaving "email customers" off in each restaurant's
  Stripe settings keeps it to one email per event.
- Tell restaurants they receive payouts directly and handle disputes.
- Complete Stripe's annual **PCI** self-assessment (SAQ A: card data never
  touches the servers).
- Place one real low-value order end to end, then refund it.

### 3. Clerk (customer sign-in)

Customers sign in on the app's own `/account` pages; Clerk provides
identity underneath. Staff and platform admins never use Clerk.

**3.1 Production instance.** In the Clerk dashboard, create the application
(or open the existing one) and **create a production instance**. Keep the
development instance for development.

**3.2 Domain.** Set the production instance's **primary domain** to the root
domain `<domain>`, not a restaurant subdomain. One sign-in then covers every
restaurant.

**3.3 DNS.** Clerk lists the records it needs, typically CNAMEs for `clerk`,
`accounts` and `clkmail`, plus two DKIM records. Add each in Cloudflare as
**DNS only** (grey cloud), then wait for Clerk to verify them.
`clerk`, `accounts` and `clkmail` are reserved slugs, so no restaurant can
take them.

**3.4 Sign-in methods** (**User & authentication**):
- **Email address**, with verification by **email code**. The pages expect
  6-digit codes, not links.
- **Password.**
- Optionally **multi-factor**: the sign-in page already handles
  authenticator-app, SMS, email and backup codes.
- Keep **bot protection** on. The sign-up page includes Clerk's captcha
  element.

**3.5 Social sign-in.** A production instance needs your own credentials for
each provider. Each provider's page in Clerk shows the **redirect URI** to
paste into that provider. A button appears in the app for every provider
you enable.

- **Google:**
  1. Google Cloud console → **OAuth consent screen**: publish it, with
     `https://<domain>/legal/privacy` and `https://<domain>/legal/terms`.
  2. **Credentials → OAuth client ID → Web application**, with Clerk's
     redirect URI.
  3. Put the client ID and secret in Clerk.
- **Facebook:**
  1. developers.facebook.com → **create an app** with Facebook Login.
  2. Set *Valid OAuth Redirect URIs* to Clerk's redirect URI.
  3. Add the **Privacy Policy URL** and the **data deletion instructions
     URL** (`https://<domain>/legal/data-deletion`).
  4. Put the App ID and secret in Clerk, then switch the app to **Live**.
- **Apple** (needs the Apple Developer Program, $99/year):
  1. Create an **App ID** with *Sign in with Apple*.
  2. Create a **Services ID** with the web domain and Clerk's return URL.
  3. Create a **Key** with *Sign in with Apple* and download the `.p8` (only
     possible once).
  4. Enter the Services ID, Team ID, Key ID and the key's contents in Clerk.

Google, Facebook and Apple review the legal pages. They must be the final,
lawyer-reviewed versions on the root domain (see
[`docs/operations/STEPS_BEFORE_PRODUCTION.md`](docs/operations/STEPS_BEFORE_PRODUCTION.md) §9).

**3.6 Keys.** Production instance → **API keys**:

| Clerk shows | Setting |
|---|---|
| Publishable key `pk_live_…` | `VITE_CLERK_PUBLISHABLE_KEY` (read by the web container at start) |
| Secret key `sk_live_…` | `CLERK_SECRET_KEY` |
| Frontend API URL (`https://clerk.<domain>`) | `CLERK_ISSUER` |
| JWKS URL (`https://clerk.<domain>/.well-known/jwks.json`) | `CLERK_JWKS_URL` |

**3.7 Webhook.** **Webhooks → Add endpoint**:
`https://<domain>/api/v1/webhooks/clerk`, events `user.created`,
`user.updated` and `user.deleted`. Copy its signing secret into
`CLERK_WEBHOOK_SECRET`. This keeps customer records in step with Clerk, and
is how a deletion made in the Clerk dashboard reaches the app. Add
`email.created` too if Clerk's own emails are to go through SendGrid (see
[Email](#email)); it does nothing until an email's Clerk delivery is off.

**3.8 Polish.** Brand the **email templates** (verification and reset codes)
with the Zenoeats name and sender. Choose the **session lifetime** and
inactivity timeout.

### 4. Google Maps Platform (delivery)

Needed only for delivery. It turns an address into a distance and fee,
suggests addresses at checkout, and shows the live tracking map. Without it
the app works for pickup, and the portal says delivery is unavailable.

> **Use your existing Google Cloud project.** The checkout's address
> suggestions use the Places Autocomplete widget from **Places API
> (Legacy)**, which Google no longer offers to new customers (since 1 March
> 2025). A project that already has it enabled keeps it; a brand-new
> project may not be able to enable it, and suggestions would then fail.
> Create the production keys in the project development already uses.
> Moving the checkout to Google's newer Places widget is a planned
> follow-up.

**4.1 Project and billing.** In the Google Cloud console, open the project
and confirm a **billing account** is attached. It is required even though
the free monthly allowance covers normal volume. Add a **budget alert**
(Billing → Budgets & alerts).

**4.2 APIs** (APIs & Services → Library), all enabled:
- **Maps JavaScript API:** the tracking map and the checkout widget
- **Places API (Legacy):** address suggestions at checkout
- **Geocoding API:** address → coordinates, on the server
- **Routes API:** delivery arrival estimates, on the server

**4.3 Two keys, never one.** APIs & Services → **Credentials → Create
credentials → API key**, twice:

| | Server key → `GOOGLE_MAPS_API_KEY` | Browser key → `GOOGLE_MAPS_BROWSER_KEY` |
|---|---|---|
| Used by | The API server only; never reaches a browser | Customers' browsers (it is public by design) |
| Application restriction | **IP addresses**: the server's public outbound IPv4 | **Websites**: `https://*.<domain>/*` and `https://<domain>/*` |
| API restrictions | Geocoding API, Routes API | Maps JavaScript API, Places API (Legacy) |

For development, add this to the browser key's websites too:
- `https://*.zenoeats.local:8443/*`

A site missing from the list fails with `RefererNotAllowedMapError` in the
browser console, and no suggestions appear. Key changes can take a few
minutes to apply.

**4.4 Check it.** Open a storefront → checkout → **Delivery**, and type part
of a real address. Suggestions appear; pick one, and a delivery fee replaces
the "checking address" message. A restaurant must also have placed itself on
the map and drawn its delivery rings (portal → Settings → Delivery).

### 5. SendGrid (email)

The app sends its own emails, such as order confirmations and staff
invitations. Customer verification codes come from Clerk (§3).

1. sendgrid.com → **Settings → Sender Authentication → Authenticate Your
   Domain**: use `<domain>` or a subdomain such as `mail.<domain>`, with
   Cloudflare as the DNS host. Add the CNAME records it lists in Cloudflare,
   as **DNS only**, and press *Verify*.
2. **Settings → API Keys → Create API Key**, *Restricted Access* with only
   *Mail Send* turned on → `SENDGRID_API_KEY`.
3. In `.env`:
   - `EMAIL_FROM=Zenoeats <orders@<domain>>`: must be on the authenticated
     domain
   - `EMAIL_REPLY_TO=`: an inbox someone reads
   - `STOREFRONT_URL_TEMPLATE=https://{slug}.{root_domain}`: where links in
     emails point, already set by `make_prod_env.py`

The app turns SendGrid's click and open tracking off on every message, so
links reach customers exactly as written.

With `SENDGRID_API_KEY` empty nothing is sent, and the portal says so.

### 6. Sentry (errors)

sentry.io → **Create project** → platform *Python / FastAPI* → copy the
**DSN** into `SENTRY_DSN`. `make_prod_env.py` already sets
`SENTRY_ENVIRONMENT` and `RELEASE`. Request bodies, local variables and
personal data are never sent (`backend/app/core/observability.py`). To
check it, trigger a test error after deploying and confirm it arrives
without customer data.

### 7. Backups (Cloudflare R2, healthchecks.io)

Nightly, encrypted, off-site, with a restore drill. The full procedure is
[`docs/operations/STEPS_BEFORE_PRODUCTION.md`](docs/operations/STEPS_BEFORE_PRODUCTION.md) §5. In short:

1. **Cloudflare → R2:**
   - create the bucket `zenoeats-backups`
   - add a lifecycle rule deleting objects after **90 days**
   - add a **bucket lock** of **30 days**
   - create an API token for **Object Read & Write** on that bucket only
2. **healthchecks.io:** a check named `zenoeats-backup`, period 1 day, grace
   2 hours.
3. **The encryption key:** the **public** age key goes on the server; the
   **private** key goes in a password manager and an offline copy, never on
   the server.
4. On the server:
   1. Write `/etc/zenoeats/backup.env` (mode 600).
   2. Install `age` and `rclone`.
   3. Enable `infra/systemd/zenoeats-backup.timer`.
   4. Run it once.
   5. Run `scripts/restore_drill.sh` from another machine.

### 8. Monitoring

In UptimeRobot, Better Stack or similar, add two HTTP monitors that alert on
anything but a 200:

- `https://<domain>/health/ready`: the database, as the app role
- `https://<domain>/health/operations`: workers and beat, webhooks, stale
  checkouts, Redis, disk (see [Health checks](#health-checks))

### 9. Platform administrators

Each operator of the super admin portal (`https://admin.<domain>/admin`) has
a named entry in `ADMIN_USERS`, which the audit log records. On the server:

```bash
docker run --rm -it ghcr.io/haswanth13901/zenoeats/api:v1.3.1 \
  python scripts/hash_password.py you@example.com
```

It asks for the password twice (at least 12 characters) and prints one
`email:hash` line. Put it in `ADMIN_USERS`; separate several operators with
`;`. Removing an entry ends that operator's access at their next request.

### 10. Going live

With every account above done and `make_prod_env.py --check .env` clean,
deploy on the server as in
[`docs/operations/STEPS_BEFORE_PRODUCTION.md`](docs/operations/STEPS_BEFORE_PRODUCTION.md) §11. Then:

1. **Rehearse in test mode first.** Generate the `.env` with `--staging`,
   which allows Stripe and Clerk test keys and nothing else, and run the
   §10 rehearsal on the real domain.
2. **Switch to live:** the live Stripe and Clerk keys, `ALLOW_TEST_KEYS=false`.
3. Create the first restaurant (§2.6), place one real low-value order end to
   end, and refund it.
4. Complete the sign-off in
   [`docs/security/SECURITY_DEPLOYMENT_CHECKLIST.md`](docs/security/SECURITY_DEPLOYMENT_CHECKLIST.md).

## Delivery

Customers can choose delivery at checkout, and a paid order can be followed
to the door on a live map. What is missing is below, under "What a
customer-facing release still needs".

**What works.** `Order.fulfillment_type` is `DELIVERY` when the customer chose
it at checkout, or when a manager sends a paid collection out, and the transition matrix in `app/models/commerce.py` carries
READY_FOR_DELIVERY and OUT_FOR_DELIVERY. Who is delivering is a column,
`orders.driver_user_id`, rather than the baseline's DRIVER_ASSIGNED and
DRIVER_ACCEPTED states: a manager may assign or reassign at any point, which as
states would mean an edge from everywhere to everywhere. A restaurant draws
rings in Settings (`delivery_zones`), is geocoded to a point of its own, and
`app/services/delivery.py` will price an address against those rings.
`price_cart` takes the fee, keeps it out of the subtotal and puts it in the
total, and `TaxService` taxes it per the restaurant's answer under a flat rate
or hands it to Stripe as `shipping_cost` under Stripe Tax. An order keeps the
fee and the distance it was charged for.

**Live tracking.** From payment, a delivery's order page shows its steps --
paid, driver assigned, ready, picked up, delivered -- read from
`order_events`. Once the driver presses Picked up, the Deliveries page on
their phone shares GPS every five seconds (`POST
/restaurant/driver/location`, refused unless they have an order of their own
on the road) and keeps the screen awake. The position lives in Redis for
minutes, never in Postgres, and the customer's map (Google Maps JavaScript
API, `GOOGLE_MAPS_BROWSER_KEY`) shows it while fresh. The arrival time comes
from the Routes API with the server key, asked after the poll's response and
at most once per `DELIVERY_ETA_REFRESH_SECONDS` per order. The browser only
reports position while the page is open, so a native driver app is the next
step if drivers need to lock their phones.

The map needs no Map ID. Each restaurant picks its colours in Storefront ->
Delivery map, from styles the storefront draws itself; Google ignores such a
style whenever a Map ID is in use, which is why the map draws its pins as
ordinary markers rather than Advanced Markers. After editing `.env` in
Docker, recreate the API with `docker compose --profile app up -d --no-deps
--no-build --force-recreate api`; restarting an existing container does not
load changed environment values. Pickup and unpaid orders have no delivery
tracking map. Driver GPS requires HTTPS and browser location permission.
The development edge at `https://<slug>.zenoeats.local:8443` provides it, on
any device that trusts the local certificate authority (see
[Running it](#3-https-certificate)). Keep the driver's Deliveries page open
after marking the order picked up.

Uploaded menu images under `/images/` are served by the API. Both development
and production nginx configurations route that prefix to the API, including
when the frontend runs as a static Docker container.

They are served `Cache-Control: public, max-age=31536000, immutable`, which
is true rather than optimistic: `services/images` mints a random key per
upload, writes it once, and releases it only when no row refers to it, so the
bytes at a key never change. Without it an ETag alone meant the browser asked
about every photograph on every view and was told 304 — thirty round trips
for a thirty-photo menu, and nothing a CDN could answer on its own.

Storage stays on the API host's disk for launch, which caps the deployment at
one API machine and makes backing up `IMAGES_DIR` non-negotiable
(`scripts/backup.sh` takes it with every database dump). Rows hold
keys and never URLs and `IMAGES_PUBLIC_BASE` already takes an absolute URL,
so moving to a bucket later is one class and one setting. The triggers for
doing so are in `docs/operations/STEPS_BEFORE_PRODUCTION.md` §4.

The local edge prefers the `api` and `web` containers directly when the app
profile is running. Docker DNS refreshes their addresses after recreation;
native development uses the host gateway backup. This avoids intermittent
timeouts caused by routing container traffic through Windows port forwarding.
The configuration requires nginx 1.27.3 or newer (the Compose image supplies
1.30, the stable line). After editing it, run `docker compose exec nginx nginx -t` and
`docker compose exec nginx nginx -s reload`. A temporary menu failure also
offers **Try again**, which only refetches the public menu and restaurant details.
Check the real edge after startup or upgrades with
`python scripts/check_storefront.py --slug spicehouse`. It makes 20 read-only
requests, reports latency, and exits unsuccessfully for errors or responses
taking 10 seconds or longer. Use your restaurant's slug if different.
For failures, `docker compose logs --tail 50 nginx` includes total request,
upstream connection and response times. Query strings, cookies and request
bodies are excluded from these access logs.

Run one local application mode at a time. With `--profile app`, stop native
Uvicorn, Vite and Celery terminals first. To switch back to native development,
run `docker compose --profile app stop api web worker beat` before starting
those terminals; leave PostgreSQL, Redis and nginx running. Duplicate app
stacks waste memory, can compete for published ports, and run extra task
consumers. `make up-all`, `make api`, `make web` and `make worker` now refuse
to start while the other mode is running (`scripts/dev_preflight.py`).
On an 8 GB laptop, avoid concurrent frontend builds while serving
the app: memory pressure can cause API worker restarts and request timeouts.
When Windows runs short of memory it pages Docker's VM out to disk, and an API
that has sat idle is paged out first: its next request then hangs for a minute
or more, which the browser shows as "Can't reach the server". The API log says
which kind of silence it was: `the whole API process was paused` means the
host, not the code; `event loop blocked` is a real bug and prints the line
responsible. The portal pages now retry and reconnect on their own either way.
Local Compose defaults to one API worker to reduce memory usage and avoid
multiprocess watchdog restarts on a busy laptop. The production file defaults
to two; size `API_WORKERS` against the host and the database pools.

**What a customer-facing release still needs.** Stripe Tax
sources tax at the restaurant's address, which is right for collection and
wrong for a delivery in a destination-sourced state, so the customer's
structured address has to reach `stripe_tax.calculate`. Then DELIVERY_FAILED
with the retry and refund handling around it, a minimum order value if that is
wanted. Polling in the order page is the thing to replace with WebSockets if
five-second updates stop being enough, and section 12 of the baseline already
specifies how.

**What was decided along the way**, so it is not relitigated. Distance is
straight-line rather than driving distance: routing costs more per lookup and
rings on a map are what a restaurant means by "we deliver within three miles".
Coordinates are cached for thirty days and never stored on an order, because
Google's terms allow caching rather than keeping; what an order keeps is the
distance and the fee, which are ours. A ring's id is not stored either, since
rings are replaced as a set on every edit and the pointer would dangle, while
"3.2 miles, $4" stays true.


### Storefront customisation limits

Eight banners total (including inactive drafts), six collections and twelve
existing items per collection. Banner dates are stored with timezones; the
editor labels dates in the device's timezone. Slides rotate every 2-10 seconds,
with focus/hover pause, touch navigation and a static reduced-motion mode.
There is no pause button: reaching the slides with a pointer or the keyboard
stops them, and the dots move between them. Photos are uploaded through the
existing image service, with a 2400px longest edge for banners and 1600px for
other images.

Each banner carries its own framing, because the banner is a fixed shape and
a photograph is not: a focal point (`focal_x`, `focal_y`, percentages of the
image) and a zoom of 100-200%. The portal's Framing control drags the photo
or takes arrow keys, previews through the same function the storefront
renders with, and shades where the headline sits. The defaults (50, 60, 100)
reproduce the fixed crop every banner had before, so migration 0032 changes
no existing storefront's appearance.

The public `/portal` response includes a nullable `storefront` block. The
module is off by default, and `/menu` keeps its existing contract. Themes use
four colours, validated on the server for readable contrast, and a curated
font pairing. Staff and platform screens keep their own theme.

Custom domains, custom CSS or HTML, per-page layouts and object storage are
out of scope. Applying restaurant themes to the separate `web/login/`
customer sign-in pages remains a follow-up.

## Licence

MIT. See [`LICENSE`](LICENSE).

That covers this source code and nothing else. The policy pages under
`web/legal/` are unreviewed drafts written for this deployment, not legal
advice and not reusable as such, and the MIT warranty disclaimer is not a
substitute for taking your own advice on them. Running this software means
handling other people's payment and contact details under whatever law
applies to you.
