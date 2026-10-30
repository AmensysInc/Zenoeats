import { request } from "@/services/apiClient";
import {
  rememberBrand,
  rememberedBrand,
  rememberedRestaurantName,
  rememberRestaurantName,
} from "@/utils/restaurantName";
import { brandFontFamily, brandFrom, loadBrandFont, type Brand } from "@/features/storefront/brand";
import { safeNextPath } from "./safe-next";

/**
 * What the customer account pages have in common.
 *
 * The markup stays outside React like the portal logins: a separate HTML entry
 * per page, and a full navigation back into the app once signed in, because the
 * session is an httpOnly cookie the app has to boot holding.
 */

/**
 * Whether this browser already holds an account session.
 *
 * Asked of the API, which is the only side that can know: the cookie is
 * unreadable from here. A guest is not "signed in", because a guest is exactly
 * who these pages are for -- and any failure counts as "no", because the point
 * is to stay on a page with a form on it rather than to leave on a maybe.
 */
export async function signedInToAccount(): Promise<boolean> {
  try {
    const session = await request<{ is_guest: boolean }>("/orders/session");
    return !session.is_guest;
  } catch {
    return false;
  }
}

export function el<T extends HTMLElement>(id: string): T {
  const node = document.getElementById(id);
  if (!node) throw new Error(`Missing #${id}; the page markup and script disagree.`);
  return node as T;
}

export function params(): URLSearchParams {
  return new URLSearchParams(window.location.search);
}

/** Where to go afterwards. Same-origin paths only: an absolute URL here would
 *  be an open redirect. */
export function nextPath(fallback = "/"): string {
  return safeNextPath(params().get("next"), fallback);
}

/** A link to another account page that keeps the return path. */
export function withNext(path: string): string {
  const next = nextPath("");
  return next ? `${path}?next=${encodeURIComponent(next)}` : path;
}

export function showMessage(box: HTMLElement, message: string | null): void {
  box.textContent = message ?? "";
  box.hidden = !message;
}

/**
 * The shortest password the API will accept.
 *
 * Mirrors MIN_PASSWORD_LENGTH in backend/app/core/customer_auth.py, which is
 * the authority -- this copy exists only so the form can say the rule before
 * someone submits and gets refused. test_customer_accounts.py asserts the two
 * agree, because a frontend that says "at least 10" against a backend that
 * wants 12 is a form nobody can submit and no error that explains why.
 *
 * It was three separate literals before that test existed: one per login page.
 */
export const MIN_PASSWORD_LENGTH = 10;

export type SocialProviderId = "google" | "apple" | "facebook";

type SocialProvider = {
  id: SocialProviderId;
  name: string;
  /** Brand treatment each provider asks for: Google's neutral button, Apple's
   *  black one, Facebook's blue one. */
  className: string;
  icon: string;
};

const BUTTON_LAYOUT = "w-full gap-3";

// Logos are static markup written by us, never data from a request.
const SOCIAL_PROVIDERS: SocialProvider[] = [
  {
    id: "google",
    name: "Google",
    className: `btn-quiet ${BUTTON_LAYOUT}`,
    icon: `<svg aria-hidden="true" width="18" height="18" viewBox="0 0 48 48"><path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"/><path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"/><path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z"/><path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"/></svg>`,
  },
  {
    id: "apple",
    name: "Apple",
    className: `btn ${BUTTON_LAYOUT} border-black bg-black text-white hover:bg-black/85`,
    icon: `<svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="currentColor"><path d="M12.152 6.896c-.948 0-2.415-1.078-3.96-1.04-2.04.027-3.91 1.183-4.961 3.014-2.117 3.675-.546 9.103 1.519 12.09 1.013 1.454 2.208 3.09 3.792 3.039 1.52-.065 2.09-.987 3.935-.987 1.831 0 2.35.987 3.96.948 1.637-.026 2.676-1.48 3.676-2.948 1.156-1.688 1.636-3.325 1.662-3.415-.039-.013-3.182-1.221-3.22-4.857-.026-3.04 2.48-4.494 2.597-4.559-1.429-2.09-3.623-2.324-4.39-2.376-2-.156-3.675 1.09-4.61 1.09zM15.53 3.83c.843-1.012 1.4-2.427 1.245-3.83-1.207.052-2.662.805-3.532 1.818-.78.896-1.454 2.338-1.273 3.714 1.338.104 2.715-.688 3.559-1.701"/></svg>`,
  },
  {
    id: "facebook",
    name: "Facebook",
    className: `btn ${BUTTON_LAYOUT} border-[#1877F2] bg-[#1877F2] text-white hover:bg-[#166FE5]`,
    icon: `<svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="currentColor"><path d="M9.101 23.691v-7.98H6.627v-3.667h2.474v-1.58c0-4.085 1.848-5.978 5.858-5.978.401 0 .955.042 1.468.103a8.68 8.68 0 0 1 1.141.195v3.325a8.623 8.623 0 0 0-.653-.036 26.805 26.805 0 0 0-.733-.009c-.707 0-1.259.096-1.675.309a1.686 1.686 0 0 0-.679.622c-.258.42-.374.995-.374 1.752v1.297h3.919l-.386 2.103-.287 1.564h-3.246v8.245C19.396 23.238 24 18.179 24 12.044c0-6.627-5.373-12-12-12s-12 5.373-12 12c0 5.628 3.874 10.35 9.101 11.647Z"/></svg>`,
  },
];

