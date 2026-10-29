import { clearCheckoutDrafts } from "@/features/storefront/checkoutDraft";
import { loginSucceeded, mayBounce } from "./loginBounce";
import { useEffect, useRef, useState } from "react";
import { ApiError } from "@/services/apiClient";
import { StatePage } from "@/components/common/Feedback";
import { Icon } from "@/components/common/icons";

/**
 * The parts every guard shares, kept apart from the guards themselves.
 *
 * Not a tidiness split. Guards.tsx used to hold all three guards together,
 * which meant the file the router imports on every page also imported the
 * admin API, the staff API and the whole manage shell -- so a customer
 * opening a menu downloaded both operator portals to be told they were not
 * using them. Nothing in this file names a portal, so both sides can have it
 * without either dragging the other in.
 */

/**
 * Send this browser to `loginPath`, carrying where we were headed so signing
 * in returns there rather than dumping everyone on the portal root.
 *
 * Returns false when it has done this too often for the same page too
 * recently, having navigated nowhere -- the caller shows the dead end
 * instead.
 */
export function toLogin(loginPath: string): boolean {
  const next = window.location.pathname + window.location.search;
  if (!mayBounce(next)) return false;

  if (loginPath === "/account/sign-in") clearCheckoutDrafts();
  window.location.replace(`${loginPath}?next=${encodeURIComponent(next)}`);
  return true;
}

export { loginSucceeded };

/**
 * The redirect to a login page, as a rendered thing.
 *
 * It used to be a bare call in the middle of a guard's render, which is a
 * side effect in a place React is free to run twice and to throw away -- so
 * the navigation could be started more than once for one decision. Here it
 * happens in an effect, once, after the render it belongs to has committed.
 */
export function SendToLogin({ loginPath }: { loginPath: string }) {
  const [stuck, setStuck] = useState(false);
  useEffect(() => {
    if (!toLogin(loginPath)) setStuck(true);
  }, [loginPath]);
  return stuck ? <LoopBroken loginPath={loginPath} /> : <Booting />;
}

/**
 * The dead end, which is still better than the loop.
 *
 * Both ways out are a full navigation the customer chooses: going to sign-in
 * by hand does not re-arm the count, and the menu is somewhere to be that
 * needs no session at all.
 */
function LoopBroken({ loginPath }: { loginPath: string }) {
  const customer = loginPath === "/account/sign-in";
  return (
    <StatePage
      title="We couldn't finish signing you in"
      action={
        // Two ways out, so they need a row of their own: StatePage puts one
        // action in a plain block and would sit these against each other.
        <div className="flex flex-wrap items-center justify-center gap-3">
          <button
            type="button"
            className="btn-primary"
            onClick={() => {
              loginSucceeded();
              window.location.replace(loginPath);
            }}
          >
            Sign in again
          </button>
          {customer && (
            <a href="/" className="btn-quiet">
              Back to the menu
            </a>
          )}
        </div>
      }
    >
      This page and the sign-in page kept handing you back to each other, so we
      stopped. Signing in again usually settles it.
    </StatePage>
  );
}

export function Booting() {
  return <StatePage busy>Loading…</StatePage>;
}

/** How long after a failed attempt the next one starts. Counted from when
 *  that attempt finished, so a slow server is never asked twice at once. */
const RECONNECT_AFTER_MS = 5_000;

export type Reconnect = { retry: () => unknown; retrying: boolean };

/**
 * Keep asking until the API answers, then get out of the way.
 *
 * This page used to be a dead end: one request that happened to land while
 * the API was restarting, or stalled, left a Reload button in front of
 * someone who had done nothing wrong -- and on a kitchen tablet, nobody
 * standing there to press it. The guard renders the real page the moment its
 * query succeeds, so recovering needs nothing but a retry.
 */
export function useReconnect(retry: () => unknown, retrying: boolean) {
  // refetch is a new function on most renders; the latest one is what counts.
  const latest = useRef(retry);
  latest.current = retry;

  useEffect(() => {
    if (retrying) return;
    const timer = window.setTimeout(() => latest.current(), RECONNECT_AFTER_MS);
    return () => window.clearTimeout(timer);
  }, [retrying]);

  useEffect(() => {
    // Straight away, rather than on the next tick, when there is reason to
    // think it will now work: the network came back, or someone looked.
    const now = () => latest.current();
    window.addEventListener("online", now);
    window.addEventListener("focus", now);
    return () => {
      window.removeEventListener("online", now);
      window.removeEventListener("focus", now);
    };
  }, []);
}

/** A 403 or 404 is an answer, not an outage. Saying "can't reach the server"
 *  for it sent people hunting for a fault that was never there. */
export function isRefusal(error: unknown): error is ApiError {
  return error instanceof ApiError && (error.status === 403 || error.status === 404);
}

export function Unreachable({ retry, retrying }: Reconnect) {
  useReconnect(retry, retrying);
  return (
    <StatePage
      title="Can't reach the server"
      busy={retrying}
      action={
        <button type="button" className="btn-quiet" onClick={() => window.location.reload()}>
          <Icon name="refresh" />
          Reload
        </button>
      }
    >
      {retrying
        ? "Trying again…"
        : "The API did not answer. This page will reconnect by itself as soon as it does."}
    </StatePage>
  );
}
