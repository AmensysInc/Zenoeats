# Redesign implementation and verification report

Verified 2026-09-17 against the current uncommitted working tree.

The redesigned implementation is present across all 42 manifest screens. Automated checks pass. **The full request is not certified 100% complete:** a passing suite is not proof of each preservation-ledger line or all 498 state variants. Partial/blocked statuses below identify missing acceptance evidence.

## Verified results

- **689 backend tests passed, zero failed**, in 289.38 seconds. Disposable PostgreSQL/Redis, with external API credentials disabled. Existing authorization, tenant isolation, money, menu, delivery, staff, reporting, retention, payment and email tests all ran.
- **99 browser checks passed, zero failed** against the real Vite app with isolated API/Clerk/maps fixtures. Responsive captures at 390/1024/1440 where listed; no production fixture data.
- Production build and ESLint: exit 0. Existing large-chunk warning and two backend dependency deprecation warnings remain.
- **810/810 reference frames rendered**, covering every screen/state/width reference. This does not mean all 498 real-app states were exercised.
- Source whitespace check: no errors. Existing uncommitted changes retained; no commit or deployment made.

## Mapping and global theme

The [screen mapping](SCREEN-MAPPING.md) records actual files, routes, baseline existence and API/hook/context sources. [Screen contracts](screen-contracts.json) retain all states and motion instructions.

The existing Tailwind/CSS-variable system holds semantic colors, customer overrides, local typography, compatible spacing, radii, shadows, layers and motion/easing. No parallel theme engine, mockup runtime, remote font or WebGL was introduced.

## Status definitions

Done: the small local state contract was directly exercised. Partial: implementation exists with the listed evidence, but full state/ledger acceptance is not established. Blocked: the provider-hosted acceptance flow was not available. Partial does not imply intentional removal of the listed controls.

| Screen | Status | States | Remaining verification |
|---|---|---:|---|
| A1 Restaurant homepage & menu | partial | 17 | All photo, filter and identity transitions not individually asserted. |
| A2 Customize an item | partial | 10 | All modifier maximum, negative-price and photo variants not individually exercised. |
| A3 Build a combo | partial | 9 | Shared slots passed; every incomplete, discount and sold-out browser state not exercised. |
| A4 Cart bar | partial | 3 | Empty/disabled-link variants were source-reviewed, not individually asserted. |
| A5 Customer sign-in | partial | 30 | Live Clerk social, SMS/TOTP/backup-code and complete validation matrix unverified. |
| A6 Create customer account | partial | 26 | Live verification, captcha, cooldown and continuation branches unverified. |
| A7 Reset customer password | partial | 14 | Real reset messages, cooldown and additional factors unverified. |
| A8 Social return | partial | 6 | Real provider callback not run. |
| A9 Review and checkout | partial | 29 | Main guest/delivery/error paths passed; all 29 states not individually asserted. |
| A10 Secure payment | partial | 11 | Real Stripe iframe, wallets, card failures and issuer challenge unverified. |
| A11 Order confirmation & tracking | partial | 30 | Controlled map fixtures passed; actual demo key, GPS and all refund/token layouts unverified. |
| A12 Customer access states | partial | 3 | Complete anonymous redirect/return sequence with Clerk unverified. |
| A13 Manage profile | partial | 8 | Real email delivery, resend/expiry and synchronization recovery unverified. |
| B0 Staff shell | partial | 9 | Five role link sets passed; every mobile disclosure/logout failure not asserted. |
| B1 Restaurant sign-in | partial | 6 | Layouts/safe-return passed; every temporary/error destination not individually exercised. |
| B2 Change staff password | partial | 9 | Browser validation and API rules passed; all success/error UI transitions not exercised. |
| B3 Accept staff invitation | partial | 7 | Invitation/API acceptance passed; all role continuations and decline errors not exercised. |
| B4 Role access denied | partial | 5 | Driver denial/API permissions passed; every role-specific visual not individually asserted. |
| B5 Kitchen and counter | partial | 36 | Ready/wrong-PIN/cancel passed; all assignment/history/sound/arrival UI transitions not asserted. |
| B6 Stock availability | partial | 9 | Toggle/search/error passed; empty/all-sold and polling conflicts not individually asserted. |
| B7 Menu builder shell | partial | 4 | Tabs passed; shared-error clearing after every mutation not asserted. |
| B7a Menu preview | partial | 8 | Preview rendered; every exclusive/photo-failure state not individually asserted. |
| B7b Items and item types | partial | 26 | API rules passed; full browser editor/upload/staged-save matrix not exercised. |
| B7c Meal periods | partial | 18 | API rules passed; full edit/picker/confirmation UI matrix not exercised. |
| B7d Combos | partial | 20 | API rules passed; full add/edit/delete browser matrix not exercised. |
| B7e Modifier library | partial | 24 | API rules passed; full staged-edit/upload browser matrix not exercised. |
| B8 Team and invitations | partial | 19 | API rules and layouts passed; all copy/dismiss/confirmation UI paths not exercised. |
| B9 Restaurant reports | partial | 10 | Calculations/layouts passed; every date/refresh UI transition not asserted. |
| B10 Deliveries | partial | 12 | API authorization/state changes passed; real GPS/device permission and every button path unverified. |
| B11 Restaurant settings | partial | 23 | Draft/blank-fee browser checks passed; all geocoding/account/ring-limit UI variants unverified. |
| C0 Platform shell | partial | 4 | Layouts passed; all expiry/logout transitions not individually asserted. |
| C1 Platform sign-in | partial | 3 | Layouts/safe return passed; every server-failure UI message not asserted. |
| C2 Restaurants dashboard | partial | 23 | API rules passed; real Connect and every destructive confirmation UI unverified. |
| C3 Restaurant orders | partial | 9 | Policy/layout checks passed; every filter/page boundary not individually asserted. |
| G1 Checking session | done | 1 | Direct browser check passed. |
| G2 API unavailable | partial | 4 | Server-error path passed; real offline and request timeout not separately induced. |
| G3 Page not found | done | 1 | Direct browser check passed. |
| G4 Customer identity check | done | 1 | Direct browser check passed. |
| E1 Order confirmation email | partial | 4 | Composition/bookkeeping passed; actual inbox delivery/rendering unverified. |
| E2 Staff invitation email | partial | 3 | Composition passed; actual inbox rendering/delivery unverified. |
| E3 Stripe Connect handoff | blocked | 2 | Live acceptance blocked: hosted Stripe onboarding not completed. |
| E4 Stripe payment boundary | blocked | 2 | Live acceptance blocked: real iframe, wallet and issuer challenge not run. |
## Fixes in this continuation

