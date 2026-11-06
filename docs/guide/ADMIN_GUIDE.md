# Using ZenoEats as an admin

The day-to-day jobs of running a restaurant on ZenoEats, and which screen
each one is on. For how it is built see
[TECHNICAL_OVERVIEW.md](TECHNICAL_OVERVIEW.md); to get a copy running see
[RUNNING_LOCALLY.md](RUNNING_LOCALLY.md).

## The two portals, and which one you want

Nearly every complaint about "I can't find it" is this distinction.

| | Restaurant portal | Platform portal |
|---|---|---|
| **Address** | `jrs.zenoeats.com/manage` | `admin.zenoeats.com/admin` |
| **Who signs in** | Staff of that restaurant | ZenoEats, from `ADMIN_USERS` |
| **Sees** | One restaurant | Every restaurant |
| **Use it for** | Everything below | Creating restaurants, activating them, platform reports, fixing a restaurant's promotion on their behalf |

They are **different sign-ins**. Being `admin` at Jr's Corner does not get you
into the platform portal, and a platform admin is a member of no restaurant.

A restaurant's screens only work on that restaurant's own subdomain: the tenant
comes from the `Host` header and nothing else.

## Who can do what

Six roles. The gates that matter:

| Role | Can |
|---|---|
| `ADMIN` | Everything below, plus settings, tax, delivery, coupons, staff |
| `MANAGER` | Kitchen, deliveries, stock, menu, reports, the assistant |
| `IT_SUPPORT` | Settings-level things: delivery, tax, coupons |
| `KITCHEN` | The kitchen board |
| `CASHIER` | Orders and handover |
| `DRIVER` | Their own deliveries |

A discount is money, so coupons sit behind the same gate as the tax rate and
the delivery rings — `ADMIN` or `IT_SUPPORT` — rather than with everyone who
can open the kitchen board.

## The jobs

| You want to | Go to |
|---|---|
| See what is cooking | **Kitchen** — polls every five seconds, chimes on a new order |
| Hand an order over | **Kitchen**, then Ready, then Complete |
| Cancel or refund an order | **Orders** — on the order itself |
| Mark something sold out | **Stock**, or say *"86 the fries"* to the assistant |
| Add or re-price a dish | **Menu**, or ask the assistant |
| Build a combo | **Menu** → Combos |
| Set delivery rings and fees | **Settings → Delivery** |
| Change the tax rate | **Settings → Tax** |
| Run a promotion | **Settings → Coupons** |
| Add or remove staff | **Staff** |
| Change the storefront look | **Storefront** |

## Promotions

All four live on **Settings → Coupons**.

**Coupons.** One code, typed at checkout. A percentage or a flat amount; a
ceiling, a minimum spend, a window, a total cap and a per-customer cap, any of
which you can leave out. It can be scoped to menu headings — "20% off burgers"
discounts only the burgers, while the minimum spend still measures the whole
order.

A flat amount may be **more than the order**, which makes that order free — it
is capped at what is being charged, never refunded as change.

> A flat amount of **zero** is in [PR #38](https://github.com/AmensysInc/Zenoeats/pull/38)
> and not merged yet. Until it is, the smallest flat discount is one cent.

**Group coupons.** One offer handed out as numbered codes: prefix `MORE` with
10 uses mints `MORE1` … `MORE10`. Each is good **once**, so ten uses means ten
people, and the table shows which of them came back — code, date, who, amount.
That is the whole reason to number them; one shared code makes every redemption
look alike.

A prefix cannot end in a digit (`MORE2` × 12 would mint `MORE21`, which is also
what `MORE` #21 would be called). Minting is refused outright if any code it
would create already exists, so a half-minted group cannot happen. Pausing or
retiring a group carries to every code it minted.

**Sign-on coupon.** One personal code per new customer, earned by adding a
mobile number and verifying it by text. The code is bound to that customer and
refused to anyone else. It expires a set number of days **after issue**, not on
a date, because everyone signs up on a different day. One per customer, ever.

> **Not usable yet.** There is no SMS provider on this deployment, so nobody
> can verify a number and no code can be claimed. The panel says so. Set
> `SMS_PROVIDER` and `SMS_FROM_NUMBER`, and for US numbers complete A2P 10DLC
> registration first — unregistered traffic is filtered by carriers rather than
> refused at the API, which looks exactly like a working integration whose
> messages never arrive.

**Who used what.** Every redemption of every code: the code, the order, who
placed it, what it took off. Narrow it to one code to trace a single `MORE7` or
one customer's welcome code. Cancelled and expired orders are absent — an order
nobody paid for is not a promotion anybody received.

## Rewards, offers and referrals

On the platform portal, under **Promotions** for a restaurant.

- **Stamp cards** — a reward after a number of qualifying purchases, counted as
  completed orders or as a particular dish or heading.
- **Referrals** — a customer shares a link; when someone new orders, the
  referrer wins a reward. A referral qualifies on the invitee's **first
  completed order**, not on the click, and one claim per invitee ever.
- **Offers** — the promotional cards shown on the storefront.

## The assistant

**The chat button in the bottom-right corner** of every page of the portal,
for owners and managers. The conversation follows you from page to page.
Type or speak what you want:

```
we're out of fries
  → Take off the menu: "Jr's French Fries"?       [Yes, do it]  [No]

add a Double Bacon Burger to Burgers for 8.50
  → Add "Double Bacon Burger" to Burgers at USD 8.50?

check inventory
  → 1 off the menu right now:  Jr's French Fries
```

**Nothing happens until you press the button.** Questions answer immediately;
anything that changes the menu is described first — and it shows the dish it
resolved to ("Jr's French Fries" when you typed "fries"), which is where a
wrong match gets caught.

It can: add a dish, change a price, take something off or put it back, cancel
an order, delete a dish, delete an empty heading, and read back the menu,
stock, order counts and coupons.

Destructive ones look different on purpose — tinted, with a red button saying
it cannot be undone — so agreeing to a cancellation never looks like agreeing
to a price change.

**It will not**: refund (money going back to a customer belongs on the order,
where the amount is in front of whoever sends it), change staff, move an order
along, or do anything in bulk. `"delete everything"` is refused before the
sentence is read.

With `OPENAI_API_KEY` set, it also understands things said in your own words
("fries should be three and a quarter now"). The model can only choose among
the actions above, so its answer arrives as the same proposal and the same
button.

**The same chat elsewhere**, both read-only and both shown only when
`OPENAI_API_KEY` is set:

- **ZenoEats admin** — questions about every restaurant: which are live, sales
  over a period, recent orders at one of them, Stripe status.
- **The storefront** — customers ask about the menu, prices, opening times,
  pickup and delivery, and their own orders. It cannot order, pay, refund or
  hand out discounts, and it sends allergy questions to the restaurant.

Anything it does not do says where that thing actually lives, rather than
"I didn't understand".

**Speaking to it** uses the browser's own recognition — no account, no key, no
audio sent anywhere we chose. Chrome and Safari have it; Firefox does not, and
there the microphone button is simply absent. Reading answers aloud is off
until you switch it on.

It runs **as you**: same session, same role gate as the screens it drives. It
can do exactly what you could do by clicking, and nothing more.
