# ASTRA REDESIGN BRIEF

> **Product:** Zenoeats: pickup ordering for restaurants, run by one platform.
> **Source of truth:** the `restaurant-portal-launch-fixes` branch, committed, audited 2026-09-16. Adds B11 (Settings) and the delivery area behind it.
> ⚠ **Not all of this is committed yet.** The guest-checkout work described below — A1's account row, A5's guest panel, A11's `?t=` order-view token and combo grouping, the `/orders/session` endpoints — is in the working tree. Everything else is committed. Verified at the time of writing: 614 backend tests pass, `tsc --noEmit` and `eslint` are clean, and the portal was clicked through in Edge at 360, 390, 768 and 1280 px.
> **How to use this document:** it is the only context you have. Section 2 is the contract: every numbered screen, element, state and rule there must exist in the redesign. Section 6 is your brief.

---

## 1. App Summary

Zenoeats is a multi-tenant, pickup-only restaurant ordering platform. Each restaurant gets its own subdomain (for example `spicehouse.zenoeats.com`). On it, customers browse the menu without an account, customise items and meal-deal combos, and identify themselves only at checkout — by signing in, or by continuing as a **guest** with nothing but an email address — pay by card through Stripe, and follow their order live until they collect it with a 6-digit pickup PIN. A guest's session is one browser's cookie, so their confirmation email carries a signed link that opens that one order from any device. The same subdomain hosts the **restaurant staff portal** at `/manage`. Staff in five roles (Admin, Manager, Kitchen, Cashier, Driver) use it to run a live kitchen board, flip items sold out, build the menu (item types, items with photos, meal periods, modifier groups, combos), manage the team, read sales reports, and run the deliveries the restaurant sends out itself. A separate **platform super-admin portal** (`admin.zenoeats.com/admin`) is where the Zenoeats operator creates restaurants, connects them to Stripe, issues owner logins, activates or suspends them, and reads platform-wide reports. Delivery is half built: a restaurant can draw a delivery area, price it by distance and say whether the fee is taxed, but a **customer** still cannot order a delivery, pay a fee or give an address — what exists at checkout is a manager handing a paid phone order to one of the restaurant's own drivers, free. There is no cash, no promotions and no reviews: the product is deliberately one flow, from browse to pay to collect — or, at the restaurant's choice, to be run out to the customer.

---

## 2. Complete Screen Inventory

### 2.0 How to read this section

- Every screen has an ID (**A** = customer, **B** = restaurant staff, **C** = platform admin, **G** = global states, **E** = out-of-app touchpoints). Section 6 asks you to reference these IDs in your preservation checklists.
- **⚠ Edge case** marks behaviour a redesign could easily lose. Keep all of them.
- Quoted text is current UI copy. You may reword it, but you must keep its meaning, especially warnings about money, refunds, and passwords that are shown only once.

#### 2.0.1 Source-file cross-check (every UI file → where it is covered)

| File | Purpose | Covered in |
|---|---|---|
| `web/index.html` | SPA shell, `<title>Order pickup</title>` | G, §5 |
| `web/public/config.js` | Runtime config (Clerk key) | §5 |
| `web/src/main.tsx`, `src/app/store.ts`, `src/app/hooks.ts` | React root, Redux store (cart, session, API cache), refetch on focus/reconnect | §5 |
| `web/src/routes/AppRoutes.tsx` | Route table, 404 page | G3, all |
| `web/src/components/layout/Guards.tsx` | Auth guards: loading, unreachable, redirect, accept invitation, not-for-your-role. ⚠ The customer guard now asks the API who is ordering, not Clerk, and the "sign-in unavailable" state is gone | G1–G2, A12, B3, B4, C0 |
| `web/src/components/layout/Shell.tsx` | Operator header (wordmark, tabs, action slot) | B0, C0 |
| `web/src/components/common/Feedback.tsx` | `Empty`, `ErrorNote`, `Panel`, `Stat`, `StatusPill` | §4 components, all |
| `web/src/components/common/icons.tsx` | Pencil and photo SVG icons | §4 |
| `web/src/components/common/MenuImage.tsx` | Lazy menu photo that hides itself on failure | A1, A2, A3, B7a |
| `web/src/styles/globals.css` | Tailwind layers, `.btn*`, `.field`, `.tnum`, reduced motion | §4 |
| `web/tailwind.config.ts`, `postcss.config.js` | Colour and font tokens | §4 |
| `web/vite.config.ts`, `web/nginx.conf`, `docker-entrypoint.d/40-zenoeats-config.sh` | Multi-page build, login URL mapping, CSP headers | §5 |
| `web/login/admin-login.{html,ts}` | Platform admin sign-in | C1 |
| `web/login/staff-login.{html,ts}` | Restaurant staff sign-in | B1 |
| `web/login/change-password.{html,ts}` | Staff temporary-password replacement / voluntary change | B2 |
| `web/login/customer-shared.ts` | Clerk helpers: social buttons, password pair validation, next-path, restaurant name — and whether there *is* a restaurant, which gates A5's guest panel | A5–A8 |
| `web/login/customer-sign-in.{html,ts}` | Customer sign-in + 2-step verification + the "Order without an account" guest panel | A5 |
| `web/login/customer-sign-up.{html,ts}` | Customer sign-up (form / continue / code) | A6 |
| `web/login/customer-forgot-password.{html,ts}` | Customer password reset | A7 |
| `web/login/customer-sso-callback.{html,ts}` | Social sign-in return page | A8 |
| `web/src/services/api.ts`, `apiClient.ts` | Request layer: timeouts, error normalisation, idempotency keys | G, §5 |
| `web/src/services/clerk.ts` | Clerk loader (customer only), plus a cookie-only "probably signed in?" hint so the menu never pulls down Clerk to be told nobody is | A5–A12 |
| `web/src/types.ts` | Menu / cart / order types | §5 |
| `web/src/utils/format.ts` | Money, price parsing, meal-hours formatting | all |
| `web/src/utils/image.ts` | Client-side photo shrink before upload | B7 ImagePicker |
| `web/src/utils/storefront.ts` | Builds the storefront URL from the admin host | C2 |
| `web/src/features/session/sessionSlice.ts` | Who is signed in (portal, email, name, role, restaurant) | B0, A9, guards |
| `web/src/features/cart/cartSlice.ts` | Cart (items + combos), per-restaurant localStorage | A1, A4, A9, A10 |
| `web/src/features/cart/useOpenCart.ts` | Binds the cart to this restaurant and restores it from localStorage. ⚠ Every page that reads or clears the cart must call it | A1, A9, A10 |
| `web/src/features/cart/modifiers.ts` | Modifier selection rules | A2, A3 |
| `web/src/features/cart/components/ModifierGroups.tsx` | Modifier option list (radio/checkbox) | A2, A3 |
| `web/src/features/cart/components/ModifierSheet.tsx` | Item customisation sheet | A2 |
| `web/src/features/cart/components/ComboSheet.tsx` | Combo builder sheet, `savingLabel` | A3, A1, B7a |
| `web/src/features/storefront/storefrontApi.ts` | Portal, menu, quote, order, payment-intent, customer-session and guest-session endpoints | A |
| `web/src/features/storefront/orderToken.ts` | Reads the `?t=` order-view token from a guest's email and moves it out of the address bar into sessionStorage | A11 |
| `web/src/features/storefront/components/CustomerAccountBar.tsx` | "Ordering as … Not you? Sign out"; for a guest, "Ordering as a guest · receipt to {email}" / "Start over" | A9 |
| `web/src/features/storefront/components/CustomerHeaderAccount.tsx` | Storefront header account row: "Sign in", or the customer's name / "Guest" with sign-in and sign-out | A1 |
| `web/src/pages/storefront/StorefrontPage.tsx` | Storefront + CartBar + ItemList | A1, A4 |
| `web/src/pages/storefront/CheckoutPage.tsx` | Checkout | A9 |
| `web/src/pages/storefront/PaymentPage.tsx` | Stripe payment | A10 |
| `web/src/pages/storefront/OrderPage.tsx` | Order tracking | A11 |
| `web/src/features/restaurant/nav.ts` | Role → tab matrix | B0, role matrix |
| `web/src/features/restaurant/components/ManageShell.tsx` | Staff portal header | B0 |
| `web/src/features/restaurant/components/StaffSignOut.tsx` | Name · role, change password link, sign out, sign out all devices | B0 |
| `web/src/features/restaurant/restaurantApi.ts` | All staff-portal endpoints | B |
| `web/src/pages/manage/KitchenBoardPage.tsx` | Kitchen board, tickets, PIN, override, cancel, assign driver, Done today | B5 |
| `web/src/features/restaurant/newOrderAlert.ts` | New-order chime, "new" marker, tab-title count, sound preference | B5 |
| `web/src/pages/manage/DeliveriesPage.tsx` | Deliveries: a driver's own orders, a manager's every delivery | B10 |
| `web/src/pages/manage/StockPage.tsx` | Sold-out toggles | B6 |
| `web/src/pages/manage/MenuPage.tsx` | Menu builder tabs | B7 |
| `web/src/features/restaurant/components/MenuPreview.tsx`, `menuPreview.ts` | Preview tab | B7a |
| `web/src/features/restaurant/components/ItemLibrary.tsx` | Items tab (list, add form, bulk editor) | B7b |
| `web/src/features/restaurant/components/ItemTypeManager.tsx`, `itemTypes.ts` | Item-type filter strip, add type, types editor | B7b |
| `web/src/features/restaurant/components/ImagePicker.tsx` | Photo add / change / remove with upload state | B7b, B7e |
| `web/src/features/restaurant/components/MealPeriods.tsx`, `allDay.ts` | Meal periods tab | B7c |
| `web/src/features/restaurant/components/ComboBuilder.tsx` | Combos tab | B7d |
| `web/src/features/restaurant/components/ModifierLibrary.tsx` | Modifier library tab | B7e |
| `web/src/pages/manage/StaffPage.tsx` | Team management | B8 |
| `web/src/pages/manage/ReportsPage.tsx` | Restaurant reports: date ranges, net of refunds, by day, per driver | B9 |
| `web/src/pages/manage/SettingsPage.tsx` | The restaurant's own record: name, address, timezone, tax | B11 |
| `web/src/features/restaurant/components/OwnAccount.tsx` | Your own name, sign-in address, password | B11 |
| `web/src/features/restaurant/components/DeliveryArea.tsx` | Placing the restaurant, rings, fees, fee tax | B11 |
| `web/src/features/admin/adminApi.ts` | All admin endpoints | C |
| `web/src/features/admin/components/AdminShell.tsx`, `AdminSignOut.tsx` | Admin header | C0 |
| `web/src/pages/admin/AdminRestaurantsPage.tsx` | Admin dashboard | C2 |
| `web/src/features/admin/components/RestaurantRow.tsx` | Restaurant table row + purge confirm | C2 |
| `web/src/features/admin/components/RestaurantEditForm.tsx` | Inline restaurant editor | C2 |
| `web/src/features/admin/components/RestaurantForms.tsx` | Create restaurant, owner login, issued credential panel | C2 |
| `web/src/pages/admin/AdminOrdersPage.tsx` | One restaurant's orders | C3 |
| `backend/app/services/notifications.py` | HTML emails: order confirmation, staff invitation | E1, E2 |

#### 2.0.2 Domain vocabulary you must design for

**Roles and what they can open** (hidden tabs are not rendered; opening a URL directly shows B4)

| Role | Kitchen board | Deliveries | Stock | Menu | Staff | Reports | Settings |
|---|---|---|---|---|---|---|---|
| ADMIN | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| MANAGER | ✓ | ✓ | ✓ | ✓ | — | ✓ | — |
| KITCHEN | ✓ | — | ✓ | — | — | — | — |
| CASHIER | ✓ | — | ✓ | — | — | — | — |
| DRIVER | — | ✓ | — | — | — | — | — |
| IT_SUPPORT | read | — | read | read | — | — | ✓ |

⚠ A driver is not floor staff with one extra screen: **Deliveries is their whole portal**, it shows only the orders assigned to them, and they land on it at sign-in. Everything else answers "not part of your role" (B4), and the API refuses it.

⚠ IT support is the other role that is not floor staff, and the only one whose screens differ by more than which tabs are there. **Three of its four tabs open read-only**: the kitchen board keeps its tickets and loses "Mark ready" and "Collect with PIN", Stock lists what is sold out as a pill instead of a toggle, and the menu builder shows its Preview tab alone with the four editing tabs gone (the tab row hides itself, since one tab is not a choice). Settings and Storefront are the two it may actually change, and both audit the change to whoever made it. Design read-only states for B5, B6 and B7 accordingly; they are not disabled buttons.

**What each role may do** (the API enforces all of it; the UI only offers what will work)

| Action | ADMIN | MANAGER | KITCHEN | CASHIER | DRIVER | IT_SUPPORT |
|---|---|---|---|---|---|---|
| See the board and today's history | ✓ | ✓ | ✓ | ✓ | — | ✓ |
| See what is sold out | ✓ | ✓ | ✓ | ✓ | — | ✓ |
| Read the menu | ✓ | ✓ | — | — | — | ✓ |
| Mark ready, collect with PIN | ✓ | ✓ | ✓ | ✓ | — | — |
| Mark items sold out or back in stock | ✓ | ✓ | ✓ | ✓ | — | — |
| Hand over without the PIN | ✓ | ✓ | — | — | — | — |
| Cancel a paid order (with a reason) | ✓ | ✓ | — | — | — | — |
| Assign a driver, change driver, back to collection | ✓ | ✓ | — | — | — | — |
| Mark picked up / delivered | ✓ | ✓ | — | — | ✓ own orders only | — |
| Edit the menu | ✓ | ✓ | — | — | — | — |
| Read reports | ✓ | ✓ | — | — | — | — |
| Edit storefront presentation (when enabled) | ✓ | ✓ | — | — | — | ✓ |
| Invite, remove, change roles, reset passwords | ✓ | — | — | — | — | — |
| Edit the restaurant, its address, timezone and tax | ✓ | — | — | — | — | ✓ |
| Set the delivery area, its fees and their tax | ✓ | — | — | — | — | ✓ |
| Change your own name and sign-in address | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |

⚠ That last row is not a mistake. Your own name and login belong to you whatever you do at the restaurant, so a driver may change theirs exactly as an owner may. Only B11 offers it today, and B11 is admin-only — if the redesign wants a "Your account" screen for the whole team, the API is already open to it.

Help text shown for each role on the Staff page: ADMIN "Everything, including the team: invitations, roles and password resets." · MANAGER "Orders, menu, reports, and handing over or cancelling orders. No staff changes." · KITCHEN "The order board and sold-out toggles." · CASHIER "The counter: collect orders with PINs, and sold-out toggles." · DRIVER "Deliveries assigned to them, and nothing else of the portal." · IT_SUPPORT "Setup and presentation: the storefront, the restaurant's details and the delivery area. Reads the board, stock and menu to diagnose them, and changes none of the three. No reports, no order actions, no staff."

Role codes are shown to people with the underscore removed and lowercased — "it support", not "IT_SUPPORT" — in both Staff dropdowns and the team table, which is what the other five already read as.

**Order status** (customer-facing title / detail):

| Status | Title | Detail | Polling |
|---|---|---|---|
| PENDING_PAYMENT | Confirming your payment | This usually takes a few seconds. Keep this page open. | every 2 s |
| AUTO_ACCEPTED | Order confirmed | The kitchen has your order. | 8 s |
| PREPARING | Being made now | We'll tell you when it's ready to collect. | 8 s |
| READY_FOR_PICKUP | Ready to collect | Give your PIN to the counter to pick it up. | 8 s |
| READY_FOR_DELIVERY | Ready, waiting for the driver | The restaurant is sending this one out to you. | 8 s |
| OUT_FOR_DELIVERY | On its way | The driver has your order. | 8 s |
| COMPLETED | Collected | Thanks for ordering. | stops |
| CANCELLED | Cancelled | This order was cancelled. | stops |
| EXPIRED | Expired | Payment wasn't completed in time. Nothing was charged. | stops |
| *(unknown)* | raw status string | *(empty)* | 8 s |

