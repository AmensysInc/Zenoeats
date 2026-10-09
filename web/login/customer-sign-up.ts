import { errorMessage, request } from "@/services/apiClient";
import {
  MIN_PASSWORD_LENGTH, activateAndContinue, clerkErrorMessage, el, loadClerk, wireGoogleButton,
  nextPath, paintWordmarks, params, readCode, showMessage, wireSocialButtons, wirePasswordPair,
  withNext,
  type ClerkInstance,
} from "./customer-shared";

/**
 * Customer sign-up through Clerk: email and password, or a social provider.
 *
 * Three steps can appear on this one page:
 *
 *   form      email + password (with Google, Apple and Facebook above it)
 *   continue  after a social sign-up, whatever Clerk still needs that the
 *             provider did not give it -- an email, a name, consent
 *   code      the 6-digit code Clerk emails to prove the address
 *
 * The account is usable once Clerk reports the sign-up complete.
 */

type SignUp = NonNullable<ClerkInstance["client"]>["signUp"];

// What this page knows how to collect. Anything else Clerk asks for (a phone
// number, a username) is reported rather than silently stuck on.
const COLLECTABLE = new Set(["email_address", "first_name", "last_name", "legal_accepted"]);

const form = el<HTMLFormElement>("sign-up-form");
const nameInput = el<HTMLInputElement>("full-name");
const emailInput = el<HTMLInputElement>("email");
const passwordInput = el<HTMLInputElement>("password");
const confirmInput = el<HTMLInputElement>("confirm");
const errorBox = el<HTMLParagraphElement>("error");
const legalInput = el<HTMLInputElement>("legal");
const submitButton = el<HTMLButtonElement>("submit");
const continueForm = el<HTMLFormElement>("continue-form");
const continueEmail = el<HTMLInputElement>("continue-email");
const continueName = el<HTMLInputElement>("continue-name");
const continueLegal = el<HTMLInputElement>("continue-legal");
const continueButton = el<HTMLButtonElement>("continue-submit");
const codeForm = el<HTMLFormElement>("code-form");
const codeInput = el<HTMLInputElement>("code");
const codeButton = el<HTMLButtonElement>("code-submit");
const resendButton = el<HTMLButtonElement>("resend");

el<HTMLAnchorElement>("sign-in").href = withNext("/account/sign-in");

void paintWordmarks();

// Offered when the API has an OAuth client; see wireGoogleButton.
void wireGoogleButton(el("social-section"), el("social-buttons"));

/**
 * Creating an account, against our own API.
 *
 * At module scope and not inside start(), because this must work on a
 * deployment with no identity provider: the API hashes the password with
 * Argon2id and mints the session cookie itself
 * (backend/app/core/customer_auth.py).
 *
 * The name and consent fields are always shown now. They used to appear only
 * if Clerk's instance settings asked for them, which meant the fields this
 * form actually submits were controlled by a third party's dashboard.
 */
el("name-field").hidden = false;

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

void loadClerk(errorBox).then((clerk) => {
  if (clerk) start(clerk);
});

function show(section: "form" | "continue" | "code"): void {
  el("form-section").hidden = section !== "form";
  el("continue-section").hidden = section !== "continue";
  el("code-section").hidden = section !== "code";
}

function splitName(full: string): { firstName?: string; lastName?: string } {
  const [firstName, ...rest] = full.trim().split(/\s+/).filter(Boolean);
  return firstName ? { firstName, lastName: rest.join(" ") || undefined } : {};
}

