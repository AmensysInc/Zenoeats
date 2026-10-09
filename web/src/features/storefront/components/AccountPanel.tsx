import { clearCheckoutDrafts } from "../checkoutDraft";
import { useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import {
  useCustomerLogoutMutation,
  useCustomerSessionQuery,
  useEndGuestSessionMutation,
} from "@/features/storefront/storefrontApi";
import { ErrorNote } from "@/components/common/Feedback";
import { Icon } from "@/components/common/icons";
import { errorMessage } from "@/services/apiClient";
import { GUEST_WARNING, GuestSessionConfirm } from "./GuestSession";
import { endSession, signInHref } from "../session";

/**
 * Who is ordering, and the offer to become someone.
 *
 * This was a row of text links the width of a caption, which is where the
 * instruction "show sign-in properly" came from: the one action on the page
 * that carries over to every future order was the smallest thing on it, and
 * guest ordering -- the path that works with no account at all -- was not
 * mentioned until checkout stopped someone.
 *
 * So it is a panel, and it says both. Signing in is the primary action
 * because it is the better outcome for anyone who orders twice; ordering as a
 * guest sits beside it rather than hidden behind it, because the counter
 * still wants the order of someone who will not sign in.
 *
 * It is still not a gate. The menu below needs no account, and nothing here
 * blocks reading it.
 *
 * `leading` is whether the restaurant is taking orders -- it shares the panel
 * because the two things a customer wants on arrival are "are you open" and
 * "who am I ordering as".
 */
export function AccountPanel({ leading }: { leading?: ReactNode }) {
  // 401 is the ordinary answer here -- most people reading a menu are nobody
  // yet -- so a failed query is a state, not an error worth showing.
  const { data: session, isLoading } = useCustomerSessionQuery({ soft: true });
  const [endGuest] = useEndGuestSessionMutation();
  const [logout] = useCustomerLogoutMutation();
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function signOut() {
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
    <section
      aria-label="Your account"
      className="mt-5 rounded-[16px] border border-[rgb(var(--ze-card-border))] bg-cream px-[17px] py-[15px] sm:px-[21px] sm:py-[17px]"
    >
      <div className="flex flex-col gap-[15px] sm:flex-row sm:items-center sm:justify-between sm:gap-6">
        <div className="min-w-0">
          {leading}
          {isLoading ? (
            <p className="mt-1 text-caption text-muted">Checking your session…</p>
          ) : !session ? (
            <>
              <p className="mt-1 font-display text-[19px] font-bold leading-[1.25] tracking-[-.3px] text-[rgb(var(--ze-item-title))] sm:text-[21px]">
                Sign in for a faster checkout
              </p>
              <p className="mt-1 max-w-[52ch] text-caption text-[rgb(var(--ze-item-description))]">
                We'll remember your details and keep your past orders. Or order as a
                guest with just an email — no account needed.
              </p>
            </>
          ) : session.is_guest ? (
            <>
              <p className="mt-1 flex flex-wrap items-center gap-2 font-display text-[19px] font-bold leading-[1.25] tracking-[-.3px] text-[rgb(var(--ze-item-title))] sm:text-[21px]">
                Ordering as a guest
                <span className="rounded-full bg-warningSoft px-[9px] py-[3px] text-[10px] font-[650] uppercase tracking-[1px] text-[rgb(var(--ze-warning))]">
                  Guest
                </span>
              </p>
              <p className="mt-1 max-w-[52ch] text-caption text-[rgb(var(--ze-item-description))]">
                {GUEST_WARNING}
              </p>
            </>
          ) : (
            <>
              <p className="mt-1 font-display text-[19px] font-bold leading-[1.25] tracking-[-.3px] text-[rgb(var(--ze-item-title))] sm:text-[21px]">
                Welcome back
                {session.full_name ? `, ${session.full_name.split(" ")[0]}` : ""}
              </p>
              <p
                className="mt-1 truncate text-caption text-[rgb(var(--ze-item-description))]"
                title={session.email ?? undefined}
              >
                {session.email}
              </p>
            </>
          )}
        </div>

        <div className="flex shrink-0 flex-wrap items-center gap-2.5">
          {isLoading ? null : !session ? (
            <>
              {/* Both land on the same page: it offers the providers above and
                  guest ordering below, and naming the second route here is
                  what stops someone with no intention of making an account
                  from bouncing off a sign-in wall. */}
              <a href={signInHref()} className="btn-primary min-h-[44px] px-5">
                Sign in
              </a>
              <a href={signInHref()} className="btn-quiet min-h-[44px] px-5">
                Order as guest
              </a>
            </>
          ) : session.is_guest ? (
            <>
              <Link to="/profile" className="btn-quiet min-h-[44px] px-4">
                Your orders
              </Link>
              <a href={signInHref()} className="btn-primary min-h-[44px] px-5">
                Sign in
              </a>
              <button
                type="button"
                className="link text-caption"
                disabled={busy}
                aria-expanded={confirming}
                onClick={() => setConfirming(true)}
              >
                End guest session
              </button>
            </>
          ) : (
            <>
              <Link to="/profile" className="btn-quiet min-h-[44px] px-4">
                Your profile
              </Link>
              <button
                type="button"
                className="link text-caption"
                disabled={busy}
                onClick={signOut}
              >
                {busy ? "Signing out…" : "Sign out"}
              </button>
            </>
          )}
        </div>
      </div>

      {/* How the account is reached, named rather than implied. Only worth
          saying to someone who has not signed in: afterwards it is history. */}
      {!isLoading && !session && (
        <p className="mt-3.5 flex flex-wrap items-center gap-x-2 gap-y-1 border-t border-[rgb(var(--ze-shortcut-border))] pt-3 text-[11px] text-muted">
          <Icon name="lock" className="h-3.5 w-3.5 shrink-0" />
          An email and password, or no account at all — your choice.
        </p>
      )}

      <ErrorNote message={error} className="mt-3" />
      {confirming && session?.is_guest && (
        <div className="mt-4">
          <GuestSessionConfirm busy={busy} onEnd={signOut} onKeep={() => setConfirming(false)} />
        </div>
      )}
    </section>
  );
}
