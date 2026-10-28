import { getClerk } from "@/services/clerk";
import { accountUrl, apiSessionLive, nextPath, paintWordmarks } from "./customer-shared";

/**
 * Where Google, Apple or Facebook send the browser back to, by way of Clerk.
 *
 * Clerk finishes the sign-in here -- or the sign-up, when a "sign in" turns
 * out to be someone new -- and then navigates on to where the customer was
 * going. When it cannot finish on its own, it sends the customer to one of
 * our pages to supply the rest, and each of those URLs is named below:
 *
 *   still needs details   the provider gave no email, or the instance asks
 *                         for consent -> sign-up, "continue" step
 *   email not verified    -> sign-up, code step
 *   two-step verification -> sign-in, code step
 *
 * Anything that goes wrong lands on our sign-in page with a reason.
 */

function backToSignIn(): void {
  window.location.replace(accountUrl("/account/sign-in", { error: "social_failed" }));
}

void paintWordmarks();

void (async () => {
  try {
    const clerk = await getClerk();
    await clerk.handleRedirectCallback({
      signInUrl: accountUrl("/account/sign-in"),
      signUpUrl: accountUrl("/account/sign-up"),
      continueSignUpUrl: accountUrl("/account/sign-up", { continue: "1" }),
      verifyEmailAddressUrl: accountUrl("/account/sign-up", { continue: "1" }),
      secondFactorUrl: accountUrl("/account/sign-in", { second_factor: "1" }),
      signInFallbackRedirectUrl: nextPath(),
      signUpFallbackRedirectUrl: nextPath(),
    });
    // handleRedirectCallback navigates by itself. Arriving here signed in
    // means it had nowhere to send us; carry on to the destination -- once
    // the API has agreed there is a session, so a provider leg that Clerk
    // finished but the API will not accept lands on the sign-in page rather
    // than in a loop between it and checkout.
    if (clerk.user && (await apiSessionLive(clerk))) window.location.replace(nextPath());
    else backToSignIn();
  } catch {
    backToSignIn();
  }
})();