- Tenant/identity/tab-scoped checkout drafts retain contact, note, guest receipt email and fulfillment across refresh/back. Failed sign-out preserves them; successful sign-out clears them.
- Guest email is editable/validated. Per-order name/email/address snapshots prevent later profile changes from rewriting an order. Registered order-specific contact does not silently replace the saved profile.
- Obsolete quotes cannot overwrite newer cart/address/mode input. Payment waits for current pricing; duplicate submissions are locked and unchanged requests retain their retry key.
- Registered email change uses Clerk verification, followed by independent backend lookup of the verified primary address.
- Map error/timeout/auth failure leaves progress usable with Retry. Zoom/Recenter are accessible; stale/offline positions suppress ETA; reduced motion avoids animated camera changes.
- Login return paths reject backslash-based external redirects. Pickup address comes from the existing portal API.

## Net-new versus existing work

- **B10 Deliveries and B11 Settings already existed in HEAD.** They were redesigned using their existing APIs.
- **New in the current working tree:** A13 profile page/API, guest-session UI/API, live delivery tracking/location plumbing, and customer history/favourites support already present in inherited changes.
- This continuation added checkout drafts, verified-email change/sync, per-order contact snapshots and migration 0030. It preserves /profile and adds the manifest's /account/profile alias.
- A10 had an existing payment component; its explicit route was added in the working-tree redesign. E3/E4 are provider boundaries, not additional emails.

## Visual/motion differences and limits

- Real restaurant content replaces fixture food photos, slogans, addresses, positions and prices. With no photo, the hero remains a gradient and product images disappear cleanly.
- The backend provides coordinates and ETA, not route geometry. The map does not fabricate the mockup's road route.
- The user reports a **Google Maps demo key**. Controlled-fixture tests do not establish actual key/map-ID/referrer behavior or real tile/GPS operation.
- Connect, payment wallets, issuer challenges and auth-provider behavior remain externally owned; pixel parity within them is not promised.
- Customer menu/item sheet, staff board/settings and platform dashboard screenshots were visually inspected. Other captures have layout/overflow checks. No complete pixel-diff or assistive-technology/device certification was performed.
- Brief CSS entrances/transitions replace a dedicated layout-animation engine. Full cross-column interpolation and every poll-driven motion were not individually verified. Money changes immediately; reduced-motion timing/camera behavior was checked.
- Emails use safe tables, inline styles and plain text. Actual inbox delivery and Outlook/Gmail/mobile rendering are unverified.

## Preservation gaps and evidence

The table lists screen-level gaps. [ledger-audit.json](ledger-audit.json) indexes every ledger checkbox, source line and global contract. It intentionally records screen-level evidence instead of inventing individual assertion coverage. [verification.json](verification.json) lists exact browser checks, backend test families and all manifest states per screen.

Unconfirmed areas include every individual builder/staged-delete/upload interaction; all staff/platform confirmations and one-time credential actions; auth-provider recovery branches; actual map/GPS/provider operation; every pagination/date boundary; and all visual/motion variants. Passing backend business-rule tests do not replace those missing UI assertions.

## Runtime and migration notes

Migration 0030 and the complete migration chain passed on the disposable QA database. Apply reviewed migrations through the normal deployment process before using the new order-contact columns. No production database/provider account was changed.

The interrupted screenshot run's temporary local staff membership was removed. Its identity row could not be deleted by the normal system role, so that identity was disabled and its temporary password cleared. No database privileges were broadened.

Runners: scripts/verify_redesign.py, web/qa/redesign.mjs and web/qa/references.mjs. Raw logs, JUnit results and screenshots are in ignored artifacts/redesign/.

## Per-screen checklists

Unchecked items remain verification requirements. Reference rendering is explicitly separated from live application acceptance.

### A1 - Restaurant homepage & menu