**Payment status:** PAID, REFUNDED, PARTIALLY_REFUNDED (plus none/null in admin views).
**Fulfillment:** every order is PICKUP until a manager assigns a driver, which makes it DELIVERY and gives it a `delivery_address` typed by staff. Taking the driver off makes it PICKUP again.
**Restaurant status:** DRAFT, ACTIVE, SUSPENDED, ARCHIVED (pill styles: ACTIVE solid dark; DRAFT and ARCHIVED outlined muted; SUSPENDED red tint). A restaurant can also be *soft-deleted* (shown faded with a "deleted" tag).
**Menu model:** *Item types* go two levels deep (a heading such as "Food" and subcategories such as "Burgers"). *Items* have one type, a price, an optional photo and description, a list of modifier groups, "included" options, and a sold-out flag. *Meal periods* (Breakfast, Lunch…) have optional hours and *serve* items; one item can appear in many periods. *Modifier groups* are Pick one / Pick several (with a max), optional or required, and hold *options* with a price change that may be negative and an optional photo. A *combo* belongs to one meal period, has one required slot per top-level type, and a discount (percent, amount, or none).

### 2.1 Global behaviour and states (apply to every screen)

- **G1 Booting:** a centred "Loading…" appears while a guard checks the session. *Not signed in* and *not checked yet* are different states, so never redirect before the check answers.
- **G2 Can't reach the server:** "The application is running but the API did not answer. Check that it is up, then reload." Shown by the staff and admin guards on any error other than 401.
- **G3 Page not found:** any unknown route shows "Page not found".
- **Redirect to login:** a full page navigation to the login page with `?next=<current path>`. After signing in, the user returns to the page they were on. ⚠ Only same-origin paths are accepted.
- **G4 Who is ordering:** on the customer surface, identity is the **API's** answer (`GET /orders/session`), not Clerk's. Three answers: a signed-in customer, a **guest**, or nobody (401). A guest is an httpOnly cookie no script can read, which is why the browser has to ask. ⚠ A guest is a full identity for ordering and a weak one for everything else — it cannot be signed back into, it lives in exactly one browser, and the UI must say so wherever it names one.
- **Request failures:** every request times out after 15 s (photo uploads after 60 s). Messages: "The server took too long to respond." / "Couldn't reach the server. Check your connection." / otherwise the server's own message. ⚠ The redesign must always leave room for a server-supplied error sentence.
- **Refetch on window focus and on reconnect** (tablets that sleep).
- **Money:** always stored as integer minor units and formatted en-US with the restaurant's currency. Figures use tabular numerals so price columns line up.
- **Tenant:** which restaurant you are in comes from the subdomain and is never typed. The storefront and staff portal only work on a restaurant subdomain; the admin portal only on `admin.`.
- **Reduced motion:** the current CSS disables all animation under `prefers-reduced-motion`. ⚠ Keep an equivalent.
- **Auth pages are separate HTML pages outside React** (A5–A8, B1, B2, C1). Moving between them and the app is a full page load (see §5).

---

### SURFACE A: CUSTOMER STOREFRONT (`{slug}.zenoeats.com`)

#### A1 · Storefront / Menu, route `/`
**Entry:** QR codes on tables, shared links, bookmarks, "Back to the menu" / "Order something else" links, and the admin "slug" link. Public: no sign-in needed.

**Data:** `GET /portal` (restaurant name, tagline, currency, `is_orderable`, `accepting_orders`) and `GET /menu` (meal periods → combos, sections → items and subsection groups). Plus `GET /orders/session` for the account row — ⚠ a 401 is the ordinary answer here (most people reading a menu are nobody yet) and is a state, not an error.

**Layout today:** a header band with the restaurant name (large serif), an optional tagline, and an optional "closed" banner. Below it, a single column (max 768 px) of meal periods, and a sticky cart bar at the bottom.

**Elements:**
0. **Account row** (new; right-aligned, above the name). ⚠ An offer, never a gate — nothing on this page needs an identity.
   - *Nobody yet:* a quiet "Sign in" link to **A5**, carrying `?next=` back to this page.
   - *Signed in:* the customer's name (or email, truncated) and "Sign out".
   - *Guest:* the word "Guest", a "Sign in" link — the standing offer of a real account — and "Sign out".
   - Signing out ends the Clerk session or drops the guest cookie, then does a **full reload** at `/` so nothing rendered from the old identity survives.
   - ⚠ Reading a menu must not pull down the Clerk SDK (≈590 KB). The row asks with a "don't load Clerk to find out" hint, so its worst case is showing "Sign in" to someone who is signed in, corrected on the next page. ⚠ Design a layout that does not shift when the answer arrives.
