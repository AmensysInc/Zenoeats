import { api } from "@/services/api";
import type { Amounts, AppliedCoupon, Contact, CustomerSession, Favourite, FulfillmentType, Meal, Offer, Order, OrderSummary, PlatformLocation, Portal, Loyalty, Referrals } from "@/types";

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
 * The order endpoints need a signed-in customer or a guest. Both are httpOnly
 * cookies the browser sends by itself, so nothing here handles a credential.
 */
/** What this customer can still earn here, and what stands between them
 *  and it. Written as "what to do next" rather than as a set of flags,
 *  because the screen has one sentence to show and working out which from
 *  four booleans is how two screens end up disagreeing. */
export type WelcomeOffer = {
  headline: string | null;
  can_earn: boolean;
  /** False where this deployment cannot send a text at all, which the screen
   *  says plainly rather than offering a button that cannot work. */
  sms_available: boolean;
  phone_verified: boolean;
};

/** A code that belongs to this customer and nobody else. */
export type WelcomeCode = {
  code: string;
  description: string | null;
  discount_kind: "PERCENT" | "AMOUNT";
  discount_value: number;
  min_subtotal_minor: number;
  ends_at: string | null;
  spent: boolean;
};

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

    /** This restaurant's loyalty programs, and the caller's cards.
     *
     *  The balance is the point: for a caller who is not signed in the
     *  server answers with the ladder and `collecting: false`, which is a
     *  different and less useful page. It does not refuse an anonymous
     *  caller, so the ladder still renders for somebody deciding whether to
     *  make an account. */
    rewards: build.query<Loyalty, void>({
      query: () => ({ url: "/rewards" }),
      providesTags: ["Rewards"],
    }),

    /** The customer's referral link and how their invitations have gone. */
    referrals: build.query<Referrals, void>({
      query: () => ({ url: "/referrals" }),
      providesTags: ["Referrals"],
    }),

    /** Note down a friend being invited. Sends nothing -- the customer sends
     *  the link themselves, from their own phone. */
    inviteFriend: build.mutation<Referrals, { name?: string; phone?: string }>({
      query: (body) => ({
        url: "/referrals/invite",
        method: "POST",
        body,
      }),
      invalidatesTags: ["Referrals"],
    }),

    /** Attach an arriving customer to whoever invited them. Answers the same
     *  way whether or not it stuck, so nothing here branches on it. */
    claimReferral: build.mutation<{ ok: boolean }, string>({
      query: (code) => ({
        url: `/referrals/claim?code=${encodeURIComponent(code)}`,
        method: "POST",
      }),
      invalidatesTags: ["Referrals"],
    }),

    /** What the restaurant is advertising. Public, like the menu: an offer is
     *  a poster in the window and the point is that a passer-by reads it. */
    offers: build.query<{ offers: Offer[] }, void>({
      query: () => ({ url: "/offers" }),
      providesTags: ["Offers"],
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
      {
        currency: string;
        amounts: Amounts;
        delivery_miles: number | null;
        /** The code, if one was sent and worked. */
        coupon: AppliedCoupon | null;
        /** Why it did not, if one was sent and did not. A bad code is not an
         *  error here: the cart still has a price and the page still has to
         *  show it, so the reason comes back beside it. */
        coupon_error: string | null;
      },
      {
        items: QuoteItem[];
        combos: QuoteCombo[];
        /** A delivery is priced from the address; the browser never names a
         *  fee. */
        fulfillment_type?: FulfillmentType;
        delivery_address?: string;
        coupon_code?: string;
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
        /** Checked again server-side against this customer and this cart. A
         *  code that no longer works refuses the order rather than quietly
         *  charging full price. */
        coupon_code?: string;
        idempotencyKey: string;
      }
    >({
      query: ({ idempotencyKey, ...body }) => ({
        url: "/orders",
        method: "POST",
        body,
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
        idempotencyKey,
      }),
    }),

    /** One order, for the tracking page.
     *
     *  `token` is the view token from a guest's confirmation email, present
     *  only when the page was opened from that link. It is what lets an order
     *  be tracked from a device that holds neither an account session nor the
     *  guest cookie -- the API checks it against this order and no other. */
    order: build.query<Order, { orderId: string; token?: string | null }>({
      query: ({ orderId, token }) => ({
        url: `/orders/${orderId}`,
        // A header rather than ?t=, which would put the token in the access
        // logs of every hop on each poll.
        orderToken: token ?? undefined,
      }),
      providesTags: (_r, _e, { orderId }) => [{ type: "Order", id: orderId }],
    }),

    /** Who is ordering: a signed-in customer, a guest, or nobody (401).
     *
     *  The only way to learn that this browser holds a guest session. That
     *  cookie is httpOnly, so nothing on this side can read it -- asking the
     *  server is not a round trip we could have saved.
     */
    customerSession: build.query<CustomerSession, void>({
      query: () => ({
        url: "/orders/session",
      }),
      providesTags: ["CustomerSession"],
    }),

    /** What this customer can still earn by proving a mobile number. */
    welcomeOffer: build.query<WelcomeOffer, void>({
      query: () => ({ url: "/welcome/offer" }),
      providesTags: ["WelcomeOffer"],
    }),

    /** Text a code to the number given. */
    startPhoneVerification: build.mutation<void, { phone: string }>({
      query: (body) => ({
        url: "/welcome/phone",
        method: "POST",
        body,
      }),
    }),

    /** Prove the number, and be given the code if one is going. */
    verifyPhone: build.mutation<WelcomeCode | null, { code: string }>({
      query: (body) => ({
        url: "/welcome/verify",
        method: "POST",
        body,
      }),
      invalidatesTags: ["WelcomeOffer", "CustomerSession"],
    }),

    /** Every personal code this customer holds here. */
    welcomeCodes: build.query<WelcomeCode[], void>({
      query: () => ({ url: "/welcome/codes" }),
      providesTags: ["WelcomeOffer"],
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
      query: (body) => ({ url: "/customer/profile", method: "PUT", body }),
      invalidatesTags: ["CustomerSession"],
    }),

    /** Close the account and take the customer's details off it. Orders
     *  stay: see legal/data-deletion.html, which this promises exactly. */
    closeAccount: build.mutation<void, void>({
      query: () => ({ url: "/customer/account", method: "DELETE" }),
      invalidatesTags: ["CustomerSession"],
    }),

    /** Agree to the terms in force. No body: what was agreed to is the
     *  wording this deployment serves, which the server knows. */
    acceptTerms: build.mutation<CustomerSession, void>({
      query: () => ({ url: "/customer/accept-terms", method: "POST" }),
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
      }),
      providesTags: ["CustomerOrders"],
    }),

    /** Signed-in customers only; a guest is answered ACCOUNT_REQUIRED, so
     *  callers skip it for them. */
    favourites: build.query<Favourite[], void>({
      query: () => ({ url: "/customer/favourites" }),
      providesTags: ["Favourites"],
    }),

    /** Save or unsave. The heart changes at once and changes back if the
     *  server refuses, so a tap never waits on the network to look right. */
    setFavourite: build.mutation<void, { itemId: string; name: string; saved: boolean }>({
      query: ({ itemId, saved }) => ({
        url: `/customer/favourites/${itemId}`,
        method: saved ? "PUT" : "DELETE",
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

    /** Guests only. A signed-in customer signs out at /customer/logout. */
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
  useCloseAccountMutation,
  useAcceptTermsMutation,
  useCustomerOrdersQuery,
  useLazyCustomerOrdersQuery,
  useFavouritesQuery,
  useSetFavouriteMutation,
  useRewardsQuery,
  useWelcomeOfferQuery,
  useWelcomeCodesQuery,
  useStartPhoneVerificationMutation,
  useVerifyPhoneMutation,
  useOffersQuery,
  useReferralsQuery,
  useInviteFriendMutation,
  useClaimReferralMutation,
} = storefrontApi;
