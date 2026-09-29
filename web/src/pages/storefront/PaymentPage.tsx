import { useEffect, useMemo, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import { Elements, PaymentElement, useElements, useStripe } from "@stripe/react-stripe-js";
import { loadStripe, type Appearance } from "@stripe/stripe-js";
import { useAppDispatch } from "@/app/hooks";
import { ErrorNote, Loading, Spinner, StatePage } from "@/components/common/Feedback";
import { Icon } from "@/components/common/icons";
import { cartCleared } from "@/features/cart/cartSlice";
import { useOpenCart } from "@/features/cart/useOpenCart";
import {
  useCreatePaymentIntentMutation,
  useOrderQuery,
  usePortalQuery,
} from "@/features/storefront/storefrontApi";
import { ApiError, errorMessage, newIdempotencyKey } from "@/services/apiClient";
import { clearCheckoutDrafts } from "@/features/storefront/checkoutDraft";
import { money } from "@/utils/format";
import { CustomerHeader } from "@/features/storefront/components/CustomerHeader";
import type { FulfillmentType } from "@/types";

/**
 * What /checkout hands over, so the ordinary path costs no extra request.
 *
 * Router state survives Back and Forward but not a reload, and it is arbitrary
 * data -- a stale history entry from an older build, or one typed by hand --
 * so it is validated here and treated as a fast path, never as the only path.
 */
type IntentBundle = {
  clientSecret: string;
  /** The restaurant's own connected account. Stripe.js is initialised with it,
   *  or the client secret for a direct charge will not resolve. */
  stripeAccountId: string;
  publishableKey: string;
  /** Sent with the card as its billing email, which Stripe screens for
   *  fraud. Stripe sends no receipt: Zenoeats emails the confirmation. */
  customerEmail?: string | null;
};

export type PaymentHandoff = IntentBundle & {
  totalMinor: number;
  /** What the total was quoted for, so the customer sees what they are paying
   *  for. Optional: a history entry from before these existed still pays. */
  fulfillment?: FulfillmentType;
  deliveryAddress?: string | null;
};

/**
 * The Payment Element's look, from the design tokens: the customer surface's
 * forest primary (revision 06). Stripe renders the element in its own iframe,
 * so only these supported appearance variables reach it -- never its
 * internal DOM, and never the 3-D Secure challenge, which belongs to the card
 * issuer.
 */
const STRIPE_APPEARANCE: Appearance = {
  theme: "flat",
  variables: {
    colorPrimary: "#174D39",
    colorBackground: "#FFFFFF",
    colorText: "#252620",
    colorDanger: "#A61C35",
    fontFamily: "system-ui, sans-serif",
    borderRadius: "9px",
    spacingUnit: "4px",
  },
};

function readHandoff(state: unknown): PaymentHandoff | null {
  if (typeof state !== "object" || state === null) return null;
  const s = state as Record<string, unknown>;
  if (
    typeof s.clientSecret !== "string" ||
    typeof s.stripeAccountId !== "string" ||
    typeof s.publishableKey !== "string" ||
    typeof s.totalMinor !== "number"
  ) {
    return null;
  }
  return {
    clientSecret: s.clientSecret,
    stripeAccountId: s.stripeAccountId,
    publishableKey: s.publishableKey,
    customerEmail: typeof s.customerEmail === "string" ? s.customerEmail : null,
    totalMinor: s.totalMinor,
    fulfillment: s.fulfillment === "DELIVERY" || s.fulfillment === "PICKUP" ? s.fulfillment : undefined,
    deliveryAddress: typeof s.deliveryAddress === "string" ? s.deliveryAddress : null,
  };
}

/**
 * Payment, on its own URL.
 *
 * The order and its PaymentIntent both exist before anything renders here:
 * /checkout creates them, then navigates. This page only confirms the intent.
 * It still does not decide that the order is paid -- only the webhook does
 * that -- so it hands off to the order page, which polls the server.
 *
 * Static by design: no motion, depth or sticky control near the card fields,
 * where a moving surface would cover a wallet sheet or a bank's challenge.
 */
export function PaymentPage() {
  const { orderId = "" } = useParams<{ orderId: string }>();
  const navigate = useNavigate();
  const dispatch = useAppDispatch();
  const { state } = useLocation();

  const handed = useMemo(() => readHandoff(state), [state]);

  // Binds the cart before anything can clear it. Paying is the one moment the
  // cart must actually be emptied in storage, and an unbound cart clears only
  // the copy in memory -- so a customer who reloaded this page (which this
  // page is built to survive) paid, went back to the menu, and found the
  // items they had just bought still sitting there.
  useOpenCart();

  const portal = usePortalQuery();
  const [createPaymentIntent] = useCreatePaymentIntentMutation();
  const [recovered, setRecovered] = useState<IntentBundle | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Fetched only when the handoff is gone. It supplies the amount and, just as
  // importantly, says whether this order is still awaiting payment at all.
  const { data: order, error: orderError } = useOrderQuery(
    { orderId },
    { skip: handed !== null || !orderId },
  );

  // Reload with no handoff. The endpoint is "create or return": its Stripe
  // idempotency key is derived from the order id, so this hands back the
  // intent that already exists instead of opening a second one.
  useEffect(() => {
    if (handed || recovered || !orderId) return;
    let alive = true;
    createPaymentIntent({ orderId, idempotencyKey: newIdempotencyKey() })
      .unwrap()
      .then((intent) => {
        if (!alive) return;
        // No amount here on purpose: the order query is the authority for it
        // on this path, and the page waits for that before it renders.
        setRecovered({
          clientSecret: intent.client_secret,
          stripeAccountId: intent.stripe_account_id,
          publishableKey: intent.publishable_key,
          customerEmail: intent.customer_email ?? null,
        });
      })
      .catch((e) => {
        if (!alive) return;
        // The order left PENDING_PAYMENT while this page was closed: paid,
        // cancelled or swept by the TTL. The order page says which.
        if (e instanceof ApiError && e.code === "ORDER_STATE_CONFLICT") {
          navigate(`/orders/${orderId}`, { replace: true });
          return;
        }
        setError(errorMessage(e));
      });
    return () => {
      alive = false;
    };
  }, [handed, recovered, orderId, createPaymentIntent, navigate]);

  // The same redirect from the other direction, for an order already settled.
  useEffect(() => {
    if (order && order.status !== "PENDING_PAYMENT") {
      navigate(`/orders/${orderId}`, { replace: true });
    }
  }, [order, orderId, navigate]);

  const payment = handed ?? recovered;
  const total = handed?.totalMinor ?? order?.amounts.total_minor ?? null;
  const fulfillment = handed?.fulfillment ?? order?.fulfillment_type ?? null;
  const destination = handed?.deliveryAddress ?? order?.delivery_address ?? null;

  // loadStripe opens a connection, so it must not run again on every render.
  // Direct charges live on the connected account, which is why Stripe.js needs
  // stripeAccount here or the client secret will not resolve.
  const stripePromise = useMemo(
    () =>
      payment
        ? loadStripe(payment.publishableKey, { stripeAccount: payment.stripeAccountId })
        : null,
    [payment],
  );

  const backToOrder = (
    <Link to="/checkout" className="btn-primary">
      Back to your order
    </Link>
  );

  if (!orderId) {
    return <StatePage title="Nothing to pay for" action={backToOrder} />;
  }

  if (error || orderError || portal.error) {
    return (
      <StatePage title="We couldn't open payment" action={backToOrder}>
        {error ?? errorMessage(orderError ?? portal.error)}
      </StatePage>
    );
  }

  if (!payment || !stripePromise || total === null || !portal.data) {
    return <StatePage busy>Opening payment…</StatePage>;
  }

  // Bound to a const after the guard: narrowing on portal.data does not survive
  // into the callbacks below, because it is a property of a mutable object.
  const restaurant = portal.data;

  return (
    <div className="flex min-h-dvh flex-col">
      <CustomerHeader restaurant={restaurant} />
      <main className="mx-auto w-full max-w-[520px] px-5 pb-[50px] pt-5 sm:px-6 sm:pb-[70px] sm:pt-10">
        <div className="sm:rounded-banner sm:border sm:border-hairline sm:bg-surface sm:p-8">
          <h1 className="mb-4 font-display text-[34px] leading-[1.12] tracking-[-1px] [overflow-wrap:anywhere] sm:text-[38px]">
            Pay {restaurant.name}
          </h1>
          <p className="text-muted">
            Your order is held while you pay. Nothing is charged until you confirm.
          </p>

          {/* What this total was quoted for. Read from the order, never
              re-chosen here: changing it means going back to checkout, which
              prices it again. */}
          {fulfillment && (
            <p className="mt-6 flex items-start gap-3 rounded-field bg-brickSoft/60 px-4 py-3 text-sm">
              <Icon name={fulfillment === "DELIVERY" ? "bag" : "store"} className="mt-px h-5 w-5 shrink-0 text-brick" />
              <span className="min-w-0 [overflow-wrap:anywhere]">
                {fulfillment === "DELIVERY" ? (
                  <>
                    <strong className="font-semibold">Delivery</strong>
                    {destination ? ` to ${destination}` : ""}
                  </>
                ) : (
                  <>
                    <strong className="font-semibold">Pick-up</strong> at {restaurant.name}
                  </>
                )}
              </span>
            </p>
          )}

          <div className="tnum mt-6 flex justify-between border-y border-hairline py-5 text-[25px] font-bold">
            <span>Total</span>
            <span>{money(total, restaurant.currency)}</span>
          </div>

          <div className="mt-6">
            <Elements
              stripe={stripePromise}
              options={{ clientSecret: payment.clientSecret, appearance: STRIPE_APPEARANCE }}
            >
              <PayForm
                orderId={orderId}
                customerEmail={payment.customerEmail ?? null}
                total={total}
                currency={restaurant.currency}
                onPaid={() => {
                  dispatch(cartCleared());
                  clearCheckoutDrafts();
                  navigate(`/orders/${orderId}`, { replace: true });
                }}
              />
            </Elements>
          </div>

          <Link
            to="/checkout"
            className="mt-4 flex min-h-[40px] items-center justify-center gap-2 text-caption text-muted underline underline-offset-[3px] hover:text-ink"
          >
            <Icon name="back" className="h-4 w-4" />
            Back to your order
          </Link>
        </div>
      </main>
    </div>
  );
}

function PayForm({
  orderId,
  customerEmail,
  total,
  currency,
  onPaid,
}: {
  orderId: string;
  customerEmail: string | null;
  total: number;
  currency: string;
  onPaid: () => void;
}) {
  const stripe = useStripe();
  const elements = useElements();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // The iframe announces when its fields are ready to type in. Until then
  // the space holds a loading line rather than an empty box.
  const [ready, setReady] = useState(false);

  async function submit() {
    if (!stripe || !elements) return;
    setBusy(true);
    setError(null);

    const { error: stripeError } = await stripe.confirmPayment({
      elements,
      confirmParams: {
        return_url: `${window.location.origin}/orders/${orderId}`,
        ...(customerEmail
          ? { payment_method_data: { billing_details: { email: customerEmail } } }
          : {}),
      },
      redirect: "if_required",
    });

    if (stripeError) {
      setError(stripeError.message ?? "That payment didn't go through.");
      setBusy(false);
      return;
    }

    // Stripe says the confirmation succeeded. That is NOT the same as the order
    // being paid. Only the webhook can say that, so we hand off to the order
    // page, which polls the server for the authoritative state.
    onPaid();
  }

  return (
    <div>
      {!ready && <Loading>Loading secure payment…</Loading>}
      <div className={ready ? "" : "h-0 overflow-hidden"}>
        <PaymentElement
          options={{ layout: "tabs" }}
          onReady={() => setReady(true)}
          onLoadError={(e) => {
            // Never leave the page on a spinner: show Stripe's own reason.
            setReady(true);
            setError(e.error.message ?? "That payment didn't go through.");
          }}
        />
      </div>
      <ErrorNote message={error} className="mt-4" />
      <button
        type="button"
        className="btn-primary mt-6 min-h-[50px] w-full rounded-full"
        disabled={busy || !stripe || !ready}
        onClick={submit}
      >
        {busy ? (
          <>
            <Spinner />
            Processing…
          </>
        ) : (
          `Pay ${money(total, currency)}`
        )}
      </button>
      <p className="mt-3 flex items-center justify-center gap-2 text-caption text-muted">
        <Icon name="lock" className="h-4 w-4" />
        Your card is charged by the restaurant through Stripe.
      </p>
    </div>
  );
}
