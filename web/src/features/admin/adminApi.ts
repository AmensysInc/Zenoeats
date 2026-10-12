import { api } from "@/services/api";
import type { DiscountKind } from "@/types";

/** A promotion code, as the platform dashboard shows it.
 *
 *  `discount_kind` is read the way a combo's is: PERCENT is basis points
 *  (1000 = 10%), AMOUNT is minor units (500 = $5.00). Every limit is nullable
 *  because a real promotion uses some and not others -- a launch offer has an
 *  end date and no cap, a referral code has a cap of one per customer and no
 *  end date at all. */
export type Coupon = {
  id: string;
  code: string;
  description: string | null;
  discount_kind: DiscountKind;
  discount_value: number;
  /** A ceiling, for a percentage only: 20% off is a different offer on a $10
   *  order and on a $400 office lunch. */
  max_discount_minor: number | null;
  min_subtotal_minor: number;
  starts_at: string | null;
  ends_at: string | null;
  max_redemptions: number | null;
  max_per_customer: number | null;
  is_active: boolean;
  /** Counted from the orders that used it, so a cancelled order is not one. */
  redemptions: number;
  /** What those orders took off in total -- the number that says whether a
   *  promotion was worth running. */
  discounted_minor: number;
  created_at: string;
};

export type CouponList = {
  /** Which restaurant these belong to, and what their amounts are in. An
   *  operator with several restaurants open needs to see which one they are
   *  editing, and a page of percentages cannot render "$5.00 off" without
   *  the currency. */
  restaurant_name: string;
  currency: string;
  coupons: Coupon[];
};

export type CouponDraft = {
  code: string;
  description?: string | null;
  discount_kind: DiscountKind;
  discount_value: number;
  max_discount_minor?: number | null;
  min_subtotal_minor?: number;
  starts_at?: string | null;
  ends_at?: string | null;
  max_redemptions?: number | null;
  max_per_customer?: number | null;
  is_active?: boolean;
};

/** An edit. The code is absent on purpose: customers have been given it, so a
 *  new code is a new coupon and the old one gets retired. */
export type CouponPatch = Partial<Omit<CouponDraft, "code">>;

export type Restaurant = {
  id: string;
  slug: string;
  name: string;
  status: string;
  currency: string;
  tax_rate_bps: number;
  accepting_orders: boolean;
  storefront_customization_enabled: boolean;
  stripe_account_id: string | null;
  charges_enabled: boolean;
  created_at: string;
  tagline: string | null;
  phone: string | null;
  timezone: string | null;
  deleted_at: string | null;
  /** FLAT applies tax_rate_bps; STRIPE_TAX calculates per order in Stripe. */
  tax_mode: TaxMode;
  tax_code: string | null;
} & RestaurantAddress;

export type TaxMode = "FLAT" | "STRIPE_TAX";

/** Where orders are picked up, and so where Stripe Tax sources the sale. */
export type RestaurantAddress = {
  address_line1: string | null;
  address_line2: string | null;
  address_city: string | null;
  address_state: string | null;
  address_postal_code: string | null;
  address_country: string | null;
};

export type Report = {
  restaurant_id: string;
  slug: string;
  name: string;
  status: string;
  currency: string;
  orders_paid: number;
  gross_revenue_minor: number;
  tax_collected_minor: number;
  average_order_value_minor: number;
  orders_pending_payment: number;
  orders_expired: number;
  last_order_at: string | null;
};

export type AdminOrder = {
  order_id: string;
  order_number: number;
  status: string;
  total_minor: number;
  tax_minor: number;
  currency: string;
  created_at: string;
  paid_at: string | null;
  expires_at: string | null;
  payment_status: string | null;
  stripe_payment_intent_id: string | null;
};

export type AdminOrderPage = {
  restaurant_id: string;
  slug: string;
  total: number;
  orders: AdminOrder[];
};

export type AdminMe = { email: string };

export type IssuedCredential = {
  user_id: string;
  email: string;
  /** Null when the address already has a login in use: that person keeps
   *  their own password and is invited instead. */
  temporary_password: string | null;
  /** ACTIVE, or INVITED when they must sign in and accept first. */
  status: "ACTIVE" | "INVITED";
  /** Whether an invitation email is on its way. False when no email
   *  provider is configured: nothing is sent. */
  email_configured: boolean;
};

/** A staff login on a restaurant. Never carries a password. */
export type RestaurantLogin = {
  user_id: string;
  email: string;
  full_name: string | null;
  /** ADMIN is the owner; the rest are staff the owner invited. */
  role: string;
  status: "INVITED" | "ACTIVE" | "REVOKED";
  /** False while the temporary password has not been replaced. */
  signed_in: boolean;
  invited_at: string | null;
};