/**
 * Offer a button per provider the API has credentials for, and reveal `section`
 * only if there is at least one. Filling a provider's settings in on the API is
 * all it takes to change what appears.
 *
 * Plain links, not fetches: the whole flow is redirects, and the API's
 * /customer/<provider>/start answers with a 302 to the provider. Carrying `next`
 * means the customer lands back where they pressed it rather than on the menu.
 */
export async function wireSocialButtons(
  mode: "sign-in" | "sign-up",
  container: HTMLElement,
  section: HTMLElement,
  errorBox: HTMLElement,
  /** Asked before the browser leaves for the provider, and refused if it
   *  answers false. Sign-up passes the consent checkbox, because the account is
   *  created at the far end of this redirect. Sign-in passes nothing: someone
   *  new arriving that way is asked by the app before they can order. */
  allowed?: () => boolean,
): Promise<void> {
  let offered: string[] = [];
  try {
    offered = (await request<{ providers: string[] }>("/customer/sign-in-options")).providers;
  } catch {
    return; // no buttons; the password form and guest ordering are unaffected
  }
  const providers = SOCIAL_PROVIDERS.filter((p) => offered.includes(p.id));
  if (providers.length === 0) return;

  const verb = mode === "sign-up" ? "Sign up" : "Continue";
  for (const provider of providers) {
    const link = document.createElement("a");
    link.className = provider.className;
    link.dataset.provider = provider.id;
    link.innerHTML = provider.icon;
    link.append(`${verb} with ${provider.name}`);
    const query = new URLSearchParams({ next: nextPath() });
    if (allowed) query.set("agreed", "true");
    link.href = `/api/v1/customer/${provider.id}/start?${query.toString()}`;
    link.addEventListener("click", (event) => {
      showMessage(errorBox, null);
      if (allowed && !allowed()) {
        event.preventDefault();
        showMessage(errorBox, "Agree to the terms to create your account.");
      }
    });
    container.append(link);
  }
  section.hidden = false;
}

/** "Sign in to order from Spice House", when the page is on a restaurant's
 *  address. The platform root has no restaurant, and says so by failing.
 *
 *  Resolves to whether there was one, because the guest panel turns on the
 *  same answer: a guest session is created against a restaurant, so there is
 *  nothing to continue as on an address that names none. */
export async function paintRestaurantName(
  target: HTMLElement,
  template: (name: string) => string,
): Promise<boolean> {
  try {
    const portal = await restaurant();
    target.textContent = template(portal.name);
    return true;
  } catch {
    /* keep the generic heading */
    return false;
  }
}

/** The restaurant this address belongs to, read once however many parts of
 *  the page want it. */
let pending: Promise<{ name: string; brand?: unknown }> | null = null;

function restaurant(): Promise<{ name: string; brand?: unknown }> {
  pending ??= request<{ name: string; brand?: unknown }>("/portal");
  return pending;
}

