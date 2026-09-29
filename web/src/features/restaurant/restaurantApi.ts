import { api } from "@/services/api";
import { request } from "@/services/apiClient";
import type { BrandFont } from "@/features/storefront/brand";
import type { DiscountKind, Meal } from "@/types";

export type StaffMe = {
  storefront_customization_enabled: boolean;
  user_id: string;
  email: string;
  full_name: string | null;
  role_code: string;
  must_change_password: boolean;
  /** Which restaurant this session is scoped to. The portal header shows it. */
  restaurant_name: string;
  /** INVITED until the person accepts from inside the portal. Every staff
   *  endpoint but accepting refuses an invited session. */
  membership_status: "ACTIVE" | "INVITED";
};

/** The restaurant's own record, as its admin may see and edit it.
 *
 *  slug, status and currency are here to be shown and not changed: the
 *  subdomain is printed on tables, the status has a readiness gate of its own,
 *  and the currency is what existing orders are denominated in. */
/** Where a restaurant delivers and what it charges, by distance.
 *
 *  A ring is described by its outer edge alone: sorted by max_miles they form
 *  bands, the first covering everything up to its edge and each one after it
 *  covering the gap from the previous edge to its own. Beyond the last ring is
 *  no delivery, not free delivery. */
export type DeliveryZone = {
  id: string;
  max_miles: number;
  fee_minor: number;
};

export type DeliverySettings = {
  delivery_enabled: boolean;
  /** Whether a customer would actually be offered delivery. The switch alone
   *  does not decide it: an unplaced address or no rings means nothing can be
   *  quoted. */
  delivery_available: boolean;
  /** Why not, in the restaurant's own words. Empty when it is available. */
  blockers: string[];
  latitude: number | null;
  longitude: number | null;
  /** The address the coordinates were found from. Different from the address
   *  as it reads now means the restaurant moved and needs placing again. */
  geocoded_address: string | null;
  pickup_address: string;
  origin_is_current: boolean;
  /** False when no API key is configured, which the screen has to say rather
   *  than offering a button that cannot work. */
  geocoding_configured: boolean;
  currency: string;
  /** Whether the fee is taxed. Only meaningful under a flat rate: a Stripe Tax
   *  restaurant hands Stripe the amount and Stripe decides per jurisdiction. */
  delivery_fee_taxable: boolean;
  tax_mode: string;
  zones: DeliveryZone[];
};

export type RestaurantProfile = {
  slug: string;
  status: string;
  currency: string;
  name: string;
  tagline: string | null;
  /** Shown to customers on the order page and in order emails. */
  phone: string | null;
  timezone: string;
  accepting_orders: boolean;
  tax_mode: "FLAT" | "STRIPE_TAX";
  tax_rate_bps: number;
  tax_code: string;
  address_line1: string | null;
  address_line2: string | null;
  address_city: string | null;
  address_state: string | null;
  address_postal_code: string | null;
  address_country: string | null;
  /** The brand customers see in the header and on the sign-in pages. Paths
   *  are what is saved; URLs are for showing them. */
  logo_path: string | null;
  logo_url: string | null;
  brand_name_image_path: string | null;
  brand_name_image_url: string | null;
  brand_name_font: BrandFont;
  /** Why Stripe Tax may be refused: it needs a connected account that has
   *  finished its own tax setup, as well as a full pickup address. */
  stripe_connected: boolean;
  charges_enabled: boolean;
};

/** Only the fields actually sent are applied, so an edit of one field cannot
 *  overwrite another admin's edit of a different one. */
export type RestaurantProfilePatch = Partial<
  Omit<
    RestaurantProfile,
    | "slug"
    | "status"
    | "currency"
    | "stripe_connected"
    | "charges_enabled"
    | "logo_url"
    | "brand_name_image_url"
  >
>;

export type StaffInvite = {
  id: string;
  email: string;
  status: string;
  /** Present only when the invitation created the person's login. Shown once;
   *  the API keeps only its hash. */
  temporary_password: string | null;
  /** Whether an invitation email is on its way. False when no email provider
   *  is configured: nothing is sent, and the admin must pass the link on. */
  email_configured: boolean;
};

