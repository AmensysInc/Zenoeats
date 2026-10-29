/**
 * The loop breaker: how often one page may be sent to the same login before
 * we stop and say so.
 *
 * A guard and a login page can disagree. The guard asks the API; the login
 * page asks whatever identity provider it uses; and when one believes there
 * is a session and the other does not, each sends the customer to the other
 * -- checkout to sign-in, sign-in straight back to checkout, several times a
 * second. It is the worst failure in the product to be caught in, because
 * there is no screen to read and no button to press. On a phone it looks
 * like the site is broken rather than like a session has gone stale.
 *
 * The real fix is making the two agree, and both sides now ask the API. This
 * is the guarantee underneath that: whatever else goes wrong between a guard
 * and a login page, it ends in a sentence rather than a loop.
 *
 * Counted per destination and over a window, so an ordinary day -- sign in,
 * come back an hour later, sign in again -- never trips it. Three in thirty
 * seconds is not somebody signing in.
 *
 * Free of React and of the DOM beyond sessionStorage, so it can be tested as
 * the small piece of arithmetic it is.
 */

export const MAX_BOUNCES = 3;
export const BOUNCE_WINDOW_MS = 30_000;
const KEY = "ze:login-bounces";

type Bounces = { next: string; count: number; at: number };

/**
 * sessionStorage, where there is one.
 *
 * Private modes, embedded browsers and anything with site data switched off
 * throw on access rather than hand back null -- and a loop breaker that
 * breaks the page instead is not an improvement. Without storage the count
 * never rises, so the breaker never trips and behaviour is exactly what it
 * was before it existed.
 */
function read(): Bounces | null {
  try {
    const raw = window.sessionStorage.getItem(KEY);
    if (!raw) return null;
    const value = JSON.parse(raw) as Partial<Bounces>;
    if (typeof value.next !== "string") return null;
    if (typeof value.count !== "number" || typeof value.at !== "number") return null;
    return { next: value.next, count: value.count, at: value.at };
  } catch {
    return null;
  }
}

function write(value: Bounces | null): void {
  try {
    if (value) window.sessionStorage.setItem(KEY, JSON.stringify(value));
    else window.sessionStorage.removeItem(KEY);
  } catch {
    /* Nothing to remember it in. */
  }
}

/**
 * Count one more redirect to a login page on the way to `next`, and say
 * whether it may go ahead.
 *
 * False means this page has been sent to a login this many times in the last
 * half minute and is getting nowhere. The count is not raised past the limit:
 * the answer stays false for as long as the attempts keep coming, rather than
 * running away.
 */
export function mayBounce(next: string, now: number = Date.now()): boolean {
  const previous = read();
  const continuing = previous !== null && previous.next === next
    && now - previous.at < BOUNCE_WINDOW_MS;
  const count = continuing ? previous.count + 1 : 1;
  if (count > MAX_BOUNCES) {
    // Held at the limit, and the clock moved on, so a page still bouncing
    // half a minute from now is still refused rather than let through by the
    // window quietly expiring under it.
    write({ next, count: MAX_BOUNCES + 1, at: now });
    return false;
  }
  write({ next, count, at: now });
  return true;
}

/** Forget the count. Called the moment a guard gets a session, because that
 *  is proof the argument is over. */
export function loginSucceeded(): void {
  write(null);
}
