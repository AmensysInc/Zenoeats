import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { StatePage } from "@/components/common/Feedback";
import { Cloche } from "@/components/common/icons";
import { useOrderQuery, usePortalQuery } from "@/features/storefront/storefrontApi";
import { DeliveryTracking } from "@/features/storefront/components/DeliveryTracking";
import { CustomerHeader } from "@/features/storefront/components/CustomerHeader";
import { errorMessage } from "@/services/apiClient";
import { money } from "@/utils/format";
import { takeOrderToken } from "@/features/storefront/orderToken";
import { mapPins } from "@/features/storefront/theme";
import { isMapStyleKey, mapStyle } from "@/features/storefront/mapStyles";
import type { OrderLine } from "@/types";

const STATUS_COPY: Record<string, { title: string; detail: string }> = {
  PENDING_PAYMENT: {
    title: "Confirming your payment",
    detail: "This usually takes a few seconds. Keep this page open.",
  },
  AUTO_ACCEPTED: { title: "Order confirmed", detail: "The kitchen has your order." },
  PREPARING: { title: "Being made now", detail: "We'll tell you when it's ready to collect." },
  READY_FOR_PICKUP: {
    title: "Ready to collect",
    detail: "Give your PIN to the counter to pick it up.",
  },
  READY_FOR_DELIVERY: {
    title: "Ready, waiting for the driver",
    detail: "The restaurant is sending this one out to you.",
  },
  OUT_FOR_DELIVERY: {
    title: "On its way",
    detail: "The driver has your order.",
  },
  COMPLETED: { title: "Collected", detail: "Thanks for ordering." },
  CANCELLED: { title: "Cancelled", detail: "This order was cancelled." },
  EXPIRED: {
    title: "Expired",
    detail: "Payment wasn't completed in time. Nothing was charged.",
  },
};

/** Where a delivery's wording differs from a collection's. */
const DELIVERY_COPY: Record<string, { title: string; detail: string }> = {
  AUTO_ACCEPTED: { title: "Order confirmed", detail: "The kitchen has your order. We'll bring it to you." },
  PREPARING: { title: "Being made now", detail: "Your driver takes it the moment it's ready." },
  COMPLETED: { title: "Delivered", detail: "Enjoy your meal. Thanks for ordering." },
};

const TERMINAL = ["COMPLETED", "CANCELLED", "EXPIRED"];

/** The path an order takes, for the progress marks. A status the path does
 *  not know shows no marks rather than wrong ones. */
const PICKUP_STEPS = ["PENDING_PAYMENT", "AUTO_ACCEPTED", "PREPARING", "READY_FOR_PICKUP", "COMPLETED"];
const DELIVERY_STEPS = [
  "PENDING_PAYMENT",
  "AUTO_ACCEPTED",
  "PREPARING",
  "READY_FOR_DELIVERY",
  "OUT_FOR_DELIVERY",
  "COMPLETED",
];