1. Restaurant **name** (h1) and **tagline** (only if set).
2. **Closed banner:** "Not taking orders right now. You can still look at the menu." Shown when not orderable or not accepting orders.
3. For each **meal period**:
   - Period **name** as a heading, ⚠ shown only when there is more than one period.
   - **Hours**, e.g. "7:00 AM – 11:00 AM". An end at or before the start adds "(next day)". Shown even when the period name is hidden. Omitted when not set.
   - **Combos block** (if any), *before* the sections: a "COMBOS" label, then one row per combo with name, description (optional), slot labels joined by " · " (e.g. "Burgers · Sides · Drinks") and a saving label in red ("12.5% off", "$2.00 off", or "Pick one of each"). The whole row is a button that opens **A3**. Disabled (faded) when the restaurant is closed.
   - **Sections** (one per top-level item type, in the restaurant's order): an uppercase label, the items filed directly on the heading, then **subsections** (subcategory label, quieter and indented) with their items.
   - **Item row** (the whole row is a button that opens **A2**): name, photo (4:3, max 320 px wide, below the name) if one exists, description (optional), "Sold out" in red when unavailable, price on the right.
   - ⚠ An item row is disabled when the item is sold out **or** the restaurant is closed. Rows still display.
4. **Empty menu:** "Nothing on the menu yet." (dashed box).
5. **A4 Cart bar** (see below).

**States:** loading ("Loading…"); error ("This menu isn't available" + server message; also what a non-existent or inactive subdomain shows); closed; empty menu; a single meal period (no period headings); several periods; items with and without photos; ⚠ a photo that fails to load is hidden entirely, never shown as a broken image. Account row: nobody / signed in / guest / still asking.
**Business rules:** opening the page binds the cart to this restaurant (`slug`) and restores a saved cart from localStorage. ⚠ Carts are per restaurant and never leak across subdomains.

#### A2 · Item customisation sheet (modal over A1)
**Entry:** tap an available item row.

**Layout:** a bottom sheet on phones and a centred dialog from 640 px up. Max height 90vh, scrolls, dim backdrop. Tapping the backdrop closes it.

**Elements:**
1. **Hero photo** (16:9) above the header, if the item has one.
2. **Sticky header:** item name, description, close button (✕, "Close").
3. **Modifier groups**, one fieldset each:
   - Legend: group name + rule hint: "Required" / "Pick up to N" / "Optional".
   - Option rows: radio (Pick one) or checkbox (Pick several), a 40 px option thumbnail if it has a photo, the option name, and the price change ("+$0.50", "−$0.50", blank for zero) or **"Included"** when this item comes with that option.
   - ⚠ Unavailable options are faded and disabled.
   - ⚠ A Pick-several group at its maximum ignores further ticks. It never silently drops an earlier choice.
   - ⚠ Options the item "comes with" are pre-selected on open, unless that option is sold out.
4. **Note for the kitchen:** text input, max 280 characters, placeholder "Allergies, how you'd like it cooked".
5. **Sticky footer:**
   - Quantity stepper (− / number / +), from 1 to 20.
   - Primary **"Add [qty] · $total"**: the total updates live and the unit price is floored at $0.
   - ⚠ The button is disabled while required groups are unmet, with red helper text: "Choose an option for {group names}."
6. On add: dispatch to cart and close. ⚠ Identical configurations (same item, options and note) merge into one cart line with summed quantity.

**States:** with or without photo, no modifier groups, required unmet, maximum reached, sold-out options.

#### A3 · Combo builder sheet (modal over A1)
**Entry:** tap a combo row.

**Layout:** same sheet pattern as A2.

**Elements:**
1. **Sticky header:** combo name, description, saving label (red), close button.
2. **One section per slot:** slot label + "Choose one"; radio list of the slot's items (thumbnail, name, "sold out" tag, price).
   - ⚠ Sold-out items are disabled.
   - Re-selecting the same item keeps its modifiers. Choosing a different item resets to that item's defaults.
3. **Nested modifiers:** once an item is chosen, its modifier groups (same component as A2) appear indented under the slot. ⚠ Required rules still apply inside a combo. ⚠ Two slots can offer the same item, and their selections must stay independent.
4. **Note for the kitchen** (280 characters).
5. **Sticky footer:**
   - When a discount applies, a line "$X separately" plus the discount in red "−$Y".
   - Quantity stepper (1–20).
   - **"Add [qty] · $total"**. The preview uses the server's maths: percent rounded once, the discount never exceeds the food total, and the total is floored at 0.
   - ⚠ Disabled until complete, with named helper text: first "Still to choose: {slot labels}.", then "Choose an option for {group names}."
6. Identical combos merge in the cart.

**States:** empty (nothing chosen), partially filled, missing modifiers, ready, no discount, percent discount, amount discount.

#### A4 · Cart bar (sticky footer on A1)
- Hidden when the cart is empty.
- A primary full-width link to `/checkout`: "Review order · N item(s)" and the subtotal preview on the right. ⚠ A combo counts as one item.
- Sub-caption "Tax is calculated at checkout."
- ⚠ Faded and not clickable when the restaurant is closed, but still shown with the count.
- The bar has a translucent, blurred background (the only glass effect in the app today).

#### A5 · Customer sign-in, `/account/sign-in` (standalone HTML page)
**Entry:** redirect from A9/A10/A11 guards (with `?next=`); link from A6 or A7; social return with `?error=` or `?second_factor=1`.

**Step 1: password**
1. Heading "Sign in to order", replaced by "Sign in to order from {Restaurant}" once the restaurant name loads (the generic heading stays on the root domain).
2. Intro copy: "Browsing is open to everyone. An account is how your order and its pickup PIN find their way back to you."
3. **Error box** (hidden until needed). On arrival with `?error=social_failed` it reads "That sign-in didn't complete. Try again."
4. **Social buttons**, rendered only for providers enabled in Clerk, in the order Google (neutral outlined, colour G logo), Apple (black), Facebook (#1877F2 blue). Labels read "Continue with {Provider}", followed by an "or" divider. ⚠ The whole social section is hidden when none are enabled. If settings can't be read, only Google shows. Clicking one disables all of them until the redirect or an error.
5. **Email** and **Password** fields, with a "Forgot password?" link inline on the password label.
6. **"Sign in"** primary. ⚠ Disabled until Clerk has loaded; shows "Signing in…" while busy; on failure selects the password text.
7. "New here? **Create an account**" link (keeps `?next=`).

**Guest panel** (same page, below step 1) — *order without an account*
A bordered section under the "Create an account" line, deliberately **quieter than the form above it**: an account is the better outcome for anyone who will order twice, but the counter still wants the order of someone who will not.
1. Heading "Order without an account".
2. Copy: "We'll email your receipt and the link to your order. Keep this browser: it's the only thing that can open your pickup PIN again."
3. **Error box** (hidden until needed).
4. **Email** (required, `autocomplete="email"`) and **Name** (optional, max 160).
5. **"Continue as guest"** — a *quiet* full-width button, never the page's primary ("Setting up…" while busy). On success: a full navigation to `next`, exactly like a finished sign-in, with the cart waiting where they left it.
6. ⚠ The whole section is **hidden until the restaurant name resolves**. A guest session belongs to the restaurant being ordered from, so on the platform root there is nothing to be a guest of.
7. ⚠ It does not touch Clerk. This is the path that still works when Clerk is unreachable or was never configured, and it must not wait on a script that may never load.
8. ⚠ Nothing here is verified. The address is where the receipt goes, not a claim about who this is; two people who type the same address are two different guests.
9. Validation: "Enter an email address we can send your receipt to." — from the page for an empty field, from the server for an address no receipt could reach.

**Step 2: verification code** (shown when Clerk needs 2-step verification or trusted-device confirmation)
1. Intro changes with the method: "We texted a 6-digit code to {masked phone}" / "We sent a 6-digit code to {masked email}" / "Enter the 6-digit code from your authenticator app." / "Enter one of the backup codes you saved when you set up two-step verification."
2. Code field: numeric, one-time-code autocomplete, wide letter spacing. For backup codes it becomes free text, "Backup code", up to 32 characters.
3. "Continue" primary ("Checking…" while busy).
4. **Alternative method links** for each other offered method: "Use your authenticator app instead", "Text me a code instead", "Email me a code instead", "Use a backup code instead".

**States and edge cases:**
- Already signed in: immediately redirected to `next`.
- Clerk not configured: shows the configuration message. ⚠ The guest panel is unaffected and is still the way through.
- Clerk unreachable: "Couldn't reach the sign-in service. Check your connection and reload." ⚠ Same: the guest panel still works.
- Validation: "Enter your email and password." / "Enter the 6-digit code." / "Enter a backup code."
- Unsupported step: "This account needs a sign-in step this page doesn't support. Try another way to sign in, or reset your password."
- Incomplete: "That didn't finish signing you in. Start again."
- Clerk's own error messages (wrong password, etc.) show verbatim.
- A pasted code with spaces ("123 456") is accepted.

#### A6 · Customer sign-up, `/account/sign-up` (standalone HTML page, three steps)
**Step 1: form**
1. Heading "Create an account"; intro "We'll email you a 6-digit code to confirm the address."
2. Social buttons ("Sign up with {Provider}") and the "or" divider, same rules as A5.
3. ⚠ **Name (optional)** field, only when the Clerk instance collects names (max 160).
4. Email.
5. Password with a live hint "At least {N} characters." (N from Clerk, default 8). The hint turns red while too short.
6. Confirm password, with a red "These do not match." while mismatched.
7. **Clerk bot-protection challenge slot** (`#clerk-captcha`): ⚠ must be kept; Clerk may render a challenge here.
8. "Create account" primary, ⚠ disabled until the email contains "@" and the passwords are valid and match ("Sending code…" while busy).
9. "Already have an account? **Sign in**".

**Step 2: continue** (after a social sign-up that is missing details)
- Intro "Almost done. We need a little more to finish your account."
- Shows only the fields Clerk still needs:
  - Email, with hint "Your order updates and receipts go here."
  - Name.
  - Checkbox "I agree to the Terms of Service and Privacy Policy."
- "Continue" ("Saving…").
- Errors: "Enter your email address." / "Enter your name." / "Agree to the terms to create your account."

**Step 3: code**
- "We sent a 6-digit code to **{email}**."
- Verification code field; "Create account" ("Checking…").
- "Nothing arrived? Check spam, or **send a new code**". ⚠ The resend link is disabled for 15 s after use.

**Edge cases:**
- Already signed in: redirect.
- `?continue=1` with no pending sign-up: "That sign-up has expired. Start again."
- Clerk needs fields this page can't collect: "Your account needs details this page can't collect ({fields}). Try another way to sign up."
- "That didn't finish creating your account. Start again."
- "Enter the 6-digit code from your email."

#### A7 · Forgot password, `/account/forgot-password` (standalone HTML page, two steps)
**Step 1:** heading "Reset your password"; intro "Enter the email you order with and we'll send a 6-digit code to choose a new password."; Email; "Send code" (⚠ disabled until Clerk loads; "Sending…"); "Remembered it? **Sign in**". Validation: "Enter your email address."

**Step 2:**
- "We sent a 6-digit code to **{email}**. Choosing a new password signs you in here."
- Code field; New password (length hint); Confirm (mismatch hint).
- "Set password" (⚠ disabled until the passwords are valid; "Saving…").
- Resend link (15 s cooldown).
- Errors: "Enter the 6-digit code from your email." / "Your password was changed, but this account needs another sign-in step. Sign in to continue."
- On success: signed in and redirected to `next`.

#### A8 · Social sign-in return, `/account/sso-callback` (standalone HTML page)
Only centred text: "Signing you in…". Clerk finishes and routes onwards to `next`, to A6 continue or code, or to A5 step 2. Any failure goes back to A5 with `?error=social_failed`. ⚠ Needs a designed interstitial or loading state.

#### A9 · Checkout, route `/checkout` (requires an identity: an account **or** a guest session)
**Entry:** A4 cart bar; "Back to your order" links from A10.

**Guard states (A12):**
- Loading ("Loading…").
- Nobody yet (401): redirect to **A5** with `?next=`. ⚠ A5 offers both an account and continuing as a guest, so this is the right destination even where Clerk is unconfigured and an account is not on offer at all.
- "Can't reach sign-in" ("Check your connection and reload. You can still browse the menu.") — any guard failure that is not a 401.
- ⚠ The full-page **"Sign-in is not configured"** state that used to appear here **no longer exists**. Do not design one.

**Data:** `GET /portal`; cart from Redux/localStorage; `POST /orders/quote` (server pricing).

**Elements:**
1. **Account bar:** "Ordering as {name or email}" with "Not you? Sign out". For a guest it reads **"Ordering as a guest · receipt to {email}"** with **"Start over"** — the address they typed minutes ago is the only thing identifying them, and saying so before payment is cheaper than a refund. ⚠ Signing out ends the Clerk session; "Start over" drops the guest cookie, which is **irreversible**: nothing else can name that guest or their orders again. Both return to `/`.
2. Heading "Pick up from {Restaurant}".
3. **Line list, combos first.** Each combo is one row: name; for each slot "{Slot}: {Item} · {modifier labels}"; note (italic); quantity stepper (− / qty / +) with aria labels "Fewer / More {name}"; line total preview.
4. **Item lines:** name; modifier labels ("Group: Option · …"); note (italic); stepper; line total preview.
   - ⚠ Pressing − at quantity 1 **removes the line**. There is no separate remove button.
   - ⚠ There is no upper limit on the checkout stepper (A2/A3 cap at 20).
5. **Totals** (appear once the server quote answers): Subtotal; **Discount** row only when > 0 ("−$X"); Tax; **Total** (emphasised, top rule).
   - ⚠ The quote re-runs on every cart change. Totals show nothing until the first quote.
6. **"Anything else?"** note: text, max 500, placeholder "Name for the counter, pickup time".
7. **Error text** (red):
   - Quote or network errors.
   - "Prices changed while you were ordering. Check the total and try again."
   - "Something in your cart just sold out. Remove it and try again."
8. **"Continue to payment"** primary, full width.
   - ⚠ Disabled while busy, before the quote arrives, or when the restaurant is not orderable. Shows "Setting up payment…" while busy.
   - On click: create the order (with an idempotency key), create the Stripe payment intent, then navigate to A10.

**States:**
- Loading (portal).
- "Checkout is unavailable" + message (portal error).
- **Empty cart:** "Your cart is empty" with a "Back to the menu" button.
- Normal; quoting; busy; each error.

**⚠ Edge cases:**
- The idempotency key is reused across retries and Back navigation (sessionStorage) and is keyed to the exact order body, so a double tap or Back never creates two orders.
- The server always reprices. Displayed line prices are previews.
- ⚠ Arriving here is usually a **full page load** — signing in and "continue as guest" both navigate out of React and back, which throws the Redux store away. The page re-binds the cart to the restaurant and restores it from localStorage on entry. A design that assumes the store survived shows "Your cart is empty" to someone whose cart is sitting in storage.

#### A10 · Payment, route `/checkout/pay/:orderId` (requires an identity: an account **or** a guest session)
**Entry:** only from A9 (with the payment secret passed invisibly), or by reloading or reopening the URL.

**Elements:**
1. Heading "Pay {Restaurant}"; subtext "Your order is held while you pay. Nothing is charged until you confirm."
2. **Total** row (bold, ruled above and below).
3. **Stripe Payment Element** (tabs layout: card, Link, wallets). It is rendered by Stripe inside an iframe. Only Stripe's appearance variables can be themed (currently theme "flat", primary #B3341F, system-ui).
4. Error text (Stripe's message, or "That payment didn't go through.").
5. **"Pay $total"** primary ("Processing…"; disabled until Stripe loads).
6. Caption "Your card is charged by the restaurant through Stripe."
7. Link "Back to your order" (→ A9).

**States:**
- "Opening payment…" (loading or recovering).
- "Nothing to pay for" (no id) with a "Back to your order" button.
- "We couldn't open payment" + message, with a "Back to your order" button.
- Ready; processing; card error.

**⚠ Edge cases:**
- On reload the page recovers the existing payment intent (never a second one) and reads the total from the order.
- If the order is no longer awaiting payment (paid, expired or cancelled), it redirects to A11.
- Success clears the cart — ⚠ in localStorage as well as in memory; the page binds the cart to the restaurant on entry so that clearing it actually empties it, otherwise a customer who reloaded this page pays and then finds the food they just bought still in their cart — and replaces the history entry with A11.
- 3-D Secure may redirect away and back to A11.
- A Stripe "success" does not mean paid. A11 waits for the server.

#### A11 · Order tracking, route `/orders/:orderId` (requires an identity **or** an order-view token)
**Entry:** after payment; the Stripe redirect return; links in the confirmation email (E1) — ⚠ a guest's copy of that link carries `?t={token}`.

**Data:** `GET /orders/{id}` (with `?t=` when opened from a guest's email), polled (2 s while pending payment, 8 s while active, stopped at a terminal status).

**Elements:**
1. "Order #{number}" (small), then the status **title** (large) and **detail** (see the status table in §2.0.2).
2. **Awaiting payment strip** (status PENDING_PAYMENT and not paid): a pulsing red dot with "Waiting for the payment confirmation from Stripe."
3. **Pickup PIN card** (only when the server returns a PIN): "Pickup PIN", the 6 digits in a large serif with wide spacing, and "Show this at the counter. Don't share it with anyone else."
4. **Item list**, shown as the customer *bought* it rather than as the kitchen plates it:
   - A single item is one row: "{qty}×", name, modifiers "Group: Option · …", note (italic), line total.
   - ⚠ A **meal deal is one row, not three**: the deal's name, the number of deals, and their combined total, with the components listed underneath — indented, quieter, each with its own modifiers and note ("Crispy Chicken · No pickles", "Fries", "Cola"). The API sends one line per component because that is what the kitchen needs to plate it; the customer's copy puts it back together. Without this the page listed three unrelated things the customer never ordered separately.
   - ⚠ Grouped by the line's **combo group**, not by name: two of the same deal in one order are two rows, because they can be built differently.
   - ⚠ Every component of one deal carries that deal's quantity, so the row's count is the number of deals, not a sum over its parts.
5. **Totals:** Subtotal, Tax, **Total**. ⚠ There is no Discount row here, unlike A9 (see §7).
6. "Payment: {status in lowercase}".
7. "Order something else" button (→ `/`).

**States:** loading ("Loading your order…"); error ("We can't find that order" + message + "Back to the menu"); each of the 7 statuses; with and without a PIN; single items, meal deals, and a mix. ⚠ Status changes happen live, without a reload.

**⚠ Edge cases:**
- **The `?t=` order-view token.** A guest's session is a cookie in one browser, so the link in their confirmation email has to work on a device that holds neither that cookie nor a Clerk session — a laptop, a phone that has never signed in to anything here. The token is signed by the API, names **exactly one order**, and lasts 7 days. It is a capability, not an identity: it opens that order and nothing else, and a wrong or expired one gets the same "We can't find that order" as a wrong id.
- ⚠ The token is **taken out of the address bar** on first read and kept in sessionStorage for that tab (surviving reloads, dying with the tab). A token in a URL is one that gets copied into a chat, mailed on, pasted into a support ticket and kept in history — and it opens a pickup PIN. ⚠ **Never design a "copy this link" or "share your order" affordance on this page.**
- ⚠ With a token the page renders without asking anyone to sign in: the guard lets it straight through, and the account row and any "sign in" prompt must not appear.

#### A12 · Customer guard states
The guard on A9, A10 and A11 asks the **API** who is ordering (`GET /orders/session`), not Clerk — a guest is an httpOnly cookie no script can read and a signed-in customer is a Clerk token, so one question to one endpoint covers both where asking Clerk could only ever see one of them.

| State | What shows |
|---|---|
| Still asking | "Loading…" (G1). ⚠ "Not asked yet" is not "nobody" — never redirect before the check answers |
| Signed-in customer, or guest | the page |
| 401 | full-page navigation to **A5** with `?next=` |
| Any other failure | "Can't reach sign-in" — "Check your connection and reload. You can still browse the menu." |

- ⚠ **A11 additionally accepts a `?t=` order-view token** and renders without asking anyone at all.
- ⚠ The **"Sign-in is not configured"** full-page state is **gone**. A5 offers guest checkout, which needs no Clerk, so there is no longer a dead end to show. Do not reintroduce it.

---

### SURFACE B: RESTAURANT STAFF PORTAL (`{slug}.zenoeats.com/manage`)

#### B0 · Staff portal shell (header on B4–B11)
1. **Wordmark slot:** the restaurant name, truncated with the full name on hover. A fixed width so a long name cannot shift the tabs along: 176 px normally, 112 px below 640 px where 176 would be half a phone. ⚠ It links to wherever that role starts, which for a driver is `/manage/deliveries`, not the board.
2. **Tabs**, filtered by role (matrix in §2.0.2), in this order: Kitchen · Deliveries · Stock · Menu · Staff · Reports · Settings. The active tab is a solid dark pill.
   - ⚠ An admin sees seven tabs and a driver sees one. The header has to hold both without
     the seven crowding and the one looking like a mistake.
   - ⚠ **Below 640 px the tab row drops to its own full-width line** under the wordmark and the
     sign-out button, and scrolls sideways; the active tab is scrolled into view. About five of
     seven are visible at 390 px and the rest are a swipe away. This is a repair, not a design:
     sharing one line left the tabs ~100 px, and before that the row simply widened the page —
     every portal screen scrolled sideways on a phone. A real mobile navigation pattern is still
     wanted (§7.11); whatever replaces this must keep every tab reachable without the document
     scrolling horizontally.
   - ⚠ Before the role is known, only the tabs the whole floor has are shown (Kitchen, Stock), so nothing appears and then disappears. A driver therefore sees those two for a moment and then only Deliveries — worth designing the swap so it does not look like a glitch.
   - ⚠ A driver's row is a single tab. Do not design the staff header as though it always holds four or more.
3. **Identity:** "{full name or email} · {role}". ⚠ Hidden below 640 px.
4. **"Change password"** link (→ B2). ⚠ Hidden below 640 px.
5. **"Sign out all devices"** link: asks first, inline in the header — "Sign out every device using this login, shared tablets included?" with a primary "Sign out everywhere" and a "cancel" link. ⚠ Hidden below 640 px.
6. **"Sign out"** button: ends the session **on this device only** and goes to B1. ⚠ That is deliberate: restaurants share one login across kitchen tablets, so one person leaving must not sign the pass out mid-service.
7. Content area max 1024 px.

⚠ There is no mobile nav pattern today. The tabs just sit in a row.

#### B1 · Staff sign-in, `/manage/login` (standalone HTML page)
- Heading "Restaurant sign-in"; subtext "Staff access for this restaurant."
- Email, Password, error box, "Sign in" ("Signing in…"; selects the password on failure).
- Footer: "Administrators are configured in the deployment environment. Contact whoever holds it if you need access." (see §7).
- Validation: "Enter your email and password."
- ⚠ A wrong password and an unknown email produce the same server message.
- On success, goes to B2 if the password is temporary, otherwise to `next` (default `/manage`). ⚠ A driver goes to `/manage/deliveries` instead: the board is not theirs to open.
- ⚠ The restaurant comes from the subdomain. There is no restaurant picker.

#### B2 · Change password, `/manage/change-password` (standalone HTML page, **two modes**)
**Mode 1: temporary password** (forced; the account can do nothing else)
- Heading "Choose a password".
- Intro "Your account was created with a temporary password. Pick your own to continue — nobody at Zenoeats will know it."
- First field labelled "Temporary password".

**Mode 2: voluntary** (signed in with own password, arrived from the B0 link)
- Heading "Change your password".
- Intro "Enter the password you use now, then choose a new one. You'll be signed out everywhere and sign in again with the new one."
- First field labelled "Current password".
- Adds a secondary "Back to the portal" button.

**Both modes:**
- New password with the hint "At least 12 characters." (red while too short).
- Confirm with "These do not match."
- Error box.
- "Set password": ⚠ disabled until current is filled, new is at least 12 characters and they match ("Saving…").
- Success goes to B1 (the session is cleared).
- Not signed in: redirect to B1 with `next=/manage/change-password`.

#### B3 · Accept invitation (guard state, any `/manage*` route while membership is INVITED)
- Heading "Join {Restaurant}".
- "You've been invited to the team as **{role}**. Accept to start using the portal, signed in as {email}."
- Error box.
- "Accept invitation" primary ("Accepting…").
- "Not now — sign out" secondary (→ B1).
- On accept, the portal loads normally. No shell is shown on this screen.

#### B4 · Not part of your role (guard state inside B0 shell)
"Not part of your role" with "You're signed in to {Restaurant} as **{role}**, which doesn't include this page. An admin at the restaurant can change your role." Shown when a role opens a page it may not use (by URL or bookmark).

#### B5 · Kitchen board, route `/manage` (all roles)
**Purpose:** a live ticket board on a shared kitchen or counter tablet all shift.

**Data:** `GET /restaurant/orders`, polled **every 5 s**. Only paid orders appear.

**Layout:** two columns from 1024 px up (stacked on narrower screens):
- **"Making now (N)"**: AUTO_ACCEPTED and PREPARING.
- **"Ready (N)"**: READY_FOR_PICKUP, READY_FOR_DELIVERY and OUT_FOR_DELIVERY — to the kitchen these are all "done, gone soon", whether they wait at the counter or for a driver.

**Page-level elements:**
0. **New-order alert** (⚠ easy to lose, and the reason the board is worth watching):
   - A right-aligned control: **"Turn on sound for new orders"**, or "Sound on for new orders · turn off" once on. ⚠ Browsers refuse sound until someone taps the page, so this tap is what unlocks it, and after a reload it reads "Tap to turn sound back on".
   - A newly arrived ticket gets a **red border and a "new" badge** until it is tapped or a minute passes.
   - The **tab title** counts them: "(2 new) …".
   - ⚠ Orders already on the board when the page opens are not "new" and do not chime.
1. Error banner (action error or board load error).
2. **Notice banner** (neutral, dark left rule) for manager action results:
   - "#{n} handed over without a PIN."
   - "#{n} cancelled. The customer has not been refunded: issue the refund from your Stripe Dashboard."
   - "#{n} cancelled. Its payment was already refunded."
   - "#{n} is {driver}'s delivery."
   - "#{n} is a collection again."
3. Column empty states: "Nothing in the queue." / "Nothing ready for the counter or a driver."
3b. **"Done today (N)"**, a collapsed section under the columns (⚠ new; it answers "I ordered twenty minutes ago, where is it?"):
   - A disclosure heading with the count, and when open an order-number search field.
   - One row per order finished today (the restaurant's today, by the day it was paid), newest first: "#{n}", what happened ("handed over" / "handed over without PIN" / "cancelled", the last in red), the time, who did it, a refunded tag where it applies, the items, and on its own line "Reason: {reason}" for an override or a cancellation.
   - Empty: "Nothing handed over or cancelled yet today."; no match: "No order today matches #{n}."
4. Footer note: "Unpaid orders never reach this board. An order appears only after Stripe confirms the payment by webhook."

**Ticket card:**
- **Header:** "#{order number}" (serif).
  - A red **"new" badge** while the order is unseen (see the alert above).
  - A dark **"delivery" badge** when a driver has been assigned.
  - A red **refund tag**: "refunded" / "partly refunded" (⚠ the only sign a Stripe Dashboard refund happened).
  - **Age:** "just now" / "{N} min ago", ⚠ counted from **payment**, not from when checkout began. Turns red after 15 minutes.
- **Lines:**
  - Plain item: "{qty}×", name, modifiers joined " · " (muted), item note in red.
  - ⚠ **Combo:** grouped as one block, "{qty}× {Combo name}" with its items indented beneath (each with modifiers and red note). The grouping keeps the ticket's original line order.
- **Delivery line** (ruled above, delivery orders only): the address, then " · {driver name}" or " · no driver yet".
- **Customer note** (red, ruled above) if present.
- **Action area** (one of the variants below).
- ⚠ Tapping anywhere on the ticket clears its "new" badge.

**Action variants:**
| Column / state | Controls |
|---|---|
| Making now | Primary "Mark ready for pickup", or **"Mark ready for the driver"** on a delivery (disabled while busy) · manager-only links "assign driver" / "change driver" and "cancel order" |
| Waiting, normal | Secondary "Collect with PIN" · manager-only row: "no PIN? hand over without it" and "cancel order" |
| Waiting, PIN entry open | Numeric 6-digit input (autofocus, digits only, placeholder "······", wide spacing) · "Hand over" (disabled until 6 digits / busy) · "Cancel" |
| Waiting, **PIN locked** (5 wrong PINs) | Red "Locked after five wrong PINs." (+ " Ask a manager to hand it over." for non-managers) · manager-only primary "Hand over without PIN" · manager "cancel order" |
| Ready, **delivery** | A line of status instead of a PIN box: "Ready, but no driver assigned yet." / "Waiting for {driver} to pick it up." / "On the road with {driver}." · manager-only links "change driver", "back to collection" (⚠ not once it is on the road) and "cancel order" |
| **Manager reason form** (replaces the action area inline, on that ticket only) | Override copy: "Hand **#n** over without the customer's PIN. Check it is theirs first, by name or their order confirmation. Your name and reason are kept with the order." · Cancel copy: "Cancel **#n**." + either "Its payment has already been refunded." or red "This does not refund the customer. Issue the refund from your Stripe Dashboard." · Reason input (autofocus, max 200, placeholders "Why? e.g. phone died, checked name" / "Why? e.g. never collected") · Confirm button "Hand over without PIN" / "Cancel order" (⚠ disabled until reason ≥ 3 chars) · "back" link |

**Assign-driver form** (⚠ new; same inline treatment as the reason form, on that ticket only):
- Copy: "Send **#n** out with a driver. They see this order and its address, and nothing else of the portal."
- A driver select ("Choose a driver…") listing the restaurant's active drivers by name, and an address field ("Delivery address, as the customer gave it", max 300), pre-filled when the order already has one.
- "Assign" (⚠ disabled until a driver is chosen and the address is at least 3 characters) and a "back" link.
- ⚠ No drivers on the team: "No drivers on the team yet. An admin can invite one from the Staff page."
- ⚠ Assigning is what makes an order a delivery. There is no other way to create one, because customers cannot order delivery.

**⚠ Rules and edge cases:**
- Only one PIN entry or manager form is open at a time across the whole board. Opening one closes the other.
- Wrong PIN shows the server message.
- `PIN_LOCKED` closes the PIN box and shows "Five wrong PINs, so this order is locked. You can hand it over without the PIN." (managers) or "…Ask a manager to hand it over." (others).
- The ticket switches to locked on the next poll.
- The manager form is deliberately **inline on the ticket, not a modal**, so the ticket stays visible on a shared screen. Keep that intent.
- Tickets leave the board when completed, delivered or cancelled (next poll) and appear under "Done today".
- ⚠ A delivery never offers the PIN box: there is no counter, and the API refuses it.
- "Back to collection" clears the driver and the address, and an order that was waiting for a driver waits at the counter instead.

#### B6 · Stock, route `/manage/stock` (all roles)
**Purpose:** mark items sold out or back in stock mid-rush.

**Data:** `GET /restaurant/stock`, polled every 30 s.

**Elements:**
1. Error banner.
2. **Search** field "Find an item": matches name or type.
3. **"Sold out (N)"** section first:
   - Rows show item name (truncated) and type label (e.g. "Food / Burgers").
   - Primary button **"Back in stock"**.
   - Empty: "Everything is in stock."
4. **"In stock (N)"** section (hidden when 0): rows with a secondary button **"Mark sold out"**.
5. Each button shows "Saving…" while busy and exposes `aria-pressed`.

**States:** loading; "No items on the menu yet."; "Nothing matches "{query}"."; error.
**⚠** Buttons are deliberately large tap targets ("tapped on a greasy tablet mid-rush"). A toggle moves the row between sections.

#### B7 · Menu builder, route `/manage/menu` (Admin, Manager)
**Data loaded once for all tabs:** restaurant menu, item library, item types, combos, modifier groups. Any load error shows in the page banner.

**Tab bar** (underline style; the active tab has a red underline): **Preview** (default) · **Items** · **Meal periods** · **Combos** · **Modifier library**. Switching tabs clears the error. The tab order mirrors how a menu is built.

**Shared conventions across the builder:**
- Errors from every tab surface in **one page-level error banner** above the tabs.
- A **pencil icon** next to a heading switches that block into edit mode.
- In edit modes, ⚠ **deleting is staged**: the row is struck through with a "keep it" link, and nothing is deleted until Save; Cancel discards everything. **Adding is immediate.**
- After a failed save the editor stays open with the edits kept.
- A warning line summarises staged deletions ("Saving will delete N items from every meal period. Cancel and nothing is removed.").
- Secondary actions are lowercase underlined text links ("add an item", "cancel", "delete", "keep it").
- **Chips** are toggle buttons with `aria-pressed`: solid dark when on, outlined when off.
- **ImagePicker** (items and options) has two sizes:
  - **md** (in forms): 80 px thumbnail (a photo icon when empty) plus links "add a photo" / "change photo" / "Uploading…" / red "remove photo", and the caption "JPEG, PNG or WebP."
  - **sm** (in dense rows): 36 px thumbnail button (click to choose) with a small × badge to remove.
  - An "…" overlay shows while uploading.
  - ⚠ Choosing a file uploads immediately (after shrinking in the browser), but the photo only goes live when the surrounding form is saved.
  - ⚠ Save buttons are disabled with "Uploading photo…" while any upload in that form is running, counted across several rows.
  - Accepts jpeg, png, webp. Upload errors go to the page banner.

##### B7a · Preview tab (read-only)
1. Summary line: "{N} items across {M} categories, {P} meal periods, {S} not served all day. Each item appears once here, however many periods serve it." (the period clause only when P > 1).
2. **Category-first listing:** section heading (serif), items, subsection headings (uppercase muted) with items.
   - Item row: name + red "sold out" tag, photo (storefront size), description, price.
   - ⚠ "{Period, Period} only" note for items not served in every period.
3. **"By meal period"** block (only if a period has exclusive items or combos): intro "Combos, and what each period serves that no other one does. Everything else on the menu is served all day."
   - Per period: name + hours; combo rows (name, slot labels, red saving label); exclusive item rows (without the "only" note).
4. **Empty:** "Nothing on the menu yet. Add items on the Items tab, then serve them in a meal period."

##### B7b · Items tab
**B7b-1 Item types strip** (reading mode):
- "Item types" label with a pencil (only if types exist) that opens the types editor.
- Filter chips: **"All {count}"**, then one chip per type with a count. The count is the items the filter shows; a heading includes its subcategories.
- ⚠ Subcategory chips are indented with a "└" marker and a dashed outline.
- Clicking the active chip clears the filter. The filter is not persisted.
- "add a type" / "cancel" link opens the **add-type row**:
  - Name input, placeholder "Tiffins, Thalis, Desserts…"; Enter adds.
  - ⚠ A "where it goes" select ("as a heading of its own" / "inside {Heading}") appears only when headings exist.
  - "Add type" ("Adding…").
  - **"Done"**: saves any typed name first and stays open if refused.
  - Once something is added: "{N} type(s) added and saved. Add another, or choose Done to close this row."
  - The row stays open for rapid entry.
  - Validation: "Enter a name for the type, like Tiffins."

**B7b-2 Types editor** (edit mode, dark border):
- Every type as a small card with:
  - Name input (the struck-through name when staged for deletion).
  - Direct item count.
  - "delete" / "keep it" link.
  - Placement select: "a heading" / "inside {Heading}". ⚠ Disabled, with the tooltip "{Type} has subcategories, so it stays a heading.", when the type has children.
- "Save types" ("Saving…") and "cancel".
- Footer copy:
  - With staged deletions (red): "Saving will delete N types. A type still on items, or a heading with subcategories under it, is refused, and the message says what is in the way."
  - Otherwise: "Renaming a type changes its heading everywhere at once. No item moves. A subcategory is a subheading on the storefront only: combos and modifier groups read the heading above it."
- Validation: "{Type} needs a name. It cannot be left blank."
- ⚠ The server may refuse moving a heading used by combos or groups. The editor stays open showing the message.

**B7b-3 Items panel header:** "Items" with a pencil (only when items exist and not adding) that toggles **bulk edit**. The action link "add an item" / "cancel" is hidden while editing. When not adding, the helper reads "An item written here can be served in any number of meal periods. One price, one sold-out toggle, wherever it appears."

**B7b-4 Add item form** (grid):
- Item name (autofocus).
- Type select: flat list labelled "Food / Burgers"; "No types yet" when empty; defaults to the first type.
- Price (placeholder 10.95).
- Description.
- Photo (ImagePicker md).
- **"Served during"** meal-period chips. With none: "No meal periods yet. Add one on the next tab, then tick it here. The item is saved either way and waits until it has somewhere to go."
- **"Modifier groups for this item, and what it comes with":**
  - Group chips (with a "required" tag), filtered to groups for the item's top-level type plus groups for every type. ⚠ The list re-filters live when the type changes.
  - For each ticked group, a "Comes with, from {Group}" row of option chips showing "free" when on, or the price change ("+0.00" when zero).
  - ⚠ Unticking a group removes its included options.
  - Empty: "No groups for {type} yet. Create one in the modifier library."
- "Add item" ("Saving…" / "Uploading photo…"), "cancel".
- Validation:
  - "Enter a name for the item."
  - "Choose a type for this item, or add one first."
  - "Enter a price for {name}. Use 0 if it is free."
  - "Enter the price for {name} as a plain amount, like 10.95." (⚠ refuses "10,95" or currency symbols)

**B7b-5 Item list** (reading mode), one row per item:
- 32 px thumbnail (if photo).
- Name.
- Type badge ("Food / Burgers").
- Meal periods joined " · ", or "not on any meal period".
- Price.
- ⚠ Inline availability toggle link: "in stock" (muted) / "sold out" (red). It works outside edit mode.

**B7b-6 Bulk items editor** (edit mode, every row editable at once):
- Row: ImagePicker sm · name input · type select · price input · "periods & options" / "less" disclosure · "delete" / "keep it".
- Expanded panel:
  - Description.
  - "Served during" chips (or "No meal periods yet.").
  - "Modifier groups" with the same group and comes-with UI as B7b-4 (empty "No groups for this type yet.").
  - Only one row is expanded at a time.
- Footer: "Save changes" ("Saving…" / "Uploading photo…"), "cancel".
  - Status text: staged-delete warning (red) or "A new price applies to new orders only."
- ⚠ Only changed fields are sent.
- ⚠ Items created while the editor is open appear without wiping in-progress edits.
- Validation: blank name, blank or unreadable price (same messages as B7b-4).

**Empty states:**
- "No items yet. Add one above, then put it on a meal period."
- Filter with no matches: "Nothing typed as {Type} yet." with a **"Show every item"** button.

##### B7c · Meal periods tab
1. **"Meal periods" panel:** name input "Breakfast, Lunch, Late night…" and "Add period". Validation: "Enter a name for the meal period, like Breakfast."
2. Empty: "No meal periods yet. Add one above, then choose which items it serves."
3. **Per-period card:**
   - **Header:** name (serif) + hours (if set) + pencil; "add items" / "cancel" link.
   - **Period editor** (replaces the header):
     - Name input.
     - "served [time] to [time]" native time inputs, with the hint "runs into the next day" when end ≤ start.
     - "Save", "cancel", red "delete period".
     - Validation:
       - "Enter a name for the meal period. It cannot be left blank."
       - "Set both a start and an end time, or clear both to leave the hours unsaid."
       - "The start and end times are the same. Set an end later than the start."
     - Clearing both times removes the hours.
   - **Delete confirm** (inline): red "{Name} will be deleted." plus either "The N items it serves are kept and stay on any other period serving them." or "It serves nothing, so nothing else changes."; "Delete period" and "keep it".
   - **Item picker** (inline panel):
     - "Items not yet on {Period}. Adding one lists it here; it stays on every other period serving it."
     - Multi-select chips of library items not yet on the period (name + type).
     - "Add {N} item(s)" ("Adding…"), "cancel".
     - Validation: "Tick the items to add to {Period} first."
     - When all are already on it: "Every item in the library is already on {Period}. Write a new one on the Items tab."
   - **All-day fold banner** (only with 2+ periods): "{N} items are on every period, so they are served all day." with a "show them" / "fold them away" toggle. ⚠ All-day items are hidden by default.
   - **Served list:**
     - Section heading, then rows of name, modifier group names (muted), price, the "in stock" / "sold out" toggle, and a red **"remove"** link (⚠ no confirmation by design: it only takes the item off this period).
     - Subsection subheadings are indented.
   - Empty: "Nothing served in this period yet. Add items from the library, or write a new one on the Items tab and tick this period."
   - All folded: "Everything {Name} serves is served all day. Nothing is on this period alone."
- ⚠ Hours are display-only. Nothing blocks ordering outside them.

##### B7d · Combos tab
1. **"Combos" panel** with "add a combo" / "cancel". Helper text:
   - With no periods: "A combo is sold during one meal period and offers items that period serves. Add a meal period first, and put some items on it."
   - Otherwise: "One item from each type you include, every one required. The saving comes off what those items cost separately."
2. Empty (periods exist): "No combos yet. Add one above."
3. **Combo card** (reading):
   - Name (serif) + pencil.
   - "{Period} · {saving in red}" (+ red " · hidden" when `is_available` is false).
   - Slot rows: type name and the offered item names joined " · ".
   - Empty slots: "Nothing in this combo yet. Open it and tick the items it includes."
4. **Combo form** (add and edit):
   - Combo name (placeholder "Burger Meal").
   - **"Sold during"** period select. ⚠ **Locked once the combo exists.** Changing it during creation clears the ticks.
   - Description.
   - Discount select: "Percentage off" (default) / "Amount off" / "No discount".
   - Value input ("Percent off the total", placeholder 10 / "Amount off the total", placeholder 1.50), hidden for No discount.
   - **Item ticks:** helper "What this combo includes. Tick the items a customer may choose from. Every type you tick becomes one required choice." For each top-level type with items served in that period: the type label (with a red dot once ticked) and item chips with prices.
   - Guidance: "Choose a meal period first." / "That meal period serves nothing yet. Put items on it first, on the Meal periods tab."
   - Buttons: "Create combo" / "Save changes" ("Saving…"), "cancel", and in edit mode a red "delete combo".
   - Validation:
     - "Enter a name for the combo, like Burger Meal."
     - "Choose the meal period this combo is sold during."
     - "Tick the items this combo includes. Each type ticked becomes a choice."
     - "A combo needs at least two types. One item on its own is just an item."
     - "Enter the discount as a percentage, like 10." / "…as an amount, like 1.50."
     - "Enter the discount as a percentage between 0 and 100."
     - "Enter the discount as a plain amount, like 1.50."
5. **Delete confirm** (replaces the card): red "{Name} comes off the menu. The items it offered are untouched and stay on sale on their own." with "Delete it" and "keep it".

##### B7e · Modifier library tab
1. **"New modifier group" panel:**
   - Group name (placeholder "Veggies, Ice level, Sauce add-ons").
   - "Choice" select: "Pick several" (default) / "Pick one".
   - "Max choices" input (only for Pick several, default 3).
   - Checkbox "Customer must choose".
   - **Type picker:** "Shows on every item type. Pick one or more to narrow it." / "Shows on these item types, and anything filed under them", with toggle chips for top-level types.
   - **Options table** (column headers Photo · Options · Price change):
     - Starts with 3 blank rows, placeholders "Lettuce", "Tomato", "Jalapenos", then "Another option".
     - Each row: ImagePicker sm, name, price change (placeholder 0.00), and a red "remove" (⚠ invisible but space-holding when only one row remains).
     - ⚠ **Enter in a name field inserts a new row below and focuses it.**
     - Helper "Leave a price blank for no change. A negative one is allowed: type -0.50 for no cheese."
     - "add another option" link.
   - "Create group" ("Creating…" / "Uploading photo…"). ⚠ It always submits and names the failing field rather than disabling itself.
   - Validation:
     - "Enter a name for the group, like Veggies or Ice level."
     - "Enter Max choices: how many options a customer may pick."
     - "Enter Max choices as a whole number, 1 or more."
     - "Enter the price change for {option} as a plain amount, like 0.50 or -0.50."
     - "Add at least one option. A group with none has nothing to offer."
   - Blank rows are ignored.
2. **"Library" panel:** a 2-column card grid. Empty: "No groups yet. Create one and it becomes reusable across every item."
3. **Group card** (reading):
   - Name + pencil.
   - Rule summary ("pick one" / "up to N", plus " · required").
   - "shows on {Types} items" line (only when narrowed).
   - Option list: 28 px thumbnail, name, price change.
4. **Group editor:**
   - Name input.
   - Rule summary (⚠ **read-only**: choice type, max and required cannot be edited after creation).
   - Type picker.
   - Option rows: ImagePicker sm, name, price change, delete / keep it (staged; struck-through rows keep column alignment).
   - "add option" opens the **add-option row**: ImagePicker sm, name (placeholder "Extra pickles"), price change (default 0.00), "Add" (⚠ immediate), "cancel".
   - "Save changes", "cancel", red "delete group".
   - Deleting the whole group is staged: the name is struck through with "keep it" and the copy "This group and its N options will be deleted when you save. Every item offering it stops offering it."
   - Staged option deletion warning: "Saving will delete N options. Cancel and nothing is removed."
   - Validation:
     - "Enter a name for the group. It cannot be left blank."
     - ⚠ "A group needs at least one option. Delete the whole group instead." (removing every option is refused)
     - "Enter a name for the option currently called {name}."
     - "Enter the price change for {name} as a plain amount, like 0.50 or -0.50."
     - "Enter a name for the option before adding it."

#### B8 · Staff / Team, route `/manage/staff` (Admin only)
1. Error banner.
2. **"Invite someone" panel:**
   - Email input (placeholder name@example.com).
   - Role select (admin / manager / kitchen / cashier / driver, default kitchen).
   - "Invite" (⚠ disabled until the email contains "@"; "Inviting…").
   - Live **role help text** for the selected role.
   - **Issued invite card** (after inviting):
     - "Invited **{email}**."
     - If a new login was created: "We've emailed them the sign-in link. Give them this temporary password yourself; it is never emailed, is shown once, and cannot be looked up again." with the **temporary password** large and select-all.
     - Otherwise: "…They already have a Zenoeats staff login and sign in with the password they have. If they've lost it and work only here, you can reset it from the team list; otherwise Zenoeats support can."
     - The sign-in URL, select-all.
     - ⚠ It disappears on leaving the page.
   - Explainer: "An invitation grants nothing on its own. We email the person a link to this restaurant's sign-in page, where they accept it before the role becomes active. Someone new also needs the temporary password shown here, which is never emailed: pass it on yourself. Someone who already works at another Zenoeats restaurant signs in with the password they have."
3. **"Team" panel:**
   - **Issued reset card** (after a reset): "New temporary password for **{email}**. Give it to them yourself; it is shown once and cannot be looked up again. They choose their own the next time they sign in." Big password (select-all), the sign-in URL, and a "done" link to dismiss.
   - **Table** columns Person · Role · Status · actions:
     - **Person:** name (or email), with the email below when a name exists.
     - **Role:** inline select that saves on change. ⚠ Plain text instead for your own row and for the **only active admin**.
     - **Status:** red "waiting to accept" or "active".
     - **Actions:**
       - Your row shows "you".
       - "reset password" (⚠ not offered for ADMIN rows; admin passwords go through Zenoeats support).
       - "remove" / "cancel invitation".
       - ⚠ The only active admin shows "only admin" instead of remove.
   - **Inline confirm row** (replaces the row, red tint):
     - Reset: "Reset {name}'s password? Their current password stops working and they are signed out on every device. You'll get a temporary password to pass on."
     - Cancel invite: "Cancel {name}'s invitation? The invitation stops working."
     - Remove: "Remove {name} from the team? They lose access to this restaurant straight away."
     - Primary button "Reset password" / "Cancel invitation" / "Remove" ("Working…") and a "keep" link.
   - States: loading, "Just you so far.", error.

#### B9 · Reports, route `/manage/reports` (Admin, Manager)
**Data:** `GET /restaurant/reports?from=&to=`, polled every 60 s. ⚠ Figures are for **a range of the restaurant's own days, in its timezone**, and an order counts on the day it was **paid** there.

1. **Range picker** (a row of pills): Today · Yesterday · Last 7 days · This month · **Choose dates**, the last revealing From and To date fields and a "Show" button.
   - ⚠ The presets are counted from the restaurant's today, which the report itself states — never from the browser's clock, which may be in another timezone.
   - Under it, a line naming the range and whose days they are: "Today, Mon 14 Sep · days in America/Chicago" or "Wed, 9 Sept to Tue, 15 Sept · days in America/Chicago".
   - ⚠ While a new range loads the old figures stay, dimmed, rather than blanking.
2. **Four headline tiles:** Net sales · Paid orders · Average order · Tax collected (tax is net of refunds).
3. **Four smaller tiles** below them: Gross sales · Refunds ("Refunds (1 order)") · Combo discounts · Cancelled orders.
   - ⚠ Net = gross − refunds. Refunds are what has since been given back on those same orders, from the restaurant's own Stripe Dashboard.
4. **"By day" table** (⚠ only when the range is more than one day): Day · Orders · Gross · Refunds ("—" when none) · Net. Empty: "No paid orders in this range."
5. **"Deliveries (N)" panel** (⚠ only when the range has any): a row per driver — Driver · Orders · Delivered · Sales — and a total row "All deliveries". Note: "Part of the sales above, not on top of them. A delivery counts on the day it was paid, like any other order."
6. **"Top items" table:** Item · Units · Revenue. Note: "Leaves out cancelled and fully refunded orders. Revenue is before combo discounts." Empty: "No paid orders in this range."
7. **"Checkouts that did not complete":** two big numbers, "waiting on payment right now" (⚠ now, whatever the range) and "expired unpaid in this range". Explainer: "Expired checkouts were never charged. A steady climb here usually means something is failing at the card step, not that customers changed their minds."

States: loading, error, and a range the restaurant sold nothing in. Completing, delivering or cancelling an order on B5 or B10 refreshes these figures.

#### B10 · Deliveries, route `/manage/deliveries` (Admin, Manager, Driver)
**Purpose:** run the orders the restaurant sends out itself. ⚠ For a **driver this is the entire portal**, on a phone, outdoors, one thumb — design it that way, not as a desktop table.

**Data:** `GET /restaurant/deliveries`, polled **every 5 s**. A driver receives only the orders assigned to them; a manager receives every delivery in play.

**Layout:** two columns from 1024 px (stacked below):
- **"To collect from the kitchen (N)"**: assigned orders not yet picked up.
- **"On the road (N)"**: OUT_FOR_DELIVERY.

**Card:**
- **Header:** "#{order number}" (serif) and the age, "just now" / "{N} min ago", counted from payment. ⚠ Turns red after 30 minutes, not 15: a delivery is expected to take longer than a collection.
- **The address, first and in medium weight** — it is what the driver opened the screen for.
- **The driver's name** underneath, ⚠ only on a manager's view ("No driver assigned" when there is none). A driver does not need to be told who they are.
- Items, as on a board ticket: "{qty}×", name (a combo line reads "{Combo}: {item}"), modifiers " · " joined and muted, item note in red.
- Customer note in red, ruled above, when present.
- Total and "· paid online" — ⚠ the driver collects no money.
- **Action:**
  - Still being made: no button, and the line "Still being made. It can be picked up once the kitchen marks it ready."
  - READY_FOR_DELIVERY: primary **"Picked up"**.
  - OUT_FOR_DELIVERY: primary **"Delivered"**.
  - Both show "Saving…" while busy.

**States:** loading; a driver with nothing: "Nothing assigned to you right now."; a manager with nothing: "No deliveries running. Assign one from a ticket on the Kitchen board."; either column empty ("Nothing waiting." / "Nothing out for delivery."); error.

**Footer note**, different by role:
- Driver: "Only the orders assigned to you appear here. There is no PIN at a doorstep: pressing Delivered is what completes the order, and it is recorded against you."
- Manager: "Customers cannot order a delivery. These are orders a manager assigned to a driver, with the address taken by phone."

**⚠ Rules and edge cases:**
- A driver never sees another driver's order, and asking for one by id answers "not found" — they cannot learn which orders exist.
- A manager can press the same two buttons for a driver whose hands are full.
- There is no PIN, no signature and no photo on delivery: the driver pressing "Delivered" completes the order, recorded against them in the order's history.
- Nothing here shows a map, a route or a phone number: the address is a line of text the restaurant typed. Do not design controls for data that does not exist (flag it in §7 if you think it should).

#### B11 · Settings, route `/manage/settings` (Admin only)
**Purpose:** everything about the restaurant that is not its menu, plus the one thing on the page that is about the person reading it. It replaces a support ticket: until now a typo in a trading name could only be fixed by the platform.

**Data:** `GET /restaurant/profile`, `GET /restaurant/delivery`, `GET /restaurant/me`. Desktop-first — this is a sit-down screen — but it must work on a phone, because a small restaurant's admin is often standing in the kitchen.

⚠ **It is six panels with three different save behaviours.** That inconsistency is deliberate and must survive the redesign; flattening it into one "Save" button would be wrong in three separate ways.

**1 · Your account** (its own component; the API is open to every role, this page is not)
- **Your name.** One field, its own "Save name" button. No password asked for: a name is what colleagues see beside an order, not a credential. Empty is allowed and means "show my email instead".
- **Sign-in address.** Shown as text with a "Change address" button that reveals two fields — the new address and **your current password**. ⚠ The password is the point: a tablet left signed in behind a counter must not be a way to move somebody's login to an address the next person controls. Copy says so.
  - Errors: "That address already has a staff login." (409) — ⚠ identical whether it belongs to a colleague or to someone at a restaurant this caller cannot see, deliberately, so it cannot be used to discover who works where.
- **Password.** Not a form: a line saying a change signs you out everywhere, and a link to **B2 mode 2**.

**2 · The restaurant**
- Restaurant name; Tagline (optional); **Taking orders** checkbox with "Orders already paid for are unaffected."

**3 · Where you are**
- Street, second line, city, state, postal code, country (2 letters, forced uppercase), then Timezone.
- Explainer: this is where customers collect **and** the address sales tax is worked out for, so it is the trading address rather than a head office.
- ⚠ Editing any address field silently invalidates the delivery origin (panel 4). The redesign should make that consequence visible *before* saving, not only after.

**4 · Delivery** (see the ⚠ rules below — this is the panel with real failure modes)
- **The switch:** "Offer delivery at checkout". ⚠ Disabled until the restaurant is placed and has at least one ring; underneath it, a red list of exactly what is missing. ⚠ When it is on but something broke since, a red line: "Delivery is switched on but nothing can be quoted, so customers are being offered collection only until this is fixed."
- **Where distances are measured from:** the pickup address, then either "Placed at 41.92270, -87.64310" or a red box. Button reads **Place on the map** / **Place again**.
  - ⚠ Four distinct states, four different messages: never placed; placed but the address has changed since ("delivery is paused until it is placed again — distances from the old address would have charged the wrong fee"); no address lookup configured on the deployment at all, where the button is replaced by a sentence because pressing it could not work; and the lookup refusing — "Address lookup is not working at the moment. This one is ours to fix rather than yours — tell us if it keeps happening. You can still set up your rings in the meantime."
  - ⚠ That last message is deliberately vague and deliberately not the restaurant's fault. The real causes — an unenabled API, a key restricted the wrong way, billing switched off — belong to whoever runs the platform and reach the log, not the screen. Do not design a more "helpful" error that guesses at them.
- **Rings:** a row each — a band label the screen computes ("0–1 mi", "1–3 mi"), "up to [N] miles", "[fee]", currency, and "remove". Plus "Add a ring" and "Save rings".
  - ⚠ The restaurant types **only the outer edge**. The inner edge is the previous ring's outer one, so a gap is impossible. Do not design a from/to pair.
  - ⚠ Past the furthest ring is **no delivery**, not free delivery. Empty state: "No rings yet, so nothing can be delivered."
  - Refusals: a ring with no fee ("Every ring needs a distance and a fee. Type 0 as the fee for free delivery."); two rings ending at the same distance (422); removing the last ring while delivery is on (409, "Switch delivery off before removing every ring."); at most 8.
  - ⚠ An empty fee is **refused, not read as zero**. It used to be taken as free delivery across that whole band and saved without comment. Free has to be asked for by typing 0.
  - ⚠ Unsaved rings survive anything else on this panel being saved — the switch, the fee-tax checkbox, placing the restaurant. They used to be discarded when fresh settings arrived, which threw away rings someone was halfway through typing.
- **Fee tax:** under a flat rate, a checkbox "Charge tax on the delivery fee" with "Some states tax delivery and some do not. If you are unsure, ask whoever files your sales tax — this changes what customers are charged." ⚠ Under **Stripe Tax there is no checkbox at all**, replaced by a sentence saying Stripe decides per jurisdiction. Do not draw a disabled checkbox there; there is nothing for it to mean.

**5 · Tax**
- Radio: **One flat rate** (reveals a percentage field, max 30) or **Work it out per order, through Stripe** (reveals the product tax code).
- ⚠ The Stripe option is **disabled** until the connected account is taking payments, with the reason inline. Switching it on without a full address is refused by the API (409) with the missing parts named.

**6 · Set by Zenoeats** — a read-only list: web address, status, currency, each with one line on why it is not editable here. ⚠ Shown rather than omitted, because "you cannot change this here" is more use than leaving someone hunting for it.

**Save behaviour**
- Panels 2, 3 and 5 share **one sticky save bar** at the bottom: "Save changes", a "Discard" link, and "Nothing to save." when clean. ⚠ It sends **only the fields that changed**, so two admins editing different things do not overwrite each other.
- Panel 1 has two small saves of its own. Panel 4 saves on the spot: the switch, the rings' "Save rings", the fee-tax checkbox.

**States:** loading; load failure; per-panel errors; "Saved." markers that clear as soon as anything is edited again.

---

### SURFACE C: PLATFORM SUPER-ADMIN PORTAL (`admin.zenoeats.com/admin`)

#### C0 · Admin shell
- Wordmark "ZenoEats" (links to `/admin`); a single tab "Restaurants"; "Sign out" button (→ C1).
- Guard states G1, G2, and redirect to C1 on 401. Content max 1024 px.

#### C1 · Admin sign-in, `/admin/login` (standalone HTML page)
- Heading "Zenoeats platform"; subtext "Super administrator sign-in."
- Email, Password, error box, "Sign in" ("Signing in…").
- Footer: "Administrators are configured in the deployment environment. Contact whoever holds it if you need access."
- Same validation and behaviour as B1. Default `next` is `/admin`.

#### C2 · Restaurants dashboard, route `/admin`
**Data:** restaurant list (optionally including deleted); platform reports (polled every 30 s).
1. Error banner.
2. **Three stat tiles:**
   - **Live restaurants:** count of ACTIVE.
   - **Paid orders:** total.
   - **Gross volume:** ⚠ summed **per currency** and shown as "$1,234.00 · €560.00 · ₹…", sorted by amount. Never add different currencies together.
3. **"Restaurants" panel**, with header actions:
   - Checkbox **"Show deleted"** (re-queries).
   - Button **"Add restaurant"** / "Cancel".
4. **Issued credential panel** (after creating or resetting an owner login):
   - **New password:** "Temporary password issued" (+ " (invitation still to accept)" when INVITED), "Give these to the owner now. The password is not stored and cannot be shown again — only reissued. They must replace it at first sign-in before the portal will do anything else.", Email and **Password** rows, and the button "I have saved it".
   - **Existing login:** "Owner invited", "{email} already has a Zenoeats staff login, so no password was issued and theirs is unchanged. They've been emailed an invitation to own {Restaurant}: they sign in to its portal with their existing password and accept it.", and the button "Done".
5. **Create restaurant form:**
   - Restaurant name (autofocus, required).
   - Subdomain (placeholder "spicehouse"), with ⚠ live validation "Lowercase letters, numbers and hyphens; must start with a letter or number."
   - Tax rate % (default 8.25).
   - "Create as draft" (⚠ disabled until name and valid slug; "Creating…"), "Cancel".
   - Note "Created restaurants stay in draft until Stripe is connected and you activate them."
6. **Owner login form** (opened per row):
   - "Owner login for {Restaurant}" and explainer: "Makes this person the restaurant's owner (ADMIN). A new address gets a temporary password they replace at first sign-in. Someone who already runs another Zenoeats restaurant keeps their own password: they're emailed an invitation and accept it after signing in here. Use Reset password for an owner who has forgotten theirs."
   - Owner email (required), Full name (optional).
   - "Create login" ("Working…"), "Reset password" (same email), "Cancel".
7. **Restaurants table**, columns Restaurant · Status · Stripe · Actions:
   - **Restaurant:** name (+ red "deleted" tag); the slug as an external link to the storefront (new tab).
   - **Status:** StatusPill.
   - **Stripe:** "Not connected" (muted) / "Charges enabled" / red "Onboarding incomplete".
     - After "Refresh Stripe" when charges are not enabled: "Stripe: {disabled reason}" and "Needs: {requirements…}" (person prefixes stripped).
     - When enabled: "Synced from Stripe."
   - **Actions (not deleted):**
     - "Connect Stripe" / "Resume Stripe" (when charges are not enabled). ⚠ **Leaves the app for Stripe-hosted onboarding.**
     - "Refresh Stripe" (when an account exists).
     - "Activate" (primary, when not ACTIVE) or "Suspend".
     - "Edit" / "Close".
     - "Owner login".
     - "Orders" (→ C3).
     - Red "Delete" ⚠ only when not ACTIVE; browser confirm "Delete {name}? It can be restored, and its subdomain stays reserved."
   - **Actions (deleted):**
     - "Restore" (primary).
     - Red **"Delete for good"**, two-step inline: "Erase {name} and its menu? This cannot be undone." with "Erase" and "keep it". ⚠ The server refuses any restaurant that has orders or payments.
   - Deleted rows are shown at 50% opacity. Row buttons are disabled while that row is busy.
   - The server may refuse **Activate** (readiness gate), and the error shows in the banner.
8. **Inline edit form** (a full-width row under the restaurant):
   - Name, Tagline (max 200, placeholder "none").
   - **Sales tax** fieldset: "How tax is calculated" select (Flat rate / Stripe Tax).
     - Flat: "Tax rate %".
     - Stripe Tax: "Stripe product tax code" (monospace, pattern `txcd_########`, default `txcd_40060003`) and the explainer "Tax is calculated on this restaurant's Stripe account for the pickup address below. The restaurant must finish Stripe's tax settings and add a registration for its state, or orders are charged no tax. txcd_40060003 is food for immediate consumption."
   - **Pickup address** fieldset (legend gains " (required for Stripe Tax)"):
     - Street address (wide).
     - Suite, unit (optional) (wide).
     - City, State, ZIP / postal code.
     - Country (2 letters, default US, uppercased).
   - Checkbox **Accepting orders**.
   - Read-only note "Subdomain `{slug}` and status `{status}` are not editable here."
   - "Save changes" ("Saving…"), "Cancel". ⚠ Only changed fields are sent. Saving with no changes just closes.
9. Table empty and loading: "No restaurants yet. Add one to get started." / "Loading…".
10. **"Reports" panel:**
    - Header button **"Download CSV"** (direct file download).
    - Table: Restaurant · Paid · Gross · Tax · AOV · Unpaid · Expired, horizontally scrollable.
    - Empty: "No order data yet."
11. Footer: "Every read on this page is written to the platform audit log with your user, the scope requested, and a correlation id."

#### C3 · Restaurant orders, route `/admin/restaurants/:id/orders`
1. Heading: the restaurant **slug** ("…" while loading).
2. **Status filter** select: All, PENDING_PAYMENT, AUTO_ACCEPTED, PREPARING, READY_FOR_PICKUP, COMPLETED, CANCELLED, EXPIRED. ⚠ Changing it resets to page 1.
3. **Table** (horizontal scroll): Order (#n) · Status · Payment (status or muted "none", plus the Stripe PaymentIntent id beneath) · Tax · Total · Placed (local date-time).
4. **Pagination** (50 per page): "{from}–{to} of {total}", "Previous" (disabled on the first page), "Next" (disabled on the last).
5. Footer: "Customer notes and pickup PINs are not shown here. The platform database role has no permission to read them." ⚠ Privacy by design: do not add customer-note or PIN columns.
6. States: loading, "No orders yet." / "No orders with status {STATUS} yet.", error.

---

### SURFACE E: OUT-OF-APP TOUCHPOINTS (keep visually consistent; optional to redesign; see §7)
- **E1 Order confirmation email:** subject "Order #{n} confirmed at {Restaurant}". Greeting, "{Restaurant} has your order and is making it now.", item table, Subtotal / Discount / Tax / Total, "Your pickup PIN is on your order page", and a red **"View your order"** button linking to A11. Styled with the same paper, white card, Georgia heading and hairline palette. ⚠ For a **guest** that button carries the signed `?t=` token (7 days) and is their only way back to the pickup PIN from another device. ⚠ The item table lists a meal deal's components as separate lines, unlike A11, which now groups them — see §7.
- **E2 Staff invitation email:** subject "You're invited to join {Restaurant} on Zenoeats". Body gives the role, password instructions (temporary vs existing), a red button "Sign in to {Restaurant}", and a disclaimer.
- **E3 Stripe-hosted Connect onboarding** (external, not themeable here) and **E4 Stripe Payment Element / 3-D Secure** (iframe; only appearance variables can be themed).

---

## 3. Core User Flows

### F1 · Customer: browse → customise → identify (account **or** guest) → pay → collect
1. **A1 Storefront** (QR or link).
   - *Branch:* restaurant closed → rows faded and not clickable; cart bar disabled; banner.
   - *Branch:* invalid or inactive subdomain → "This menu isn't available".
2. Tap an item → **A2** → pick options (required groups block Add), set quantity and note → **Add**. Or tap a combo → **A3** → fill every slot and its required options → **Add**.
3. **A4 cart bar** appears → "Review order".
4. **Guard.** The API answers who is ordering (`GET /orders/session`).
   - Already signed in, or already holding a guest cookie → A9. (Either may have been established back on **A1's account row**, before there was a cart.)
   - Nobody yet → **A5 Sign in** with `next=/checkout`. **Two ways forward, and both end at A9:**
     - **With an account:**
       - Email and password → (optional **2-step code**, with switch-method links) → back to A9.
       - Social button → provider → **A8 callback**:
         - → back to A9, or
         - → **A6 continue step** (missing email, name or terms) → **A6 code step**, or
         - → **A5 code step** (2FA), or
         - → A5 with "That sign-in didn't complete."
       - "Create an account" → **A6** form → code → back to A9.
       - "Forgot password?" → **A7** email → code + new password → signed in → back to A9.
     - **Without one:** the **guest panel** → email (+ optional name) → **"Continue as guest"** → back to A9 holding a guest cookie. ⚠ Nothing is verified. ⚠ The order, its receipt and its pickup PIN now hang off this browser and the confirmation email, and nothing else.
   - Clerk not configured or unreachable → the account form fails with its own message; ⚠ **the guest panel still works and is the way through**. Guard failures that are not a 401 → A12 "Can't reach sign-in".
   - ⚠ Either way the return to A9 is a **full page load**: the cart comes back from localStorage, not from memory.
5. **A9 Checkout:** review lines (adjust quantity; − at 1 removes), wait for the server quote, add a note, "Not you? Sign out" if needed.
   - *Branch:* empty cart → "Your cart is empty" → back to A1.
   - *Branch:* quote error → red text, button stays disabled.
6. **Continue to payment.** The order is created (pending), then the payment intent.
   - *Errors:* PRICE_CHANGED → "Prices changed…"; ITEM_UNAVAILABLE → "Something in your cart just sold out…"; other errors → message. The customer stays on A9 and retries with the same idempotency key.
7. **A10 Payment:** Stripe Payment Element → **Pay**.
   - *Branch:* card declined → Stripe message, retry.
   - *Branch:* 3-D Secure → Stripe challenge → returns to A11.
   - *Branch:* reload → intent recovered; order no longer pending → redirected to A11.
   - *Branch:* "Back to your order" → A9 (same pending order is reused if the cart is unchanged).
8. **A11 Tracking:** "Confirming your payment" (pulsing, 2 s polling) → "Order confirmed" / "Being made now" (PIN card appears) → "Ready to collect" → customer shows the PIN at the counter → "Collected".
   - *Branches:* EXPIRED (never paid, nothing charged); CANCELLED (by a manager on B5). Email **E1** is sent on payment.
   - *Guest branch:* their **E1** carries a `?t=` link that reopens A11 on any device, for 7 days. ⚠ A guest who loses this browser **and** this email has lost the order — there is no "send it to me again" — which is exactly what A5's guest panel warns about before they choose it.

### F2 · Kitchen and counter: ticket → ready → hand over
1. **B1 sign-in** (on the restaurant subdomain).
   - *Branch:* temporary password → **B2 mode 1** → new password → B1 again.
   - *Branch:* invited membership → **B3 Accept invitation** (or sign out).
2. **B5 board:** a paid order appears in "Making now" within about 5 s. The age turns red after 15 min.
3. **Mark ready for pickup** → the ticket moves to "Waiting for collection". The customer's A11 changes to "Ready to collect".
4. Customer arrives → **Collect with PIN** → type 6 digits → **Hand over** → ticket leaves the board; B9 figures update.
   - *Branch:* wrong PIN → error; retry.
   - *Branch:* 5 wrong PINs → locked. Non-managers see "Ask a manager". A manager clicks **Hand over without PIN** → enters a reason (≥ 3 chars) → confirm → notice.
   - *Branch:* customer lost the PIN (not locked) → manager uses "no PIN? hand over without it" → reason → confirm.
   - *Branch:* order never collected or problem → manager uses **cancel order** → reason → confirm → notice says whether a Stripe Dashboard refund is still needed. The customer's A11 shows "Cancelled".
   - *Branch:* a refund was made in Stripe → the ticket shows "refunded" / "partly refunded".
   - *Branch:* the order is going out with a driver → **F7**.
5. Whatever happened, the order appears under **"Done today"** with who did it and any reason, searchable by number when the customer rings.

### F3 · Manager: build the menu from nothing
1. **B7 Menu** → **Items** tab → **add a type** (e.g. Food, Drinks; optionally "Burgers" inside Food) → Done.
2. **Modifier library** → create groups (e.g. "Veggies", Pick several, max 3, shows on Food; options with photos and price changes; Enter adds a row) → Create group.
3. **Items** → **add an item**: name, type, price, description, photo, served-during periods (may be none yet), modifier groups and "comes with" options → Add item.
   - *Validation branches:* missing name, type or price; unreadable price; upload in progress blocks save.
4. **Meal periods** → add "Breakfast", "Lunch" → pencil to set hours (both or neither; end ≤ start means next day) → **add items** (multi-tick) → Add.
   - Items on every period fold into "served all day".
   - *Branch:* delete period → inline confirm (items are kept).
5. **Combos** → add a combo: name, sold-during period (locked later), discount, tick items across at least 2 types → Create.
   - *Validation branches* as listed in B7d.
6. **Preview** → check the category-first menu and the "By meal period" block → open the storefront (A1).
7. **Ongoing edits:** pencil on Items → bulk edit prices and types, stage deletions → Save (or cancel = nothing changes). Types editor: rename, nest, stage delete → server may refuse (items still attached) → the editor stays open with the message.

### F4 · Any staff: sold out mid-rush
**B6 Stock** → search "falafel" → **Mark sold out**. The row moves to "Sold out" and the storefront (A1) shows "Sold out" and disables the row on its next read. Later → **Back in stock**. (Managers can also toggle from the B7b list or B7c served rows.)
- *Branch:* a customer already has it in the cart → A9 shows "Something in your cart just sold out".

### F5 · Restaurant admin: grow and manage the team
1. **B8 Staff** → email + role (help text updates) → **Invite**.
   - *New person:* the temporary password is shown once → the admin passes it on; E2 email is sent.
   - *Existing Zenoeats staff login:* no password; they use their own.
2. The invitee opens E2 → **B1** → (**B2** if temporary) → **B3 Accept invitation** → portal with role-filtered tabs.
3. Later:
   - Change a role via the inline select (not offered on yourself or the only admin).
   - **reset password** → inline confirm → temporary password card → pass it on (not offered for ADMIN rows).
   - **remove** / **cancel invitation** → inline confirm.
   - *Guard:* the last active admin cannot be demoted or removed ("only admin").
4. Any staff member can change their own password via B0 → **B2 mode 2** → signed out everywhere → B1. A lost phone is handled by **"Sign out all devices"** in the header, which ends every session without changing the password.
5. Inviting a **driver** is the same flow; they land on B10 rather than the board.
6. Anyone can fix their own name, and change the address they sign in with, in **B11 → Your account** — though only an admin can currently reach that page.

### F6 · Admin: set up a delivery area
1. **B11 → Where you are** → check the pickup address is the real trading address → Save.
2. **B11 → Delivery** → **Place on the map** → the address is geocoded and the coordinates shown.
   - *Branch:* the address cannot be found → refused, invited to check it.
   - *Branch:* no API key on the deployment, or the provider refuses → "Address lookup is not working at the moment. This one is ours to fix rather than yours." ⚠ Rings can still be set up meanwhile.
3. **Add a ring** → "up to 3 miles, $4" → repeat → **Save rings**.
4. **Offer delivery at checkout** → now allowed, because both halves exist.
   - *Branch:* the address is edited later → the coordinates are dropped, the panel turns red, and customers are quietly offered collection only until it is placed again. ⚠ No delivery beats a wrong fee.

### F7 · Manager and driver: an order the restaurant runs out
1. A customer rings and asks for the order to be delivered. Staff take the address. ⚠ There is still no delivery option at checkout: the customer ordered and paid as a collection, and is charged nothing for the journey — even where the restaurant has set up a delivery area in B11. See §7.2.
2. **B5 board** → the manager opens **assign driver** on the ticket → picks a driver, types the address → **Assign** → the ticket gains a "delivery" badge, the address and the driver's name; the notice reads "#{n} is {driver}'s delivery."
3. The kitchen's button on that ticket now reads **"Mark ready for the driver"** → the ticket's status line becomes "Waiting for {driver} to pick it up."
4. **B10** (the driver's whole portal): the order is in "To collect from the kitchen" → **Picked up** → it moves to "On the road" → the customer's A11 reads "On its way" → **Delivered** → the order completes and the screen empties.
5. The order lands in **"Done today"** on B5, credited to the driver, and in **B9** under Deliveries, counted per driver.
   - *Branch:* wrong driver → the manager uses "change driver" (the same form) → the order moves to the other driver's B10.
   - *Branch:* the customer will collect after all → "back to collection" → the driver and address are cleared, and the ticket returns to the PIN flow. ⚠ Refused once the driver has it: cancel it, or let them deliver it.
   - *Branch:* nobody is there → the manager cancels with a reason; the refund is still theirs to issue in Stripe.

### F8 · Platform operator: onboard a restaurant
1. **C1 sign-in** → **C2**.
2. **Add restaurant** → name, subdomain (validated), tax % → **Create as draft**.
3. **Edit** → tagline, tax mode (Flat or Stripe Tax + tax code), pickup address, accepting orders → Save (only changes are sent; server errors show in the banner).
4. **Connect Stripe** → leaves the app → Stripe-hosted onboarding → returns to C2 → **Refresh Stripe** → see "Charges enabled" or "Needs: …" → **Resume Stripe** if incomplete.
5. **Owner login** → email and name → **Create login** → issued credential panel (temporary password, or "Owner invited") → "I have saved it". *Branch:* forgotten owner password → **Reset password** in the same form.
6. **Activate** (the server may refuse if not ready) → status ACTIVE → the slug link opens the live storefront.
7. **Monitor:** stat tiles, reports table, **Download CSV**, **Orders** → C3 (filter by status, paginate).
8. **Offboard:**
   - **Suspend** (ACTIVE → SUSPENDED).
   - **Delete** (only when not ACTIVE; browser confirm) → "Show deleted" → **Restore** or **Delete for good** (two-step; refused if it has orders or payments).

---

## 4. Current Design System

### 4.1 Overall character
A quiet, editorial, paper-and-ink look: a warm off-white page, white surfaces, hairline borders, a single brick-red accent, Georgia serif for display, system sans for everything else. There are almost no shadows, very little radius, no gradients, no illustrations and no dark mode. The operator portals are deliberately "plainer… a tool people use all shift, not a shopfront." Secondary actions are overwhelmingly **lowercase underlined text links**.

### 4.2 Colour tokens (`tailwind.config.ts`)
| Token | Hex | Use |
|---|---|---|
| `paper` | `#F5F3EE` | Page background; nested/expanded panels; hover on quiet buttons |
| `surface` | `#FFFFFF` | Cards, headers, sheets, inputs |
| `ink` | `#1A1A17` | Body text; active tab pill; "on" chips; editor borders (`border-ink` marks edit mode) |
| `muted` | `#6E6A61` | Secondary text, captions, labels |
| `hairline` | `#E2DED4` | All borders and dividers |
| `brick` | `#B3341F` | Primary buttons, errors, warnings, sold-out, savings, focus ring, accent-colour for radios and checkboxes, Stripe primary |
| `brickDark` | `#8E2818` | Primary button hover |

Derived tints in use: `brick/5` (error and PIN backgrounds), `brick/10` (suspended pill, refund tag), `brick/30` (PIN and credential card borders), `ink/40` (modal backdrop), `ink/70` (subsection labels), `surface/95` + backdrop-blur (cart bar), `surface/80` (upload overlay).
Third-party brand colours: Apple button `#000` (hover 85%), Facebook `#1877F2` (hover `#166FE5`), Google's four-colour logo.
⚠ Brick red currently means both "primary action" and "error or warning". A redesign should separate these semantics while keeping the brand accent.

### 4.3 Typography
- **Display:** `var(--font-display)` = Georgia, "Times New Roman", serif. Used for h1 page titles, restaurant name, sheet titles, meal-period and combo names, stat values, ticket numbers, PIN and temporary-password displays.
- **Body:** `var(--font-body)` = system-ui, -apple-system, Segoe UI, Roboto, Helvetica Neue, Arial, sans-serif.
- ⚠ No webfont is loaded. Swapping fonts means reassigning these two CSS variables. CSP only allows **self-hosted** fonts (§5).
- **Sizes in use:** 10 px (upload overlay), 11 px, `text-xs` 12, 13 px (subsection labels), `text-sm` 14, 15 px (menu row names and prices), `text-base` 16, `text-lg` 18, `text-xl` 20, `text-2xl` 24, `text-3xl` 30, `text-4xl` 36 (storefront name, PIN).
- **Weights:** normal and `font-medium` only.
- **Tracking:** `uppercase tracking-wide` for section labels; `tracking-[0.2em]` PIN; `tracking-[0.3em]` code and PIN inputs; `tracking-wider` temporary passwords.
- **Numerals:** `.tnum` (tabular) on all prices, quantities and figures.

### 4.4 Spacing, layout, radius, elevation
- Tailwind default 4 px spacing scale. Common: `px-5` page gutter, `py-24` centred state pages, `px-4 py-3` rows, `gap-2/3/4`, `mb-8/10` panels.
- **Containers:**
  - `max-w-sm` 384 px: all auth pages, B3.
  - `max-w-lg` 512 px: checkout, payment, order tracking, state pages, sheets.
  - `max-w-3xl` 768 px: storefront.
  - `max-w-5xl` 1024 px: both portals.
  - `max-w-prose` / `max-w-xs` for descriptions and photos.
- **Breakpoints:** Tailwind defaults. `sm` 640 px (sheet becomes a centred dialog, grids go to multi-column, portal identity text appears) and `lg` 1024 px (kitchen board goes to 2 columns) are the ones used.
- **Radius:** `rounded` 4 px (chips, pills, thumbnails), `rounded-md` 6 px (buttons, inputs, photos, PIN card), `rounded-xl` 12 px (sheets; `rounded-t-xl` on mobile), `rounded-full` (dots, remove badges).
- **Elevation:** none (no box-shadows). Depth comes only from paper vs surface and hairline borders. Modals use a 40% ink backdrop.
- **Accent rules:** `border-l-2` coloured left rule for error, notice and helper blocks; dashed borders for empty states and subcategory chips.

### 4.5 Components that exist today (all custom; no component library)
| Component | Description |
|---|---|
| `.btn` | Inline-flex, `px-4 py-2.5`, 14 px medium, `rounded-md`, colour transition, 2 px brick focus-visible outline, 40% opacity when disabled |
| `.btn-primary` | Brick fill, white text, brickDark hover |
| `.btn-quiet` | White with hairline border, paper hover |
| `.field` | Full-width input/select: hairline border, surface, `px-3 py-2`, 14 px, brick border on focus |
| Text-link action | `text-xs underline text-muted` (or `text-brick` for destructive) |
| `Panel` | Section: small medium title (optionally with a pencil) + right-aligned action slot |
| `Empty` | Dashed-border centred muted message |
| `ErrorNote` | Brick left rule, brick/5 background, brick text |
| Notice | Ink left rule, paper background |
| `Stat` | Label + serif figure. Two variants (admin: 14 px label / 30 px value; restaurant: 12 px label / 24 px value, tabular) laid in a 1 px hairline grid |
| `StatusPill` | Restaurant status badge (4 variants) |
| `Chip` | Toggle button with `aria-pressed`; dark when on. Variants: nested (└, dashed), with count, with sub-label/price |
| Tabs | (a) Shell nav: rounded pills, active = ink fill. (b) Menu builder: underline, active = 2 px brick bottom border |
| Sheet / dialog | Bottom sheet → centred dialog, sticky header and footer, scroll body |
| Quantity stepper | Bordered − / number / + group |
| Ticket card | Bordered surface article |
| Tables | Hairline-ruled rows, `text-xs` muted headers, right-aligned tabular figures |
| One-time secret card | Brick/30 border, brick/5 background, large serif select-all value |
| Inline confirm | Replaces a row or card with a sentence + primary confirm + "keep it" / "keep" / "back" link |
| `ImagePicker` | See B7 |
| `MenuImage` | Lazy image that disappears on error |
| Icons | `PencilIcon`, `PhotoIcon` (inline SVG, `currentColor`, em-sized). Everything else is text glyphs: ✕ × − + └ · ⋯ "······" |
| Social buttons | Google (quiet + logo), Apple (black), Facebook (blue) |

### 4.6 Motion today
- `transition-colors` on `.btn` only.
- `animate-pulse` on the "waiting for Stripe" dot (A11).
- `backdrop-blur` on the cart bar.
- **No** entrance, exit, page or state animations, no skeletons, and no animation library.
- Global `prefers-reduced-motion: reduce` sets animation and transition durations to 0.01 ms.

### 4.7 Brand assets
- **None in the repo:** no logo file, no favicon (`web/public` holds only `config.js`), no illustrations, no icon set. `images/restaurants/` holds only uploaded menu photos (runtime data).
- The wordmark is plain text: "ZenoEats" in the admin header, the restaurant name in the staff header, "Zenoeats" in copy.
- The Stripe Payment Element is themed `flat`, primary `#B3341F`, font system-ui.
- Emails reuse the palette (`#F5F3EE` background, white card, `#E2DED4` border, radius 8, Georgia 24 px heading, `#B3341F` button, `#6E6A61` muted).
- Tab titles: "Order pickup" (app), "Zenoeats platform sign-in", "Restaurant sign-in", "Choose a password", "Sign in to order", "Create an account", "Reset your password", "Signing you in…".

### 4.8 Voice
Plain, precise, reassuring, sentence case, explains consequences ("Cancel and nothing is removed."). It never blames the user, names the exact field that is wrong, and gives an example value ("like 10.95"). Keep this voice.

---

## 5. Technical Constraints

### 5.1 Stack
| Concern | Current |
|---|---|
| Framework | **React 18.3** (StrictMode), client-rendered SPA. Not Next.js (migrated off it) |
| Routing | **react-router-dom 7** (`BrowserRouter`, declarative `<Routes>`) |
| State | **Redux Toolkit 2.5** + **RTK Query** (server cache with tag invalidation and polling); react-redux 9. Slices: `cart` (localStorage-persisted per restaurant), `session` |
| Build | **Vite 6**, multi-page: 1 SPA entry + **7 standalone auth HTML entries** |
| Language | TypeScript 5.7 (strict) |
| Styling | **Tailwind CSS 3.4** + PostCSS/autoprefixer; one `globals.css` with `@layer` component classes. No CSS-in-JS, no SCSS |
| Component library | **None**. All components are custom |
| Animation libraries | **None** |
| Icon library | **None** (2 inline SVGs) |
| Payments UI | `@stripe/react-stripe-js` 3 + `@stripe/stripe-js` 5 (Payment Element, Stripe Connect direct charges) |
| Customer auth | **Clerk JS v6** loaded at runtime from Clerk's CDN (not bundled), used headlessly: all auth UI is custom markup. **Plus guest checkout:** a signed httpOnly cookie (`zenoeats_guest_session`, 30 days) minted by the API, with no Clerk involved at all. ⚠ The menu avoids loading Clerk by reading Clerk's own `__client_uat` cookie as a hint |
| Staff and admin auth | httpOnly session cookies, platform-issued passwords |
| Serving | nginx container (static) behind an edge nginx; FastAPI backend at `/api/v1` |

### 5.2 Platform and targets
- **Responsive web app only.** Not a PWA (no manifest or service worker), not native. No offline mode beyond the cart surviving reloads and refetch on reconnect.
- **Customer surface (A):** mobile-first (phones reached by QR code), also desktop.
- **Kitchen board and stock (B5, B6):** shared tablets, usually landscape, used all shift, touch, sometimes greasy, often asleep.
- **Deliveries (B10):** ⚠ a driver's phone, held one-handed, outdoors, in daylight and rain. It is the only screen a driver ever opens, so it is phone-first in a way no other staff screen is.
- **Menu builder, staff, reports, settings (B7–B9, B11) and admin (C):** desktop/laptop primary, must still work on tablet and phone. ⚠ B11 is where a small restaurant's owner stands in the kitchen with a phone, so it is the one of these that most needs the narrow layout to be good rather than merely possible.
- Locale: **en-US hard-coded** (number, currency and time formats); currency varies per restaurant. No i18n, no RTL, no dark mode.

### 5.3 Hard constraints a redesign must respect
1. **Auth pages are plain HTML + vanilla TypeScript, outside React** (A5–A8, B1, B2, C1), deliberately so no framework runs on the credential path. Their designs must be buildable as static HTML with Tailwind classes and small DOM scripts: no React components, no heavy runtime. Element IDs are bound by scripts, so each distinct field, button, message box and step section in the inventory must exist.
2. **Content-Security-Policy** (set at container start):
   - `script-src 'self'` + Clerk + Stripe + Cloudflare challenge. ⚠ No third-party CDNs (e.g. three.js or Lottie from a CDN); any library must be bundled.
   - `font-src 'self'`. ⚠ Webfonts must be self-hosted; no Google Fonts.
   - `img-src 'self' data:` + Clerk/Stripe/optional image bucket. ⚠ 3D textures and images must be self-hosted or data URIs.
   - `style-src 'self' 'unsafe-inline'` (inline styles OK).
   - `worker-src 'self' blob:`, `connect-src 'self'` + Clerk/Stripe. ⚠ No fetching remote 3D assets.
   - No inline `<script>`. `frame-ancestors 'none'`.
3. **Performance intent:** the team moved Clerk off the bundle to save ~590 KB on phones at checkout, lazy-loads menu photos, and keeps Clerk out of the portal bundle. ⚠ Any 3D or WebGL must be code-split, loaded only where used, optional, and must never block menu rendering, checkout, or the kitchen board.
4. **Stripe Payment Element** renders in Stripe's iframe. Only its `appearance` API (theme, variables, rules) can be styled. Stripe onboarding (E3) is external.
5. **Clerk bot-protection element** (`#clerk-captcha`) must remain on A6.
6. **One theme for all tenants.** Restaurants have no brand settings (only name, tagline, photos). Per-restaurant theming would be new backend work.
7. **URLs must not change** (QR codes printed on tables, emailed links, bookmarks).
8. **Server-authoritative data:** prices shown before the quote are previews; order status comes only from polling. Designs must not imply instant confirmation.
9. **Real-time is polling, not sockets:** kitchen board 5 s; order tracking 2 s/8 s; stock 30 s; admin reports 30 s; restaurant reports 60 s.
10. **Accessibility already present, which must be kept or improved:** `role="dialog"` + `aria-modal` on sheets, `aria-label` on icon buttons and steppers, `aria-pressed` on toggles, radios and checkboxes scoped per slot, focus-visible outlines, autocomplete attributes (`one-time-code`, `new-password`, `username`), `inputmode="numeric"` on code and PIN fields, reduced-motion support.

**Storefront module amendment:** The new platform-gated Storefront module supersedes
the earlier prohibitions on per-restaurant theming and Google Fonts. Custom
CSS/HTML, arbitrary font URLs and separate sign-in page theming remain excluded.
The super-admin restaurant PATCH accepts `storefront_customization_enabled`.

### 5.4 API endpoints the frontend calls (data contract; do not invent others without flagging)
**Customer (tenant from Host; order calls carry the Clerk token *or* the `zenoeats_guest_session` httpOnly cookie, which the browser sends itself):**
- `GET /portal`: restaurant header, optional gated `storefront` (theme, logo, scheduled banners, category shortcuts and collection item IDs), open/closed (A1, A9, A10, A5 heading)
- `GET /menu`: storefront menu (A1)
- `GET /orders/session`: who is ordering — `{email, full_name, is_guest}`, or **401 for nobody** (A1 account row, A9/A10/A11 guard). ⚠ 401 is the ordinary answer on A1 and is not an error state
- `POST /orders/guest-session`: order without an account — `{email, full_name?}` in, the session cookie back (A5). Refused on an address with no restaurant
- `DELETE /orders/session`: drop the guest cookie ("Start over"). Guests only; a signed-in customer signs out through Clerk (A1, A9)
- `POST /orders/quote`: server totals (A9)
- `POST /orders`: create pending order, idempotent (A9)
- `POST /orders/{id}/payment-intent`: create or return intent (A9, A10)
- `GET /orders/{id}?t={token}`: tracking, polled. `t` is optional — the order-view token from a guest's confirmation email, checked against this order and no other (A10, A11)
- *(exists, unused by UI: `GET /orders`, list of the customer's orders; see §7)*

**Staff (`/restaurant/...`):**
- Session: `POST login`, `POST logout`, `POST logout-everywhere`, `GET me`, `POST change-password`
- Board: `GET orders`, `GET orders/history` (today's finished orders), `POST orders/{id}/ready`, `POST orders/{id}/complete` {pin}, `POST orders/{id}/override-complete` {reason}, `POST orders/{id}/cancel` {reason}
- Deliveries: `GET deliveries`, `GET drivers` (managers, for the assign form), `POST orders/{id}/assign-driver` {membership_id, delivery_address}, `POST orders/{id}/unassign-driver`, `POST orders/{id}/picked-up`, `POST orders/{id}/delivered`
- Stock: `GET stock`, `PATCH items/{id}/availability?is_available=`
- Menu: `GET menu`
- Item types: `GET` / `POST item-types`, `PATCH` / `DELETE item-types/{id}`
- Items: `GET` / `POST items`, `PATCH` / `DELETE items/{id}`
- Photos: `POST images?kind=items|options|banners|categories|branding` (multipart; new kinds require Storefront enabled)
- Meal periods: `POST meals`, `PATCH` / `DELETE meals/{id}`, `POST meals/{id}/items`, `DELETE meals/{id}/items/{itemId}`
- Combos: `GET` / `POST combos`, `PATCH` / `DELETE combos/{id}`
- Modifiers: `GET` / `POST modifier-groups`, `PATCH` / `DELETE modifier-groups/{id}`, `POST modifier-groups/{id}/options`, `PATCH` / `DELETE modifier-options/{id}`
- Staff: `GET` / `POST staff`, `POST staff/accept`, `PATCH staff/{id}` {role_code}, `POST staff/{id}/reset-password`, `DELETE staff/{id}`
- Storefront (Admin/Manager, platform gate required): `GET storefront`, `PATCH storefront/theme` {theme?, logo_path?, banner_interval_ms?}, `PUT storefront/banners` {banners}, `PATCH item-types/{id}/storefront` {image_path?, show_in_shortcuts?}, `PUT storefront/collections` {collections}. PUT replaces the ordered set. `GET me` exposes `storefront_customization_enabled`.
- Reports: `GET reports?from=&to=` (restaurant-local dates, both inclusive; defaults to today there)
- The restaurant itself: `GET` / `PATCH profile`
- Delivery area: `GET delivery`, `PATCH delivery` {delivery_enabled?, delivery_fee_taxable?}, `POST delivery/locate`, `PUT delivery/zones` {zones: [{max_miles, fee_minor}]}
- Your own account: `PATCH me` {full_name}, `POST change-email` {email, current_password}

**Admin (`/admin/...`):**
- Session: `POST login`, `POST logout`, `GET me`
- Restaurants: `GET restaurants?include_deleted=`, `POST restaurants`, `PATCH` / `DELETE restaurants/{id}`, `DELETE restaurants/{id}/permanent`, `POST restaurants/{id}/restore`, `POST restaurants/{id}/activate`, `POST restaurants/{id}/suspend`
- Stripe: `POST restaurants/{id}/stripe-onboarding`, `POST restaurants/{id}/stripe-refresh`
- Owner: `POST restaurants/{id}/owner`, `POST restaurants/{id}/owner/reset-password`
- Reports and orders: `GET reports`, `GET reports.csv` (download link), `GET restaurants/{id}/orders?status=&limit=50&offset=`

---

## 6. Redesign Brief for Astra

**To Astra, the design AI:**

**Redesign the following application end-to-end with a modern, professional visual language and purposeful 3D animation and motion design (for example depth through layered surfaces, subtle parallax, animated transitions between states, and 3D iconography or hero elements where appropriate). Motion must not be decoration for its own sake.**

**Every screen and every interactive element listed in the Screen Inventory above must appear in the redesign. Do not simplify away any listed functionality, state, or edge case.**

### 6.1 What to deliver
- **Format:** high-fidelity **design mockups plus a clickable interactive prototype**, not production code. Engineers will implement it in the existing React + Tailwind stack, so express every visual decision as **design tokens** (colour, type, spacing, radius, elevation, motion) and **component specs** that map cleanly onto Tailwind utilities and CSS variables.
- **Breakpoints to show for every screen:**
  - Phone **390 px**.
  - Tablet **1024 px** landscape (mandatory for B5 and B6).
  - Desktop **1440 px**.
  - Customer screens are phone-first; operator screens are desktop/tablet-first but must be shown on phone.
- **Global artefacts:**
  1. **Token sheet:** colours with semantic roles (separate *primary action* from *error* from *warning* from *success*; today brick red means all of them), typography (self-hostable fonts only), spacing, radius, elevation and depth layers, motion durations and easings, and a reduced-motion alternative for every animated token.
  2. **Component library:** every component in §4.5, redesigned, with all states (default, hover, focus-visible, active, disabled, busy/loading, error, selected/pressed).
  3. **Motion and 3D spec:** what moves, why, duration and easing, and the static fallback.
  4. **Iconography set** replacing the text glyphs (✕ − + └ ×) and the two SVGs, plus any new icons. Provide it as SVG, stylistically compatible with any 3D icon treatment.
  5. **Logo and wordmark proposal:** there is none today. Treat it as a proposal and keep the product name.
  6. **Mobile navigation pattern** for the staff portal (five role-filtered tabs) and the admin portal.

### 6.2 Tone, by surface (one brand system, three registers)
| Surface | Register |
|---|---|
| **A Customer storefront and checkout** | **Premium hospitality / appetite-forward consumer.** Warm, editorial, photo-led, confident. It should feel like a good restaurant's own site, not a marketplace. The restaurant's name, tagline and dish photos carry the identity; Zenoeats stays in the background. Checkout and payment must feel **trustworthy and calm, like premium fintech**. |
| **B Restaurant staff portal** | **Calm, high-contrast operations tool** (think a premium kitchen display system and a modern back-office). Glanceable at arm's length, large touch targets on B5 and B6, dense but orderly in the menu builder. |
| **C Platform admin** | **Restrained enterprise / fintech dashboard.** Precise, data-first, quiet, with careful handling of destructive and money-related actions. |

### 6.3 Brand constraints: keep vs. free to change
**Keep:**
- The product name "Zenoeats" (casing is an open question, §7).
- A warm, food-appropriate neutral foundation.
- The **brick-red family as the brand accent**. It is also used in transactional emails and the Stripe payment form, so if you shift the hue, give exact values for those too.
- The plain, precise, reassuring copy voice (§4.8).
- All routes and URLs.
- Tabular numerals for money.
- Light theme as the default. A dark theme for the kitchen board is welcome as an **optional** addition; label it optional.

**Free to change:**
- Typography (self-hosted only), radius, elevation, iconography, layout and information hierarchy.
- Component styling, empty-state treatments, illustration style (must be self-hosted assets).
- Navigation patterns, the use of drawers versus inline panels (with the exceptions below).

**Do not:**
- Introduce per-restaurant theming, or any feature that needs new backend data. If you want to propose one (e.g. "My orders", date-range reports, restaurant logos), put it in a clearly labelled **"Optional proposals — not in current scope"** appendix, never in the main screens.
- Remove any role-based variant or merge role states.

### 6.4 3D and motion principles (purposeful, performant, accessible)
1. **Motion explains state change.** Use it for the order lifecycle (A11 status progression and PIN reveal), items entering the cart (A2/A3 → A4), sheets rising as layered surfaces over the menu, tickets arriving, moving between board columns and leaving (B5), rows moving between Sold out and In stock (B6), staged deletions striking through and settling (B7), inline confirms expanding in place (B8, C2), and tab changes in the menu builder.
2. **Depth through layering, not clutter.** Suggested surface stack: page → section surface → card → raised sheet or dialog → toast or notice. Use subtle parallax only on the storefront header and hero photos and the auth-page backgrounds.
3. **3D hero elements and 3D iconography only where they add meaning:**
   - Storefront empty and closed states.
   - Order-tracking status illustration (paid → cooking → ready → collected).
   - Auth-page ambience.
   - Admin empty states and the Stripe-connection status.
   - Never on the kitchen board ticket area, the stock list, dense builder forms or data tables.
4. **Kitchen board rule:** it is a shared screen watched all shift, so motion there must be brief, calm and informative (new-ticket arrival, column move, overdue-after-15-min emphasis). No looping or ambient animation.
5. **Performance budget:**
   - Prefer CSS 3D transforms, pre-rendered 3D assets (self-hosted WebP/AVIF/SVG), and lightweight bundled libraries.
   - Any real-time WebGL must be code-split, lazy-loaded, optional, paused off-screen, and absent from the checkout, payment and kitchen routes.
   - The menu and checkout must render and work before any 3D asset loads.
6. **Reduced motion:** every animation needs a specified static or crossfade equivalent under `prefers-reduced-motion`.
7. **Auth pages** (A5–A8, B1, B2, C1) are static HTML with small scripts (§5.3). Their motion must be achievable with CSS (and optional small vanilla JS), not a framework.

### 6.5 Non-negotiable behaviours (easy to lose, do not)
- **Inline, not modal**, for the kitchen-board manager reason form (the ticket must stay visible) and for the inline confirms on B8, B7 and C2. You may restyle them heavily, but they stay attached to their row, card or ticket.
- **One-time secrets:** temporary passwords (B8 invite, B8 reset, C2 issued credential) must look unmistakably "shown once", be easy to select or copy, and have an explicit dismiss where one exists today.
- **Money warnings:** "This does not refund the customer…" and "The customer has not been refunded…" must stay prominent.
- **Disabled-with-reason:** A2 and A3 Add buttons show *which* choices are missing. B7e "Create group" always submits and names the failing field. Keep both patterns exactly as specified.
- **Staged edit mode** in the menu builder: struck-through rows with "keep it", nothing saved until Save, Cancel discards, the uploading-photo lock on Save, and the staged-deletion summary line.
- **Role gating:** tabs hidden per role; manager-only ticket actions; the driver's single-screen portal (B10) and their own-orders-only list; the admin-only settings and delivery area (B11); B4 "Not part of your role"; B3 invitation; B2 two modes; the only-admin and self protections on B8.
- **Guest checkout is a first-class path, not a fallback.** "Continue as guest" stays reachable on A5 with no account and no Clerk, stays *quieter* than signing in, and carries its warning — "this browser is the only thing that can open your pickup PIN again" — wherever a guest is named: A5's panel, A1's account row, A9's account bar. ⚠ A guest's tracking URL is a credential: never design it as something to copy or share.
- **A meal deal stays one thing on A11.** What the customer bought as one row must never be listed back to them as three unrelated items.
- **Closed restaurant:** the menu stays readable but every order control is disabled.
- **Sold-out** is visible and disabled everywhere it appears (A1, A2 options, A3 slot items, B6, B7).
- **Photos:** a missing or broken photo must degrade to a clean layout with no placeholder on the storefront.
- **Every loading, empty and error state in §2**, including the full-page states G1–G3, A12, and the "unavailable / can't find / couldn't open" pages.

### 6.6 Required output for each screen
For **every** screen ID in Section 2 (A1–A12, B0–B11 including B7a–B7e, C0–C3, and a light-touch pass on E1–E2), deliver:

1. **Layout description:** structure and hierarchy at phone, tablet and desktop; what is sticky; where each inventory element lives.
2. **3D and motion treatment:** what animates, trigger, duration and easing, depth layer, and the reduced-motion fallback. Write "None: static by design" where that is the right answer.
3. **States gallery:** a frame for every state listed for that screen (loading, empty, error, success, role variants, step variants, closed, locked, busy, etc.).
4. **Preservation checklist:** a checkbox list that repeats **every numbered element, state, validation message and ⚠ edge case** from that screen's inventory entry and marks each ✅ preserved (with where it now lives). Anything intentionally changed must be marked 🔁 with the reason. Nothing may be marked removed.

Finish with a **global cross-check table** (screen ID → frames delivered → checklist complete Y/N) and a list of any open questions you had to assume answers for.

---

## 7. Open Questions / Uncertainties

1. **Brand casing:** copy says "Zenoeats"; the admin header wordmark says "ZenoEats". Which is canonical?
2. **Delivery is half a feature, and the halves have moved.** A restaurant can now draw a delivery area, price it by distance and say whether the fee is taxed (B11), and the pricing that would charge for it exists and is tested. What a **customer** still cannot do is choose delivery at checkout, type their own address, or be charged for the journey — so every delivery today is still a phone order a manager sent out, free. Design B11 as a real, working screen; do **not** design a delivery option into A9 checkout. There is still no failed-delivery state, no proof of delivery and no driver location.
3. **Customer order history:** ⚠ *partly resolved* — the storefront header now has an account row (A1 element 0), so there **is** a sign-in entry point outside checkout. But `GET /api/v1/orders` (list a customer's orders) is still unused, there is still no "My orders" screen, and a guest could not have one at all: their identity is a cookie, not an account. Should a signed-in customer get one? Assumed **no** (excluded from scope).
4. **Combo "hidden" state:** the builder shows " · hidden" when a combo's `is_available` is false, and the API accepts `is_available`, but no control in the UI sets it. Is there meant to be a hide/show toggle? Assumed not in scope; the "hidden" label is kept.
5. **Delivery contact details:** the driver's card shows an address and the items, and no phone number, because orders hold none. Should the customer's phone reach the driver's screen? It would need backend work.

5b. **B11 saves three different ways** — a sticky bar for the restaurant's details, two small buttons for your account, and immediate saves for delivery. Each has a reason (see B11), but if the redesign can make one pattern carry all three without losing those reasons, that is an improvement worth taking.
6. **Discount row missing on order tracking (A11):** checkout shows Discount when > 0, and the confirmation email does too, but A11 still shows only Subtotal, Tax and Total — still true after the guest-checkout work. Likely an oversight. Should the redesign add it? (It would not need backend changes: `discount_minor` is already returned.)
7. **Kitchen role and PIN collection:** the role help says Kitchen = "order board and sold-out toggles" and Cashier = "collect orders with PINs", but the UI and API let *every floor* role mark ready and collect with PIN (a driver cannot: they have no board). The brief keeps current behaviour.
8. **"Delete for good" confirm (C2):** a code comment says moving the mouse away cancels the armed state, but the code only cancels via "keep it". Brief assumes the explicit "keep it" link.
9. **Admin soft-delete uses the native browser `confirm()` dialog.** Assumed the redesign may replace it with a designed inline confirm carrying the same message.
10. **Staff login footer copy** ("Administrators are configured in the deployment environment…") is copied from the admin login and is likely wrong for restaurant staff. May it be rewritten (e.g. "Ask your restaurant's admin for access")?
11. **Mobile navigation:** the tab row now drops to its own full-width line under 640 px and scrolls sideways, with the active tab scrolled into view; the wordmark narrows to 7rem and truncates. That was a launch fix, not a design: the page no longer scrolls sideways and every tab is reachable, but an admin still swipes to reach the last two of seven, and the staff header still hides identity and "Change password" under 640 px. Assumed the redesign introduces a proper mobile pattern without removing either item.
12. **Sheet accessibility:** A2 and A3 have no Escape-to-close or focus trap today. Assumed the redesign may add them (improvement, not a removal).
13. **Reports scope:** B9 now takes a date range (§B9); the platform dashboard (C2) is still all-time and still counts refunds as revenue. Assumed the redesign leaves C2's figures as they are.
14. **Meal-period hours are informational only.** Nothing prevents ordering outside them. The design must not imply the storefront is closed outside hours.
15. **Social providers vary by Clerk configuration** (Google, Apple, Facebook, any subset, or none). Designs should show the 0, 1 and 3 button cases.
16. **Emails (E1, E2):** in scope for redesign, or just keep them visually consistent? Assumed a light-touch consistency pass.
17. **Platform root domain:** `zenoeats.com` with no restaurant subdomain has no marketing or landing page; the storefront route would show "This menu isn't available". Is a landing page wanted? Assumed out of scope.
18. **Kitchen board overdue threshold** (red after 15 minutes) and the checkout stepper having no upper limit (while A2/A3 cap at 20) are hard-coded behaviours kept as-is. Confirm neither should change.
19. **Dark mode:** none exists. Assumed light-only, with an optional dark kitchen-board variant.
20. **Deliverable type:** assumed mockups plus interactive prototype plus token and component specs, not production code. Tell Astra if you want code output instead, and in which form (Tailwind HTML, React).
21. **A guest's order is unrecoverable by design.** Losing the browser *and* the confirmation email loses the pickup PIN. The address is never verified, there is no "email my order again", and two people who type the same address are two different guests. A5 says so in one sentence before anyone chooses it. Is one sentence the right weight, or should choosing guest checkout cost an explicit confirmation? Assumed one sentence, kept where it is.
22. **Where guest checkout is offered.** Today it lives only on A5, reached by a redirect out of checkout — so a customer who wanted to avoid an account has to visit the sign-in page to find out they needn't. An inline "or continue as a guest" on A9 would need no backend work. Flagged, not assumed: it is a conversion question the redesign is well placed to answer.
23. **E1 does not group meal deals; A11 now does.** The confirmation email lists a combo's components as separate lines, while the tracking page groups them into one row under the deal's name. The email is the older of the two. Should E1 be brought into line? (Backend copy change only — the data is already there.)
24. **"Sign out" on a guest is destructive, and is named two different things.** A1's account row calls it "Sign out"; A9's account bar calls it "Start over". Either way it drops the cookie irreversibly and nothing can name that guest's orders again. ⚠ Assumed the redesign unifies the wording, makes the consequence visible, and gives a guest sign-out a confirm that a signed-in customer's does not need.
25. **Session lifetimes are invisible.** The guest cookie lasts 30 days, the order-view token in E1 lasts 7, and an abandoned guest row is swept after 45. Nothing in the UI says any of it. Should A11 say when its link stops working? Assumed not in scope.
26. **The account row sits above the restaurant's name (A1)** — on the one surface whose brief says the restaurant's identity comes first and Zenoeats stays in the background. Assumed it stays quiet and secondary, but the placement is genuinely open: it could live in a corner, fold into the cart bar, or appear only on scroll.
