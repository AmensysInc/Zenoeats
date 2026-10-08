import { api } from "@/services/api";
import type {
  Amounts, Contact, CustomerSession, Favourite, FulfillmentType, Meal, Order, OrderSummary,
  PlatformLocation, Portal,
} from "@/types";

export type QuoteItem = {
  menu_item_id: string;
  quantity: number;
  note?: string;
  modifiers: { option_id: string; quantity: number }[];
};

/** A combo as the API takes it: which deal, which choices, how many. No
 *  price, here or anywhere else the client speaks. */
export type QuoteCombo = {
  combo_id: string;
  quantity: number;
  note?: string;
  selections: {
    slot_id: string;
    menu_item_id: string;
    modifiers: { option_id: string; quantity: number }[];
  }[];
};

/**
 * The customer surface.
 *
 * Branding, menu and ordering. Which restaurant is resolved from the Host
 * header, so nothing here carries a tenant id.
 *
 * The order endpoints need a signed-in customer. Customers authenticate
 * through Clerk, so those requests carry Clerk's session token -- fetched by
 * the base query (customerAuth), never by a page.
 */
export const storefrontApi = api.injectEndpoints({
  endpoints: (build) => ({
    portal: build.query<Portal, void>({
      query: () => ({ url: "/portal" }),
      providesTags: ["Portal"],
    }),

    publicMenu: build.query<{ meals: Meal[] }, void>({
      query: () => ({ url: "/menu" }),
      providesTags: ["Menu"],
    }),

    /** Change the address on this account. The current password goes with it:
     *  a session alone must not be enough to redirect every future receipt
     *  and password-reset link. */
    changeEmail: build.mutation<CustomerSession, { email: string; password: string }>({
      query: (body) => ({ url: "/customer/email", method: "PUT", body }),
      invalidatesTags: ["CustomerSession"],
    }),

    /** End a signed-in customer's session: the API clears the cookie it set.
     *
     *  Harmless when there is no such cookie, which is what lets every
     *  sign-out path call it without first working out which kind of session
     *  it is ending. */
    customerLogout: build.mutation<void, void>({
      query: () => ({ url: "/customer/logout", method: "POST" }),
      invalidatesTags: ["CustomerSession"],
    }),

    /** Every location the platform offers, open or not.
     *
     *  Served on any host including the bare root domain, which is the only
     *  place that reads it: the root has no restaurant to resolve, so this is
     *  what it renders instead of an error. */
    locations: build.query<{ locations: PlatformLocation[] }, void>({
      query: () => ({ url: "/locations" }),
    }),

    /** Authoritative pricing. Whatever the cart showed locally is a guess
     *  until the server answers. Creates nothing. */
    quote: build.mutation<
      { currency: string; amounts: Amounts; delivery_miles: number | null },
      {
        items: QuoteItem[];
        combos: QuoteCombo[];
        /** A delivery is priced from the address; the browser never names a
         *  fee. */
        fulfillment_type?: FulfillmentType;
        delivery_address?: string;
      }
    >({
      query: (body) => ({ url: "/orders/quote", method: "POST", body }),
    }),

    createOrder: build.mutation<
      Order,
      {
        items: QuoteItem[];
        combos: QuoteCombo[];
        customer_note: string | null;
        contact: Contact;
        guest_email?: string;
        /** A delivery goes to contact.address. */
        fulfillment_type: FulfillmentType;
        expected_total_minor: number;
        idempotencyKey: string;
      }
    >({
      query: ({ idempotencyKey, ...body }) => ({
        url: "/orders",
        method: "POST",
        body,
        customerAuth: true,
        idempotencyKey,
      }),
    }),

    createPaymentIntent: build.mutation<
      {
        client_secret: string;
        stripe_account_id: string;
        publishable_key: string;
        /** For the payment's billing details; Stripe sends no receipt to it. */
        customer_email?: string | null;
      },
      { orderId: string; idempotencyKey: string }
    >({
      query: ({ orderId, idempotencyKey }) => ({
        url: `/orders/${orderId}/payment-intent`,
        method: "POST",
        body: {},
        customerAuth: true,
        idempotencyKey,
      }),
    }),

    /** One order, for the tracking page.
     *
     *  `token` is the view token from a guest's confirmation email, present
     *  only when the page was opened from that link. It is what lets an order
     *  be tracked from a device that holds neither a Clerk session nor the
     *  guest cookie -- the API checks it against this order and no other. */
    order: build.query<Order, { orderId: string; token?: string | null }>({
      query: ({ orderId, token }) => ({
        url: `/orders/${orderId}`,
        // A header rather than ?t=, which would put the token in the access
        // logs of every hop on each poll.
        orderToken: token ?? undefined,
        // With a token, nothing else is needed and Clerk is not worth
        // loading -- this is the path opened from an email, often on a device
        // that has never signed in to anything here.
        customerAuth: token ? "if-signed-in" : true,
      }),
      providesTags: (_r, _e, { orderId }) => [{ type: "Order", id: orderId }],
    }),

    /** Who is ordering: a signed-in customer, a guest, or nobody (401).
     *
     *  The only way to learn that this browser holds a guest session. That
     *  cookie is httpOnly, so nothing on this side can read it -- asking the
     *  server is not a round trip we could have saved.
     *
     *  `soft` decides what a wrong answer costs. The storefront header passes
     *  it, so reading a menu never pulls down the Clerk SDK; the worst case
     *  there is a header that says "Sign in" to someone who already is, and
     *  fixes itself on the next page. A guard must not pass it: a 401 there
     *  redirects to sign-in, which would send a signed-in customer straight
     *  back, and around. */
    customerSession: build.query<CustomerSession, { soft?: boolean } | void>({
      query: (args) => ({
        url: "/orders/session",
        customerAuth: args?.soft ? "if-signed-in" : true,
      }),
      providesTags: ["CustomerSession"],
    }),

    /** Order without an account. The session is the httpOnly cookie this
     *  sets; the body is only what the receipt will be addressed to. */
    startGuestSession: build.mutation<
      { email: string; full_name: string | null },
      { email: string; full_name?: string | null }
    >({
      query: (body) => ({ url: "/orders/guest-session", method: "POST", body }),
      invalidatesTags: ["CustomerSession"],
    }),

    /** Save name, phone and address from the profile page. The same rules
     *  checkout holds them to; the email is not editable. */
    updateProfile: build.mutation<CustomerSession, Contact>({
      query: (body) => ({ url: "/customer/profile", method: "PUT", body, customerAuth: true }),
      invalidatesTags: ["CustomerSession"],
    }),

    syncProfileEmail: build.mutation<CustomerSession, void>({
      query: () => ({ url: "/customer/profile/email-sync", method: "POST", customerAuth: true }),
      invalidatesTags: ["CustomerSession"],
    }),

    /** Close the account and take the customer's details off it. Orders
     *  stay: see legal/data-deletion.html, which this promises exactly. */
    closeAccount: build.mutation<void, void>({
      query: () => ({ url: "/customer/account", method: "DELETE", customerAuth: true }),
      invalidatesTags: ["CustomerSession"],
    }),

    /** Agree to the terms in force. No body: what was agreed to is the
     *  wording this deployment serves, which the server knows. */
    acceptTerms: build.mutation<CustomerSession, void>({
      query: () => ({ url: "/customer/accept-terms", method: "POST", customerAuth: true }),
      invalidatesTags: ["CustomerSession"],
    }),

    /** Paid orders at this restaurant, newest first, a page at a time.
     *  `before` is the previous page's next_before. */
    customerOrders: build.query<
      { orders: OrderSummary[]; next_before: string | null },
      { before?: string | null } | void
    >({
      query: (args) => ({
        url: args?.before
          ? `/customer/orders?before=${encodeURIComponent(args.before)}`
          : "/customer/orders",
        customerAuth: true,
      }),
      providesTags: ["CustomerOrders"],
    }),

    /** Signed-in customers only; a guest is answered ACCOUNT_REQUIRED, so
     *  callers skip it for them. */
    favourites: build.query<Favourite[], void>({
      query: () => ({ url: "/customer/favourites", customerAuth: true }),
      providesTags: ["Favourites"],
    }),

    /** Save or unsave. The heart changes at once and changes back if the
     *  server refuses, so a tap never waits on the network to look right. */
    setFavourite: build.mutation<void, { itemId: string; name: string; saved: boolean }>({
      query: ({ itemId, saved }) => ({
        url: `/customer/favourites/${itemId}`,
        method: saved ? "PUT" : "DELETE",
        customerAuth: true,
      }),
      async onQueryStarted({ itemId, name, saved }, { dispatch, queryFulfilled }) {
        const patch = dispatch(
          storefrontApi.util.updateQueryData("favourites", undefined, (draft) => {
            const at = draft.findIndex((f) => f.item_id === itemId);
            if (saved && at < 0) {
              draft.unshift({ item_id: itemId, name, saved_at: new Date().toISOString() });
            } else if (!saved && at >= 0) {
              draft.splice(at, 1);
            }
          }),
        );
        try {
          await queryFulfilled;
        } catch {
          patch.undo();
        }
      },
    }),

    /** Guests only. A signed-in customer signs out through Clerk. */
    endGuestSession: build.mutation<void, void>({
      query: () => ({ url: "/orders/session", method: "DELETE" }),
      invalidatesTags: ["CustomerSession"],
    }),
  }),
});

export const {
  usePortalQuery,
  usePublicMenuQuery,
  useLocationsQuery,
  useQuoteMutation,
  useCreateOrderMutation,
  useCreatePaymentIntentMutation,
  useOrderQuery,
  useCustomerSessionQuery,
  useStartGuestSessionMutation,
  useEndGuestSessionMutation,
  useCustomerLogoutMutation,
  useChangeEmailMutation,
  useUpdateProfileMutation,
  useSyncProfileEmailMutation,
  useCloseAccountMutation,
  useAcceptTermsMutation,
  useCustomerOrdersQuery,
  useLazyCustomerOrdersQuery,
  useFavouritesQuery,
  useSetFavouriteMutation,
} = storefrontApi;
