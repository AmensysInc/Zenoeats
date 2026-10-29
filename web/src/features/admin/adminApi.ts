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
  /** The headings it applies to. Empty means the whole cart. */
  applies_to: MenuHeading[];
  /** Counted from the orders that used it, so a cancelled order is not one. */
  redemptions: number;
  /** What those orders took off in total -- the number that says whether a
   *  promotion was worth running. */
  discounted_minor: number;
  created_at: string;
};

/** One heading on a restaurant's menu, as the coupon form offers it. */
export type MenuHeading = {
  id: string;
  name: string;
};

export type CouponList = {
  /** Which restaurant these belong to, and what their amounts are in. An
   *  operator with several restaurants open needs to see which one they are
   *  editing, and a page of percentages cannot render "$5.00 off" without
   *  the currency. */
  restaurant_name: string;
  currency: string;
  /** This restaurant's menu headings, for the "applies to" field. Sent with
   *  the list so the form needs no second request. */
  item_types: MenuHeading[];
  coupons: Coupon[];
};

/** One dish a program's reward may be taken as. */
export type ProgramChoice = {
  item_id: string;
  item_name: string;
  is_available: boolean;
};

/** A loyalty program as the admin list shows it.
 *
 *  `summary` is the rule in a sentence, built server-side so this list and
 *  anything later read it the same way rather than each reassembling it from
 *  five fields. */
export type LoyaltyProgram = {
  id: string;
  name: string;
  /** REFERRAL counts people rather than purchases; it has its own block
   *  on the admin page and at most one may run. */
  purchase_type: "COMPLETED_ORDERS" | "MENU_ITEM" | "REFERRAL";
  required_count: number;
  minimum_spend_minor: number;
  qualifying_item_id: string | null;
  qualifying_item_type_id: string | null;
  summary: string;
  reward_expires_days: number | null;
  is_active: boolean;
  sort_order: number;
  choices: ProgramChoice[];
};

export type ProgramDraft = {
  name: string;
  purchase_type: "COMPLETED_ORDERS" | "MENU_ITEM" | "REFERRAL";
  required_count: number;
  minimum_spend_minor?: number;
  /** MENU_ITEM only, and exactly one of the two: a named dish counts only
   *  itself, a named heading counts everything filed under it. */
  qualifying_item_id?: string | null;
  qualifying_item_type_id?: string | null;
  reward_expires_days?: number | null;
  is_active?: boolean;
  sort_order?: number;
  /** The dishes the reward may be taken as. At least one. */
  choices: string[];
};

export type MenuDish = { id: string; name: string; base_price_minor: number };

export type ProgramList = {
  restaurant_name: string;
  currency: string;
  items: MenuDish[];
  headings: MenuHeading[];
  programs: LoyaltyProgram[];
};

/** An offer as the admin list shows it. Named AdminOffer because the
 *  storefront has its own, narrower Offer: this one carries the window and
 *  the switch, which a customer never sees. */
export type AdminOffer = {
  id: string;
  title: string;
  body: string | null;
  image_path: string | null;
  image_url: string | null;
  starts_at: string | null;
  ends_at: string | null;
  is_active: boolean;
  sort_order: number;
  /** Whether a customer would see it now: the window and the switch, worked
   *  out server-side so this list and the storefront cannot disagree. */
  is_live: boolean;
};

export type OfferDraft = {
  title: string;
  body?: string | null;
  image_path?: string | null;
  starts_at?: string | null;
  ends_at?: string | null;
  is_active?: boolean;
  sort_order?: number;
};

export type OfferList = {
  restaurant_name: string;
  offers: AdminOffer[];
};

/** The two ways a discount can be stated, and exactly one of them is sent.
 *
 *  An operator thinks in a percentage or in an amount -- "10% off" or "$5
 *  off" -- so the form asks for both and sends whichever was answered. Both
 *  at once is refused by the API rather than guessed: "10% off and $5 off"
 *  has no single answer, and the thing it resembles is a percentage with a
 *  ceiling, which `max_discount_minor` already is. */
export type CouponDraft = {
  code: string;
  description?: string | null;
  /** Basis points: 1000 is 10%. */
  percent_off_bps?: number | null;
  /** Minor units: 500 is $5.00. */
  amount_off_minor?: number | null;
  max_discount_minor?: number | null;
  min_subtotal_minor?: number;
  /** Menu heading ids. Empty means the whole cart. */
  applies_to?: string[];
  starts_at?: string | null;
  ends_at?: string | null;
  max_redemptions?: number | null;
  max_per_customer?: number | null;
  is_active?: boolean;
};

/** An edit. The code is absent on purpose: customers have been given it, so a
 *  new code is a new coupon and the old one gets retired. */
export type CouponPatch = Partial<Omit<CouponDraft, "code">>;

/** One code minted by a group, and whether anybody has spent it.
 *
 *  `redeemed_by` is the whole reason a group exists: an order names the code
 *  it used, so MORE7 is traceable to whoever was handed MORE7 where one
 *  shared code makes every redemption look alike. Null while the code is
 *  unused, and also where a guest with no account spent it. */