- [x] Real implementation/data source mapped: web/src/pages/storefront/StorefrontPage.tsx.
- [x] Reference family rendered. Manifest states: default, loading, error, closed, empty, one-period, overnight, no-photos, broken-photo, no-tagline, sold-out, account-loading, signed-in, guest, guest-confirm, lunch-menu, dinner-menu.
- Preserved scope reviewed: Public browsing, tenant cart, periods, optional photos, closed/empty and account states.
- [ ] Every state and preserved interaction independently confirmed at runtime.
- Browser evidence: A1-home-390; A1-home-1024; A1-home-1440; A1-public-empty; A1-closed-A4-disabled.
- Backend evidence: test_menu_view (32), test_tenant_resolution (6), test_guest_checkout (23).
- Remaining: All photo, filter and identity transitions not individually asserted.

### A2 - Customize an item

- [x] Real implementation/data source mapped: web/src/features/cart/components/ModifierSheet.tsx, web/src/features/cart/components/ModifierGroups.tsx.
- [x] Reference family rendered. Manifest states: default, no-photo, no-groups, required, max, sold-option, negative, quantity-20, coffee, iced-tea.
- Preserved scope reviewed: Required/default modifiers, sold-out choices, quantity 1-20, notes, pricing, merging and focus.
- [ ] Every state and preserved interaction independently confirmed at runtime.
- Browser evidence: A2-item-sheet-keyboard-A4-cart; A2-limits-merge-and-focus-restoration.
- Backend evidence: test_included_options (14), test_money (5), test_menu_validation (22).
- Remaining: All modifier maximum, negative-price and photo variants not individually exercised.

### A3 - Build a combo

- [x] Real implementation/data source mapped: web/src/features/cart/components/ComboSheet.tsx.
- [x] Reference family rendered. Manifest states: default, partial, missing-modifiers, ready, none, percent, amount, sold-out, shared-item.
- Preserved scope reviewed: Independent slot choices/modifiers, required choices, discount/floor, quantity and merging.
- [ ] Every state and preserved interaction independently confirmed at runtime.
- Browser evidence: A3-combo-sheet; A3-independent-shared-item-slots.
- Backend evidence: test_combo_pricing (25), test_combo_slots (5), test_included_options (14).
- Remaining: Shared slots passed; every incomplete, discount and sold-out browser state not exercised.

### A4 - Cart bar

- [x] Real implementation/data source mapped: web/src/pages/storefront/StorefrontPage.tsx.
- [x] Reference family rendered. Manifest states: default, empty, closed.
- Preserved scope reviewed: Hidden empty cart, count/subtotal, disabled closed link, checkout and tax caption.
- [ ] Every state and preserved interaction independently confirmed at runtime.
- Browser evidence: No screen-specific assertion.
- Backend evidence: test_combo_pricing (25).
- Remaining: Empty/disabled-link variants were source-reviewed, not individually asserted.

### A5 - Customer sign-in

- [x] Real implementation/data source mapped: web/login/customer-sign-in.html, web/login/customer-sign-in.ts, web/src/features/storefront/components/GuestSession.tsx.
- [x] Reference family rendered. Manifest states: default, providers-0, providers-1, root, loading, busy, error, social-failed, not-configured, unreachable, sms, email-code, totp, backup, checking, unsupported, incomplete, already, guest, guest-error, guest-busy, tenant-loading, validation-1, validation-2, validation-3, validation-4, validation-5, validation-6, validation-7, validation-8.
- Preserved scope reviewed: Lazy identity, safe next, enabled providers, password/2FA and guest recovery warning.
- [ ] Every state and preserved interaction independently confirmed at runtime.
- Browser evidence: A5-auth-layout-390; A5-auth-layout-1024; A5-auth-layout-1440.
- Backend evidence: test_guest_checkout (23), test_clerk_customers (19).
- Remaining: Live Clerk social, SMS/TOTP/backup-code and complete validation matrix unverified.

### A6 - Create customer account

- [x] Real implementation/data source mapped: web/login/customer-sign-up.html, web/login/customer-sign-up.ts.
- [x] Reference family rendered. Manifest states: default, no-name, providers-0, providers-1, short, mismatch, challenge, busy, continue, continue-email, continue-name, continue-terms, code, cooldown, checking, expired, unsupported, incomplete, already, validation-1, validation-2, validation-3, validation-4, validation-5, validation-6, validation-7.
- Preserved scope reviewed: Signup validation, verification/resend, incomplete signup continuation and captcha.
- [ ] Every state and preserved interaction independently confirmed at runtime.
- Browser evidence: A6-auth-layout-390; A6-auth-layout-1024; A6-auth-layout-1440.
- Backend evidence: test_clerk_customers (19).
- Remaining: Live verification, captcha, cooldown and continuation branches unverified.

### A7 - Reset customer password

- [x] Real implementation/data source mapped: web/login/customer-forgot-password.html, web/login/customer-forgot-password.ts.
- [x] Reference family rendered. Manifest states: default, loading, sending, code, short, mismatch, cooldown, busy, additional-step, success, validation-1, validation-2, validation-3, validation-4.
- Preserved scope reviewed: Reset email/code, resend, password validation and additional-factor handling.
- [ ] Every state and preserved interaction independently confirmed at runtime.
- Browser evidence: A7-auth-layout-390; A7-auth-layout-1024; A7-auth-layout-1440.
- Backend evidence: test_clerk_customers (19).
- Remaining: Real reset messages, cooldown and additional factors unverified.

### A8 - Social return

