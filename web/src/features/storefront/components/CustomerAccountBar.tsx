import { clearCheckoutDrafts } from "../checkoutDraft";
import { useState } from "react";
import {
  useCustomerSessionQuery,
  useCustomerLogoutMutation,
  useEndGuestSessionMutation,
} from "@/features/storefront/storefrontApi";
import { ErrorNote } from "@/components/common/Feedback";
import { errorMessage } from "@/services/apiClient";
import { GUEST_WARNING, GuestSessionConfirm } from "./GuestSession";
import { endSession } from "../session";

/**
 * Who is ordering, and a way to stop being them.
 *
 * Checkout is where it matters: a shared phone or a family tablet is often
 * signed in as someone else, and the order, its receipt and its pickup PIN all
 * go to whoever that is. Saying so before payment is cheaper than a refund.
 *
 * A guest gets the address spelled out rather than a name, because it is the
 * one they typed minutes ago and the only copy of the order that will leave
 * this browser. Ending a guest session is irreversible, so it asks first;
 * signing a customer out does not need to.
 */
export function CustomerAccountBar() {
  const { data: session } = useCustomerSessionQuery();
  const [endGuest] = useEndGuestSessionMutation();
  const [logout] = useCustomerLogoutMutation();
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!session) return null;

  async function leave() {
    setBusy(true);
    setError(null);
    try {
      await endSession(session, {
        endGuest: () => endGuest().unwrap(),
        logout: () => logout().unwrap(),
      });
      clearCheckoutDrafts();
      window.location.assign("/");
    } catch (e) {
      setError(errorMessage(e));
      setBusy(false);
    }
  }

  return (
    <div className="pb-3 sm:pb-6">
      <div className="flex items-center justify-between gap-2.5 pt-3.5 text-caption sm:gap-5">
        {/* Wraps rather than truncating: for a guest the address is the whole
            point, and a clipped one cannot be checked. */}
        <span className="min-w-0 [overflow-wrap:anywhere]">
          {session.is_guest ? (
            <>
              Ordering as a guest · receipt to <strong className="font-semibold">{session.email}</strong>
            </>
          ) : (
            <>
              Ordering as <strong className="font-semibold">{session.full_name ?? session.email}</strong>
            </>
          )}
        </span>
        <button
          type="button"
          className="link min-h-[24px] shrink-0 text-caption"
          disabled={busy}
          aria-expanded={session.is_guest ? confirming : undefined}
          onClick={session.is_guest ? () => setConfirming(true) : leave}
        >
          {session.is_guest ? "End guest session" : busy ? "Signing out…" : "Not you? Sign out"}
        </button>
      </div>
      {session.is_guest && <p className="field-hint max-w-prose">{GUEST_WARNING}</p>}
      <ErrorNote message={error} className="mt-3" />
      {confirming && session.is_guest && (
        <GuestSessionConfirm busy={busy} onEnd={leave} onKeep={() => setConfirming(false)} />
      )}
    </div>
  );
}
