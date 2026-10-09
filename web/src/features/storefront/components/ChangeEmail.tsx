import { useState } from "react";
import { ErrorNote, Spinner } from "@/components/common/Feedback";
import { errorMessage } from "@/services/apiClient";
import { useChangeEmailMutation } from "../storefrontApi";
import { moveCheckoutDraft } from "../checkoutDraft";
import type { CustomerSession } from "@/types";

/**
 * Change the address on this account.
 *
 * This was a four-step dance through Clerk: create an address, send a code,
 * attempt verification, promote it to primary, then sync our mirror of it.
 * With the credential held here, it is one request -- the new address and the
 * current password, which is what proves the person asking owns the account
 * rather than merely found it signed in.
 *
 * The API enforces the rest: one account per address, and the account must
 * still be active.
 */
export function ChangeEmail({
  session,
  slug,
  open,
  onOpenChange,
}: {
  session: CustomerSession;
  slug: string;
  /** Held by the page, because the link that opens this sits beside the
   *  email field rather than inside this component. */
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const setOpen = onOpenChange;
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [changeEmail, { isLoading: busy }] = useChangeEmailMutation();

  async function submit() {
    if (busy) return;
    setError(null);

    const value = email.trim().toLowerCase();
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value) || value === session.email.toLowerCase()) {
      setError("Enter a different, valid email address.");
      return;
    }
    if (!password) {
      setError("Enter your current password.");
      return;
    }

    try {
      const updated = await changeEmail({ email: value, password }).unwrap();
      // The checkout draft is keyed by who is ordering, so it moves with them
      // rather than being stranded under the old address.
      moveCheckoutDraft(slug, session, updated);
      setOpen(false);
      setEmail("");
      setPassword("");
      setSaved(true);
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  if (!open) {
    return saved ? (
      <p className="mt-2 text-caption text-success" role="status">
        Your email has been changed.
      </p>
    ) : null;
  }

  return (
    <form
      className="mt-3 rounded-banner border border-hairline bg-surface p-4 sm:p-5"
      onSubmit={(event) => {
        event.preventDefault();
        void submit();
      }}
    >
      <p className="text-caption text-muted">
        Receipts, order links and password resets all go to this address, so we
        ask for your password before moving it.
      </p>

      <label className="mt-3 block">
        <span className="label">New email</span>
        <input
          className="field mt-[7px]"
          type="email"
          autoComplete="email"
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          required
        />
      </label>

      <label className="mt-3 block">
        <span className="label">Your current password</span>
        <input
          className="field mt-[7px]"
          type="password"
          autoComplete="current-password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          required
        />
      </label>

      <ErrorNote message={error} className="mt-3" />

      <div className="mt-4 flex flex-wrap items-center gap-2.5">
        <button type="submit" className="btn-primary min-h-[44px] px-5" disabled={busy}>
          {busy ? <Spinner /> : "Save new email"}
        </button>
        <button
          type="button"
          className="btn-quiet min-h-[44px] px-5"
          disabled={busy}
          onClick={() => {
            setOpen(false);
            setError(null);
          }}
        >
          Cancel
        </button>
      </div>
    </form>
  );
}