- [x] Real implementation/data source mapped: web/login/customer-sso-callback.html, web/login/customer-sso-callback.ts.
- [x] Reference family rendered. Manifest states: default, to-checkout, to-continue, to-code, to-2fa, failed.
- Preserved scope reviewed: SSO completion, safe return and verification/2FA continuations.
- [ ] Every state and preserved interaction independently confirmed at runtime.
- Browser evidence: No screen-specific assertion.
- Backend evidence: test_clerk_customers (19).
- Remaining: Real provider callback not run.

### A9 - Review and checkout

- [x] Real implementation/data source mapped: web/src/pages/storefront/CheckoutPage.tsx.
- [x] Reference family rendered. Manifest states: default, loading, unavailable, empty, quoting, requote, busy, closed, price-changed, item-unavailable, network, no-discount, quantity-one, large-quantity, guest, guest-confirm, restore, validation-1, validation-2, guest-complete, guest-invalid, delivery, delivery-quoted, delivery-quoting, delivery-outside, delivery-unavailable, delivery-error, delivery-free, guest-delivery.
- Preserved scope reviewed: Server quotes, stale-response protection, contact, delivery fee, drafts and idempotent retry.
- [ ] Every state and preserved interaction independently confirmed at runtime.
- Browser evidence: A9-guest-validation-draft-and-retry; A9-quote-invalidated-on-address-edit; A9-delivery-unavailable; A9-guest-end-failure-keeps-draft.
- Backend evidence: test_checkout_contact (23), test_guest_checkout (23), test_delivery_pricing (15), test_order_delivery_fee (6), test_combo_pricing (25), test_stripe_tax (25), test_order_flow (5).
- Remaining: Main guest/delivery/error paths passed; all 29 states not individually asserted.

### A10 - Secure payment

- [x] Real implementation/data source mapped: web/src/pages/storefront/PaymentPage.tsx.
- [x] Reference family rendered. Manifest states: default, loading, recovering, no-id, error, processing, card-error, stripe-loading, challenge, redirect, delivery.
- Preserved scope reviewed: Intent recovery, authoritative totals, Stripe Element/error handling and webhook-owned paid status.
- [ ] Every state and preserved interaction independently confirmed at runtime.
- Browser evidence: No screen-specific assertion.
- Backend evidence: test_payment_reconciliation (15), test_order_flow (5), test_stripe_webhook (7).
- Remaining: Real Stripe iframe, wallets, card failures and issuer challenge unverified.

### A11 - Order confirmation & tracking

- [x] Real implementation/data source mapped: web/src/pages/storefront/OrderPage.tsx, web/src/features/storefront/components/DeliveryTracking.tsx.
- [x] Reference family rendered. Manifest states: default, AUTO_ACCEPTED, PREPARING, READY_FOR_PICKUP, COMPLETED, CANCELLED, EXPIRED, unknown, no-pin, refunded, partial-refund, loading, error, READY_FOR_DELIVERY, OUT_FOR_DELIVERY, token, token-error, combo-groups, linked, delivery-preparing, map-loading, map-waiting, map-stale, map-offline, map-error, delivery-arriving, delivery-completed, delivery-cancelled, delivery-pending, delivery-confirmed.
- Preserved scope reviewed: Authorized polling/token, pickup PIN, terminal/refund states, real coordinates, fresh ETA and map recovery.
- [ ] Every state and preserved interaction independently confirmed at runtime.
- Browser evidence: A11-pickup-PENDING_PAYMENT; A11-pickup-AUTO_ACCEPTED; A11-pickup-PREPARING; A11-pickup-READY_FOR_PICKUP; A11-pickup-COMPLETED; A11-pickup-CANCELLED; A11-pickup-EXPIRED; A11-pickup-UNKNOWN; A11-delivery-PREPARING; A11-delivery-READY_FOR_DELIVERY; A11-delivery-OUT_FOR_DELIVERY; A11-delivery-COMPLETED; A11-delivery-CANCELLED; A11-reduced-motion; A11-map-controls-normal; A11-map-controls-reduced; A11-map-failure-retry-and-late-auth-failure; A11-stale-location-no-ETA.
- Backend evidence: test_live_tracking (13), test_guest_checkout (23), test_pickup_pin (6), test_payment_reconciliation (15).
- Remaining: Controlled map fixtures passed; actual demo key, GPS and all refund/token layouts unverified.

### A12 - Customer access states

- [x] Real implementation/data source mapped: web/src/components/layout/Guards.tsx.
- [x] Reference family rendered. Manifest states: default, unreachable, redirect.
- Preserved scope reviewed: Session/loading/error guard, safe sign-in return and public-menu escape.
- [ ] Every state and preserved interaction independently confirmed at runtime.
- Browser evidence: A12-customer-network-error.
- Backend evidence: test_guest_checkout (23), test_clerk_customers (19).
- Remaining: Complete anonymous redirect/return sequence with Clerk unverified.

### A13 - Manage profile

- [x] Real implementation/data source mapped: web/src/pages/storefront/ProfilePage.tsx, web/src/features/storefront/components/ChangeEmail.tsx, backend/app/api/v1/customer.py.
- [x] Reference family rendered. Manifest states: default, editing, saving, saved, save-error, verify-email, loading, unauthorized.
- Preserved scope reviewed: Registered profile, failed-save drafts, verified-email sync, own orders/favourites and checkout refresh.
- [ ] Every state and preserved interaction independently confirmed at runtime.
- Browser evidence: A13-save-failure-retains-draft-and-verified-email.
- Backend evidence: test_customer_profile (16), test_checkout_contact (23).
- Remaining: Real email delivery, resend/expiry and synchronization recovery unverified.