export type BoardOrder = {
  order_id: string;
  order_number: number;
  status: string;
  total_minor: number;
  currency: string;
  created_at: string;
  /** When payment put it on the board: where the kitchen's clock starts. */
  paid_at: string | null;
  customer_note: string | null;
  /** PAID, or REFUNDED / PARTIALLY_REFUNDED after a refund from the Stripe
   *  Dashboard. A refund never moves the order itself, so this is how the
   *  board knows to say so. */
  payment_status: string | null;
  /** Five wrong PINs. Only a manager override can hand it over now. */
  pin_locked: boolean;
  /** DELIVERY when the customer chose it at checkout, or when a manager
   *  handed a phone order to one of the restaurant's drivers. */
  fulfillment_type: "PICKUP" | "DELIVERY";
  delivery_address: string | null;
  /** Non-zero when the customer paid for delivery at checkout. Such an order
   *  cannot be turned back into a collection. */
  delivery_fee_minor: number;
  /** The driver's name, once one is assigned. */
  driver: string | null;
  /** Who to call and whose name to call out, as given at checkout. Null on
   *  orders from before checkout asked. */
  contact_name: string | null;
  contact_phone: string | null;
  items: {
    name: string;
    quantity: number;
    note: string | null;
    modifiers: string[];
    /** Set on the lines a combo produced. Lines sharing a group number are
     *  one meal deal and are plated together. */
    combo_name: string | null;
    combo_group: number | null;
  }[];
};

/** An order that left the board today: handed over or cancelled. */
export type HistoryOrder = {
  order_id: string;
  order_number: number;
  status: "COMPLETED" | "CANCELLED";
  total_minor: number;
  currency: string;
  paid_at: string | null;
  finished_at: string;
  payment_status: string | null;
  fulfillment_type: "PICKUP" | "DELIVERY";
  delivery_address: string | null;
  items: { name: string; quantity: number; combo_name: string | null }[];
  /** The last staff action on it, from the order's history. Null for an order
   *  finished before that history was kept. */
  last_action: {
    action: "MARKED_READY" | "COMPLETED_WITH_PIN" | "COMPLETED_BY_OVERRIDE" | "CANCELLED";
    by: string | null;
    reason: string | null;
  } | null;
};

/** One delivery, as its driver needs it: what to take and where to. */
export type Delivery = {
  order_id: string;
  order_number: number;
  status: string;
  total_minor: number;
  currency: string;
  created_at: string;
  paid_at: string | null;
  delivery_address: string | null;
  customer_note: string | null;
  contact_name: string | null;
  contact_phone: string | null;
  driver: string | null;
  /** Assigned to whoever is reading. A manager sees every delivery. */
  mine: boolean;
  items: BoardOrder["items"];
};

/** A driver a manager may hand an order to. */
export type DriverOption = { membership_id: string; name: string };

export type OrderHistory = {
  /** Today where the restaurant is. */
  date: string;
  timezone: string;
  orders: HistoryOrder[];
};

/** Cancelling never moves money; refund_needed says the refund is still to
 *  be issued from the restaurant's Stripe Dashboard. */
export type CancelledOrder = {
  order_id: string;
  status: string;
  payment_status: string;
  refund_needed: boolean;
  /** Stripe's refusal, when a refund was asked for and did not go through.
   *  The cancellation still stands; only the money did not move. */
  refund_problem?: string | null;
};

/** Shown once to the admin who reset it; only its hash is kept. */
export type StaffPasswordReset = {
  id: string;
  email: string;
  temporary_password: string;
  /** Whether it was also emailed to them. */
  email_configured: boolean;
};

export type StaffMember = {
  id: string;
  email: string;
  full_name: string | null;
  role_code: string;
  status: string;
  invited_at: string | null;
  accepted_at: string | null;
  /** The signed-in admin's own row. Removing yourself is refused. */
  is_you: boolean;
  /** What became of the last invitation email, written back by the worker.
   *  Null while it is still being sent, and for invitations sent before this
   *  was recorded. */
  invitation_email_status: "SENT" | "FAILED" | "NOT_CONFIGURED" | null;
  invitation_email_at: string | null;
  /** Why it failed, in words for the admin. */
  invitation_email_problem: string | null;
};

/**
 * One row of the item library.
 *
 * Not the same shape as an item inside /menu. That one is arranged for
 * reading a menu and carries whole modifier groups with their options; this
 * one is arranged for editing and carries the ids the forms bind to -- which
 * periods serve it, which groups it offers.
 */
export type LibraryItem = {
  id: string;
  name: string;
  item_type_id: string;
  description: string | null;
  /** kcal as the restaurant states it, or null where it has not. */
  calories: number | null;
  base_price_minor: number;
  currency: string;
  is_available: boolean;
  /** Charged no sales tax: left out of the tax on every order it is in. */
  tax_exempt: boolean;
  /** The storage key, sent back unchanged when an edit leaves the photo
   *  alone, and null when there is none. */
  image_path: string | null;
  /** Where the thumbnail loads from. */
  image_url: string | null;
  meal_ids: string[];
  modifier_groups: { id: string; name: string }[];
  /** What the item comes with: chosen for the customer, and charged at
   *  nothing on this item. */
  included_option_ids: string[];
};

/** The fields an item form owns. Every one is optional on an edit. */
/** A cancelled order still holding the customer's money. */
export type RefundDue = {
  order_id: string;
  order_number: number;
  status: string;
  total_minor: number;
  currency: string;
  created_at: string;
  payment_status: string;
  cancelled_reason: string | null;
};

