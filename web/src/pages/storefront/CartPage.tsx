import { Link } from "react-router-dom";
import { useAppSelector } from "@/app/hooks";
import { BackLink } from "@/components/common/BackLink";
import { Cloche } from "@/components/common/icons";
import { CartLines } from "@/features/cart/components/CartLines";
import {
  selectCartCombos,
  selectCartCount,
  selectCartEmpty,
  selectCartLines,
  selectCartPreviewSubtotal,
} from "@/features/cart/cartSlice";
import { CustomerHeader } from "@/features/storefront/components/CustomerHeader";
import { usePortalQuery } from "@/features/storefront/storefrontApi";
import { useOpenCart } from "@/features/cart/useOpenCart";
import { money } from "@/utils/format";

/**
 * The order as it stands, before anybody is asked for an address or a card.
 *
 * Checkout used to be the first place a customer could read their own order
 * back, so changing their mind meant entering the page that asks for a
 * delivery address and a payment method. The two decisions are separated
 * here: what is being bought, then how it is paid for.
 *
 * Every figure on this page is the browser's own arithmetic and says so. The
 * server prices the order at checkout, and its answer is what is charged --
 * which is why this page shows a subtotal and never a total.
 */
export function CartPage() {
  const portal = usePortalQuery();
  useOpenCart();

  const lines = useAppSelector(selectCartLines);
  const combos = useAppSelector(selectCartCombos);
  const count = useAppSelector(selectCartCount);
  const empty = useAppSelector(selectCartEmpty);
  const subtotal = useAppSelector(selectCartPreviewSubtotal);
  const currency = portal.data?.currency ?? "USD";

  // Not orderable is not the same as empty: the cart is still theirs to read
  // and edit, and checkout is what refuses.
  const orderable = !!portal.data?.is_orderable && !!portal.data?.accepting_orders;

  return (
    <div className="min-h-screen bg-paper">
      {portal.data && <CustomerHeader restaurant={portal.data} />}

      <main className="mx-auto max-w-[760px] px-5 pb-16 pt-8 sm:px-7">
        <BackLink />
        <p className="eyebrow mt-[18px] text-muted">Your order</p>
        <h1 className="mt-2 font-display text-[34px] font-bold leading-[1.1] tracking-[-1px] text-brick sm:text-[40px]">
          Your cart
        </h1>

        {empty ? (
          <div className="empty mt-10">
            <Cloche className="mx-auto mb-4" />
            <h2 className="font-display text-2xl text-ink">Nothing in it yet.</h2>
            <p className="mt-2 text-sm text-muted">
              Anything you add from the menu waits here until you are ready.
            </p>
            <Link to="/" className="btn-primary mt-6">
              Back to the menu
            </Link>
          </div>
        ) : (
          <>
            <p className="mt-2 text-sm text-muted">
              Change anything here before you pay.{" "}
              <span className="sr-only">
                {count} {count === 1 ? "item" : "items"} in your cart.
              </span>
            </p>

            <div className="mt-6 rounded-banner border border-hairline bg-surface p-5 sm:p-6">
              <CartLines lines={lines} combos={combos} currency={currency} />

              <div className="mt-5 flex items-baseline justify-between">
                <span className="text-[17px] font-semibold">Subtotal</span>
                <span className="tnum text-[17px] font-semibold">{money(subtotal, currency)}</span>
              </div>
              {/* Said here rather than at the total, because this is the number
                  a customer is deciding on. */}
              <p className="mt-1 text-caption text-muted">
                Tax, and any delivery fee, are worked out at checkout.
              </p>
            </div>

            {!orderable && (
              <p className="note-warning mt-5">
                {portal.data?.name ?? "This restaurant"} is not taking orders right now. Your cart
                is kept, and checkout opens again when they do.
              </p>
            )}

            <div className="mt-6 flex flex-wrap items-center gap-4">
              <Link
                to="/checkout"
                aria-disabled={!orderable}
                tabIndex={orderable ? undefined : -1}
                className={`btn-primary min-h-[52px] flex-1 justify-between rounded-full px-6 sm:flex-none sm:justify-center sm:gap-4 ${
                  orderable ? "" : "pointer-events-none opacity-[.44]"
                }`}
              >
                Go to checkout
                <span className="tnum">{money(subtotal, currency)}</span>
              </Link>
              <Link to="/" className="link">
                add more items
              </Link>
            </div>
          </>
        )}
      </main>
    </div>
  );
}