/** Put the restaurant's own name in the wordmarks, as the storefront header
 *  does -- its logo in place of the initial, and its own lettering or chosen
 *  font for the name. These pages belong to the restaurant being ordered
 *  from, not to the platform; on the platform root there is no restaurant
 *  and the Zenoeats wordmark in the markup stands. */
export async function paintWordmarks(): Promise<void> {
  // The page's own title, before any restaurant name is added to it, so a
  // repaint replaces the suffix rather than stacking a second one.
  const baseTitle = document.title;
  const paint = (name: string, brand: Brand) => {
    const initial = name.trim().charAt(0).toUpperCase();
    if (!brand.name_image_url) loadBrandFont(brand.name_font);
    document.querySelectorAll<HTMLElement>("[data-wordmark-name]").forEach((el) => {
      if (brand.name_image_url) {
        const img = document.createElement("img");
        img.src = brand.name_image_url;
        img.alt = name;
        img.className = "brand-name-image";
        el.replaceChildren(img);
        el.style.fontFamily = "";
      } else {
        el.textContent = name;
        el.style.fontFamily = brandFontFamily(brand.name_font) ?? "";
      }
    });
    document.querySelectorAll<HTMLElement>("[data-wordmark-mark]").forEach((el) => {
      if (brand.logo_url) {
        const img = document.createElement("img");
        img.src = brand.logo_url;
        img.alt = "";
        el.replaceChildren(img);
        el.classList.add("has-logo");
      } else {
        el.textContent = initial;
        el.classList.remove("has-logo");
      }
    });
    document.title = `${baseTitle} · ${name}`;
  };
  const same = (a: Brand, b: Brand) =>
    a.logo_url === b.logo_url && a.name_image_url === b.name_image_url && a.name_font === b.name_font;
  // Shown only once it says the right thing: the stylesheet hides the
  // wordmark until this attribute is set, so the platform name never flashes
  // up first on a restaurant's page.
  const reveal = () => document.documentElement.setAttribute("data-wordmark-ready", "");

  // The name the storefront saw on the way here, if there was one. Painted
  // straight away, before the API has been asked anything.
  const remembered = rememberedRestaurantName();
  const rememberedMark = brandFrom(rememberedBrand());
  if (remembered) {
    paint(remembered, rememberedMark);
    reveal();
  }

  try {
    const portal = await restaurant();
    const brand = brandFrom(portal.brand);
    rememberRestaurantName(portal.name);
    rememberBrand(brand);
    if (portal.name !== remembered || !same(brand, rememberedMark)) paint(portal.name, brand);
  } catch {
    // No restaurant at this address -- the platform root -- or the API is
    // unreachable. Keep whatever is showing: the remembered name, or the
    // Zenoeats wordmark in the markup.
  } finally {
    reveal();
  }
}

/** Why a social sign-in came back to the sign-in page, in words. */
export const RETURN_ERRORS: Record<string, string> = {
  social_failed: "That sign-in didn't complete. Try again.",
  social_no_email:
    "That account didn't share an email address, and we need one for your receipts. " +
    "Try another way to sign in.",
  social_unverified:
    "That account's email address isn't verified, so we can't use it to sign in. " +
    "Try another way to sign in.",
  social_email_in_use:
    "There's already an account with that email. Sign in with your password or the " +
    "way you first signed up, or use \"Forgot password\".",
  account_closed: "This account has been closed.",
};

/** Mirrors the API's password rules so the submit button enables only on input
 *  it will accept. The API enforces them regardless. */
export function wirePasswordPair(
  minLength: number,
  password: HTMLInputElement,
  confirm: HTMLInputElement,
  lengthHint: HTMLElement,
  matchHint: HTMLElement,
  onChange: (valid: boolean) => void,
): void {
  lengthHint.textContent = `At least ${minLength} characters.`;
  const validate = () => {
    const tooShort = password.value.length > 0 && password.value.length < minLength;
    const mismatch = confirm.value.length > 0 && password.value !== confirm.value;
    lengthHint.className = `mt-2 block text-caption ${tooShort ? "text-danger" : "text-muted"}`;
    matchHint.hidden = !mismatch;
    onChange(password.value.length >= minLength && password.value === confirm.value);
  };
  password.addEventListener("input", validate);
  confirm.addEventListener("input", validate);
  validate();
}