export type ItemDraft = {
  name: string;
  item_type_id: string;
  description: string | null;
  /** kcal, or null for "not stated". */
  calories: number | null;
  base_price_minor: number;
  tax_exempt: boolean;
  modifier_group_ids: string[];
  meal_ids: string[];
  included_option_ids: string[];
  /** A key from uploadImage, or null to take the photo off. */
  image_path: string | null;
};

/** A combo as the builder edits it: ids to bind to, not a priced menu.
 *
 *  The storefront reads combos out of /menu instead, arranged for ordering
 *  and carrying whole items with their modifier groups. Same two shapes as
 *  items, for the same reason. */
export type BuilderCombo = {
  id: string;
  meal_id: string;
  name: string;
  description: string | null;
  discount_kind: DiscountKind;
  discount_value: number;
  is_available: boolean;
  /** The deal's own photo: the key that is saved, and the URL that shows it. */
  image_path: string | null;
  image_url: string | null;
  slots: { id: string; item_type_id: string; item_ids: string[] }[];
};

/** A slot as it is sent. One per kind, and never empty: a kind with nothing
 *  ticked is a kind the combo does not include. */
export type ComboSlotDraft = { item_type_id: string; item_ids: string[] };

/** An item as the sold-out screen needs it, and nothing more. */
export type StockItem = {
  id: string;
  name: string;
  /** The type's name, with its heading when it is a subcategory. */
  type: string;
  is_available: boolean;
};

/** One of the restaurant's item types, with how many items would be
 *  orphaned by deleting it.
 *
 *  The list arrives flat but in the order the menu reads: every heading is
 *  followed by its own subcategories. `parent_id` is what tells them apart,
 *  and it is null on a heading, which is most of them.
 *
 *  `items` is the direct count, never rolled up. It is the number the
 *  deletion rule reads, so a heading with nothing filed on it reports zero
 *  even when its subcategories are full. */
export type ItemTypeRow = {
  id: string;
  name: string;
  parent_id: string | null;
  sort_order: number;
  items: number;
};

/** What a group edit may send. Only what changed is sent; the server checks the
 *  rules it adds up to against the group's options and the items using it. */
export type ModifierGroupChanges = {
  name?: string;
  applies_to_type_ids?: string[];
  selection_type?: "SINGLE" | "MULTI";
  is_required?: boolean;
  min_select?: number;
  max_select?: number;
};

export type ModifierGroupSummary = {
  id: string;
  name: string;
  selection_type: "SINGLE" | "MULTI";
  is_required: boolean;
  min_select: number;
  max_select: number;
  /** Which item kinds the builder offers this group for. Empty means every
   *  kind -- there is no null case, so no call site tests for one. */
  applies_to_type_ids: string[];
  options: {
    id: string;
    name: string;
    price_delta_minor: number;
    /** kcal this choice adds, or null where no change is stated. */
    calories_delta: number | null;
    image_path: string | null;
    image_url: string | null;
  }[];
};

/** What a photo is of. Part of where it is stored, and checked when it is
 *  attached: an option's thumbnail cannot be saved onto an item. */
export type ImageKind = "items" | "options" | "banners" | "categories" | "branding";

/** A stored photo that nothing shows yet. Saving an item or option with the
 *  key is what puts it on the menu. */
export type UploadedImage = { image_path: string; image_url: string };

/** Photos are larger than anything else this portal sends, over whatever
 *  connection a manager has in the kitchen. */
const UPLOAD_TIMEOUT_MS = 60_000;

/**
 * Store a photo and get its key back.
 *
 * A plain request rather than an RTK Query mutation. A mutation's arguments
 * are kept in the store, and a Blob there trips Redux's serializability check
 * on every upload; nothing needs caching or invalidating here anyway, because
 * nothing on screen changes until the item or option holding the key is
 * saved, and that save invalidates what it should.
 */
export function uploadImage(kind: ImageKind, file: Blob, filename: string) {
  const body = new FormData();
  body.append("file", file, filename);
  return request<UploadedImage>(`/restaurant/images?kind=${kind}`, {
    method: "POST",
    body,
    timeoutMs: UPLOAD_TIMEOUT_MS,
  });
}

/** A range of the restaurant's own days, as YYYY-MM-DD, both inclusive.
 *  Left out, the report is for today where the restaurant is. */
export type ReportRange = { from?: string; to?: string };

