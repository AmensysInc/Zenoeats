import { errorMessage, request } from "@/services/apiClient";
import {
  RETURN_ERRORS, activateAndContinue, clerkErrorMessage, el, loadClerk, nextPath, wireGoogleButton,
  paintRestaurantName, paintWordmarks, params, readCode, showMessage, wireSocialButtons,
  withNext,
  type ClerkInstance,
} from "./customer-shared";

/**
 * Customer sign-in: Google, Apple, Facebook, or an email and password, through Clerk.
 *
 * Reached from checkout carrying ?next=, so the cart is waiting afterwards;
 * from a social sign-in that did not complete, carrying ?error=; and from a
 * social sign-in that needs a second step, carrying ?second_factor=1.
 */

type SignIn = NonNullable<ClerkInstance["client"]>["signIn"];

type SecondFactor = "totp" | "phone_code" | "email_code" | "backup_code";

// Preferred first: an authenticator app needs no message to arrive, and a
// backup code is the last resort it is meant to be.
const SECOND_FACTOR_ORDER: SecondFactor[] = ["totp", "phone_code", "email_code", "backup_code"];

const SWITCH_LABEL: Record<SecondFactor, string> = {
  totp: "Use your authenticator app instead",
  phone_code: "Text me a code instead",
  email_code: "Email me a code instead",
  backup_code: "Use a backup code instead",
};

const form = el<HTMLFormElement>("login-form");
const emailInput = el<HTMLInputElement>("email");
const passwordInput = el<HTMLInputElement>("password");
const errorBox = el<HTMLParagraphElement>("error");
const submitButton = el<HTMLButtonElement>("submit");
const codeForm = el<HTMLFormElement>("code-form");
const codeInput = el<HTMLInputElement>("code");
const codeButton = el<HTMLButtonElement>("code-submit");
const codeIntro = el<HTMLParagraphElement>("code-intro");
const codeLabel = el<HTMLSpanElement>("code-label");
const alternatives = el<HTMLDivElement>("code-alternatives");

el<HTMLAnchorElement>("forgot").href = withNext("/account/forgot-password");
el<HTMLAnchorElement>("sign-up").href = withNext("/account/sign-up");

const returned = params().get("error");
if (returned) showMessage(errorBox, RETURN_ERRORS[returned] ?? "Sign-in didn't complete. Try again.");

void paintWordmarks();

// Offered when the API has an OAuth client; see wireGoogleButton.
void wireGoogleButton(el("social-section"), el("social-buttons"));

void paintRestaurantName(el("heading"), (name) => `Sign in to order from ${name}`).then(
  (hasRestaurant) => {
    if (hasRestaurant) wireGuestCheckout();
  },
);

/**
 * The submit button starts disabled in the markup and is enabled here, once
 * there is something to submit.
 *
 * It used to be Clerk's start() that enabled it, so on a deployment with no
 * Clerk the button stayed greyed out forever and the form could not be sent
 * at all. Enabling it from the fields themselves is both the fix and the
 * better behaviour: it no longer depends on anything outside this page.
 */
function refreshSubmit(): void {
  submitButton.disabled = !(emailInput.value.trim() && passwordInput.value);
}
emailInput.addEventListener("input", refreshSubmit);
passwordInput.addEventListener("input", refreshSubmit);
refreshSubmit();

/**
 * Email and password, against our own API.
 *
 * Wired at module scope rather than inside start(), because this is the one
 * path that must not wait for -- or depend on -- an identity provider. The
 * API holds the Argon2id digest and mints the session cookie itself
 * (backend/app/core/customer_auth.py), so this form works on a deployment
 * with no Clerk configured at all.
 *
 * Clerk, where it is configured, is left with exactly one job: brokering
 * Google and Apple. It no longer owns passwords.
 */
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
 * Deliberately independent of Clerk: this is the path that still works when
 * Clerk is unreachable or was never configured, and it must not wait on a
 * script that may never load.
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

    // The account half.
    el("social-section").hidden = true;
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

void loadClerk(errorBox).then((clerk) => {
  if (clerk) start(clerk);
});

