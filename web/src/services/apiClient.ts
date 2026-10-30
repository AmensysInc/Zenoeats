const BASE = import.meta.env.VITE_API_BASE ?? "/api/v1";

/** Nothing should hang the UI forever. A request that has not answered by now
 *  is not going to. */
const DEFAULT_TIMEOUT_MS = 15_000;

export class ApiError extends Error {
  code: string;
  /** HTTP status, or 0 when the request never reached the server. */
  status: number;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

export type RequestOptions = {
  method?: string;
  body?: unknown;
  idempotencyKey?: string;
  /** A guest's order-view token. Sent as a header, never in the URL: a query
   *  string is written to every access log the request passes through. */
  orderToken?: string | null;
  signal?: AbortSignal;
  timeoutMs?: number;
};

/**
 * The single place a request is made.
 *
 * Every failure reaching a caller is an ApiError with a usable `code`, so
 * `catch (e) { (e as ApiError).code }` is never a lie. The one exception is a
 * caller-initiated abort, rethrown untouched so callers can tell "I cancelled
 * this" apart from "it failed".
 */
export async function request<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  // A file upload is FormData, sent as the browser encodes it. Its
  // Content-Type carries the multipart boundary, which only the browser can
  // write, so naming one here would leave the server unable to find the file.
  const isForm = typeof FormData !== "undefined" && opts.body instanceof FormData;
  const headers: Record<string, string> = isForm ? {} : { "Content-Type": "application/json" };
  if (opts.idempotencyKey) headers["Idempotency-Key"] = opts.idempotencyKey;
  if (opts.orderToken) headers["X-Order-Token"] = opts.orderToken;

  const timeout = new AbortController();
  const timer = setTimeout(() => timeout.abort(), opts.timeoutMs ?? DEFAULT_TIMEOUT_MS);
  const unlink = link(opts.signal, timeout);

  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, {
      method: opts.method ?? "GET",
      headers,
      body:
        opts.body === undefined
          ? undefined
          : isForm
            ? (opts.body as FormData)
            : JSON.stringify(opts.body),
      cache: "no-store",
      // Portal sessions are httpOnly cookies. Same-origin is the fetch default, but
      // stating it means a future change of API origin fails loudly rather
      // than silently dropping the session.
      credentials: "same-origin",
      signal: timeout.signal,
    });
  } catch (cause) {
    if (opts.signal?.aborted) throw cause; // the caller cancelled; their business
    if (timeout.signal.aborted) {
      throw new ApiError(0, "TIMEOUT", "The server took too long to respond.");
    }
    // fetch rejects with a bare TypeError for DNS, refused connections,
    // offline and CORS alike. None of those carry a code of their own.
    throw new ApiError(0, "NETWORK_ERROR", "Couldn't reach the server. Check your connection.");
  } finally {
    clearTimeout(timer);
    unlink();
  }

  if (!res.ok) throw await toApiError(res);
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

/** FastAPI wraps HTTPException detail; the 500 handler does not. Both shapes
 *  are handled, plus a non-JSON body such as a proxy 502 or an HTML page. */
export async function toApiError(res: Response): Promise<ApiError> {
  let code = "UNKNOWN";
  let message = "Something went wrong.";
  try {
    const data = await res.json();
    const detail = data?.detail ?? data;
    if (Array.isArray(detail)) {
      // FastAPI's raw request-validation shape: a list of {loc, msg}. The API
      // normalises these into the usual envelope, so reaching this branch
      // means something answered without going through that handler. Read it
      // anyway rather than falling through to "Something went wrong.", which
      // is what a blank number field used to produce.
      const first = detail[0];
      const where = (first?.loc ?? [])
        .filter((part: unknown) => part !== "body")
        .join(" ")
        .replace(/_/g, " ");
      code = "VALIDATION_ERROR";
      if (first?.msg) message = where ? `${where}: ${first.msg}` : first.msg;
    } else if (detail && typeof detail === "object") {
      code = detail.code ?? code;
      message = detail.message ?? message;
    } else if (typeof detail === "string") {
      message = detail;
    }
  } catch {
    /* non-JSON error body */
  }
  return new ApiError(res.status, code, message);
}

/** Abort `child` when `parent` aborts, without accumulating listeners on a
 *  long-lived caller signal. */
function link(parent: AbortSignal | undefined, child: AbortController): () => void {
  if (!parent) return () => {};
  if (parent.aborted) {
    child.abort();
    return () => {};
  }
  const onAbort = () => child.abort();
  parent.addEventListener("abort", onAbort, { once: true });
  return () => parent.removeEventListener("abort", onAbort);
}

/** Message for display. Accepts anything a catch block can receive. */
export function errorMessage(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  if (e instanceof Error) return e.message;
  return "Something went wrong.";
}

/** True when the throw came from a cancelled request rather than a failure. */
export function isAbort(e: unknown): boolean {
  return e instanceof DOMException && e.name === "AbortError";
}

/**
 * Idempotency keys are generated once per checkout attempt and reused on
 * retry, so a flaky network cannot produce two orders or two charges.
 *
 * crypto.randomUUID() is restricted to secure contexts. Development serves the
 * app over plain HTTP on a .local subdomain, which is not one, so the method
 * is undefined in exactly the place the app is normally used and checkout
 * throws on render. getRandomValues carries no such restriction.
 */
export function newIdempotencyKey(): string {
  const c: Crypto | undefined = globalThis.crypto;
  if (typeof c?.randomUUID === "function") return c.randomUUID();

  if (typeof c?.getRandomValues !== "function") {
    throw new Error("No secure random source available; cannot start checkout.");
  }
  const b = c.getRandomValues(new Uint8Array(16));
  b[6] = (b[6]! & 0x0f) | 0x40; // version 4
  b[8] = (b[8]! & 0x3f) | 0x80; // variant 10xx
  const hex = Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}
