import { errorMessage, request } from "@/services/apiClient";
import {
  RETURN_ERRORS, el, nextPath, paintRestaurantName, paintWordmarks, params, showMessage,
  signedInToAccount, wireSocialButtons, withNext,
} from "./customer-shared";

/**
 * Customer sign-in: Google, Apple or Facebook, an email and password held by
 * our own API, or continuing as a guest.
 *
 * Reached from checkout carrying ?next=, so the cart is waiting afterwards; and
 * from a social sign-in that did not complete, carrying ?error=.
 */

const form = el<HTMLFormElement>("login-form");
const emailInput = el<HTMLInputElement>("email");
const passwordInput = el<HTMLInputElement>("password");
const errorBox = el<HTMLParagraphElement>("error");
const submitButton = el<HTMLButtonElement>("submit");

el<HTMLAnchorElement>("forgot").href = withNext("/account/forgot-password");
el<HTMLAnchorElement>("sign-up").href = withNext("/account/sign-up");

const returned = params().get("error");
if (returned) showMessage(errorBox, RETURN_ERRORS[returned] ?? "Sign-in didn't complete. Try again.");

void paintWordmarks();

// Nothing to do here for somebody who already has an account open. Asked of the
// same side as the checkout guard, so the two can never send each other round
// in a loop. A guest stays: signing in is what they came to do.
void signedInToAccount().then((signedIn) => {
  if (signedIn) window.location.replace(nextPath());
});

void wireSocialButtons("sign-in", el("social-buttons"), el("social-section"), errorBox);

void paintRestaurantName(el("heading"), (name) => `Sign in to order from ${name}`).then(
  (hasRestaurant) => {
    if (hasRestaurant) wireGuestCheckout();
  },
);

function refreshSubmit(): void {
  submitButton.disabled = !(emailInput.value.trim() && passwordInput.value);
}
emailInput.addEventListener("input", refreshSubmit);
passwordInput.addEventListener("input", refreshSubmit);
refreshSubmit();

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  showMessage(errorBox, null);

  const email = emailInput.value.trim();
  const password = passwordInput.value;
  if (!email || !password) {
    showMessage(errorBox, "Enter your email and password.");
    return;
  }

  setBusy(true);
  try {
    await request("/customer/login", { method: "POST", body: { email, password } });
    // A full navigation rather than a client route: the session is an
    // httpOnly cookie, so the app has to boot holding it.
    window.location.replace(nextPath());
  } catch (e) {
    showMessage(errorBox, errorMessage(e));
    passwordInput.select();
    setBusy(false);
  }
});

/**
 * "Continue as guest": order with no account behind it.
 *
 * Nothing here is verified. The address is where the receipt and the link to
 * the order are sent, and the session it creates is an httpOnly cookie -- so
 * losing this browser loses the order, which is what the panel says before
 * anyone chooses it.
 *
 */
function wireGuestCheckout(): void {
  const section = el<HTMLElement>("guest-section");
  const guestForm = el<HTMLFormElement>("guest-form");
  const guestEmail = el<HTMLInputElement>("guest-email");
  const guestName = el<HTMLInputElement>("guest-name");
  const guestButton = el<HTMLButtonElement>("guest-submit");
  const guestError = el<HTMLParagraphElement>("guest-error");
  const reveal = el<HTMLButtonElement>("guest-reveal");
  section.hidden = false;

  // Choosing guest commits the page to it: the account half goes away and
  // what is left is the two things a guest order needs. Leaving a password
  // form and a "create an account" link above the fields would be offering
  // the choice again to somebody who has already made it.
  //
  // A reload brings the full page back, which is the way out of a mis-press.
  reveal.addEventListener("click", () => {
    reveal.hidden = true;
    el("guest-why").hidden = true;
    reveal.setAttribute("aria-expanded", "true");

    // The account half. A class as well as the attribute, because the provider
    // buttons may still be on their way and reveal their section on arrival.
    el("social-section").hidden = true;
    el("social-section").classList.add("hidden");
    form.hidden = true;
    el("sign-up").closest(".auth-foot")?.setAttribute("hidden", "");

    // Guest is the whole page now, so the rule and spacing that separated it
    // from the form above come off with the form.
    section.classList.remove(
      "mt-[22px]", "border-t", "border-hairline", "pt-[22px]",
      "sm:mt-[26px]", "sm:pt-[26px]",
    );
    // The h1 still said "Sign in to order" above a page with no sign-in on
    // it. Rewritten from the name the restaurant is known by, which
    // paintRestaurantName has already put in the heading.
    const heading = el("heading");
    const known = heading.textContent?.replace(/^Sign in to order from /, "") ?? "";
    heading.textContent = known && known !== heading.textContent
      ? `Order from ${known}`
      : "Start your order";

    el("guest-heading").classList.remove("sr-only");
    el("guest-heading").className =
      "mb-[18px] font-display text-[26px] leading-[1.2] tracking-[-.6px]";
    el("guest-heading").textContent = "Order as a guest";

    guestForm.hidden = false;
    // Straight into the one field that is required.
    guestEmail.focus();
  });

  guestForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    showMessage(guestError, null);

    const email = guestEmail.value.trim();
    if (!email) {
      showMessage(guestError, "Enter an email address we can send your receipt to.");
      guestEmail.focus();
      return;
    }

    guestButton.disabled = true;
    guestButton.textContent = "Setting up…";
    try {
      await request("/orders/guest-session", {
        method: "POST",
        body: { email, full_name: guestName.value.trim() || null },
      });
      // A full navigation, like a finished sign-in: the app boots holding the
      // cookie, and the cart is where they left it.
      window.location.replace(nextPath());
    } catch (e) {
      showMessage(guestError, errorMessage(e));
      guestButton.disabled = false;
      guestButton.textContent = "Continue as guest";
    }
  });
}

function setBusy(busy: boolean): void {
  submitButton.textContent = busy ? "Signing in…" : "Sign in";
  // Not a plain `disabled = busy`: coming back from a failed attempt must
  // re-apply the field check rather than enable an empty form.
  if (busy) submitButton.disabled = true;
  else refreshSubmit();
}