export function OrderPage() {
  const { orderId = "" } = useParams<{ orderId: string }>();
  // Present when the page was opened from the link in a guest's confirmation
  // email, which is the one way back to a pickup PIN from another device.
  // Held in state because reading it takes it out of the address bar, so this
  // must survive every later render rather than be looked up again.
  //
  // Nothing on this page offers to copy or share the link, and it never asks
  // anyone to sign in: with a token the link is the credential, and a PIN it
  // opens must not be one tap from a group chat.
  const [token] = useState(takeOrderToken);
  // Poll fast while payment is unconfirmed, slower once the kitchen has it, and
  // stop entirely once nothing further can change. Held in state rather than
  // derived inline, because deriving it from the query's own result would make
  // the query's options depend on its output.
  const [pollMs, setPollMs] = useState(2_000);

  const { data: order, error } = useOrderQuery(
    { orderId, token },
    { skip: !orderId, pollingInterval: pollMs },
  );
  // For the map key. Public and already cached from the menu in most visits.
  const portal = usePortalQuery();
  // The map's colours, built once per restaurant setting rather than on every
  // render. The map is rebuilt when these change, and this page re-renders on
  // each poll, so a fresh object here would reload the whole map every five
  // seconds instead of just moving the driver.
  const theme = portal.data?.storefront?.theme ?? null;
  const pinsThemed = portal.data?.map_pins_themed ?? true;
  const styleKey = portal.data?.map_style_key;
  const pins = useMemo(() => mapPins(theme, pinsThemed), [theme, pinsThemed]);
  const style = useMemo(
    () => mapStyle(isMapStyleKey(styleKey) ? styleKey : null, theme),
    [styleKey, theme],
  );

  useEffect(() => {
    if (!order) return;
    if (TERMINAL.includes(order.status)) setPollMs(0);
    else if (order.status === "PENDING_PAYMENT") setPollMs(2_000);
    // A driver on the road moves; every five seconds keeps the dot honest.
    else if (order.fulfillment_type === "DELIVERY") setPollMs(5_000);
    else setPollMs(8_000);
  }, [order]);

  if (error) {
    return (
      <StatePage
        title="We can't find that order"
        action={
          <Link to="/" className="btn-primary">
            Back to the menu
          </Link>
        }
      >
        {errorMessage(error)}
      </StatePage>
    );
  }

  if (!order) {
    return <StatePage busy>Loading your order…</StatePage>;
  }

  const delivering = order.fulfillment_type === "DELIVERY";
  const copy = (delivering ? DELIVERY_COPY[order.status] : undefined) ??
    STATUS_COPY[order.status] ?? { title: order.status, detail: "" };
  // Only once the restaurant has actually issued the refund: a cancellation
  // made without one must not promise money that is not on its way.
  const refundNotice =
    order.status === "CANCELLED" && order.cancelled_by_restaurant && order.refund_minor
      ? `Your order #${order.order_number} has been cancelled, and a refund of ` +
        `${money(order.refund_minor, order.currency)} will be credited to your original ` +
        "payment method within 10 to 14 business days."
      : null;
  const awaitingPayment =
    order.status === "PENDING_PAYMENT" && order.payment_status !== "PAID";
  const steps = delivering ? DELIVERY_STEPS : PICKUP_STEPS;
  const step = steps.indexOf(order.status);
  const ready = order.status === "READY_FOR_PICKUP" || order.status === "READY_FOR_DELIVERY";

  return (
    <div className="flex min-h-dvh flex-col">
      {/* No account row and no sign-in prompt: opened from a guest's email,
          the link itself is the credential. */}
      {portal.data && <CustomerHeader restaurant={portal.data} />}
      <main className="mx-auto w-full max-w-[720px] px-5 pb-[50px] pt-6 sm:px-6 sm:pb-[70px] sm:pt-10 lg:max-w-[1040px]">
      <div className="grid grid-cols-1 items-start gap-7 lg:grid-cols-[minmax(0,1.25fr)_minmax(0,1fr)] lg:gap-[50px]">
        <section className="min-w-0">
          {/* The cloche is for a collection. A delivery has its own card
              below, which says more than an illustration could. */}
          {!delivering && (
            <Cloche ready={ready} lifted={order.status === "COMPLETED"} className="mb-[22px]" />
          )}
          <p className="eyebrow text-muted">
            Order #{order.order_number} · {delivering ? "Delivery" : "Pick-up"}
          </p>
          {/* Keyed by status: the title settles in only when the server says
              something new, never on a timer. Announced as it changes. */}
          <div aria-live="polite">
            <h1
              key={order.status}
              className="my-[18px] animate-reveal font-display text-[37px] leading-[1.12] tracking-[-1.2px] sm:text-[44px]"
            >
              {copy.title}
            </h1>
            <p className="text-muted">{refundNotice ?? copy.detail}</p>
          </div>

          {step >= 0 && (
            <div className="my-[30px] flex gap-[7px]" aria-hidden="true">
              {steps.slice(1).map((_, index) => index + 1).map((i) => (
                <span
                  key={i}
                  className={`h-1 flex-1 rounded transition-colors duration-pin ${
                    i <= step ? "bg-brick" : "bg-hairline"
                  }`}
                />
              ))}
            </div>
          )}

          {/* From the moment payment is confirmed: the steps, the driver,
              and once it leaves the restaurant, the live map. */}
          {/* Spaced from the text above when there are no progress marks
              between them, as on a cancelled order. */}
          {order.tracking && (
            <div className={step < 0 ? "mt-6" : undefined}>
              <DeliveryTracking
                tracking={order.tracking}
                status={order.status}
                destination={order.delivery_address}
                mapsKey={portal.data?.maps_browser_key ?? null}
                restaurantName={portal.data?.name ?? "The restaurant"}
                pins={pins}
                style={style}
              />
            </div>
          )}

          {awaitingPayment && (
            <div className="note mt-6 flex items-center gap-3">
              <span className="h-[7px] w-[7px] shrink-0 animate-pending rounded-full bg-brick" aria-hidden />
              Waiting for the payment confirmation from Stripe.
            </div>
          )}

          {/* Only once the server supplies one. Its arrival is announced; the
              digits are not read out on their own. */}
          {order.pickup_pin && (
            <div className="my-[26px] animate-reveal rounded-product border border-[#DCE2D5] bg-brickSoft p-[22px]">
              <p className="eyebrow">Pickup PIN</p>
              <p className="tnum my-2.5 font-display text-[44px] leading-[1.3] tracking-[.16em] sm:text-5xl sm:tracking-[.2em]">
                {order.pickup_pin}
              </p>
              <p className="text-caption text-muted">
                Show this at the counter. Don&apos;t share it with anyone else.
              </p>
            </div>
          )}
          <p className="sr-only" aria-live="polite">
            {order.pickup_pin ? "Your pickup PIN is ready." : ""}
          </p>
        </section>

        <section className="rounded-banner border border-hairline bg-surface p-5 shadow-raised sm:p-6 lg:sticky lg:top-6">
          <p className="eyebrow text-muted">Your order</p>
          <ul className="mt-2">
            {groupLines(order.items).map((row, i) => (
              <OrderRow key={i} row={row} currency={order.currency} />
            ))}
          </ul>

          {/* No discount row here, unlike checkout: the subtotal is already
              what was charged for the food. */}
          <dl className="tnum my-6 flex flex-col gap-2.5 text-sm">
            <div className="flex justify-between gap-6">
              <dt>Subtotal</dt>
              <dd>{money(order.amounts.subtotal_minor, order.currency)}</dd>
            </div>
            {order.amounts.delivery_fee_minor > 0 && (
              <div className="flex justify-between gap-6">
                <dt>Delivery fee</dt>
                <dd>{money(order.amounts.delivery_fee_minor, order.currency)}</dd>
              </div>
            )}
            <div className="flex justify-between gap-6">
              <dt>Tax</dt>
              <dd>{money(order.amounts.tax_minor, order.currency)}</dd>
            </div>
            <div className="mt-1.5 flex justify-between gap-6 border-t border-hairline pt-3.5 text-[23px] font-[650]">
              <dt>Total</dt>
              <dd>{money(order.amounts.total_minor, order.currency)}</dd>
            </div>
          </dl>

          {/* Once paid, the delivery card names the address; until then
              this is the only place it is shown. */}
          {order.delivery_address && !order.tracking && (
            <p className="mb-2 text-caption text-muted [overflow-wrap:anywhere]">
              Delivering to {order.delivery_address}
            </p>
          )}
          <p className="text-caption text-muted">Payment: {order.payment_status.toLowerCase()}</p>

          {/* The restaurant is who a customer turns to about an order -- a
              missing item, a late delivery, a refund -- so how to reach it
              sits beside the order itself. */}
          {portal.data?.phone && (
            <p className="mt-4 text-sm">
              Questions about this order? Call {portal.data.name} on{" "}
              <a
                className="font-semibold underline"
                href={`tel:${portal.data.phone.replace(/[^+0-9]/g, "")}`}
              >
                {portal.data.phone}
              </a>
              .
            </p>
          )}

          <Link to="/" className="btn-quiet mt-6 min-h-[50px] w-full rounded-full">
            Order something else
          </Link>
        </section>
      </div>
      </main>
    </div>
  );
}