export type RestaurantReport = {
  currency: string;
  /** The restaurant's timezone, which decides where each day starts. */
  timezone: string;
  /** Today's date there, so the page can offer "yesterday" and "this month"
   *  without trusting the browser's clock or timezone. */
  today: string;
  from: string;
  to: string;
  orders_paid: number;
  orders_completed: number;
  orders_cancelled: number;
  orders_refunded: number;
  /** What was taken, before refunds. */
  gross_sales_minor: number;
  /** What has since been refunded on those same orders. */
  refunds_minor: number;
  net_sales_minor: number;
  /** Net of the tax returned with refunds. */
  tax_collected_minor: number;
  combo_discounts_minor: number;
  average_order_value_minor: number;
  /** Deliveries the restaurant ran itself, counted apart from collections.
   *  The same sales, split, not added. */
  orders_delivery: number;
  orders_delivered: number;
  delivery_sales_minor: number;
  by_driver: {
    driver: string;
    orders: number;
    delivered: number;
    gross_minor: number;
  }[];
  /** Right now, whatever the range. */
  orders_pending_payment: number;
  orders_expired: number;
  by_day: {
    date: string;
    orders: number;
    gross_minor: number;
    refunds_minor: number;
    net_minor: number;
  }[];
  top_items: { name: string; units: number; revenue_minor: number }[];
};

/**
 * The restaurant portal.
 *
 * Which restaurant this is never appears in a request: the API resolves it
 * from the Host header of the subdomain the page was served on, and
 * restaurant_users under RLS decides what the caller may do. So these
 * endpoints carry no tenant id, and a session presented on another
 * restaurant's subdomain simply finds no membership.
 */
