import { useEffect, useState, type ReactNode } from "react";
import { useAppDispatch } from "@/app/hooks";
import { sessionEstablished, sessionEnded } from "@/features/session/sessionSlice";
import { ApiError } from "@/services/apiClient";
import { useCustomerSessionQuery } from "@/features/storefront/storefrontApi";
import { AgreeToTerms } from "@/features/storefront/components/AgreeToTerms";
import { takeOrderToken } from "@/features/storefront/orderToken";
import { StatePage } from "@/components/common/Feedback";
import { Booting, loginSucceeded, SendToLogin, useReconnect, type Reconnect } from "./guardParts";

/**
 * The customer guard. The two operator portals have their own, in
 * OperatorGuards.tsx, kept separate so that importing this one does not pull
 * both portals into the bundle every storefront visitor downloads.
 *
 * A guard is a convenience, not a security control. The API authorises every
 * request independently; what this buys is not showing an empty shell to
 * someone who is about to be redirected.
 */

/**
 * Checkout, payment and order tracking.
 *
 * Browsing the menu never needs an identity; paying does -- but an identity is
 * not the same as an account. The server answers this, because both a guest
 * and a signed-in customer are httpOnly cookies no script can read: one
 * question to one endpoint covers both.
 *
 * The redirect goes to our own sign-in page -- a separate entry outside React,
 * like the portal logins -- and carries the path back, so the cart is waiting
 * whichever way they choose to come back.
 */
export function RequireCustomer({
  children,
  allowOrderToken = false,
}: {
  children: ReactNode;
  /** Let an order-view token (#t=, or ?t= from older links) through unasked.
   *
   *  Order tracking sets it, because the link in a guest's confirmation email
   *  is meant to be opened on whatever device the email is read on -- a phone
   *  that holds no cookie. Bouncing it to sign-in would make that link useless
   *  to exactly the people it is for.
   *
   *  It grants nothing: the token names one order, is signed by the API and
   *  is checked there. A wrong one gets the same 404 as a wrong order id. */
  allowOrderToken?: boolean;
}) {
  const dispatch = useAppDispatch();
  // Read once, on the first render that can see it: takeOrderToken moves the
  // token out of the address bar, so a later reader would find no token and
  // must get the same answer from where this put it.
  const [hasOrderToken] = useState(() => (allowOrderToken ? takeOrderToken() !== null : false));
  const { data, error, isLoading, isFetching, refetch } = useCustomerSessionQuery(undefined, {
    skip: hasOrderToken,
  });

  useEffect(() => {
    if (data) {
      // An answer, so whatever round trip to sign-in got us here is over and
      // the loop breaker's count means nothing now.
      loginSucceeded();
      dispatch(
        sessionEstablished({
          portal: "customer",
          email: data.email,
          fullName: data.full_name,
        }),
      );
    } else if (error) dispatch(sessionEnded());
  }, [data, error, dispatch]);

  if (hasOrderToken) return <>{children}</>;
  // "Not asked yet" is not "nobody", exactly as in the portals.
  if (isLoading) return <Booting />;
  if (data) {
    // An account that never passed a consent step: "sign in with Google" on
    // the sign-in page creates an account for someone new, and that page has
    // no checkbox. Asked here rather than at a URL of its own, so there is
    // nothing to arrive at out of order.
    //
    // Guests are excluded on purpose: they are shown the same sentence above
    // the checkout button and their agreement is recorded with the order.
    if (!data.is_guest && !data.terms_accepted) return <AgreeToTerms email={data.email} />;
    return <>{children}</>;
  }
  // Nobody yet. The sign-in page offers both an account and continuing as a
  // guest.
  if (error instanceof ApiError && error.status === 401)
    return <SendToLogin loginPath="/account/sign-in" />;
  if (error) return <SignInUnreachable retry={refetch} retrying={isFetching} />;
  return <Booting />;
}

/** Scoped to the routes that need an identity: browsing and both portals
 *  work without it. */
function SignInUnreachable({ retry, retrying }: Reconnect) {
  useReconnect(retry, retrying);
  return (
    <StatePage
      title="Can't reach sign-in"
      busy={retrying}
      action={
        <a href="/" className="btn-quiet">
          Back to the menu
        </a>
      }
    >
      This page will retry by itself. You can still browse the menu.
    </StatePage>
  );
}