### B0 - Staff shell

- [x] Real implementation/data source mapped: web/src/features/restaurant/components/ManageShell.tsx, web/src/components/layout/Shell.tsx, web/src/features/restaurant/nav.ts.
- [x] Reference family rendered. Manifest states: default, MANAGER, KITCHEN, CASHIER, unknown, account, busy, DRIVER, signout-all.
- Preserved scope reviewed: Five-role navigation, mobile More, own account and separate logout actions.
- [ ] Every state and preserved interaction independently confirmed at runtime.
- Browser evidence: B0-role-ADMIN; B0-role-MANAGER; B0-role-KITCHEN; B0-role-CASHIER; B0-role-DRIVER.
- Backend evidence: test_role_coverage (3), test_own_account (23), test_session_revocation (9).
- Remaining: Five role link sets passed; every mobile disclosure/logout failure not asserted.

### B1 - Restaurant sign-in

- [x] Real implementation/data source mapped: web/login/staff-login.html, web/login/staff-login.ts.
- [x] Reference family rendered. Manifest states: default, busy, error, temporary, DRIVER, validation-1.
- Preserved scope reviewed: Password login, busy/error, temporary-password destination and safe return.
- [ ] Every state and preserved interaction independently confirmed at runtime.
- Browser evidence: B1-auth-layout-390; B1-auth-layout-1024; B1-auth-layout-1440; B1-safe-return.
- Backend evidence: test_session_revocation (9), test_staff_management (9).
- Remaining: Layouts/safe-return passed; every temporary/error destination not individually exercised.

### B2 - Change staff password

- [x] Real implementation/data source mapped: web/login/change-password.html, web/login/change-password.ts.
- [x] Reference family rendered. Manifest states: default, voluntary, short, mismatch, busy, error, success, redirect, validation-1.
- Preserved scope reviewed: Current password, length/match, forced/voluntary flow and session redirect.
- [ ] Every state and preserved interaction independently confirmed at runtime.
- Browser evidence: B2-auth-layout-390; B2-auth-layout-1024; B2-auth-layout-1440; B2-password-length-and-mismatch.
- Backend evidence: test_own_account (23), test_session_revocation (9).
- Remaining: Browser validation and API rules passed; all success/error UI transitions not exercised.

### B3 - Accept staff invitation

- [x] Real implementation/data source mapped: web/src/components/layout/Guards.tsx.
- [x] Reference family rendered. Manifest states: default, ADMIN, MANAGER, CASHIER, busy, error, DRIVER.
- Preserved scope reviewed: Invitation acceptance before access, role/restaurant/email and decline action.
- [ ] Every state and preserved interaction independently confirmed at runtime.
- Browser evidence: B3-invitation.
- Backend evidence: test_staff_invite (4), test_role_coverage (3).
- Remaining: Invitation/API acceptance passed; all role continuations and decline errors not exercised.

### B4 - Role access denied

- [x] Real implementation/data source mapped: web/src/components/layout/Guards.tsx.
- [x] Reference family rendered. Manifest states: default, CASHIER, MANAGER, DRIVER, ADMIN.
- Preserved scope reviewed: Hidden unauthorized links, direct-route denial and authoritative API permissions.
- [ ] Every state and preserved interaction independently confirmed at runtime.
- Browser evidence: B4-driver-access-denied.
- Backend evidence: test_role_coverage (3).
- Remaining: Driver denial/API permissions passed; every role-specific visual not individually asserted.

### B5 - Kitchen and counter

- [x] Real implementation/data source mapped: web/src/pages/manage/KitchenBoardPage.tsx, web/src/features/restaurant/components/BoardColumn.tsx.
- [x] Reference family rendered. Manifest states: default, MANAGER, KITCHEN, CASHIER, loading, empty, making-empty, waiting-empty, error, overdue, refunded, partly-refunded, pin, wrong-pin, locked, locked-kitchen, override, cancel, cancel-refunded, busy, notice-override, notice-cancel, notice-refunded, assign, assign-error, assign-busy, no-drivers, delivery-making, delivery-ready, delivery-road, change-driver, back-collection, history-open, history-empty, new-order, sound-resume.
- Preserved scope reviewed: Paid-only board, paid age, combo notes, PIN/lock, override/cancel, refund warning, assignment/history.
- [ ] Every state and preserved interaction independently confirmed at runtime.
- Browser evidence: B5-layout-390; B5-layout-1024; B5-layout-1440; B5-ready-and-invalid-PIN; B5-reasoned-cancel-refund-warning.
- Backend evidence: test_order_board_actions (11), test_pickup_pin (6), test_deliveries (23), test_restaurant_reports (11).
- Remaining: Ready/wrong-PIN/cancel passed; all assignment/history/sound/arrival UI transitions not asserted.

### B6 - Stock availability

