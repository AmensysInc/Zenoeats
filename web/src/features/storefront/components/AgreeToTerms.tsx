import { useState } from "react";
import { useAcceptTermsMutation } from "@/features/storefront/storefrontApi";
import { StatePage } from "@/components/common/Feedback";

/**
 * The consent step for an account that never passed one.
 *
 * The sign-up page asks before it will submit or leave for a provider. This
 * covers the other way in: someone new pressing Google, Apple or Facebook on
 * the sign-in page, which has no checkbox and creates the account all the same.
 *
 * Shown in place by the guard rather than as a route of its own, so there is
 * no URL to arrive at out of order and nothing to loop between. Browsing the
 * menu is unaffected -- only the pages that lead to placing an order are
 * behind it, which is where agreement starts to matter.
 *
 * Guests never see this. They are shown the same sentence above the checkout
 * button and their agreement is recorded with the order; putting a second
 * gate in front of the one path deliberately kept short would be friction for
 * nothing.
 */
export function AgreeToTerms({ email }: { email: string }) {
  const [accept, { isLoading }] = useAcceptTermsMutation();
  const [failed, setFailed] = useState(false);

  const agree = async () => {
    setFailed(false);
    try {
      const session = await accept().unwrap();
      // The server records this and answers with the session as it now
      // stands. If it could not write the record it says so rather than
      // throwing, and claiming agreement we did not store would be worse
      // than asking again.
      if (!session.terms_accepted) setFailed(true);
    } catch {
      setFailed(true);
    }
  };

  return (
    <StatePage title="One thing before you order">
      <div className="mx-auto max-w-[440px] space-y-4 text-left">
        <p>
          You signed in as <strong>{email}</strong>. Before placing an order,
          please agree to the terms you order under.
        </p>
        <p className="legal-links">
          <a href="/legal/terms" target="_blank" rel="noopener">
            Terms of Service
          </a>
          <a href="/legal/privacy" target="_blank" rel="noopener">
            Privacy Policy
          </a>
          <a href="/legal/refunds" target="_blank" rel="noopener">
            Refunds &amp; Cancellations
          </a>
        </p>
        <p className="text-caption text-muted">
          Food is prepared by the restaurant, which takes your payment and
          issues any refund. If you have a food allergy, contact them before
          ordering.
        </p>
        {failed && (
          <p className="text-caption text-danger">
            We couldn’t save that just now. Try again.
          </p>
        )}
        <button
          type="button"
          className="btn-primary w-full"
          disabled={isLoading}
          onClick={agree}
        >
          {isLoading ? "Saving…" : "I agree, continue"}
        </button>
      </div>
    </StatePage>
  );
}
