import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAppSelector } from "@/app/hooks";
import {
  selectCartCombos,
  selectCartLines,
} from "@/features/cart/cartSlice";
import {
  useCreateOrderMutation,
  useCreatePaymentIntentMutation,
  useCustomerSessionQuery,
  usePortalQuery,
  useQuoteMutation,
} from "@/features/storefront/storefrontApi";
import { useOpenCart } from "@/features/cart/useOpenCart";
import { ApiError, errorMessage, newIdempotencyKey } from "@/services/apiClient";
import { money } from "@/utils/format";
import { ErrorNote, Loading, Spinner, StatePage } from "@/components/common/Feedback";
import { Cloche, Icon } from "@/components/common/icons";
import { CartLines } from "@/features/cart/components/CartLines";
import { CustomerAccountBar } from "@/features/storefront/components/CustomerAccountBar";
import { CustomerHeader } from "@/features/storefront/components/CustomerHeader";
import { ContactFields } from "@/features/storefront/components/ContactFields";
import {
  CONTACT_FIELDS,
  collapse,
  contactErrors,
  contactInputId,
} from "@/features/storefront/contact";
import type { PaymentHandoff } from "./PaymentPage";
import type { Amounts, Contact, FulfillmentType } from "@/types";
import { readCheckoutDraft, saveCheckoutDraft } from "@/features/storefront/checkoutDraft";
import { loadGooglePlaces, type PlacesAutocomplete } from "@/services/googleMaps";
import { BackLink } from "@/components/common/BackLink";

/** The answers a delivery quote gives about the address rather than the
 *  cart. They belong beside the address field, where retyping can fix them. */
const ADDRESS_PROBLEMS = new Set([
  "OUT_OF_DELIVERY_RANGE",
  "ADDRESS_NOT_FOUND",
  "DELIVERY_CHECK_UNAVAILABLE",
  "DELIVERY_NOT_OFFERED",
]);

/** How long typing has to pause before an address is priced. Each check is a
 *  geocoding lookup, so not one per keystroke. */
const ADDRESS_SETTLE_MS = 700;

/** What a quote was priced for, so payment can tell a total for this address
 *  from one for the address as it was a moment ago. */
function pricingKey(fulfillment: FulfillmentType, address: string): string {
  return fulfillment === "DELIVERY" ? `DELIVERY:${collapse(address)}` : "PICKUP";
}

/**
 * One idempotency key per distinct checkout attempt, reused across retries, so
 * a network hiccup can never create a second order for the same intent.
 *
 * It lives in sessionStorage rather than a ref because payment is now its own
 * URL: pressing Back unmounts this page, and a ref would come back empty and
 * open a second order for a cart the customer never changed. Keyed by the
 * exact request body, because that is what the server hashes -- so an edited
 * note or a re-quoted total correctly earns a new key instead of colliding
 * with the old one.
 */
const ATTEMPT_STORAGE_KEY = "zenoeats:checkout-attempt";

function attemptKeyFor(signature: string): string {
  try {
    const raw = sessionStorage.getItem(ATTEMPT_STORAGE_KEY);
    if (raw) {
      const saved = JSON.parse(raw) as { signature?: string; key?: string };
      if (saved.signature === signature && saved.key) return saved.key;
    }
  } catch {
    // A private window, or storage the browser refuses. A fresh key is still
    // correct; it only costs the customer a duplicate pending order if they
    // navigate back, and the TTL sweep expires that without charging anything.
  }
  const key = newIdempotencyKey();
  try {
    sessionStorage.setItem(ATTEMPT_STORAGE_KEY, JSON.stringify({ signature, key }));
  } catch {
    // Same as above: unstored is survivable, unkeyed is not.
  }
  return key;
}