export type GroupCode = {
  id: string;
  code: string;
  number: number;
  redemptions: number;
  discounted_minor: number;
  redeemed_by: string | null;
  redeemed_at: string | null;
};

/** One offer, handed out as numbered codes: "MORE" with ten uses is MORE1
 *  through MORE10, each good once, each for one person. */
export type CouponGroup = {
  id: string;
  code_prefix: string;
  description: string | null;
  discount_kind: DiscountKind;
  discount_value: number;
  max_discount_minor: number | null;
  min_subtotal_minor: number;
  starts_at: string | null;
  ends_at: string | null;
  /** How many codes were minted. Each is good once, so this is how many
   *  people the offer is for rather than how many goes one code gets. */
  uses: number;
  max_per_customer: number | null;
  is_active: boolean;
  applies_to: MenuHeading[];
  issued: number;
  used: number;
  discounted_minor: number;
  codes: GroupCode[];
  created_at: string;
};

export type CouponGroupList = {
  restaurant_name: string;
  currency: string;
  item_types: MenuHeading[];
  groups: CouponGroup[];
};

export type CouponGroupDraft = {
  code_prefix: string;
  uses: number;
  description?: string | null;
  percent_off_bps?: number | null;
  amount_off_minor?: number | null;
  max_discount_minor?: number | null;
  min_subtotal_minor?: number;
  applies_to?: string[];
  starts_at?: string | null;
  ends_at?: string | null;
  max_per_customer?: number | null;
  is_active?: boolean;
};

/** Neither the prefix nor the count is editable: the codes have been handed
 *  out, so renaming them would break every printed copy, and lowering the
 *  count would have to decide which holders to cancel. */
export type CouponGroupPatch = {
  description?: string | null;
  is_active?: boolean;
};

/** The welcome offer: one code per new customer, earned by proving a mobile
 *  number. A singleton per restaurant -- two would both fire on the same
 *  sign-up and hand one account two codes. */
export type SignupOffer = {
  id: string;
  code_prefix: string;
  description: string | null;
  discount_kind: DiscountKind;
  discount_value: number;
  max_discount_minor: number | null;
  min_subtotal_minor: number;
  starts_at: string | null;
  ends_at: string | null;
  /** How long one customer's code lasts from the day it is issued. A date
   *  would be a meaner offer that gets worse the later you sign up. */
  valid_days: number | null;
  is_active: boolean;
  applies_to: MenuHeading[];
  issued: number;
  used: number;
  discounted_minor: number;
  created_at: string;
};

export type SignupOfferState = {
  restaurant_name: string;
  currency: string;
  item_types: MenuHeading[];
  /** False where this deployment cannot send a text at all. The screen says
   *  so rather than offering a switch whose verification step cannot run. */
  sms_configured: boolean;
  offer: SignupOffer | null;
};

export type SignupOfferDraft = {
  code_prefix: string;
  valid_days?: number | null;
  description?: string | null;
  percent_off_bps?: number | null;
  amount_off_minor?: number | null;
  max_discount_minor?: number | null;
  min_subtotal_minor?: number;
  applies_to?: string[];
  starts_at?: string | null;
  ends_at?: string | null;
  is_active?: boolean;
};

/** One order that used a coupon: which code, who placed it, what it cost. */
export type Redemption = {
  order_id: string;
  order_number: number;
  code: string;
  customer_name: string | null;
  customer_email: string | null;
  customer_phone: string | null;
  is_guest: boolean;
  discount_minor: number;
  order_total_minor: number;
  status: string;
  created_at: string;
};

