import { errorMessage, request } from "@/services/apiClient";
import {
  MIN_PASSWORD_LENGTH, el, paintWordmarks, params, showMessage, wirePasswordPair,
  withNext,
} from "./customer-shared";

/**
 * Resetting a forgotten password, against our own API.
 *
 * One page, two states, decided by whether the URL carries a token:
 *
 *   no token    ask for the address, POST /customer/password/forgot
 *   ?token=…    choose a new password, POST /customer/password/reset
 *
 * The token in the link IS the credential, which is why there is no code to
 * type any more -- that belonged to Clerk, which emailed a six-digit code
 * and held the password itself. The link is signed, expires in an hour, and
 * stops working the moment any password change lands on the account
 * (backend/app/core/customer_auth.py).
 *
 * Asking is deliberately uninformative. The API answers 204 whether or not
 * the address has an account and so does this page, because a form that
 * says "no account with that email" is a way to ask us who our customers
 * are.
 */

const errorBox = el<HTMLParagraphElement>("error");
const emailSection = el<HTMLElement>("email-section");
const emailForm = el<HTMLFormElement>("email-form");
const emailInput = el<HTMLInputElement>("email");
const emailSubmit = el<HTMLButtonElement>("email-submit");
const resetSection = el<HTMLElement>("reset-section");
const resetForm = el<HTMLFormElement>("reset-form");
const passwordInput = el<HTMLInputElement>("password");
const confirmInput = el<HTMLInputElement>("confirm");
const resetSubmit = el<HTMLButtonElement>("reset-submit");

el<HTMLAnchorElement>("sign-in").href = withNext("/account/sign-in");

void paintWordmarks();

const token = params().get("token");

if (token) {
  showResetStep(token);
} else {
  showAskStep();
}

/**
 * Step one: which address?
 *
 * The confirmation replaces the form rather than sitting under it. Leaving a
 * filled-in form beside "we've sent you a link" invites a second press, and
 * the second link silently invalidates the first -- so someone who pressed
 * twice would find the link in their inbox already dead.
 */
function showAskStep(): void {
  resetSection.hidden = true;
  emailSection.hidden = false;

  const refresh = () => {
    emailSubmit.disabled = !emailInput.value.includes("@");
  };
  emailInput.addEventListener("input", refresh);
  refresh();

  emailForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    showMessage(errorBox, null);
    emailSubmit.disabled = true;
    emailSubmit.textContent = "Sending…";

    try {
      await request("/customer/password/forgot", {
        method: "POST",
        body: { email: emailInput.value.trim() },
      });
      emailForm.hidden = true;
      el("heading").textContent = "Check your email";
      const intro = emailSection.querySelector<HTMLElement>(".auth-intro");
      if (intro) {
        // Deliberately not "we've sent you a link": we will not say whether
        // there was an account to send one to.
        intro.textContent =
          "If there's an account for that address, a link to choose a new password " +
          "is on its way. It works once and expires in an hour.";
      }
    } catch (e) {
      showMessage(errorBox, errorMessage(e));
      emailSubmit.textContent = "Send the link";
      refresh();
    }
  });
}

/** Step two: the new password, with the token from the link. */
function showResetStep(resetToken: string): void {
  emailSection.hidden = true;
  resetSection.hidden = false;
  el("heading").textContent = "Choose a new password";
  el("resend-foot").hidden = true;

  let passwordsValid = false;
  const refresh = () => {
    resetSubmit.disabled = !passwordsValid;
  };
  wirePasswordPair(
    MIN_PASSWORD_LENGTH, passwordInput, confirmInput, el("length-hint"), el("match-hint"),
    (valid) => {
      passwordsValid = valid;
      refresh();
    },
  );
  refresh();

  resetForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    showMessage(errorBox, null);
    resetSubmit.disabled = true;
    resetSubmit.textContent = "Saving…";

    try {
      await request("/customer/password/reset", {
        method: "POST",
        body: { token: resetToken, password: passwordInput.value },
      });
    } catch (e) {
      showMessage(errorBox, errorMessage(e));
      resetSubmit.textContent = "Set password";
      refresh();
      return;
    }

    // The reset ends every session, this browser's included, so there is
    // nothing to continue into: they sign in with the password they just
    // chose, which also proves they remember it.
    el("heading").textContent = "Password changed";
    resetForm.hidden = true;
    const intro = resetSection.querySelector<HTMLElement>(".auth-intro");
    if (intro) intro.textContent = "Sign in with your new password to carry on.";
    const link = document.createElement("a");
    link.className = "btn-primary mt-5 w-full";
    link.href = withNext("/account/sign-in");
    link.textContent = "Sign in";
    intro?.after(link);
  });
}
