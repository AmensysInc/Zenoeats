import { createApi, type BaseQueryFn } from "@reduxjs/toolkit/query/react";
import { ApiError, request, type RequestOptions } from "./apiClient";
import { getCustomerToken, getCustomerTokenIfSignedIn } from "./clerk";

export type QueryArgs = {
  url: string;
  method?: string;
  body?: unknown;
  /** Send the signed-in customer's Clerk session token. Fetched per request,
   *  because Clerk's tokens last about a minute and Clerk refreshes them.
   *
   *  "if-signed-in" is the same, except it will not load Clerk to find out:
   *  for endpoints a guest may reach with nothing but their cookie, on pages
   *  where most visitors are neither. */
  customerAuth?: boolean | "if-signed-in";
  idempotencyKey?: string;
  /** A guest's order-view token, sent as the X-Order-Token header. */
  orderToken?: string | null;
};

/**
 * RTK Query over the existing client rather than fetchBaseQuery.
 *
 * fetchBaseQuery would mean two request paths with two sets of behaviour. The
 * timeout, the abort linking and the normalisation of every failure into an
 * ApiError with a real `code` all live in apiClient, and the vanilla login
 * pages use it too -- so reusing it keeps one definition of what a request is.
 */
const baseQuery: BaseQueryFn<QueryArgs | string, unknown, ApiError> = async (
  args,
  api,
) => {
  const opts: QueryArgs = typeof args === "string" ? { url: args } : args;
  const safeToRepeat = (opts.method ?? "GET").toUpperCase() === "GET";
  let result = await attempt(opts, api.signal);
  // A read that got no answer is asked again before anyone is shown an error.
  // One slow moment -- an API restarting behind a deploy, a proxy recycling
  // a connection, a laptop waking up -- used to put a dead-end "can't reach
  // the server" page in front of whoever happened to load a page just then.
  // Only GETs: repeating a write is the idempotency key's job, not this one's.
  for (const delay of safeToRepeat ? RETRY_DELAYS_MS : []) {
    if (!("error" in result) || !isTransient(result.error) || api.signal.aborted) break;
    await pause(delay, api.signal);
    if (api.signal.aborted) break;
    result = await attempt(opts, api.signal);
  }
  return result;
};

/** Backoff before the second and third try of a read. */
const RETRY_DELAYS_MS = [500, 2_000];

/** Failures that say nothing about the request itself: no answer came back,
 *  or a proxy answered on behalf of an API that did not. */
export function isTransient(e: ApiError): boolean {
  if (e.status === 0) return e.code === "TIMEOUT" || e.code === "NETWORK_ERROR";
  return e.status === 502 || e.status === 503 || e.status === 504;
}

/** Waits, or gives up early when the caller loses interest -- leaving nothing
 *  attached to the signal either way. */
function pause(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const done = () => {
      clearTimeout(timer);
      signal.removeEventListener("abort", done);
      resolve();
    };
    const timer = setTimeout(done, ms);
    signal.addEventListener("abort", done, { once: true });
  });
}

async function attempt(
  opts: QueryArgs,
  signal: AbortSignal,
): Promise<{ data: unknown } | { error: ApiError }> {
  try {
    const token =
      opts.customerAuth === "if-signed-in"
        ? await getCustomerTokenIfSignedIn()
        : opts.customerAuth
          ? await getCustomerToken()
          : undefined;
    const data = await request<unknown>(opts.url, {
      method: opts.method,
      body: opts.body,
      token,
      idempotencyKey: opts.idempotencyKey,
      orderToken: opts.orderToken,
      // RTK Query aborts on unmount and on refetch; passing its signal through
      // is what makes a superseded request stop rather than land late.
      signal,
    } satisfies RequestOptions);
    return { data };
  } catch (e) {
    if (e instanceof ApiError) return { error: e };
    // A caller-initiated abort. RTK Query discards the result either way; the
    // shape just has to be an error.
    return { error: new ApiError(0, "ABORTED", "Request cancelled.") };
  }
}

/**
 * Tags are what make mutations refresh the right screens. Creating a
 * restaurant invalidates the list without any page needing to know it should
 * refetch, which is the behaviour the hand-rolled hooks did not have.
 */
export const api = createApi({
  reducerPath: "api",
  baseQuery,
  tagTypes: [
    "Storefront",
    "Restaurant",
    "AdminOrder",
    "AdminReport",
    "RestaurantLogin",
    "Portal",
    "Menu",
    "Item",
    "ItemType",
    "Combo",
    "ModifierGroup",
    "Board",
    "Staff",
    "RestaurantProfile",
    "Delivery",
    "RestaurantReport",
    "Order",
    "Session",
    "CustomerSession",
    "CustomerOrders",
    "Favourites",
  ],
  endpoints: () => ({}),
});