function start(clerk: ClerkInstance): void {
  // Already signed in: this page has nothing to offer.
  if (clerk.user) {
    window.location.replace(nextPath());
    return;
  }

  const signIn = (): SignIn => clerk.client!.signIn;
  let factor: SecondFactor | null = null;

  /** The second-step methods this sign-in offers that this page can handle,
   *  in preference order. */
  function availableFactors(attempt: SignIn): SecondFactor[] {
    const offered = new Set((attempt.supportedSecondFactors ?? []).map((f) => f.strategy));
    return SECOND_FACTOR_ORDER.filter((s) => offered.has(s));
  }

  /** Ask for (and, where it is sent, send) one second-step code. */
  async function beginSecondFactor(attempt: SignIn, chosen: SecondFactor): Promise<void> {
    const offered = attempt.supportedSecondFactors ?? [];
    const details = offered.find((f) => f.strategy === chosen);
    const where = details && "safeIdentifier" in details ? details.safeIdentifier : null;

    if (chosen === "phone_code") {
      await attempt.prepareSecondFactor({
        strategy: "phone_code",
        ...(details && "phoneNumberId" in details ? { phoneNumberId: details.phoneNumberId } : {}),
      });
      codeIntro.textContent = `We texted a 6-digit code to ${where ?? "your phone"}.`;
    } else if (chosen === "email_code") {
      await attempt.prepareSecondFactor({
        strategy: "email_code",
        ...(details && "emailAddressId" in details ? { emailAddressId: details.emailAddressId } : {}),
      });
      codeIntro.textContent = `We sent a 6-digit code to ${where ?? "your email"}.`;
    } else if (chosen === "totp") {
      codeIntro.textContent = "Enter the 6-digit code from your authenticator app.";
    } else {
      codeIntro.textContent = "Enter one of the backup codes you saved when you set up two-step verification.";
    }

    factor = chosen;
    // Backup codes are letters and digits; the others are six digits.
    const backup = chosen === "backup_code";
    codeLabel.textContent = backup ? "Backup code" : "Verification code";
    codeInput.inputMode = backup ? "text" : "numeric";
    codeInput.autocomplete = backup ? "off" : "one-time-code";
    codeInput.maxLength = backup ? 32 : 7;
    codeInput.value = "";

    alternatives.replaceChildren();
    for (const other of availableFactors(attempt)) {
      if (other === chosen) continue;
      const button = document.createElement("button");
      button.type = "button";
      button.className = "link";
      button.textContent = SWITCH_LABEL[other];
      button.addEventListener("click", async () => {
        showMessage(errorBox, null);
        try {
          await beginSecondFactor(signIn(), other);
        } catch (e) {
          showMessage(errorBox, clerkErrorMessage(e));
        }
      });
      alternatives.append(button);
    }

    el("password-section").hidden = true;
    el("code-section").hidden = false;
    codeInput.focus();
  }

  /** Finish, or move to the second step, or say plainly that we cannot. */
  async function advance(attempt: SignIn): Promise<void> {
    if (attempt.status === "complete") {
      await activateAndContinue(clerk, attempt.createdSessionId);
      return;
    }

    // needs_second_factor: two-step verification is on for this account.
    // needs_client_trust: Clerk wants this new device confirmed with a code.
    if (attempt.status === "needs_second_factor" || attempt.status === "needs_client_trust") {
      const [first] = availableFactors(attempt);
      if (first) {
        await beginSecondFactor(attempt, first);
        return;
      }
    }

    showMessage(
      errorBox,
      "This account needs a sign-in step this page doesn't support. Try another way to sign in, or reset your password.",
    );
  }

  // Sent back here by the social callback, mid-sign-in.
  const pending = signIn();
  if (params().get("second_factor") === "1" && pending?.status === "needs_second_factor") {
    void advance(pending).catch((e) => showMessage(errorBox, clerkErrorMessage(e)));
  }

  wireSocialButtons(clerk, "sign-in", el("social-buttons"), el("social-section"), errorBox);
  submitButton.disabled = false;

  codeForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    showMessage(errorBox, null);
    if (!factor) return;

    const code = factor === "backup_code" ? codeInput.value.trim() : readCode(codeInput);
    if (factor === "backup_code" ? code.length === 0 : code.length !== 6) {
      showMessage(
        errorBox,
        factor === "backup_code" ? "Enter a backup code." : "Enter the 6-digit code.",
      );
      return;
    }

    codeButton.disabled = true;
    codeButton.textContent = "Checking…";
    try {
      const attempt = await signIn().attemptSecondFactor({ strategy: factor, code });
      if (attempt.status === "complete") {
        await activateAndContinue(clerk, attempt.createdSessionId);
        return;
      }
      showMessage(errorBox, "That didn't finish signing you in. Start again.");
    } catch (e) {
      showMessage(errorBox, clerkErrorMessage(e));
    }
    codeButton.disabled = false;
    codeButton.textContent = "Continue";
  });
}