export type StripeSync = {
  stripe_account_id: string;
  charges_enabled: boolean;
  payouts_enabled: boolean;
  details_submitted: boolean;
  onboarding_status: string;
  disabled_reason: string | null;
  currently_due: string[];
  past_due: string[];
  changed: boolean;
  /** The storefront's Apple Pay / Google Pay registration on this account.
   *  Null until the account can charge, and on a development domain. */
  wallet_domain: {
    domain: string;
    apple_pay: string;
    google_pay: string;
    problem: string | null;
  } | null;
};

export type RestaurantPatch = Partial<{
  name: string;
  tagline: string | null;
  phone: string | null;
  timezone: string;
  currency: string;
  tax_rate_bps: number;
  accepting_orders: boolean;
  storefront_customization_enabled: boolean;
  tax_mode: TaxMode;
  tax_code: string;
} & RestaurantAddress>;

/**
 * Platform administration.
 *
 * Every mutation declares what it invalidates, which is the whole reason for
 * moving to RTK Query: the hand-written hooks made each caller remember to
 * refetch, and a forgotten refresh() showed stale data with no error. Here a
 * restaurant edit refreshes the list because the tag says so, not because a
 * component remembered.
 */
export const adminApi = api.injectEndpoints({
  endpoints: (build) => ({
    adminMe: build.query<AdminMe, void>({
      query: () => ({ url: "/admin/me" }),
      providesTags: ["Session"],
    }),

    adminLogout: build.mutation<void, void>({
      query: () => ({ url: "/admin/logout", method: "POST" }),
      invalidatesTags: ["Session"],
    }),

    listRestaurants: build.query<Restaurant[], { includeDeleted: boolean }>({
      query: ({ includeDeleted }) => ({
        url: `/admin/restaurants?include_deleted=${includeDeleted}`,
      }),
      // Tagging each row by id as well as the list means a single-restaurant
      // mutation does not have to invalidate every other view of it.
      providesTags: (result) => [
        { type: "Restaurant" as const, id: "LIST" },
        ...(result ?? []).map((r) => ({ type: "Restaurant" as const, id: r.id })),
      ],
    }),

    createRestaurant: build.mutation<
      Restaurant,
      { slug: string; name: string; tax_rate_bps: number; tagline?: string | null }
    >({
      query: (body) => ({ url: "/admin/restaurants", method: "POST", body }),
      invalidatesTags: [{ type: "Restaurant", id: "LIST" }],
    }),

    updateRestaurant: build.mutation<Restaurant, { id: string; changes: RestaurantPatch }>({
      query: ({ id, changes }) => ({
        url: `/admin/restaurants/${id}`,
        method: "PATCH",
        body: changes,
      }),
      invalidatesTags: (_r, _e, arg) => [
        { type: "Restaurant", id: arg.id },
        { type: "Restaurant", id: "LIST" },
      ],
    }),

    deleteRestaurant: build.mutation<{ deleted: boolean }, string>({
      query: (id) => ({ url: `/admin/restaurants/${id}`, method: "DELETE" }),
      invalidatesTags: [{ type: "Restaurant", id: "LIST" }],
    }),

    restoreRestaurant: build.mutation<{ status: string }, string>({
      query: (id) => ({ url: `/admin/restaurants/${id}/restore`, method: "POST" }),
      invalidatesTags: [{ type: "Restaurant", id: "LIST" }],
    }),

    /**
     * Remove a deleted restaurant and everything it owns, for good.
     *
     * The server refuses one holding any order or payment, and says how many
     * are in the way. So this cannot destroy trading history however it is
     * called, and the confirmation in the UI is about the menu and staff that
     * do go, not about the records that cannot.
     */
    purgeRestaurant: build.mutation<{ purged: boolean }, string>({
      query: (id) => ({ url: `/admin/restaurants/${id}/permanent`, method: "DELETE" }),
      invalidatesTags: [{ type: "Restaurant", id: "LIST" }],
    }),

    setRestaurantStatus: build.mutation<
      { status: string },
      { id: string; action: "activate" | "suspend" }
    >({
      query: ({ id, action }) => ({
        url: `/admin/restaurants/${id}/${action}`,
        method: "POST",
      }),
      // Status changes move revenue between "live" and not, so the platform
      // totals are stale too.
      invalidatesTags: [{ type: "Restaurant", id: "LIST" }, "AdminReport"],
    }),

    // Where Stripe returns to is the server's decision -- it builds both URLs
    // from the portal's own hostname. This used to send them as query
    // parameters, which made the onboarding link an open redirect.
    startStripeOnboarding: build.mutation<
      { onboarding_url: string; stripe_account_id: string },
      string
    >({
      query: (id) => ({
        url: `/admin/restaurants/${id}/stripe-onboarding`,
        method: "POST",
        body: {},
      }),
      invalidatesTags: [{ type: "Restaurant", id: "LIST" }],
    }),

    refreshStripeStatus: build.mutation<StripeSync, string>({
      query: (id) => ({ url: `/admin/restaurants/${id}/stripe-refresh`, method: "POST" }),
      invalidatesTags: [{ type: "Restaurant", id: "LIST" }],
    }),

    createOwner: build.mutation<
      IssuedCredential,
      { id: string; email: string; full_name: string | null }
    >({
      query: ({ id, ...body }) => ({
        url: `/admin/restaurants/${id}/owner`,
        method: "POST",
        body,
      }),
      invalidatesTags: (_r, _e, arg) => [{ type: "RestaurantLogin", id: arg.id }],
    }),

    resetOwnerPassword: build.mutation<IssuedCredential, { id: string; email: string }>({
      query: ({ id, email }) => ({
        url: `/admin/restaurants/${id}/owner/reset-password`,
        method: "POST",
        body: { email },
      }),
      invalidatesTags: (_r, _e, arg) => [{ type: "RestaurantLogin", id: arg.id }],
    }),

    restaurantLogins: build.query<RestaurantLogin[], string>({
      query: (id) => ({ url: `/admin/restaurants/${id}/logins` }),
      providesTags: (_r, _e, id) => [{ type: "RestaurantLogin", id }],
    }),

    // --- coupons ----------------------------------------------------------
    //
    // Promotion codes are the platform's to create, not a tenant's: there is
    // no coupon screen in the restaurant portal and no endpoint behind one.
    // These are on the admin surface, per restaurant.

    coupons: build.query<CouponList, string>({
      query: (id) => ({ url: `/admin/restaurants/${id}/coupons` }),
      providesTags: (_r, _e, id) => [{ type: "Coupon", id }],
    }),

    createCoupon: build.mutation<Coupon, { id: string; draft: CouponDraft }>({
      query: ({ id, draft }) => ({
        url: `/admin/restaurants/${id}/coupons`,
        method: "POST",
        body: draft,
      }),
      invalidatesTags: (_r, _e, { id }) => [{ type: "Coupon", id }],
    }),

    updateCoupon: build.mutation<
      Coupon,
      { id: string; couponId: string; patch: CouponPatch }
    >({
      query: ({ id, couponId, patch }) => ({
        url: `/admin/restaurants/${id}/coupons/${couponId}`,
        method: "PATCH",
        body: patch,
      }),
      invalidatesTags: (_r, _e, { id }) => [{ type: "Coupon", id }],
    }),

    // Soft, and the code becomes free again: orders that used it keep their
    // own snapshot, so a receipt from last month still reads the same.
    retireCoupon: build.mutation<void, { id: string; couponId: string }>({
      query: ({ id, couponId }) => ({
        url: `/admin/restaurants/${id}/coupons/${couponId}`,
        method: "DELETE",
      }),
      invalidatesTags: (_r, _e, { id }) => [{ type: "Coupon", id }],
    }),

    platformReports: build.query<Report[], void>({
      query: () => ({ url: "/admin/reports" }),
      providesTags: ["AdminReport"],
    }),

    restaurantOrders: build.query<
      AdminOrderPage,
      { id: string; status?: string; limit: number; offset: number }
    >({
      query: ({ id, status, limit, offset }) => {
        const q = new URLSearchParams({ limit: String(limit), offset: String(offset) });
        if (status) q.set("status", status);
        return { url: `/admin/restaurants/${id}/orders?${q.toString()}` };
      },
      providesTags: ["AdminOrder"],
    }),
  }),
});

export const {
  useAdminMeQuery,
  useAdminLogoutMutation,
  useListRestaurantsQuery,
  useCreateRestaurantMutation,
  useUpdateRestaurantMutation,
  useDeleteRestaurantMutation,
  useRestoreRestaurantMutation,
  usePurgeRestaurantMutation,
  useSetRestaurantStatusMutation,
  useStartStripeOnboardingMutation,
  useRefreshStripeStatusMutation,
  useCreateOwnerMutation,
  useResetOwnerPasswordMutation,
  useRestaurantLoginsQuery,
  usePlatformReportsQuery,
  useRestaurantOrdersQuery,
  useCouponsQuery,
  useCreateCouponMutation,
  useUpdateCouponMutation,
  useRetireCouponMutation,
} = adminApi;
