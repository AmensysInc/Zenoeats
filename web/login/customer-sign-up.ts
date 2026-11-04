import { errorMessage, request } from "@/services/apiClient";
import {
  MIN_PASSWORD_LENGTH, el, nextPath, paintWordmarks, showMessage, signedInToAccount,
  wirePasswordPair, wireSocialButtons, withNext,
} from "./customer-shared";

/**
 * Creating a customer account: Google, Apple or Facebook, or an email and
 * password held by our own API (backend/app/core/customer_auth.py).
 */

const form = el<HTMLFormElement>("sign-up-form");
const nameInput = el<HTMLInputElement>("full-name");
const emailInput = el<HTMLInputElement>("email");
const passwordInput = el<HTMLInputElement>("password");
const confirmInput = el<HTMLInputElement>("confirm");
const errorBox = el<HTMLParagraphElement>("error");
const legalInput = el<HTMLInputElement>("legal");
const submitButton = el<HTMLButtonElement>("submit");

el<HTMLAnchorElement>("sign-in").href = withNext("/account/sign-in");

void paintWordmarks();

// Nothing to create for somebody who already has an account open here.
void signedInToAccount().then((signedIn) => {
  if (signedIn) window.location.replace(nextPath());
});

// The checkbox gates the provider buttons as well as the form: the account is
// created at the far end of that redirect, and this is the only place on the
// way there that asks.
void wireSocialButtons(
  "sign-up", el("social-buttons"), el("social-section"), errorBox,
  () => legalInput.checked,
);

let passwordsValid = false;
const refresh = () => {
  submitButton.disabled = !(
    passwordsValid && emailInput.value.includes("@") && legalInput.checked
  );
};
emailInput.addEventListener("input", refresh);
legalInput.addEventListener("change", refresh);
wirePasswordPair(
  MIN_PASSWORD_LENGTH, passwordInput, confirmInput, el("length-hint"), el("match-hint"),
  (valid) => {
    passwordsValid = valid;
    refresh();
  },
);

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  showMessage(errorBox, null);
  submitButton.disabled = true;
  submitButton.textContent = "Creating account…";

  try {
    await request("/customer/register", {
      method: "POST",
      body: {
        email: emailInput.value.trim(),
        password: passwordInput.value,
        full_name: nameInput.value.trim() || null,
        terms_accepted: legalInput.checked,
      },
    });
    // Registering signs them in, so the cookie is already set: a full
    // navigation boots the app holding it.
    window.location.replace(nextPath());
    return;
  } catch (e) {
    // The API names the problem: an address already in use, a password under
    // the minimum, a spent rate-limit budget.
    showMessage(errorBox, errorMessage(e));
  }
  submitButton.textContent = "Create account";
  refresh();
});
