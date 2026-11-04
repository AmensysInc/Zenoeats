/**
 * What Stripe says when it sends a customer back to the order page.
 *
 * Most card payments finish on our payment page. Some leave it -- a bank's own
 * check, Cash App, Klarna and the like -- and Stripe brings the customer back
 * to /orders/:id with the outcome in the query string:
 *
 *   ?payment_intent=pi_…&payment_intent_client_secret=pi_…_secret_…&redirect_status=succeeded
 *
 * The page used to ignore all of it. So a paid order left its items in the cart,
 * a failed one said "waiting for the payment confirmation" for ever, and the
 * client secret stayed in the address bar and in browser history.
 *
 * This reads the outcome once and takes Stripe's parameters out of the address
 * bar, leaving everything else (the order-view token among it) where it was.
 * Only the webhook decides that an order is paid; this decides what the page
 * says and does in the meantime.
 */

export type PaymentReturn = "succeeded" | "processing" | "failed";

const STRIPE_PARAMS = ["payment_intent", "payment_intent_client_secret", "redirect_status"];

/** The outcome if Stripe has just sent this browser here, else null. */
export function takePaymentReturn(): PaymentReturn | null {
  if (typeof window === "undefined") return null;
  const url = new URL(window.location.href);
  if (!url.searchParams.has("payment_intent")) return null;

  const status = url.searchParams.get("redirect_status");
  for (const name of STRIPE_PARAMS) url.searchParams.delete(name);
  window.history.replaceState(window.history.state, "", url.pathname + url.search + url.hash);

  if (status === "succeeded" || status === "processing") return status;
  // "failed", "requires_payment_method", or anything Stripe adds later that is
  // not a payment going through.
  return "failed";
}
