import { useState } from "react";
import { ErrorNote, Spinner } from "@/components/common/Feedback";
import { errorMessage } from "@/services/apiClient";
import { useCloseAccountMutation, useCustomerLogoutMutation } from "../storefrontApi";
import type { CustomerSession } from "@/types";

/**
 * Close this account, and take the customer's details off it.
 *
 * Two steps on purpose. The first says what goes and what stays, in the same
 * words as the deletion policy, because "delete my account" means different
 * things at different restaurants and a customer should not have to guess
 * which one this is. The second is the button that does it.
 *
 * Orders are the part people are surprised by, so it is said before the
 * button rather than afterwards: a paid order is the restaurant's record of
 * a sale and is kept, carrying the details it was placed with.
 */
export function CloseAccount({ session }: { session: CustomerSession }) {
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [closeAccount] = useCloseAccountMutation();
  const [logout] = useCustomerLogoutMutation();

  // A guest has no account to close: the session ends on its own, and the
  // record behind it is cleared out with the rest.
  if (session.is_guest) return null;

  async function close() {
    setBusy(true);
    setError(null);
    try {
      await closeAccount().unwrap();
      // Signed out here rather than left holding a session for an account
      // that no longer exists. The reload lands on the menu as a stranger.
      // The API has already cleared the credential; this clears the cookie.
      await logout().unwrap();
      window.location.assign("/");
    } catch (e) {
      setError(errorMessage(e));
      setBusy(false);
    }
  }

  return (
    <section className="mt-6 rounded-banner border border-hairline bg-surface p-5 sm:p-6" aria-label="Close your account">
      <h2 className="font-display text-xl">Close your account</h2>
      {!asking ? (
        <>
          <p className="mt-2 max-w-prose text-sm text-muted">
            Your sign-in, your saved details and your favourites are removed. Orders you have
            already placed are kept.
          </p>
          <button type="button" className="link-danger mt-4" onClick={() => setAsking(true)}>
            Close my account
          </button>
        </>
      ) : (
        <div className="animate-disclose">
          <p className="mt-2 max-w-prose text-sm">
            <strong>This cannot be undone.</strong> Closing the account removes your sign-in, your
            name, phone number, address and email, and your favourites.
          </p>
          <p className="mt-3 max-w-prose text-sm text-muted">
            Orders you have already placed stay with the restaurant, carrying the name and address
            they were placed with: they are its record of a sale.{" "}
            <a className="link" href="/legal/data-deletion.html" target="_blank" rel="noreferrer">
              What we keep, and why
            </a>
          </p>
          <ErrorNote message={error} className="mt-4" />
          <div className="mt-5 flex flex-wrap items-center gap-4">
            <button type="button" className="btn-danger" disabled={busy} onClick={() => void close()}>
              {busy && <Spinner />}
              {busy ? "Closing…" : "Close my account"}
            </button>
            <button type="button" className="link" disabled={busy} onClick={() => setAsking(false)}>
              keep it
            </button>
          </div>
        </div>
      )}
    </section>
  );
}