- [x] Real implementation/data source mapped: web/src/pages/manage/StockPage.tsx.
- [x] Reference family rendered. Manifest states: default, loading, empty, all-stock, all-sold, search, no-match, error, busy.
- Preserved scope reviewed: 30-second poll, type/name search, sold-out-first, server-confirmed toggle and busy/error.
- [ ] Every state and preserved interaction independently confirmed at runtime.
- Browser evidence: B6-layout-390; B6-layout-1024; B6-layout-1440; B6-stock-confirmed-toggle-search-error.
- Backend evidence: test_stock (4), test_role_coverage (3).
- Remaining: Toggle/search/error passed; empty/all-sold and polling conflicts not individually asserted.

### B7 - Menu builder shell

- [x] Real implementation/data source mapped: web/src/pages/manage/MenuPage.tsx.
- [x] Reference family rendered. Manifest states: default, loading, error, MANAGER.
- Preserved scope reviewed: Five builder tabs, page error/loading and manager permissions.
- [ ] Every state and preserved interaction independently confirmed at runtime.
- Browser evidence: B7-layout-390; B7-layout-1024; B7-layout-1440; B7-all-builder-tabs.
- Backend evidence: test_role_coverage (3), test_menu_view (32).
- Remaining: Tabs passed; shared-error clearing after every mutation not asserted.

### B7a - Menu preview

- [x] Real implementation/data source mapped: web/src/features/restaurant/components/MenuPreview.tsx.
- [x] Reference family rendered. Manifest states: default, empty, single-period, no-exclusive, no-photo, broken-photo, sold-out, exclusive.
- Preserved scope reviewed: Menu hierarchy, periods/exclusives, optional/broken images and availability.
- [ ] Every state and preserved interaction independently confirmed at runtime.
- Browser evidence: No screen-specific assertion.
- Backend evidence: test_menu_view (32), test_item_type_nesting (14).
- Remaining: Preview rendered; every exclusive/photo-failure state not individually asserted.

### B7b - Items and item types

- [x] Real implementation/data source mapped: web/src/features/restaurant/components/ItemLibrary.tsx, web/src/features/restaurant/components/ItemTypeManager.tsx.
- [x] Reference family rendered. Manifest states: default, empty, filter, no-match, add-type, no-types, type-added, types-edit, types-staged, types-refused, add, no-periods, no-groups, upload, photo-error, bulk, expanded, staged, save-error, busy, validation-1, validation-2, validation-3, validation-4, validation-5, validation-6.
- Preserved scope reviewed: Types/items, staged CRUD, included modifiers, periods, prices/tax and image upload locks.
- [ ] Every state and preserved interaction independently confirmed at runtime.
- Browser evidence: No screen-specific assertion.
- Backend evidence: test_item_types (43), test_item_type_nesting (14), test_menu_validation (22), test_menu_delete (8), test_images (44), test_included_options (14), test_item_tax_exempt (3).
- Remaining: API rules passed; full browser editor/upload/staged-save matrix not exercised.

### B7c - Meal periods

- [x] Real implementation/data source mapped: web/src/features/restaurant/components/MealPeriods.tsx.
- [x] Reference family rendered. Manifest states: default, empty, edit, overnight, no-hours, delete, delete-empty, picker, all-added, all-day, all-folded, empty-period, busy, validation-1, validation-2, validation-3, validation-4, validation-5.
- Preserved scope reviewed: Meal names/hours/overnight, membership ordering, folding and safe deletion.
- [ ] Every state and preserved interaction independently confirmed at runtime.
- Browser evidence: No screen-specific assertion.
- Backend evidence: test_menu_ordering (3), test_menu_rename (19), test_menu_delete (8), test_menu_view (32).
- Remaining: API rules passed; full edit/picker/confirmation UI matrix not exercised.

### B7d - Combos

- [x] Real implementation/data source mapped: web/src/features/restaurant/components/ComboBuilder.tsx.
- [x] Reference family rendered. Manifest states: default, empty, no-periods, add, edit, none, amount, percent, no-items, empty-slots, hidden, delete, busy, validation-1, validation-2, validation-3, validation-4, validation-5, validation-6, validation-7.
- Preserved scope reviewed: Meal-scoped combos, discount kinds, slots/types/choices and safe deletion.
- [ ] Every state and preserved interaction independently confirmed at runtime.
- Browser evidence: No screen-specific assertion.
- Backend evidence: test_combo_slots (5), test_combo_pricing (25), test_menu_validation (22).
- Remaining: API rules passed; full add/edit/delete browser matrix not exercised.

### B7e - Modifier library

- [x] Real implementation/data source mapped: web/src/features/restaurant/components/ModifierLibrary.tsx.
- [x] Reference family rendered. Manifest states: default, empty, pick-one, required, narrowed, last-row, upload, creating, edit, add-option, staged-option, staged-group, all-options, save-error, validation-1, validation-2, validation-3, validation-4, validation-5, validation-6, validation-7, validation-8, validation-9, validation-10.
- Preserved scope reviewed: Group rules/applicability, option pricing, staged deletions and uploads.
- [ ] Every state and preserved interaction independently confirmed at runtime.
- Browser evidence: No screen-specific assertion.
- Backend evidence: test_modifier_editing (14), test_menu_validation (22), test_images (44), test_included_options (14).
- Remaining: API rules passed; full staged-edit/upload browser matrix not exercised.

### B8 - Team and invitations

