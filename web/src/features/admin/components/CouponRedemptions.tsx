import { useState } from "react";
import { Empty, ErrorNote, Loading } from "@/components/common/Feedback";
import { useCouponRedemptionsQuery } from "@/features/admin/adminApi";
import { errorMessage } from "@/services/apiClient";
import { money } from "@/utils/format";

/**
 * Every coupon redemption at one restaurant: which code, which order, who
 * placed it, and what it took off.
 *
 * READ FROM THE ORDERS, which is the only honest source. A counter on each
 * coupon would be a second truth about something the orders already know,
 * and it would part company with them the first time one was cancelled --
 * which is exactly when somebody goes looking at this screen.
 *
 * CANCELLED AND EXPIRED ORDERS ARE ABSENT for the same reason they are not
 * counted anywhere else: an order that was never paid for is not a
 * promotion anybody received.
 *
 * A guest is a real row carrying whatever name was typed at checkout, so a
 * name cannot tell a guest from an account. The flag beside it can, and
 * "was this a regular or a passer-by" is the question being asked.
 */
export function CouponRedemptions({ restaurantId }: { restaurantId: string }) {
  // Narrowing to one code is how a group's MORE7, or one customer's welcome
  // code, gets traced from here.
  const [filter, setFilter] = useState("");
  const [applied, setApplied] = useState("");
  const history = useCouponRedemptionsQuery({
    id: restaurantId,
    code: applied || undefined,
  });

  const currency = history.data?.currency ?? "USD";
  const rows = history.data?.redemptions ?? [];

  return (
    <section
      aria-labelledby="redemptions-heading"
      className="mt-10 border-t border-hairline pt-8"
    >
      <h2 id="redemptions-heading" className="text-lg font-semibold">
        Who used what
      </h2>
      <p className="mt-1 max-w-[70ch] text-caption text-muted">
        Every order that used a code here, newest first. Cancelled and expired
        orders are left out — they were never paid for, so nobody received the
        promotion.
      </p>

      <ErrorNote
        message={history.error ? errorMessage(history.error) : null}
        className="mt-4"
      />

      <form
        className="mt-5 flex flex-wrap items-end gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          setApplied(filter.trim());
        }}
      >
        <label className="min-w-[200px] flex-1 sm:flex-none">
          <span className="label">Narrow to one code</span>
          <input
            className="field mt-[7px] font-mono uppercase"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="MORE7"
            maxLength={32}
          />
        </label>
        <button type="submit" className="btn-quiet">
          Show
        </button>
        {applied && (
          <button
            type="button"
            className="link"
            onClick={() => {
              setFilter("");
              setApplied("");
            }}
          >
            Clear
          </button>
        )}
      </form>

      {history.data && (
        <p className="tnum mt-5 text-[17px] font-semibold">
          {history.data.total_redemptions}{" "}
          {history.data.total_redemptions === 1 ? "redemption" : "redemptions"}
          <span className="font-normal text-muted">
            {" · "}
            {money(history.data.total_discounted_minor, currency)} given away
          </span>
        </p>
      )}

      {history.isLoading ? (
        <Loading />
      ) : !rows.length ? (
        <Empty>
          {applied ? `Nothing has used ${applied.toUpperCase()} yet.` : "No codes used yet."}
        </Empty>
      ) : (
        // Its own scroll container: six columns must not widen the page, and
        // an operator on a laptop still wants them side by side.
        <div className="mt-4 overflow-x-auto">
          <table className="w-full min-w-[720px] border-collapse text-sm">
            <thead>
              <tr className="border-b border-hairline text-left text-caption text-muted">
                <th scope="col" className="py-2 pr-4 font-medium">Code</th>
                <th scope="col" className="py-2 pr-4 font-medium">Order</th>
                <th scope="col" className="py-2 pr-4 font-medium">Who</th>
                <th scope="col" className="py-2 pr-4 font-medium">When</th>
                <th scope="col" className="py-2 pr-4 text-right font-medium">Took off</th>
                <th scope="col" className="py-2 text-right font-medium">Order total</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.order_id} className="border-b border-hairline/60">
                  <td className="py-2 pr-4 font-mono">{row.code}</td>
                  <td className="tnum py-2 pr-4">#{row.order_number}</td>
                  <td className="py-2 pr-4">
                    <span className="block">{row.customer_name ?? "—"}</span>
                    <span className="block text-caption text-muted">
                      {row.is_guest ? "guest" : row.customer_email ?? "account"}
                    </span>
                  </td>
                  <td className="py-2 pr-4 text-muted">{when(row.created_at)}</td>
                  <td className="tnum py-2 pr-4 text-right">
                    {money(row.discount_minor, currency)}
                  </td>
                  <td className="tnum py-2 text-right text-muted">
                    {money(row.order_total_minor, currency)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function when(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}