export function CheckoutPage() {
  const navigate = useNavigate();

  // Restores the saved cart. Checkout is reached by a full navigation back
  // from sign-in, so without this the store is empty and the customer is told
  // their cart is, too.
  useOpenCart();

  const portal = usePortalQuery();
  // Already loaded by the guard in front of this page, so this is a cache read.
  const session = useCustomerSessionQuery();
  const lines = useAppSelector(selectCartLines);
  const combos = useAppSelector(selectCartCombos);

  const [amounts, setAmounts] = useState<Amounts | null>(null);
  const [note, setNote] = useState("");
  const [guestEmail, setGuestEmail] = useState("");
  const [fulfillment, setFulfillment] = useState<FulfillmentType>("PICKUP");
  const [contact, setContact] = useState<Contact>({ full_name: "", phone: "", address: "" });
  // Mistakes are named once the customer has tried to continue, not while
  // they are still typing their name.
  const [showErrors, setShowErrors] = useState(false);
  // Why the address cannot be delivered to, from the quote.
  const [addressProblem, setAddressProblem] = useState<string | null>(null);
  // Which kind of problem, so the delivery panel can offer the fix that fits:
  // another try when the lookup itself failed, pick-up when it cannot deliver.
  const [problemCode, setProblemCode] = useState<string | null>(null);
  // Bumped by "Check again", to run the same quote once more.
  const [retry, setRetry] = useState(0);
  const [deliveryMiles, setDeliveryMiles] = useState<number | null>(null);
  const [pricedFor, setPricedFor] = useState<string | null>(null);
  const [settledAddress, setSettledAddress] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submitting = useRef(false);
  const addressInput = useRef<HTMLInputElement>(null);
  const [draftReady, setDraftReady] = useState(false);
  // A quote in flight. The last answer stays on screen, dimmed, and payment
  // waits for the fresh one rather than sending a total the server is about
  // to contradict.
  const [quoting, setQuoting] = useState(false);

  const [quote] = useQuoteMutation();
  const [createOrder] = useCreateOrderMutation();
  const [createPaymentIntent] = useCreatePaymentIntentMutation();

  const payload = useMemo(
    () =>
      lines.map((l) => ({
        menu_item_id: l.menu_item_id,
        quantity: l.quantity,
        note: l.note,
        modifiers: l.modifiers.map((m) => ({ option_id: m.option_id, quantity: m.quantity })),
      })),
    [lines],
  );

  // Which deal and which choices. No price, here or anywhere else: the
  // server reads what a combo costs from the menu, on the quote and again on
  // the order.
  const comboPayload = useMemo(
    () =>
      combos.map((c) => ({
        combo_id: c.combo_id,
        quantity: c.quantity,
        note: c.note,
        selections: c.selections.map((s) => ({
          slot_id: s.slot_id,
          menu_item_id: s.menu_item_id,
          modifiers: s.modifiers.map((m) => ({
            option_id: m.option_id,
            quantity: m.quantity,
          })),
        })),
      })),
    [combos],
  );

  const empty = lines.length === 0 && combos.length === 0;
  const delivering = fulfillment === "DELIVERY";
  const errors = contactErrors(contact, delivering);
  const emailError = session.data?.is_guest && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(guestEmail.trim())
    ? "Enter an email address for your receipt." : undefined;

  // Filled from what the last order saved, once. Never again after that, so a
  // background refetch cannot overwrite something the customer is typing.
  const prefilled = useRef(false);
  useEffect(() => {
    if (prefilled.current || !session.data || !portal.data) return;
    prefilled.current = true;
    const saved = readCheckoutDraft(portal.data.slug, session.data);
    setContact(saved?.contact ?? {
      full_name: session.data.full_name ?? "",
      phone: session.data.phone ?? "",
      address: session.data.address ?? "",
    });
    setFulfillment(portal.data.delivery_offered ? saved?.fulfillment ?? "PICKUP" : "PICKUP");
    setNote(saved?.note ?? "");
    setGuestEmail(saved?.email ?? session.data.email);
    setDraftReady(true);
  }, [session.data, portal.data]);

  useEffect(() => {
    if (draftReady && portal.data && session.data)
      saveCheckoutDraft(portal.data.slug, session.data, { contact, fulfillment, note, ...(session.data.is_guest ? { email: guestEmail } : {}) });
  }, [draftReady, portal.data, session.data, contact, fulfillment, note, guestEmail]);

  // A restaurant that stops delivering mid-checkout takes the choice with it.
  useEffect(() => {
    if (portal.data && !portal.data.delivery_offered) setFulfillment("PICKUP");
  }, [portal.data]);

  // Suggestions are optional. If Google or the Places API is unavailable,
  // manual entry and the authoritative server-side address check still work.
  useEffect(() => {
    const key = portal.data?.maps_browser_key;
    if (!delivering || !key || !addressInput.current) return;
    let alive = true;
    let autocomplete: PlacesAutocomplete | null = null;
    let listener: { remove(): void } | null = null;
    loadGooglePlaces(key)
      .then((places) => {
        if (!alive || !addressInput.current) return;
        autocomplete = new places.Autocomplete(addressInput.current, {
          fields: ["formatted_address"],
          types: ["address"],
        });
        listener = autocomplete.addListener("place_changed", () => {
          const selected = autocomplete?.getPlace().formatted_address?.trim();
          if (!selected) return;
          setAddressProblem(null);
          setProblemCode(null);
          setContact((current) => ({ ...current, address: selected }));
        });
      })
      .catch(() => undefined);
    return () => {
      alive = false;
      listener?.remove();
    };
  }, [delivering, portal.data?.maps_browser_key]);

  useEffect(() => {
    const timer = setTimeout(() => setSettledAddress(collapse(contact.address)), ADDRESS_SETTLE_MS);
    return () => clearTimeout(timer);
  }, [contact.address]);

  // Priced for delivery only once there is an address worth looking up.
  // Until then the quote is the food alone, and the fee row says what it is
  // waiting for.
  const priceDelivery = delivering && settledAddress.length >= 5;

  const cartSignature = JSON.stringify([payload, comboPayload]);
  const requestedKey = cartSignature + ":" + pricingKey(fulfillment, contact.address);

  // Authoritative pricing. Whatever the cart displayed locally is a guess until
  // the server answers.
  useEffect(() => {
    if (empty) {
      setAmounts(null);
      return;
    }
    // Abandon an old address immediately, including during debounce.
    if (delivering && collapse(contact.address) !== settledAddress) {
      setPricedFor(null);
      setQuoting(false);
      return;
    }
    let alive = true;
    setQuoting(true);
    const key = cartSignature + ":" + (priceDelivery ? pricingKey("DELIVERY", settledAddress) : "PICKUP");
    const request = quote({
      items: payload,
      combos: comboPayload,
      ...(priceDelivery
        ? { fulfillment_type: "DELIVERY" as const, delivery_address: settledAddress }
        : {}),
    });
    request.unwrap()
      .then((q) => {
        if (!alive) return;
        setAmounts(q.amounts);
        setDeliveryMiles(q.delivery_miles);
        setPricedFor(key);
        setAddressProblem(null);
        setProblemCode(null);
        setError(null);
      })
      .catch((e) => {
        if (!alive) return;
        setPricedFor(null);
        if (priceDelivery && e instanceof ApiError && ADDRESS_PROBLEMS.has(e.code ?? "")) {
          setAddressProblem(e.message);
          setProblemCode(e.code ?? null);
        } else {
          setError(errorMessage(e));
        }
      })
      .finally(() => {
        if (alive) setQuoting(false);
      });
    return () => {
      alive = false;
      request.abort();
    };
  }, [payload, comboPayload, cartSignature, empty, quote, priceDelivery, settledAddress, retry, delivering, contact.address]);

  // The total on screen is for exactly what is being ordered: this choice,
  // and for a delivery this address as typed, not as it was a moment ago.
  const priced = pricedFor !== null && pricedFor === requestedKey;
  const checkingAddress = delivering && !errors.address && !addressProblem && !priced;

  /** Name every mistake and put the cursor on the first. */
  function refuseDetails(): boolean {
    const first = CONTACT_FIELDS.filter(field => field !== "address").find(field => errors[field]);
    const invalidId = first ? contactInputId(first) : emailError ? "contact-email"
      : errors.address || (delivering && addressProblem) ? contactInputId("address") : null;
    if (!invalidId) return false;
    setShowErrors(true);
    document.getElementById(invalidId)?.focus();
    return true;
  }

  async function startPayment() {
    if (!amounts || submitting.current || quoting) return;
    if (!portal.data?.is_orderable || !portal.data.accepting_orders) return;
    if (refuseDetails()) return;
    if (session.data?.email_pending) {
      setError("We are still confirming your email address. Try again in a moment.");
      return;
    }
    if (!priced) return;
    submitting.current = true;
    setBusy(true);
    setError(null);

    try {
      const body = {
        items: payload,
        combos: comboPayload,
        customer_note: note.trim() || null,
        ...(session.data?.is_guest ? { guest_email: guestEmail.trim().toLowerCase() } : {}),
        contact: {
          full_name: collapse(contact.full_name),
          phone: collapse(contact.phone),
          address: delivering ? collapse(contact.address) : "",
        },
        fulfillment_type: fulfillment,
        expected_total_minor: amounts.total_minor,
      };
      const key = attemptKeyFor(JSON.stringify(body));

      // Step 1: the order row exists before any charge is attempted. If the
      // customer walks away now, the TTL sweep expires it and nothing was
      // ever charged.
      const order = await createOrder({ ...body, idempotencyKey: key }).unwrap();

      // Step 2: PaymentIntent on the restaurant's connected account.
      const intent = await createPaymentIntent({
        orderId: order.order_id,
        idempotencyKey: `${key}-pi`,
      }).unwrap();

      // Step 3: payment is its own URL, so it gets a real navigation with its
      // own history entry. Everything the payment page needs travels in router
      // state, which keeps the client secret out of the address bar; it
      // re-requests the intent by order id if that state is lost to a reload.
      const handoff: PaymentHandoff = {
        clientSecret: intent.client_secret,
        stripeAccountId: intent.stripe_account_id,
        publishableKey: intent.publishable_key,
        customerEmail: intent.customer_email ?? null,
        totalMinor: amounts.total_minor,
        fulfillment,
        deliveryAddress: delivering ? collapse(contact.address) : null,
      };
      navigate(`/checkout/pay/${order.order_id}`, { state: handoff });
    } catch (e) {
      if (e instanceof ApiError && e.code === "PRICE_CHANGED") {
        setPricedFor(null);
        setRetry(n => n + 1);
        setError("Prices changed while you were ordering. Check the refreshed total before continuing.");
      } else if (e instanceof ApiError && e.code === "ITEM_UNAVAILABLE") {
        setError("Something in your cart just sold out. Remove it and try again.");
      } else if (e instanceof ApiError && ADDRESS_PROBLEMS.has(e.code ?? "")) {
        setAddressProblem(e.message);
        setProblemCode(e.code ?? null);
        setShowErrors(true);
        document.getElementById(contactInputId("address"))?.focus();
      } else {
        setError(errorMessage(e));
      }
    } finally {
      submitting.current = false;
      setBusy(false);
    }
  }

  if (!portal.data) {
    if (portal.error) {
      return <StatePage title="Checkout is unavailable">{errorMessage(portal.error)}</StatePage>;
    }
    return <StatePage busy>Loading…</StatePage>;
  }

  // Bound to a const after the guard above: narrowing on portal.data does not
  // survive into the callbacks below, because it is a property of a mutable
  // object rather than a local.
  const restaurant = portal.data;

  if (empty) {
    return (
      <StatePage
        title="Your cart is empty"
        illustration={<Cloche />}
        action={
          <button type="button" className="btn-primary" onClick={() => navigate("/")}>
            Back to the menu
          </button>
        }
      />
    );
  }

  const currency = restaurant.currency;
  const itemCount =
    lines.reduce((n, l) => n + l.quantity, 0) + combos.reduce((n, c) => n + c.quantity, 0);
  // A delivery total is only a total once this address has been priced. Until
  // then the food is known and the rest is not, so the rest says so.
  const pending = delivering && !priced;

  return (
    <div className="flex min-h-dvh flex-col">
      <CustomerHeader restaurant={restaurant} />
      <main className="mx-auto w-full max-w-[720px] px-5 pb-[50px] pt-3 sm:px-6 sm:pb-[70px] sm:pt-6 lg:max-w-[1040px]">
        <CustomerAccountBar />
        <BackLink />
        <h1 className="mt-[18px] font-display text-[34px] leading-[1.12] tracking-[-1px] [overflow-wrap:anywhere] sm:mt-5 sm:text-[40px]">
          {delivering ? `Delivery from ${restaurant.name}` : `Pick up from ${restaurant.name}`}
        </h1>
        <p className="mb-7 mt-3 text-muted">
          {restaurant.delivery_offered
            ? "Choose pick-up or delivery, then check your details."
            : "Check your details and your order, then continue to payment."}
        </p>

        <div className="grid grid-cols-1 items-start gap-7 lg:grid-cols-[minmax(0,1.45fr)_minmax(0,1fr)] lg:gap-10">
          <div className="min-w-0">
            {(
              <fieldset className="mb-6" disabled={busy}>
                <legend className="mb-4 text-lg font-bold sm:text-xl">How would you like your order?</legend>
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                  <FulfillmentChoice
                    value="PICKUP"
                    checked={!delivering}
                    icon="store"
                    title="Pick-up"
                    detail={restaurant.pickup_address ?? ("Collect at " + restaurant.name)}
                    onSelect={() => setFulfillment("PICKUP")}
                  />
                  <FulfillmentChoice
                    value="DELIVERY"
                    checked={delivering}
                    icon="bag"
                    title="Delivery"
                    detail={restaurant.delivery_offered ? "Delivered to your door" : "Delivery is unavailable right now"}
                    disabled={!restaurant.delivery_offered}
                    onSelect={() => setFulfillment("DELIVERY")}
                  />
                </div>
              </fieldset>
            )}

            {/* Pickup needs contact details only; delivery also needs the
                destination used for its authoritative server-side quote. */}
            <section
              className="rounded-banner border border-hairline bg-surface p-5 sm:p-6"
              aria-labelledby="details-heading"
            >
              <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
                <h2 id="details-heading" className="eyebrow text-muted">
                  {delivering ? "Contact and delivery details" : "Contact details"}
                </h2>
                {session.data && !session.data.is_guest && (
                  <Link to="/profile" className="btn-quiet btn-compact rounded-full">
                    <Icon name="edit" className="h-4 w-4" />
                    Manage profile
                  </Link>
                )}
              </div>
              <ContactFields
                contact={contact}
                onChange={(next) => {
                  if (collapse(next.address) !== collapse(contact.address)) {
                    setAddressProblem(null);
                    setProblemCode(null);
                  }
                  setContact(next);
                }}
                errors={{
                  ...(showErrors ? errors : {}),
                  // Where we cannot deliver is worth saying straight away.
                  ...(delivering && addressProblem && !(showErrors && errors.address)
                    ? { address: addressProblem }
                    : {}),
                }}
                email={session.data?.is_guest ? guestEmail : session.data?.email ?? ""}
                onEmailChange={session.data?.is_guest ? setGuestEmail : undefined}
                emailError={showErrors ? emailError : undefined}
                emailHint={
                  session.data?.email_pending
                    ? "Confirming your email address…"
                    : session.data?.is_guest
                      ? "Your receipt and order updates go here."
                      : "The email on your account. Receipts go here."
                }
                addressLabel={delivering ? "Delivery address" : "Address"}
                showAddress={delivering}
                addressAutocomplete={delivering && !!restaurant.maps_browser_key}
                addressInputRef={addressInput}
                addressHint={
                  !delivering
                    ? undefined
                    : priced && deliveryMiles !== null
                      ? `About ${deliveryMiles.toFixed(1)} miles from ${restaurant.name}.`
                      : restaurant.maps_browser_key
                        ? "Start typing, then choose an address from Google's suggestions."
                        : "Street, city and ZIP code, so we can check we deliver there."
                }
                disabled={busy}
              />

              {delivering && (
                <DeliveryStatus
                  addressMissing={!!errors.address}
                  checking={checkingAddress}
                  priced={priced}
                  feeMinor={amounts?.delivery_fee_minor ?? null}
                  currency={currency}
                  problem={addressProblem}
                  canRetry={problemCode === "DELIVERY_CHECK_UNAVAILABLE"}
                  disabled={busy}
                  onRetry={() => {
                    setAddressProblem(null);
                    setProblemCode(null);
                    setRetry((n) => n + 1);
                  }}
                  onPickup={() => setFulfillment("PICKUP")}
                />
              )}
            </section>

            <h2 className="mb-2 mt-9 font-display text-[26px] leading-[1.2] tracking-[-.5px]">
              Your items
              <span className="sr-only">, {itemCount}</span>
            </h2>
            {/* The same rows the cart page draws, from the same component:
                a quantity changed in one place and not the other would be two
                carts, and only one of them real. */}
            <CartLines lines={lines} combos={combos} currency={currency} />
          </div>

          <aside className="rounded-banner border border-hairline bg-surface p-5 shadow-raised sm:p-6 lg:sticky lg:top-6">
            <p className="eyebrow text-muted">Your order</p>

            {/* Nothing until the server has priced the order: a total from the
                browser would be a guess. On a re-quote the last answer stays,
                dimmed, and is replaced outright when the new one lands. */}
            {!amounts ? (
              <div className="mt-6">
                <Loading>Working out your total…</Loading>
              </div>
            ) : (
              <dl
                className={`tnum my-6 flex flex-col gap-2.5 text-sm transition-opacity duration-color ${
                  quoting ? "opacity-[.48]" : ""
                }`}
                aria-busy={quoting}
              >
                <Row label="Subtotal" value={money(amounts.subtotal_minor, currency)} />
                {amounts.discount_minor > 0 && (
                  <Row
                    label="Discount"
                    value={`−${money(amounts.discount_minor, currency)}`}
                    valueClass="text-brick"
                  />
                )}
                <Row
                  label="Tax"
                  value={pending ? "Pending" : money(amounts.tax_minor, currency)}
                  valueClass={pending ? "text-muted" : ""}
                />
                {delivering && (
                  <Row
                    label="Delivery fee"
                    value={
                      priced
                        ? amounts.delivery_fee_minor === 0
                          ? "Free"
                          : money(amounts.delivery_fee_minor, currency)
                        : addressProblem
                          ? "Unavailable"
                          : errors.address
                            ? "Add your address"
                            : "Checking…"
                    }
                    valueClass={priced ? "" : "text-muted"}
                  />
                )}
                <div className="mt-2 flex items-baseline justify-between gap-6 border-t border-hairline pt-4 text-[23px] font-[650]">
                  <dt>Total</dt>
                  <dd className={pending ? "text-lg text-muted" : ""}>
                    {pending ? "Pending" : money(amounts.total_minor, currency)}
                  </dd>
                </div>
              </dl>
            )}
            {amounts && quoting && (
              <p className="-mt-3 mb-4 text-caption text-muted" role="status">
                Updating your total…
              </p>
            )}

            <label className={`block ${amounts ? "" : "mt-6"}`}>
              <span className="label">Anything else?</span>
              <textarea
                className="field mt-[7px]"
                value={note}
                maxLength={500}
                placeholder={delivering ? "Gate code, where to leave it" : "Pickup time, allergies"}
                onChange={(e) => setNote(e.target.value)}
              />
            </label>

            <ErrorNote message={error} className="mt-4" />
            {error && !priced && !quoting && (
              <button type="button" className="btn-quiet mt-3 w-full" disabled={busy}
                onClick={() => setRetry(n => n + 1)}>Check total again</button>
            )}
            {!restaurant.is_orderable && (
              <p className="note-warning mt-3" role="status">
                Not taking orders right now. You can still look at the menu.
              </p>
            )}

            <button
              type="button"
              className="btn-primary mt-6 min-h-[50px] w-full rounded-full"
              // Not while the address stands refused: the panel above offers
              // the ways on. An address not yet typed stays pressable, so the
              // press can name the missing field and put the cursor in it.
              disabled={
                busy ||
                !amounts ||
                quoting ||
                checkingAddress ||
                (delivering && !!addressProblem) ||
                (!priced && !errors.address) ||
                !restaurant.is_orderable ||
                !restaurant.accepting_orders
              }
              onClick={startPayment}
            >
              {busy ? (
                <>
                  <Spinner />
                  Setting up payment…
                </>
              ) : (
                <>
                  Continue to payment
                  <Icon name="arrow" />
                </>
              )}
            </button>
            <p className="field-hint">Line prices are previews. Your total is confirmed before payment.</p>
            {/* Shown where the order is actually placed, not only in a policy
                nobody opens. A paid order is generally too late to cancel --
                the kitchen starts on it -- and the restaurant, not Zenoeats,
                issues any refund, so both belong in front of the button. */}
            <p className="field-hint mt-3">
              By continuing you agree to our{" "}
              <a href="/legal/terms" target="_blank" rel="noopener" className="underline">terms</a>{" "}
              and{" "}
              <a href="/legal/refunds" target="_blank" rel="noopener" className="underline">refund policy</a>.
              Once the kitchen starts your order it usually cannot be cancelled, and
              refunds are issued by {restaurant.name}.
            </p>
          </aside>
        </div>
      </main>
    </div>
  );
}