export const restaurantApi = api.injectEndpoints({
  endpoints: (build) => ({
    staffMe: build.query<StaffMe, void>({
      query: () => ({ url: "/restaurant/me" }),
      providesTags: ["Session"],
    }),

    staffLogout: build.mutation<void, void>({
      query: () => ({ url: "/restaurant/logout", method: "POST" }),
      invalidatesTags: ["Session"],
    }),

    // Every session this account holds, on every device -- not just this one.
    staffLogoutEverywhere: build.mutation<void, void>({
      query: () => ({ url: "/restaurant/logout-everywhere", method: "POST" }),
      invalidatesTags: ["Session"],
    }),

    // Your own name. Every role may, drivers included -- it is not a
    // privilege, it is what colleagues see beside an order.
    updateOwnAccount: build.mutation<StaffMe, { full_name: string | null }>({
      query: (body) => ({ url: "/restaurant/me", method: "PATCH", body }),
      invalidatesTags: ["Session"],
    }),

    // Separate from the name, because it takes the current password: the
    // address you sign in with is a credential and the name is not.
    changeStaffEmail: build.mutation<void, { email: string; current_password: string }>({
      query: (body) => ({ url: "/restaurant/change-email", method: "POST", body }),
      invalidatesTags: ["Session"],
    }),

    deliverySettings: build.query<DeliverySettings, void>({
      query: () => ({ url: "/restaurant/delivery" }),
      providesTags: ["Delivery"],
    }),

    // Finds the restaurant's own coordinates from its pickup address. An
    // action someone takes rather than something that happens on every save:
    // it costs a paid lookup.
    locateRestaurant: build.mutation<DeliverySettings, void>({
      query: () => ({ url: "/restaurant/delivery/locate", method: "POST" }),
      invalidatesTags: ["Delivery"],
    }),

    // Only what is sent is applied, so the switch and the tax answer are
    // separate decisions that do not overwrite each other.
    setDeliverySettings: build.mutation<
      DeliverySettings,
      { delivery_enabled?: boolean; delivery_fee_taxable?: boolean }
    >({
      query: (body) => ({ url: "/restaurant/delivery", method: "PATCH", body }),
      // Portal too: whether a customer is offered delivery follows from this.
      invalidatesTags: ["Delivery", "Portal"],
    }),

    // The whole set at once. Rings are only meaningful against each other, so
    // there is no sensible way to save one of them.
    setDeliveryZones: build.mutation<
      DeliverySettings,
      { zones: { max_miles: number; fee_minor: number }[] }
    >({
      query: (body) => ({ url: "/restaurant/delivery/zones", method: "PUT", body }),
      invalidatesTags: ["Delivery", "Portal"],
    }),

    restaurantProfile: build.query<RestaurantProfile, void>({
      query: () => ({ url: "/restaurant/profile" }),
      providesTags: ["RestaurantProfile"],
    }),

    // Session too: the header shows the restaurant's name, so renaming it
    // should not need a reload to take effect.
    updateRestaurantProfile: build.mutation<RestaurantProfile, RestaurantProfilePatch>({
      query: (body) => ({ url: "/restaurant/profile", method: "PATCH", body }),
      // Delivery too: changing the address forgets where the restaurant
      // is, so the delivery panel must not go on showing it as placed.
      invalidatesTags: ["RestaurantProfile", "Session", "Portal", "Delivery"],
    }),

    orderBoard: build.query<BoardOrder[], void>({
      query: () => ({ url: "/restaurant/orders" }),
      providesTags: ["Board"],
    }),

    // Tagged with the board, so handing over or cancelling moves an order
    // from one list to the other in the same refresh.
    orderHistory: build.query<OrderHistory, void>({
      query: () => ({ url: "/restaurant/orders/history" }),
      providesTags: ["Board"],
    }),

    // Tagged with the board: assigning, picking up and delivering move an
    // order between the two screens, and both refresh together.
    deliveries: build.query<Delivery[], void>({
      query: () => ({ url: "/restaurant/deliveries" }),
      providesTags: ["Board"],
    }),

    drivers: build.query<DriverOption[], void>({
      query: () => ({ url: "/restaurant/drivers" }),
      providesTags: ["Staff"],
    }),

    assignDriver: build.mutation<
      { driver: string; delivery_address: string },
      { orderId: string; membership_id: string; delivery_address: string }
    >({
      query: ({ orderId, ...body }) => ({
        url: `/restaurant/orders/${orderId}/assign-driver`,
        method: "POST",
        body,
      }),
      invalidatesTags: ["Board"],
    }),

    unassignDriver: build.mutation<unknown, string>({
      query: (orderId) => ({
        url: `/restaurant/orders/${orderId}/unassign-driver`,
        method: "POST",
      }),
      invalidatesTags: ["Board"],
    }),

    pickedUp: build.mutation<unknown, string>({
      query: (orderId) => ({ url: `/restaurant/orders/${orderId}/picked-up`, method: "POST" }),
      invalidatesTags: ["Board"],
    }),

    /** The signed-in driver's position, for their customers' maps. Refused
     *  (NOT_ON_A_DELIVERY) unless they have an order of their own on the road. */
    sendDriverLocation: build.mutation<
      { sharing: boolean; orders: number },
      { latitude: number; longitude: number; heading: number | null }
    >({
      query: (body) => ({ url: "/restaurant/driver/location", method: "POST", body }),
    }),

    delivered: build.mutation<unknown, string>({
      query: (orderId) => ({ url: `/restaurant/orders/${orderId}/delivered`, method: "POST" }),
      invalidatesTags: ["Board", "RestaurantReport"],
    }),

    markReady: build.mutation<unknown, string>({
      query: (orderId) => ({ url: `/restaurant/orders/${orderId}/ready`, method: "POST" }),
      invalidatesTags: ["Board"],
    }),

    completeOrder: build.mutation<unknown, { orderId: string; pin: string }>({
      query: ({ orderId, pin }) => ({
        // In the body, not the query string: URLs are written to access logs.
        url: `/restaurant/orders/${orderId}/complete`,
        method: "POST",
        body: { pin },
      }),
      // Completing an order moves it off the board and into the day's takings.
      invalidatesTags: ["Board", "RestaurantReport"],
    }),

    overrideComplete: build.mutation<unknown, { orderId: string; reason: string }>({
      query: ({ orderId, reason }) => ({
        url: `/restaurant/orders/${orderId}/override-complete`,
        method: "POST",
        body: { reason },
      }),
      invalidatesTags: ["Board", "RestaurantReport"],
    }),

    cancelOrder: build.mutation<
      CancelledOrder,
      { orderId: string; reason: string; refund: boolean }
    >({
      query: ({ orderId, reason, refund }) => ({
        url: `/restaurant/orders/${orderId}/cancel`,
        method: "POST",
        body: { reason, refund },
      }),
      invalidatesTags: ["Board", "RestaurantReport"],
    }),

    // Cancelled orders whose money is still with the restaurant. A
    // cancellation leaves the board, so this is the only place a refund
    // nobody issued is still visible.
    refundsDue: build.query<RefundDue[], void>({
      query: () => ({ url: "/restaurant/orders/refunds-due" }),
      providesTags: ["Board"],
    }),

    // For the cancellation made without a refund, and for the refund Stripe
    // refused the first time. Only a cancelled order has one to give.
    refundOrder: build.mutation<CancelledOrder, { orderId: string; reason: string }>({
      query: ({ orderId, reason }) => ({
        url: `/restaurant/orders/${orderId}/refund`,
        method: "POST",
        body: { reason },
      }),
      invalidatesTags: ["Board", "RestaurantReport"],
    }),

    // The builder's view, not the storefront's. /menu answers only for an
    // ACTIVE restaurant and hides meal periods that are still empty, so a
    // draft saw nothing at all and everyone else saw a new period vanish the
    // moment it was created.
    menu: build.query<{ meals: Meal[] }, void>({
      query: () => ({ url: "/restaurant/menu" }),
      providesTags: ["Menu"],
    }),

    // Every item the restaurant sells, whether or not a period serves it.
    // The Items tab writes this list; the meal periods tab arranges it.
    itemTypes: build.query<ItemTypeRow[], void>({
      query: () => ({ url: "/restaurant/item-types" }),
      providesTags: ["ItemType"],
    }),

    createItemType: build.mutation<
      unknown,
      { name: string; parent_id?: string | null; sort_order?: number }
    >({
      query: (body) => ({ url: "/restaurant/item-types", method: "POST", body }),
      invalidatesTags: ["ItemType"],
    }),

    // Renaming a type changes a heading on the storefront and the words the
    // item forms offer, so the menu and the item library are re-read too.
    // parent_id is only sent when it is meant to change. The server applies
    // what it is sent and nothing else, which is what makes an explicit null
    // mean "promote this back to a heading of its own".
    updateItemType: build.mutation<
      unknown,
      {
        typeId: string;
        changes: { name?: string; parent_id?: string | null; sort_order?: number };
      }
    >({
      query: ({ typeId, changes }) => ({
        url: `/restaurant/item-types/${typeId}`,
        method: "PATCH",
        body: changes,
      }),
      invalidatesTags: ["ItemType", "Menu", "Item", "Combo", "ModifierGroup"],
    }),

    // Refused by the server while items still carry it, which is why the
    // combo and modifier lists only need re-reading on success.
    deleteItemType: build.mutation<unknown, string>({
      query: (typeId) => ({ url: `/restaurant/item-types/${typeId}`, method: "DELETE" }),
      invalidatesTags: ["ItemType", "Menu", "Item", "Combo", "ModifierGroup"],
    }),

    items: build.query<LibraryItem[], void>({
      query: () => ({ url: "/restaurant/items" }),
      providesTags: ["Item"],
    }),

    modifierGroups: build.query<ModifierGroupSummary[], void>({
      query: () => ({ url: "/restaurant/modifier-groups" }),
      providesTags: ["ModifierGroup"],
    }),

    createMeal: build.mutation<unknown, { name: string; sort_order?: number }>({
      query: (body) => ({ url: "/restaurant/meals", method: "POST", body }),
      invalidatesTags: ["Menu"],
    }),

    // Partial, like every other update in this builder: only the keys sent
    // are applied. Clearing the hours is sending both as null, which is why
    // they are typed as nullable rather than merely optional -- leaving them
    // out means "don't touch them" and would make clearing impossible.
    updateMeal: build.mutation<
      unknown,
      {
        mealId: string;
        changes: {
          name?: string;
          starts_at?: string | null;
          ends_at?: string | null;
        };
      }
    >({
      query: ({ mealId, changes }) => ({
        url: `/restaurant/meals/${mealId}`,
        method: "PATCH",
        body: changes,
      }),
      invalidatesTags: ["Menu"],
    }),

    // Soft delete on the server: the row stays so paid orders keep resolving,
    // and the menu reader stops returning it. The items the period served are
    // kept -- they belong to the restaurant, not to the period -- but every
    // library row loses a meal id, so both lists are re-read.
    deleteMeal: build.mutation<unknown, string>({
      query: (mealId) => ({ url: `/restaurant/meals/${mealId}`, method: "DELETE" }),
      invalidatesTags: ["Menu", "Item"],
    }),

    // Pull existing items onto a period.
    addMealItems: build.mutation<unknown, { mealId: string; item_ids: string[] }>({
      query: ({ mealId, item_ids }) => ({
        url: `/restaurant/meals/${mealId}/items`,
        method: "POST",
        body: { item_ids },
      }),
      invalidatesTags: ["Menu", "Item"],
    }),

    // Stop serving one item during one period. The item survives, as does
    // every other period serving it.
    removeMealItem: build.mutation<unknown, { mealId: string; itemId: string }>({
      query: ({ mealId, itemId }) => ({
        url: `/restaurant/meals/${mealId}/items/${itemId}`,
        method: "DELETE",
      }),
      invalidatesTags: ["Menu", "Item"],
    }),

    // ItemType as well as Item: the type strip shows a count per type, and a
    // new item changes one of them.
    createItem: build.mutation<unknown, Partial<ItemDraft> & { name: string }>({
      query: (body) => ({ url: "/restaurant/items", method: "POST", body }),
      invalidatesTags: ["Item", "ItemType", "Menu"],
    }),

    // Partial: only the fields sent are touched, so editing a price leaves
    // the description alone. meal_ids and modifier_group_ids are whole lists
    // when sent -- the server replaces the set rather than merging into it.
    updateItem: build.mutation<unknown, { itemId: string; changes: Partial<ItemDraft> }>({
      query: ({ itemId, changes }) => ({
        url: `/restaurant/items/${itemId}`,
        method: "PATCH",
        body: changes,
      }),
      // Retyping an item moves it between two counts, so the strip is stale
      // in two places until it is re-read.
      invalidatesTags: ["Item", "ItemType", "Menu"],
    }),

    // Reaches every period at once, which is the point of one item having
    // one definition.
    deleteItem: build.mutation<unknown, string>({
      query: (itemId) => ({ url: `/restaurant/items/${itemId}`, method: "DELETE" }),
      invalidatesTags: ["Item", "ItemType", "Menu"],
    }),

    // Every staff role may read this; /items is managers only. Tagged Item so
    // a toggle here or an edit in the menu builder refreshes both screens.
    stock: build.query<StockItem[], void>({
      query: () => ({ url: "/restaurant/stock" }),
      providesTags: ["Item"],
    }),

    setItemAvailability: build.mutation<unknown, { itemId: string; is_available: boolean }>({
      // is_available is a query parameter, not a body. FastAPI treats a bare
      // bool argument as a query param, so sending it as JSON would leave the
      // endpoint with a missing required parameter.
      query: ({ itemId, is_available }) => ({
        url: `/restaurant/items/${itemId}/availability?is_available=${is_available}`,
        method: "PATCH",
      }),
      invalidatesTags: ["Item", "Menu"],
    }),

    combos: build.query<BuilderCombo[], void>({
      query: () => ({ url: "/restaurant/combos" }),
      providesTags: ["Combo"],
    }),

    createCombo: build.mutation<
      unknown,
      {
        meal_id: string;
        name: string;
        description?: string | null;
        discount_kind: DiscountKind;
        discount_value: number;
        image_path?: string | null;
        slots: ComboSlotDraft[];
      }
    >({
      query: (body) => ({ url: "/restaurant/combos", method: "POST", body }),
      // The storefront menu carries combos, so it is re-read too.
      invalidatesTags: ["Combo", "Menu"],
    }),

    // Partial, like the others. Sending `slots` replaces every slot and
    // choice: the builder always knows the whole combo it means, and a merge
    // cannot express taking the last drink out of a slot.
    updateCombo: build.mutation<
      unknown,
      {
        comboId: string;
        changes: {
          name?: string;
          description?: string | null;
          discount_kind?: DiscountKind;
          discount_value?: number;
          is_available?: boolean;
          image_path?: string | null;
          slots?: ComboSlotDraft[];
        };
      }
    >({
      query: ({ comboId, changes }) => ({
        url: `/restaurant/combos/${comboId}`,
        method: "PATCH",
        body: changes,
      }),
      invalidatesTags: ["Combo", "Menu"],
    }),

    deleteCombo: build.mutation<unknown, string>({
      query: (comboId) => ({ url: `/restaurant/combos/${comboId}`, method: "DELETE" }),
      invalidatesTags: ["Combo", "Menu"],
    }),

    createModifierGroup: build.mutation<unknown, Record<string, unknown>>({
      query: (body) => ({ url: "/restaurant/modifier-groups", method: "POST", body }),
      invalidatesTags: ["ModifierGroup"],
    }),

    // Partial: only the fields sent are touched. applies_to_type_ids is a whole
    // list when sent, and an empty one is a real value meaning "offer this
    // everywhere" -- so it must not be dropped from the body when empty.
    updateModifierGroup: build.mutation<
      unknown,
      { groupId: string; changes: ModifierGroupChanges }
    >({
      query: ({ groupId, changes }) => ({
        url: `/restaurant/modifier-groups/${groupId}`,
        method: "PATCH",
        body: changes,
      }),
      // The kinds decide which items are offered the group, so the item forms
      // re-read too.
      invalidatesTags: ["ModifierGroup", "Item", "Menu"],
    }),

    // A group is attached to items, so the menu has to be re-read too: every
    // item that opted in stops offering it the moment it goes.
    deleteModifierGroup: build.mutation<unknown, string>({
      query: (groupId) => ({
        url: `/restaurant/modifier-groups/${groupId}`,
        method: "DELETE",
      }),
      invalidatesTags: ["ModifierGroup", "Menu"],
    }),

    createModifierOption: build.mutation<
      unknown,
      {
        groupId: string;
        name: string;
        price_delta_minor: number;
        /** kcal this choice adds, or null for "no change stated". */
        calories_delta?: number | null;
        image_path?: string | null;
      }
    >({
      query: ({ groupId, ...body }) => ({
        url: `/restaurant/modifier-groups/${groupId}/options`,
        method: "POST",
        body,
      }),
      invalidatesTags: ["ModifierGroup", "Menu"],
    }),

    // image_path follows the partial rule: null takes the photo off, and
    // leaving it out leaves the photo alone.
    updateModifierOption: build.mutation<
      unknown,
      {
        optionId: string;
        changes: {
          name?: string;
          price_delta_minor?: number;
          calories_delta?: number | null;
          image_path?: string | null;
        };
      }
    >({
      query: ({ optionId, changes }) => ({
        url: `/restaurant/modifier-options/${optionId}`,
        method: "PATCH",
        body: changes,
      }),
      invalidatesTags: ["ModifierGroup", "Menu"],
    }),

    deleteModifierOption: build.mutation<unknown, string>({
      query: (optionId) => ({
        url: `/restaurant/modifier-options/${optionId}`,
        method: "DELETE",
      }),
      invalidatesTags: ["ModifierGroup", "Menu"],
    }),

    staff: build.query<StaffMember[], void>({
      query: () => ({ url: "/restaurant/staff" }),
      providesTags: ["Staff"],
    }),

    inviteStaff: build.mutation<StaffInvite, { email: string; role_code: string }>({
      query: (body) => ({ url: "/restaurant/staff", method: "POST", body }),
      invalidatesTags: ["Staff"],
    }),

    /** Send a pending invitation's email again. For someone who never signed
     *  in this also issues a fresh temporary password, returned once. */
    resendStaffInvite: build.mutation<StaffInvite, string>({
      query: (membershipId) => ({
        url: `/restaurant/staff/${membershipId}/resend-invite`,
        method: "POST",
      }),
      invalidatesTags: ["Staff"],
    }),

    acceptInvitation: build.mutation<unknown, void>({
      query: () => ({ url: "/restaurant/staff/accept", method: "POST" }),
      // The session's membership changes from INVITED to ACTIVE, which is
      // what the guard reads to decide between the invitation and the portal.
      invalidatesTags: ["Session", "Staff"],
    }),

    changeStaffRole: build.mutation<unknown, { membershipId: string; role_code: string }>({
      query: ({ membershipId, role_code }) => ({
        url: `/restaurant/staff/${membershipId}`,
        method: "PATCH",
        body: { role_code },
      }),
      invalidatesTags: ["Staff"],
    }),

    resetStaffPassword: build.mutation<StaffPasswordReset, string>({
      query: (membershipId) => ({
        url: `/restaurant/staff/${membershipId}/reset-password`,
        method: "POST",
      }),
    }),

    revokeStaff: build.mutation<unknown, string>({
      query: (membershipId) => ({
        url: `/restaurant/staff/${membershipId}`,
        method: "DELETE",
      }),
      invalidatesTags: ["Staff"],
    }),

    restaurantReports: build.query<RestaurantReport, ReportRange>({
      query: ({ from, to }) => {
        const params = new URLSearchParams();
        if (from) params.set("from", from);
        if (to) params.set("to", to);
        const qs = params.toString();
        return { url: `/restaurant/reports${qs ? `?${qs}` : ""}` };
      },
      providesTags: ["RestaurantReport"],
    }),
  }),
});