export type RedemptionList = {
  restaurant_name: string;
  currency: string;
  total_redemptions: number;
  total_discounted_minor: number;
  redemptions: Redemption[];
};

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

    // --- rewards and offers ------------------------------------------------
    //
    // --- group coupons ----------------------------------------------------
    // One offer, minted as numbered codes. The codes are ordinary coupons
    // underneath, so they share the Coupon tag: minting one changes what
    // the coupon list would show if it did not filter them out.
    couponGroups: build.query<CouponGroupList, string>({
      query: (id) => ({ url: `/admin/restaurants/${id}/coupon-groups` }),
      providesTags: (_r, _e, id) => [{ type: "Coupon", id }],
    }),

    createCouponGroup: build.mutation<CouponGroup, { id: string; draft: CouponGroupDraft }>({
      query: ({ id, draft }) => ({
        url: `/admin/restaurants/${id}/coupon-groups`,
        method: "POST",
        body: draft,
      }),
      invalidatesTags: (_r, _e, { id }) => [{ type: "Coupon", id }],
    }),

    updateCouponGroup: build.mutation<
      CouponGroup,
      { id: string; groupId: string; patch: CouponGroupPatch }
    >({
      query: ({ id, groupId, patch }) => ({
        url: `/admin/restaurants/${id}/coupon-groups/${groupId}`,
        method: "PATCH",
        body: patch,
      }),
      invalidatesTags: (_r, _e, { id }) => [{ type: "Coupon", id }],
    }),

    retireCouponGroup: build.mutation<void, { id: string; groupId: string }>({
      query: ({ id, groupId }) => ({
        url: `/admin/restaurants/${id}/coupon-groups/${groupId}`,
        method: "DELETE",
      }),
      invalidatesTags: (_r, _e, { id }) => [{ type: "Coupon", id }],
    }),

    // --- the welcome offer, and who used what -----------------------------
    signupOffer: build.query<SignupOfferState, string>({
      query: (id) => ({ url: `/admin/restaurants/${id}/signup-offer` }),
      providesTags: (_r, _e, id) => [{ type: "Coupon", id }],
    }),

    setSignupOffer: build.mutation<SignupOffer, { id: string; draft: SignupOfferDraft }>({
      query: ({ id, draft }) => ({
        url: `/admin/restaurants/${id}/signup-offer`,
        method: "PUT",
        body: draft,
      }),
      invalidatesTags: (_r, _e, { id }) => [{ type: "Coupon", id }],
    }),

    retireSignupOffer: build.mutation<void, { id: string }>({
      query: ({ id }) => ({
        url: `/admin/restaurants/${id}/signup-offer`,
        method: "DELETE",
      }),
      invalidatesTags: (_r, _e, { id }) => [{ type: "Coupon", id }],
    }),

    couponRedemptions: build.query<RedemptionList, { id: string; code?: string }>({
      query: ({ id, code }) => ({
        url: `/admin/restaurants/${id}/coupon-redemptions${code ? `?code=${encodeURIComponent(code)}` : ""}`,
      }),
      providesTags: (_r, _e, { id }) => [{ type: "Coupon", id }],
    }),

    // The platform's to create, for the reason coupons are: what a promotion
    // costs is ZenoEats' business as much as the kitchen's. No screen for
    // either in the restaurant portal, and no endpoint behind one.

    rewards: build.query<ProgramList, string>({
      query: (id) => ({ url: `/admin/restaurants/${id}/rewards` }),
      providesTags: (_r, _e, id) => [{ type: "Reward", id }],
    }),

    createReward: build.mutation<LoyaltyProgram, { id: string; draft: ProgramDraft }>({
      query: ({ id, draft }) => ({
        url: `/admin/restaurants/${id}/rewards`,
        method: "POST",
        body: draft,
      }),
      invalidatesTags: (_r, _e, { id }) => [{ type: "Reward", id }],
    }),

    updateReward: build.mutation<
      LoyaltyProgram,
      { id: string; rewardId: string; patch: Partial<ProgramDraft> }
    >({
      query: ({ id, rewardId, patch }) => ({
        url: `/admin/restaurants/${id}/rewards/${rewardId}`,
        method: "PATCH",
        body: patch,
      }),
      invalidatesTags: (_r, _e, { id }) => [{ type: "Reward", id }],
    }),

    // Soft, like a coupon's: orders that were given this dish free keep
    // their own lines and figures, and the row stays answerable.
    retireReward: build.mutation<void, { id: string; rewardId: string }>({
      query: ({ id, rewardId }) => ({
        url: `/admin/restaurants/${id}/rewards/${rewardId}`,
        method: "DELETE",
      }),
      invalidatesTags: (_r, _e, { id }) => [{ type: "Reward", id }],
    }),

    offers: build.query<OfferList, string>({
      query: (id) => ({ url: `/admin/restaurants/${id}/offers` }),
      providesTags: (_r, _e, id) => [{ type: "Offer", id }],
    }),

    createOffer: build.mutation<AdminOffer, { id: string; draft: OfferDraft }>({
      query: ({ id, draft }) => ({
        url: `/admin/restaurants/${id}/offers`,
        method: "POST",
        body: draft,
      }),
      invalidatesTags: (_r, _e, { id }) => [{ type: "Offer", id }],
    }),

    updateOffer: build.mutation<
      AdminOffer,
      { id: string; offerId: string; patch: Partial<OfferDraft> }
    >({
      query: ({ id, offerId, patch }) => ({
        url: `/admin/restaurants/${id}/offers/${offerId}`,
        method: "PATCH",
        body: patch,
      }),
      invalidatesTags: (_r, _e, { id }) => [{ type: "Offer", id }],
    }),

    retireOffer: build.mutation<void, { id: string; offerId: string }>({
      query: ({ id, offerId }) => ({
        url: `/admin/restaurants/${id}/offers/${offerId}`,
        method: "DELETE",
      }),
      invalidatesTags: (_r, _e, { id }) => [{ type: "Offer", id }],
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
  useCouponGroupsQuery,
  useCreateCouponGroupMutation,
  useUpdateCouponGroupMutation,
  useRetireCouponGroupMutation,
  useSignupOfferQuery,
  useSetSignupOfferMutation,
  useRetireSignupOfferMutation,
  useCouponRedemptionsQuery,
  useRewardsQuery,
  useCreateRewardMutation,
  useUpdateRewardMutation,
  useRetireRewardMutation,
  useOffersQuery,
  useCreateOfferMutation,
  useUpdateOfferMutation,
  useRetireOfferMutation,
} = adminApi;