/**
 * Pick-up or delivery, as a native radio inside a card: the whole card is
 * the label, so it is one tap target and one choice for a screen reader.
 */
function FulfillmentChoice({
  value,
  checked,
  icon,
  title,
  detail,
  disabled = false,
  onSelect,
}: {
  value: FulfillmentType;
  checked: boolean;
  icon: "store" | "bag";
  title: string;
  detail: string;
  disabled?: boolean;
  onSelect: () => void;
}) {
  return (
    <label
      className={`flex min-h-[88px] cursor-pointer items-center gap-3.5 rounded-product border-2 bg-surface px-[19px] py-4 transition-colors duration-color ease-standard has-[:disabled]:cursor-not-allowed has-[:disabled]:opacity-[.6] has-[:focus-visible]:outline has-[:focus-visible]:outline-[3px] has-[:focus-visible]:outline-offset-[3px] has-[:focus-visible]:outline-focus ${
        checked ? "border-brick bg-brickSoft/70" : "border-hairline hover:border-[#AFC0A3]"
      }`}
    >
      <input
        type="radio"
        name="fulfillment"
        value={value}
        checked={checked}
        disabled={disabled}
        onChange={onSelect}
        className="h-5 w-5 shrink-0 focus-visible:outline-none"
      />
      <Icon name={icon} className="h-5 w-5 shrink-0 text-brick" />
      <span className="min-w-0">
        <span className="block text-[17px] font-bold">{title}</span>
        <span className="block text-caption text-muted [overflow-wrap:anywhere]">{detail}</span>
      </span>
    </label>
  );
}