- [x] Real implementation/data source mapped: web/src/pages/manage/StaffPage.tsx.
- [x] Reference family rendered. Manifest states: default, loading, empty, error, invite-new, invite-existing, invite-busy, role-manager, role-admin, role-cashier, reset, reset-issued, remove, cancel-invite, self, only-admin, busy, role-driver, invite-driver.
- Preserved scope reviewed: Five roles, invitations, one-time credentials, reset, revoke and last-admin/self safeguards.
- [ ] Every state and preserved interaction independently confirmed at runtime.
- Browser evidence: B8-layout-390; B8-layout-1024; B8-layout-1440.
- Backend evidence: test_staff_invite (4), test_staff_management (9), test_staff_removal (5), test_role_coverage (3), test_own_account (23).
- Remaining: API rules and layouts passed; all copy/dismiss/confirmation UI paths not exercised.

### B9 - Restaurant reports

- [x] Real implementation/data source mapped: web/src/pages/manage/ReportsPage.tsx.
- [x] Reference family rendered. Manifest states: default, loading, error, empty, yesterday, last-7, month, dates, refreshing, no-deliveries.
- Preserved scope reviewed: Local dates, presets, money/refunds/discounts, delivery/by-driver and refresh.
- [ ] Every state and preserved interaction independently confirmed at runtime.
- Browser evidence: B9-layout-390; B9-layout-1024; B9-layout-1440.
- Backend evidence: test_restaurant_reports (11).
- Remaining: Calculations/layouts passed; every date/refresh UI transition not asserted.

### B10 - Deliveries

- [x] Real implementation/data source mapped: web/src/pages/manage/DeliveriesPage.tsx, web/src/features/restaurant/useShareDriverLocation.ts.
- [x] Reference family rendered. Manifest states: default, DRIVER, ADMIN, MANAGER, preparing, on-road, empty, loading, error, busy, overdue, not-found.
- Preserved scope reviewed: Own-driver assignments, manager overview, delivery state sequence, address/notes and 5-second poll.
- [ ] Every state and preserved interaction independently confirmed at runtime.
- Browser evidence: B10-layout-390; B10-layout-1024; B10-layout-1440.
- Backend evidence: test_deliveries (23), test_live_tracking (13), test_role_coverage (3).
- Remaining: API authorization/state changes passed; real GPS/device permission and every button path unverified.

### B11 - Restaurant settings

- [x] Real implementation/data source mapped: web/src/pages/manage/SettingsPage.tsx, web/src/features/restaurant/components/DeliveryArea.tsx, web/src/features/restaurant/components/OwnAccount.tsx.
- [x] Reference family rendered. Manifest states: default, loading, load-error, unplaced, address-stale, address-dirty, no-provider, lookup-error, rings-empty, rings-blank, rings-duplicate, rings-limit, rings-last, locating, rings-saving, profile-saving, stripe-address, stripe-tax, stripe-disabled, email-change, email-conflict, save-error, saved.
- Preserved scope reviewed: Partial profile patch, independent drafts, blank versus zero fees, ring rules and origin invalidation.
- [ ] Every state and preserved interaction independently confirmed at runtime.
- Browser evidence: B11-layout-390; B11-layout-1024; B11-layout-1440; B11-independent-profile-and-ring-drafts.
- Backend evidence: test_restaurant_profile (17), test_delivery_zones (25), test_delivery_pricing (15), test_origin_pinning (6), test_own_account (23), test_stripe_tax (25).
- Remaining: Draft/blank-fee browser checks passed; all geocoding/account/ring-limit UI variants unverified.

### C0 - Platform shell

- [x] Real implementation/data source mapped: web/src/features/admin/components/AdminShell.tsx, web/src/components/layout/Guards.tsx.
- [x] Reference family rendered. Manifest states: default, loading, error, redirect.
- Preserved scope reviewed: Platform session guard/loading/error/redirect, navigation and logout.
- [ ] Every state and preserved interaction independently confirmed at runtime.
- Browser evidence: No screen-specific assertion.
- Backend evidence: test_session_revocation (9), test_role_coverage (3).
- Remaining: Layouts passed; all expiry/logout transitions not individually asserted.

### C1 - Platform sign-in

- [x] Real implementation/data source mapped: web/login/admin-login.html, web/login/admin-login.ts.
- [x] Reference family rendered. Manifest states: default, busy, error.
- Preserved scope reviewed: Platform login, busy/error and safe return.
- [ ] Every state and preserved interaction independently confirmed at runtime.
- Browser evidence: C1-auth-layout-390; C1-auth-layout-1024; C1-auth-layout-1440; C1-safe-return.
- Backend evidence: test_session_revocation (9).
- Remaining: Layouts/safe return passed; every server-failure UI message not asserted.

### C2 - Restaurants dashboard

- [x] Real implementation/data source mapped: web/src/pages/admin/AdminRestaurantsPage.tsx, web/src/features/admin/components/RestaurantRow.tsx, web/src/features/admin/components/RestaurantForms.tsx, web/src/features/admin/components/RestaurantEditForm.tsx.
- [x] Reference family rendered. Manifest states: default, loading, empty, error, currencies, show-deleted, add, invalid-slug, creating, owner, credential, credential-invited, existing-owner, edit, stripe-tax, stripe-needs, stripe-synced, activate-error, delete, purge, purge-refused, busy, empty-reports.
- Preserved scope reviewed: Separate currencies, CRUD/status, owner credentials, Connect/sync and delete/purge consequences.
- [ ] Every state and preserved interaction independently confirmed at runtime.
- Browser evidence: C2-layout-390; C2-layout-1024; C2-layout-1440.
- Backend evidence: test_admin_restaurants (9), test_restaurant_purge (5), test_stripe_onboarding (5), test_restaurant_reports (11).
- Remaining: API rules passed; real Connect and every destructive confirmation UI unverified.