type Row = {
  /** Set when this row is a meal deal rather than a single item. */
  comboName: string | null;
  quantity: number;
  totalMinor: number;
  lines: OrderLine[];
};

/**
 * The order's lines as the customer bought them.
 *
 * The API sends a combo as one line per component, which is what the kitchen
 * needs to plate it, and tags each with the deal's name and a group number so
 * the customer's copy can put it back together. Without this the tracking page
 * listed "Mc. Crispy / Coke / Fries" as three unrelated things the customer
 * never ordered separately.
 *
 * Grouped by combo_group rather than by name: two of the same deal in one
 * order are two rows, because they can be built differently.
 */
function groupLines(items: OrderLine[]): Row[] {
  const rows: Row[] = [];
  const byGroup = new Map<number, Row>();

  for (const item of items) {
    if (item.combo_group === null || !item.combo_name) {
      rows.push({
        comboName: null,
        quantity: item.quantity,
        totalMinor: item.line_total_minor,
        lines: [item],
      });
      continue;
    }
    const existing = byGroup.get(item.combo_group);
    if (existing) {
      existing.totalMinor += item.line_total_minor;
      existing.lines.push(item);
    } else {
      const row: Row = {
        comboName: item.combo_name,
        // Every component of one combo carries that combo's quantity, so the
        // first is the count of deals, not a sum over its parts.
        quantity: item.quantity,
        totalMinor: item.line_total_minor,
        lines: [item],
      };
      byGroup.set(item.combo_group, row);
      rows.push(row);
    }
  }
  return rows;
}

/** One row as the customer bought it: a single item, or a whole meal deal. */
function OrderRow({ row, currency }: { row: Row; currency: string }) {
  const single = row.lines[0];
  if (!single) return null;

  return (
    <li className="border-b border-hairline py-5">
      <div className="flex items-start justify-between gap-4">
        <p className="min-w-0 font-semibold">
          <span className="tnum">{row.quantity}×</span> {row.comboName ?? single.name}
        </p>
        <span className="tnum whitespace-nowrap">{money(row.totalMinor, currency)}</span>
      </div>

      {row.comboName ? (
        /* The deal's parts, indented under it. The kitchen plates three
           things; the customer bought one. */
        <ul className="mt-2 border-l-2 border-hairline pl-3 text-[13px] text-muted">
          {row.lines.map((part, j) => (
            <li key={j} className="mt-1 first:mt-0">
              {part.name}
              {part.modifiers.length > 0 && (
                <span> · {part.modifiers.map((m) => m.option_name).join(" · ")}</span>
              )}
              {part.item_note && <span className="italic"> · {part.item_note}</span>}
            </li>
          ))}
        </ul>
      ) : (
        <>
          {single.modifiers.length > 0 && (
            <p className="mt-[7px] text-[13px] text-muted">
              {single.modifiers.map((m) => `${m.group_name}: ${m.option_name}`).join(" · ")}
            </p>
          )}
          {single.item_note && (
            <p className="mt-[7px] text-[13px] italic text-muted">{single.item_note}</p>
          )}
        </>
      )}
    </li>
  );
}