/**
 * Whether this address can be delivered to, and what it costs, in words
 * beside the address. The quote is the only source: nothing here is decided
 * in the browser. Where it cannot deliver, the two ways on are offered --
 * another try when the lookup itself failed, or collecting instead.
 */
function DeliveryStatus({
  addressMissing,
  checking,
  priced,
  feeMinor,
  currency,
  problem,
  canRetry,
  disabled,
  onRetry,
  onPickup,
}: {
  addressMissing: boolean;
  checking: boolean;
  priced: boolean;
  feeMinor: number | null;
  currency: string;
  problem: string | null;
  canRetry: boolean;
  disabled: boolean;
  onRetry: () => void;
  onPickup: () => void;
}) {
  let body;
  if (problem) {
    // The server's own sentence is already beside the address field; this
    // names the situation and offers the ways on.
    body = (
      <div className="rounded-field border-l-[3px] border-warning bg-warningSoft px-4 py-3.5 text-sm text-warning">
        <p className="flex items-center gap-2 font-semibold">
          <Icon name="warning" className="h-4 w-4 shrink-0" />
          {canRetry ? "We couldn't check that address" : "Delivery isn't available to this address"}
        </p>
        <div className="mt-2.5 flex flex-wrap items-center gap-x-5 gap-y-1">
          {canRetry && (
            <button type="button" className="link text-warning hover:text-warning" disabled={disabled} onClick={onRetry}>
              Check again
            </button>
          )}
          <button type="button" className="link text-warning hover:text-warning" disabled={disabled} onClick={onPickup}>
            Switch to pick-up
          </button>
        </div>
      </div>
    );
  } else if (priced && feeMinor !== null) {
    body = (
      <p className="flex items-start gap-2.5 text-sm">
        <Icon name="check" className="mt-0.5 h-4 w-4 shrink-0 text-success" />
        <span>
          <strong className="block font-semibold text-success">Delivery available</strong>
          {feeMinor === 0
            ? "Free delivery to this address."
            : `Delivery fee: ${money(feeMinor, currency)} · included in your total.`}
        </span>
      </p>
    );
  } else if (checking && !addressMissing) {
    body = (
      <p className="flex items-center gap-2.5 text-sm text-muted">
        <Spinner />
        Checking we deliver there…
      </p>
    );
  } else {
    body = (
      <p className="text-caption text-muted">
        Add your delivery address to see whether we deliver there and what it costs.
      </p>
    );
  }

  return (
    <div className="mt-5 border-t border-hairline pt-4" role="status" aria-live="polite">
      {body}
    </div>
  );
}

function Row({ label, value, valueClass = "" }: { label: string; value: string; valueClass?: string }) {
  return (
    <div className="flex justify-between gap-6">
      <dt>{label}</dt>
      <dd className={valueClass}>{value}</dd>
    </div>
  );
}