### C3 - Restaurant orders

- [x] Real implementation/data source mapped: web/src/pages/admin/AdminOrdersPage.tsx.
- [x] Reference family rendered. Manifest states: default, loading, error, empty, filter, filter-empty, page-two, last-page, no-payment.
- Preserved scope reviewed: Read-only orders, status filter, pagination, payment references and privacy.
- [ ] Every state and preserved interaction independently confirmed at runtime.
- Browser evidence: C3-layout-390; C3-layout-1024; C3-layout-1440.
- Backend evidence: test_admin_restaurants (9), test_role_coverage (3).
- Remaining: Policy/layout checks passed; every filter/page boundary not individually asserted.

### G1 - Checking session

- [x] Real implementation/data source mapped: web/src/components/layout/Guards.tsx, web/src/components/common/Feedback.tsx.
- [x] Reference family rendered. Manifest states: default.
- Preserved scope reviewed: Shared loading screen until the session resolves.
- [x] Every state and preserved interaction independently confirmed at runtime.
- Browser evidence: G1-session-loading-resolves.
- Backend evidence: not applicable.
- Remaining: None for this local single-state contract.

### G2 - API unavailable

- [x] Real implementation/data source mapped: web/src/components/layout/Guards.tsx, web/src/services/apiClient.ts.
- [x] Reference family rendered. Manifest states: default, timeout, offline, server.
- Preserved scope reviewed: Unavailable-server screen, retry and no false sign-in redirect.
- [ ] Every state and preserved interaction independently confirmed at runtime.
- Browser evidence: G2-staff-network-error.
- Backend evidence: test_error_envelope (13).
- Remaining: Server-error path passed; real offline and request timeout not separately induced.

### G3 - Page not found

- [x] Real implementation/data source mapped: web/src/routes/AppRoutes.tsx, web/src/components/common/Feedback.tsx.
- [x] Reference family rendered. Manifest states: default.
- Preserved scope reviewed: Responsive catch-all not-found view and return path.
- [x] Every state and preserved interaction independently confirmed at runtime.
- Browser evidence: G3-layout-390; G3-layout-1024; G3-layout-1440.
- Backend evidence: not applicable.
- Remaining: None for this local single-state contract.

### G4 - Customer identity check

- [x] Real implementation/data source mapped: web/src/components/layout/Guards.tsx.
- [x] Reference family rendered. Manifest states: default.
- Preserved scope reviewed: Customer identity loading until server response, with order-token bypass retained.
- [x] Every state and preserved interaction independently confirmed at runtime.
- Browser evidence: G4-session-loading-resolves.
- Backend evidence: test_guest_checkout (23).
- Remaining: None for this local single-state contract.

### E1 - Order confirmation email

- [x] Real implementation/data source mapped: backend/app/services/notifications.py.
- [x] Reference family rendered. Manifest states: default, no-discount, no-name, guest.
- Preserved scope reviewed: Escaped HTML/plain text, discount/name/delivery variants, guest link, receipt snapshot, no PIN and send-once.
- [ ] Every state and preserved interaction independently confirmed at runtime.
- Browser evidence: No screen-specific assertion.
- Backend evidence: test_notifications (16), test_guest_checkout (23), test_checkout_contact (23).
- Remaining: Composition/bookkeeping passed; actual inbox delivery/rendering unverified.

### E2 - Staff invitation email

- [x] Real implementation/data source mapped: backend/app/services/notifications.py.
- [x] Reference family rendered. Manifest states: default, existing, driver.
- Preserved scope reviewed: Escaped role/restaurant, existing/new-account instructions, login link and no emailed password.
- [ ] Every state and preserved interaction independently confirmed at runtime.
- Browser evidence: No screen-specific assertion.
- Backend evidence: test_notifications (16), test_staff_invite (4).
- Remaining: Composition passed; actual inbox rendering/delivery unverified.

### E3 - Stripe Connect handoff

- [x] Real implementation/data source mapped: web/src/features/admin/components/RestaurantRow.tsx, backend/app/services/stripe_service.py.
- [x] Reference family rendered. Manifest states: default, return.
- Preserved scope reviewed: Server-created Connect account link and return/sync boundary.
- [ ] Every state and preserved interaction independently confirmed at runtime.
- Browser evidence: No screen-specific assertion.
- Backend evidence: test_stripe_onboarding (5).
- Remaining: Live acceptance blocked: hosted Stripe onboarding not completed.

### E4 - Stripe payment boundary

- [x] Real implementation/data source mapped: web/src/pages/storefront/PaymentPage.tsx.
- [x] Reference family rendered. Manifest states: default, challenge.
- Preserved scope reviewed: Stripe-owned Element/challenge and supported Appearance API.
- [ ] Every state and preserved interaction independently confirmed at runtime.
- Browser evidence: No screen-specific assertion.
- Backend evidence: test_payment_reconciliation (15), test_stripe_webhook (7).
- Remaining: Live acceptance blocked: real iframe, wallet and issuer challenge not run.