export const {
  useStaffMeQuery,
  useStaffLogoutMutation,
  useStaffLogoutEverywhereMutation,
  useOrderBoardQuery,
  useOrderHistoryQuery,
  useDeliveriesQuery,
  useDriversQuery,
  useAssignDriverMutation,
  useUnassignDriverMutation,
  useSendDriverLocationMutation,
  usePickedUpMutation,
  useDeliveredMutation,
  useMarkReadyMutation,
  useCompleteOrderMutation,
  useOverrideCompleteMutation,
  useCancelOrderMutation,
  useRefundOrderMutation,
  useRefundsDueQuery,
  useMenuQuery,
  useItemTypesQuery,
  useCreateItemTypeMutation,
  useUpdateItemTypeMutation,
  useDeleteItemTypeMutation,
  useItemsQuery,
  useModifierGroupsQuery,
  useCreateMealMutation,
  useCreateItemMutation,
  useUpdateMealMutation,
  useUpdateItemMutation,
  useDeleteMealMutation,
  useDeleteItemMutation,
  useAddMealItemsMutation,
  useRemoveMealItemMutation,
  useCombosQuery,
  useCreateComboMutation,
  useUpdateComboMutation,
  useDeleteComboMutation,
  useSetItemAvailabilityMutation,
  useStockQuery,
  useCreateModifierGroupMutation,
  useUpdateModifierGroupMutation,
  useDeleteModifierGroupMutation,
  useCreateModifierOptionMutation,
  useUpdateModifierOptionMutation,
  useDeleteModifierOptionMutation,
  useStaffQuery,
  useInviteStaffMutation,
  useResendStaffInviteMutation,
  useAcceptInvitationMutation,
  useRevokeStaffMutation,
  useChangeStaffRoleMutation,
  useResetStaffPasswordMutation,
  useRestaurantReportsQuery,
  useRestaurantProfileQuery,
  useUpdateRestaurantProfileMutation,
  useUpdateOwnAccountMutation,
  useChangeStaffEmailMutation,
  useDeliverySettingsQuery,
  useLocateRestaurantMutation,
  useSetDeliverySettingsMutation,
  useSetDeliveryZonesMutation,
} = restaurantApi;