function start(clerk: ClerkInstance): void {
  if (clerk.user) {
    window.location.replace(nextPath());
    return;
  }

  const signUp = (): SignUp => clerk.client!.signUp;

  /**
   * Move an unfinished sign-up forward: complete it, ask for a code, or ask
   * for the missing details. Used after every step, so the page always shows
   * whatever Clerk is actually waiting for.
   */
  async function advance(current: SignUp): Promise<void> {
    if (current.status === "complete") {
      await activateAndContinue(clerk, current.createdSessionId);
      return;
    }

    const missing = current.missingFields ?? [];
    const unsupported = missing.filter((field) => !COLLECTABLE.has(field));
    if (unsupported.length) {
      show("form");
      showMessage(
        errorBox,
        `Your account needs details this page can't collect (${unsupported
          .join(", ")
          .replace(/_/g, " ")}). Try another way to sign up.`,
      );
      return;
    }

    if (missing.length === 0 && (current.unverifiedFields ?? []).includes("email_address")) {
      await current.prepareEmailAddressVerification({ strategy: "email_code" });
      el("sent-to").textContent = current.emailAddress ?? "your email";
      show("code");
      codeInput.focus();
      return;
    }

    if (missing.length === 0) {
      show("form");
      showMessage(errorBox, "That didn't finish creating your account. Start again.");
      return;
    }

    el("continue-email-field").hidden = !missing.includes("email_address");
    el("continue-name-field").hidden =
      !missing.includes("first_name") && !missing.includes("last_name");
    el("continue-legal-field").hidden = !missing.includes("legal_accepted");
    show("continue");
    (missing.includes("email_address") ? continueEmail : continueName).focus();
  }

  // Sent here by the Google, Apple or Facebook callback with a sign-up Clerk
  // could not finish by itself.
  const pending = signUp();
  if (params().get("continue") === "1" && pending?.status === "missing_requirements") {
    void advance(pending).catch((e) => showMessage(errorBox, clerkErrorMessage(e)));
  } else if (params().get("continue") === "1") {
    showMessage(errorBox, "That sign-up has expired. Start again.");
  }

  // The checkbox gates the provider buttons as well as the form. Without
  // this, "Sign up with Google" walks past the only place agreement is asked
  // for -- Clerk completes that sign-up itself and never sends anyone back to
  // the consent step.
  wireSocialButtons(
    clerk, "sign-up", el("social-buttons"), el("social-section"), errorBox,
    () => legalInput.checked,
  );
  continueForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    showMessage(errorBox, null);
    const missing = signUp().missingFields ?? [];

    const update: Parameters<SignUp["update"]>[0] = {};
    if (missing.includes("email_address")) {
      const email = continueEmail.value.trim();
      if (!email.includes("@")) {
        showMessage(errorBox, "Enter your email address.");
        return;
      }
      update.emailAddress = email;
    }
    if (missing.includes("first_name") || missing.includes("last_name")) {
      const name = splitName(continueName.value);
      if (!name.firstName) {
        showMessage(errorBox, "Enter your name.");
        return;
      }
      Object.assign(update, name);
    }
    if (missing.includes("legal_accepted")) {
      if (!continueLegal.checked) {
        showMessage(errorBox, "Agree to the terms to create your account.");
        return;
      }
      update.legalAccepted = true;
    }

    continueButton.disabled = true;
    continueButton.textContent = "Saving…";
    try {
      await advance(await signUp().update(update));
    } catch (e) {
      showMessage(errorBox, clerkErrorMessage(e));
    }
    continueButton.disabled = false;
    continueButton.textContent = "Continue";
  });

  codeForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    showMessage(errorBox, null);
    const code = readCode(codeInput);
    if (code.length !== 6) {
      showMessage(errorBox, "Enter the 6-digit code from your email.");
      return;
    }

    codeButton.disabled = true;
    codeButton.textContent = "Checking…";
    try {
      await advance(await signUp().attemptEmailAddressVerification({ code }));
    } catch (e) {
      showMessage(errorBox, clerkErrorMessage(e));
    }
    codeButton.disabled = false;
    codeButton.textContent = "Create account";
  });

  resendButton.addEventListener("click", async () => {
    showMessage(errorBox, null);
    resendButton.disabled = true;
    try {
      await signUp().prepareEmailAddressVerification({ strategy: "email_code" });
      codeInput.value = "";
      codeInput.focus();
    } catch (e) {
      showMessage(errorBox, clerkErrorMessage(e));
    }
    // A short pause, so a double tap is not two emails.
    setTimeout(() => {
      resendButton.disabled = false;
    }, 15_000);
  });
}
