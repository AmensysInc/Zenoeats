import { useEffect } from "react";
import { useClaimReferralMutation, useCustomerSessionQuery } from "./storefrontApi";

/** Where an arriving code waits until there is somebody to attach it to. */
const KEY = "zenoeats.referral";

/**
 * Remember the referral link somebody arrived on, and claim it once they
 * have an account.
 *
 * The two halves rarely happen together. A link is opened by somebody who
 * has never been here, who then reads the menu, makes an account and orders
 * -- several pages and possibly several minutes later. So the code is kept
 * until there is a customer to attach it to, and cleared once it has been
 * offered.
 *
 * Offered, not accepted: the server decides, and answers the same way
 * either way. A visitor who already orders here, or who opened their own
 * link, is simply not claimed, and nothing on screen changes -- which is
 * the point. Telling them why would describe the rules to the one person
 * trying to get round them.
 */
export function useReferralCode() {
  const session = useCustomerSessionQuery();
  const [claim] = useClaimReferralMutation();

  // Take the code out of the URL as soon as it is seen, so it survives the
  // reader wandering off to another page -- and so a link that gets shared
  // onward from the address bar does not carry somebody else's code.
  useEffect(() => {
    const code = new URLSearchParams(window.location.search).get("ref");
    if (!code) return;
    try {
      window.localStorage.setItem(KEY, code);
    } catch {
      // Private browsing, or storage refused. The claim below simply will
      // not happen, which costs a referral and breaks nothing.
    }
    const url = new URL(window.location.href);
    url.searchParams.delete("ref");
    window.history.replaceState({}, "", url.toString());
  }, []);

  const customer = session.data && !session.data.is_guest;
  useEffect(() => {
    if (!customer) return;
    let code: string | null = null;
    try {
      code = window.localStorage.getItem(KEY);
    } catch {
      return;
    }
    if (!code) return;
    // Cleared whatever the answer is. A code that failed once will fail
    // every time -- the reasons are all permanent -- and retrying it on
    // every page view would be a request that can never succeed.
    try {
      window.localStorage.removeItem(KEY);
    } catch {
      /* nothing to do */
    }
    void claim(code);
  }, [customer, claim]);
}
