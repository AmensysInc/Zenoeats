# Zenoeats: brief for legal review

**Prepared:** 29 September 2026, for software release `v1.1.0` and the fixes after it
**For:** the lawyer reviewing Zenoeats' customer-facing policies before launch
**Status of the service:** built and tested; not yet live. No real customer
has used it, and no real money has moved.

This brief describes what the Zenoeats software actually does with people's
information and money. The descriptions come from the code, not from
intentions, so they can be relied on. Where the software and a policy page
disagree, or where something is missing, it is listed in
[section 8](#8-gaps-and-discrepancies-we-found).

---

## 1. What we are asking you to do

1. **Review the four customer-facing pages** and mark up their text:
   - Privacy Policy
   - Terms of Service
   - Refunds & Cancellations
   - Deleting your data

   Printed copies are in [`review-2026-09-29/`](review-2026-09-29/). Each page
   currently shows a "Draft — not yet reviewed by a lawyer" banner, and ends
   with a paragraph headed **"Before this page goes live"** listing the
   questions specific to that page.
2. **Answer the questions in [section 9](#9-questions-for-you).** They collect
   those per-page questions, plus the ones that span several pages.
3. **Tell us which documents are missing and must exist before launch.** We
   know of three:
   - an agreement with each restaurant, including how personal data is
     handled between us
   - a privacy notice for restaurant staff, including delivery drivers
   - possibly a cookie consent banner
4. **Return:** the marked-up text of the four pages, your answers, and a list
   of anything that must be added or changed.

Once you have approved the pages, we remove the draft banner and the "Before
this page goes live" paragraph from each.

---

## 2. Details only the business can supply

The pages contain `PLACEHOLDER` wherever one of these belongs. They are not
decided yet, and some of your answers depend on them.

| Detail | Where it appears | Notes |
|---|---|---|
| Legal entity name (company, or an individual trading as a sole proprietor) | Terms, Privacy | Not named anywhere yet |
| Trading or registered address | Terms, Privacy | |
| Contact email for privacy, deletion and complaints | Privacy, Refunds, Deleting your data | Must be a monitored mailbox with a named person behind it |
| Where the business operates: country, and state if in the US | Determines governing law and which privacy and consumer laws apply | |
| Deletion response windows | Deleting your data: "confirmation within ___ days, deletion complete within ___ days" | Suggested: confirm within 7 days, complete within 30 |

---

## 3. How Zenoeats works

**What it is.** Zenoeats is software that restaurants use to take orders
online. It is not a restaurant, a food seller or a delivery company. Each
restaurant gets its own web address (for example `spicehouse.zenoeats.com`)
with its menu, and customers order from it for pickup or delivery.

**The three kinds of user:**
- **Customers** order food. They either create an account or order as a
  guest with just an email address.
- **Restaurant staff** run the restaurant's side: owners, managers, kitchen
  staff, cashiers and delivery drivers. Each restaurant invites its own
  staff.
- **Zenoeats operators** ("super admins") set up restaurants and support
  them.

**Who sells the food, and who takes the money.**
- The **restaurant** sells the food. The contract for the food is between
  the customer and the restaurant.
- Payment is taken by Stripe, **on the restaurant's own Stripe account**, so
  the **restaurant is the merchant of record**. The money goes to the
  restaurant, and refunds and card disputes are the restaurant's.
- **Zenoeats never holds customer money.** It can take a per-order fee from
  the restaurant, collected by Stripe out of the restaurant's charge and
  never added to the customer's bill. The fee is currently set to zero.
- **Sales tax** is calculated either at a flat rate the restaurant sets, or
  by Stripe Tax on the restaurant's account. Either way, the tax is the
  restaurant's.

**An order, step by step:**
1. The customer builds a cart, gives a name, phone number, email address and
   address, and chooses pickup or delivery.
2. The server calculates the price and creates the order. **Nothing has been
   charged yet.**
3. The customer pays by card, or Apple Pay / Google Pay, in a form provided
   by Stripe. Card details go directly to Stripe and never reach Zenoeats.
4. Stripe confirms the payment to Zenoeats. Only then is the order accepted
   and sent to the kitchen.
5. **Pickup:** the customer gets a six-digit pickup code and shows it at the
   counter.
   **Delivery:** one of the restaurant's own drivers takes the order, and the
   customer can follow the driver's position on a map.
6. **An order never paid** within 30 minutes expires. Nothing is charged.

**Cancellations and refunds.**
- The restaurant can cancel a paid order. With one button ("Cancel and
  refund") the whole amount goes back to the customer's card, including any
  Zenoeats fee.
- The restaurant can also cancel **without** a refund, for example when a
  customer never collects food that was already cooked.
- Partial refunds are made in the restaurant's own Stripe dashboard.
- In every case the customer is told, by email and on their order page, the
  amount and that it reaches their card "within 10 to 14 business days".
- Customers cannot cancel an order themselves in the software; they have to
  contact the restaurant.

**Agreement to the terms.**
- **Sign-up:** a customer creating an account must tick a box agreeing to
  the Terms and Privacy Policy, both linked.
- **Checkout:** the page says that continuing means agreeing. When the order
  is placed, the software records **the time and the version of the terms**
  agreed to, for account holders and guests alike.
- **When the terms change materially,** the version number is increased, and
  everyone is asked again on their next order.

---

## 4. Whose personal information is held, and why

| Person | Information | Why | Where it is held |
|---|---|---|---|
| **Customer with an account** | Email, name, phone, address, saved favourite items; the time and version of their agreement to the terms | Orders, and filling in details next time | Zenoeats database |
| | Password, and any Google / Apple / Facebook sign-in | Signing in | **Clerk only**; Zenoeats never sees the password |
| **Guest customer** | Email address (plus the details on their orders) | Reaching their order and sending its emails | Zenoeats database. A cookie in their browser links them to their order |
| **Any customer, per order** | Name, phone, email, address, the items ordered, any note, prices, delivery address and fee | The restaurant's record of the sale; tax | Zenoeats database, visible to that restaurant only |
| | A pickup code | Proving who collects the food | Stored encrypted |
| **Restaurant staff** | Email, name, role, sign-in password (stored as a one-way hash), invitation and sign-in history | Running the restaurant's side | Zenoeats database |
| **Restaurants** | A phone number, shown to customers on their order page and in order emails | So a customer can reach the restaurant about an order | Zenoeats database |
| **Delivery drivers** (restaurant staff) | Live position from their phone, while a delivery is on the road | Showing the customer where their order is | Held briefly: each position is discarded within 10 minutes |
| | First name | Shown to the customer ("Dana is on the way") on the order page and in the "on its way" email | |
| **Zenoeats operators** | Email and a password hash, set in the server's configuration | Platform administration; what each operator did is logged | Server configuration and an audit log |

**Isolation between restaurants.** Each restaurant sees only its own orders,
customers' order details and staff. This is enforced inside the database
itself, not just by the application, and automated tests check it.

**What is not collected.** No advertising or analytics tracking, and no
marketing lists. Card numbers never reach Zenoeats.

---

## 5. Service providers that receive personal information

All of these are companies based in the United States. Where the business
and its customers are outside the US, this is an **international transfer**
(see question 7 in [section 9](#9-questions-for-you)).

| Provider | What it does | What it receives |
|---|---|---|
| **Stripe** | Payments, on each restaurant's own account | The amount, order reference, the customer's email address (used by Stripe for fraud screening) and the card details, which the customer types directly into Stripe. **Stripe is not asked to email receipts**: Zenoeats sends the emails itself |
| **Clerk** | Customer accounts and sign-in, including Google / Apple / Facebook sign-in | Email, name, password, sign-in activity |
| **Twilio SendGrid** | Sends every email Zenoeats sends (section 6) | The recipient's address and the content of each email |
| **Google Maps Platform** | Turning a delivery address into a distance; driving-time estimates; address suggestions at checkout; the tracking map | The delivery address and driver position. Also the customer's IP address, and what they type into the address box, because the suggestions and the map load from Google in the browser |
| **Sentry** (optional) | Error reports from the server | Error details with personal information removed first (pickup codes, payment identifiers, email addresses, order notes) |
| **Cloudflare** (planned) | Domain, secure connections, and the network in front of the server | All web traffic passes through it, including IP addresses |
| **A hosting provider** (not yet chosen) | The server that runs Zenoeats | Everything above is stored on it |
| **Cloudflare R2** (planned) | Nightly off-site backups of the database | Encrypted before upload; Cloudflare cannot read them |

The **restaurant** also receives each of its own orders: the customer's
name, phone, email, the order, and the delivery address. It receives nothing
about the customer's orders at any other restaurant.

---

## 6. The emails Zenoeats sends

All are **transactional**: each is about an order, a refund, an account or a
job, and none contains offers or promotions. They carry no unsubscribe link.
No marketing email is sent, and no marketing consent is collected.

| To | Email | When |
|---|---|---|
| Customer | Order confirmed | Payment succeeds |
| Customer | Ready to collect | The kitchen marks a pickup order ready |
| Customer | On its way / Delivered | The driver picks up / hands over a delivery |
| Customer | Order cancelled | The restaurant cancels a paid order. It states the refund and "10 to 14 business days" only when a refund was actually issued |
| Customer | Refund on its way | A refund made later, including a partial one |
| Customer | Welcome | A new account (under a day old) first opens a restaurant's page. The restaurant's name is on it |
| Customer | Account closed | Once, when an account is closed, confirming what was removed |
| Customer | Verification / password-reset code | Normally sent by Clerk. Can be switched to Zenoeats' own sender |
| Restaurant staff | Invitation | Includes a **temporary password**, which must be changed at first sign-in and stops working then |
| Restaurant staff | Welcome, role changed, removed from the team, password reset | As they happen. The reset email includes the new temporary password |
| Restaurant staff | "Has joined" | To the person who sent an invitation, when it is accepted |
| Restaurant admins and managers | Refund didn't go through | Stripe refused a refund after a cancellation |

**Question:** is the "Welcome" email still transactional where the business
operates? It contains no offer, but it is sent because an account was
created, not because of an order.

---

## 7. How long information is kept: what actually happens

| Information | Kept for | Notes |
|---|---|---|
| Orders, and the customer details on them | **Indefinitely** at present | The policies say "as long as the restaurant is required to keep them". No retention period is set, and nothing deletes old orders |
| Customer account details | Until the account is closed | Closing removes name, phone, address, email and favourites at once, and deletes the Clerk sign-in |
| Guest records that never became an order | About 45 days | Deleted automatically |
| Delivery address coordinates (from Google) | Up to 30 days | Google's terms allow about 30 days |
| Driver positions | Under 10 minutes each | |
| Records of messages from Stripe and Clerk | 365 days, **with personal details removed within an hour** of each being dealt with | Names, emails, phones and addresses are stripped; ids, amounts and statuses stay for audit. A message not yet dealt with keeps its details until it is. See gap 2 |
| **Backups** | **Up to 90 days** | Encrypted and off-site. A 30-day lock means no backup can be deleted early, even on request. The Privacy Policy and the deletion page now say so. See gap 2 |
| Server access logs | **Not yet limited** | Contain visitors' IP addresses. See gap 4 |
| Error reports (Sentry) | Sentry's own retention | Personal information removed before sending |
| Emails sent | No content kept | Only a record that an email about a given order was sent, so it is never sent twice |
| Staff accounts | Until removed | A removed staff member's account remains as the record of who did what on past orders |

---

## 8. Gaps and discrepancies we found

We found these while preparing this brief. Gaps 1 and 2 needed software
changes and have been made; the rest need your advice or a decision.

1. **No way to phone the restaurant: fixed.**
   - The software did not hold a phone number for a restaurant, although the
     refunds page said the number was on the restaurant's page and in the
     confirmation email.
   - **Now:** each restaurant gives a phone number, and a restaurant cannot
     go live without one. Customers see it on their order page and in every
     email about their order. The refunds page says so (section 10).

2. **Copies that outlived a closed account: fixed where possible, and
   disclosed.**
   - Closing an account removes the account details immediately. Copies of
     the customer's email and name remained in two places:
     - **Records of messages from Stripe and Clerk**, kept for 365 days.
       **Fixed:** personal details are now stripped from every such record
       within an hour of it being dealt with. Only ids, amounts and statuses
       remain.
     - **Backups**, for up to 90 days, which cannot be deleted early (a
       30-day lock protects them from tampering). **Disclosed:** the
       deletion page and the Privacy Policy now say that encrypted backups
       hold the details until each is deleted, within 90 days, and are used
       only to restore the service.
   - Is 90 days acceptable, and is the wording adequate?

3. **Orders are kept forever.** What retention period does tax and
   accounting law require for a restaurant's sales records, and must
   personal details be removed from orders after that?

4. **Server logs with IP addresses have no retention limit yet.** We will set
   one. What period is appropriate?

5. **No privacy notice for restaurant staff.**
   - Staff, including delivery drivers, are not customers, and the Privacy
     Policy is written for customers.
   - Drivers' phones report their position during deliveries, and their
     first name is shown to customers.
   - Who gives these people a privacy notice: the restaurant as their
     employer, or Zenoeats?

6. **No restaurant agreement yet.** It needs to cover at least:
   - Zenoeats' fee
   - merchant-of-record duties
   - who bears the cost of refunds and chargebacks (this must agree with the
     refunds page)
   - sales tax responsibility
   - the accuracy of menu and allergen information
   - drivers and their employment
   - how personal data is shared between Zenoeats and the restaurant: which
     of them controls it, and whether one processes it for the other
   - how refunds are issued: a full refund from the kitchen board, anything
     else from the restaurant's own Stripe dashboard

7. **No privacy-law-specific sections.** The Privacy Policy has no section on
   legal bases for processing, no statement of rights in the form a
   particular law requires (for example UK/EU GDPR), no CCPA notice, and no
   statement about international transfers.

8. **Customers cannot cancel a paid order themselves.** The refunds page
   says a paid order "generally cannot be cancelled". Consumer law may give
   a right that contradicts this.

9. **Allergens.** The terms and every customer page carry a general
   allergen warning ("kitchens handle allergens, cross-contact cannot be
   ruled out, contact the restaurant"). Is a statutory format required?

10. **Children.** The Privacy Policy says the service is "not intended for
    children". There is no age check.

11. **Cookies.**
    - Zenoeats sets only cookies needed to run the site: sign-in sessions
      for staff and operators, and the guest-order cookie.
    - Clerk and Stripe set their own cookies for sign-in, payment and fraud
      prevention.
    - Google's scripts load for the address suggestions and the tracking
      map.
    - Is a consent banner required where the business operates?

12. **The "email us to delete" route is manual.** A person must check the
    request, then delete the account. The response windows on the page are a
    commitment that needs a monitored mailbox.

13. **Temporary passwords are sent by email to staff.** This is a deliberate,
    accepted risk: the password must be replaced at first sign-in and stops
    working then. Does it raise any legal concern?

14. **Sign-in with Google, Apple and Facebook.** Each provider requires
    public links to the privacy policy (and, for Facebook, to data deletion
    instructions) on the business's main domain before it will approve the
    sign-in. The pages are built to be served there.

---

## 9. Questions for you

**Law and jurisdiction**
1. Which law governs, and which consumer protection and privacy laws apply,
   given where the business operates?
2. What dispute-resolution and jurisdiction clause should the terms have?

**Roles and agreements**

3. Given how money and data flow (section 3), is each restaurant a separate
   controller of its customers' data, or is Zenoeats processing it on the
   restaurant's behalf, or the reverse? What must the restaurant agreement
   say as a result?
4. Are the terms' allocation of responsibility (food and delivery are the
   restaurant's; the software is Zenoeats') and the limitation of liability
   enforceable, and do they need to mirror the restaurant agreement?
5. Who owes restaurant staff, especially delivery drivers, a privacy notice?

**Privacy Policy**

6. What does the Privacy Policy need to add for the applicable law: legal
   bases, a rights section, how to complain to a regulator?
7. How should the transfers to US providers (section 5) be disclosed and
   made lawful?
8. Is naming the providers as in section 5 enough, or is a separate list of
   service providers needed?
9. Is a cookie consent banner required, given the cookies in gap 11?
10. Does the children statement need an age check behind it?

**Retention and deletion**

11. What retention period applies to orders and the customer details on
    them (gap 3)?
12. Is the deletion page's account of backups (kept up to 90 days) and of
    the records of messages (details stripped within an hour) adequate
    (gap 2)?
13. Are 7 days to confirm and 30 days to complete a deletion request
    acceptable (section 2)?
14. Is keeping paid orders, with the contact details on them, after an
    account is closed justified, and is the page's explanation adequate?

**Refunds and cancellations**

15. What cancellation rights do consumers have for prepared food, and does
    "a paid order generally cannot be cancelled" need to change?
16. Is the refund timing statement ("within 10 to 14 business days")
    acceptable as worded? Card refunds normally show within that time, but
    it depends on the customer's bank.
17. Does the "if you cannot reach the restaurant" route commit Zenoeats to a
    response time or to acting for the customer?

**Terms**

18. Does the allergen warning need a statutory form (gap 9)?
19. Is agreement by "continuing means agreeing" at checkout, recorded with
    time and version, sufficient, alongside the checkbox at sign-up?

**Emails**

20. Is the Welcome email transactional (section 6)?
21. Any concern with emailing temporary passwords to staff (gap 13)?

---

## 10. What we corrected before sending

We brought these statements into line with the software. They are factual
corrections only; nothing was changed that is a legal judgement.

**Privacy Policy**
- **Stripe:** said Stripe receives the email address "for the receipt". It
  now says Stripe uses it for fraud screening and does not email receipts;
  Zenoeats sends the emails.
- **SendGrid:** said it sends "the emails about your orders". It now lists
  every kind (order updates, refunds, account notices and, where set up,
  sign-in codes) and says SendGrid receives each email's content.
- **Google:** now also says Google receives the customer's IP address and
  what they type into the address box, because the suggestions and the map
  load from Google.
- **Driver positions:** said they are not kept after a delivery. It now says
  each is discarded within 10 minutes of being reported, which is what
  happens.
- **Retention:** now lists the records of messages from Stripe and Clerk
  (details stripped within an hour) and backups (up to 90 days) (gap 2).

**Deleting your data**
- **What gets deleted:** now says that two copies take longer: the records
  of messages, cleaned within an hour, and encrypted backups, deleted within
  90 days (gap 2).

**Refunds & Cancellations**
- **Restaurant contact:** said the restaurant's phone number was on its page
  and in the confirmation email, when the software held none. The software
  now requires one (gap 1), and the page says it is on the order page and in
  every order email.
- **Note for reviewers:** said the software has no refund button. It now has
  one, and the note describes how refunds are issued.

---

## 11. Where things are

- **The four pages as sent to you:** PDFs in
  [`review-2026-09-29/`](review-2026-09-29/).
- **The pages in the software:** `web/legal/privacy.html`, `terms.html`,
  `refunds.html` and `data-deletion.html`.
- **Once live**, they are at `https://<domain>/legal/privacy`, `/legal/terms`,
  `/legal/refunds` and `/legal/data-deletion` on the main domain and on every
  restaurant's address.
- **The launch checklist:** `docs/operations/STEPS_BEFORE_PRODUCTION.md`,
  §9 "Legal and business".
- **The security review** (useful for the "Security" section of the Privacy
  Policy): `docs/security/SECURITY_AUDIT_REPORT.md`.
