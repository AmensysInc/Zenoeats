import type { Brand } from "@/features/storefront/brand";
import type { Storefront } from "@/features/storefront/theme";
export type Option = {
  id: string;
  name: string;
  price_delta_minor: number;
  /** What choosing this adds to the item's calories, or null where the
   *  restaurant stated no change. Negative takes calories off. */
  calories_delta?: number | null;
  is_available: boolean;
  /** A thumbnail beside the choice, or null when the option has none. */
  image_url: string | null;
};

export type ModifierGroup = {
  id: string;
  name: string;
  selection_type: "SINGLE" | "MULTI";
  is_required: boolean;
  min_select: number;
  max_select: number;
  options: Option[];
};

export type Item = {
  /** kcal as the restaurant states it, or null where it has not. A combo's
   *  total is added up from the items chosen for it. */
  calories?: number | null;
  id: string;
  name: string;
  /** Which type, by id. The name is on the section or slot above it, so a
   *  rename reaches every item at once. */
  item_type_id: string;
  description: string | null;
  base_price_minor: number;
  currency: string;
  is_available: boolean;
  /** Ready for an img tag, or null when the restaurant has not added one.
   *  A URL rather than a storage key, so the storefront never needs to know
   *  where the photos are kept. */
  image_url: string | null;
  modifier_groups: ModifierGroup[];
  /** What the item comes with: chosen before the customer sees it, and
   *  charged at nothing even where the same option costs money elsewhere. */
  included_option_ids: string[];
};

/** One subcategory block inside a section: Burgers, under Food. The same
 *  shape as the section above it, minus the nesting, because there is none.
 *  A menu goes two levels deep. */
export type Subsection = { item_type_id: string; label: string; items: Item[] };

/** A heading inside a meal period. Derived by the server from the types of
 *  the items served, so there is no such thing as an empty one. `label` is
 *  the restaurant's own word, and the order is the order it chose.
 *
 *  `items` are filed on the heading itself and read first. `groups` are its
 *  subcategories, each with a subheading of its own. Both can be empty, but
 *  never both at once, and most menus send `groups` empty forever. */
export type Section = {
  item_type_id: string;
  label: string;
  items: Item[];
  groups: Subsection[];
};

/** One required choice inside a combo. Exactly one item, always. */
export type ComboSlot = {
  id: string;
  item_type_id: string;
  label: string;
  items: Item[];
};

/** How a combo is cheaper than its parts. PERCENT carries basis points,
 *  1250 being 12.5%; AMOUNT carries minor units. */
export type DiscountKind = "NONE" | "PERCENT" | "AMOUNT";

/** A meal deal: one item from each slot, sold together for less.
 *
 *  No finished price, because there is not one until the choices are made.
 *  The browser previews a total from the discount; the server prices the
 *  real one, and only that one is charged. */
export type Combo = {
  id: string;
  name: string;
  description: string | null;
  discount_kind: DiscountKind;
  discount_value: number;
  /** The deal's own photo. Null borrows one from an item inside it. */
  image_url: string | null;
  slots: ComboSlot[];
};

/** A meal period: Breakfast, Lunch, Late night. It serves items rather than
 *  owning them, so the same item can appear in several. */
export type Meal = {
  id: string;
  name: string;
  /** The hours it is served, as HH:MM, or both null where the restaurant has
   *  not said. Wall clock in the restaurant's own day, for a customer to
   *  read: nothing is gated on them, here or on the server. An end at or
   *  before the start runs into the next day, which is what late night is. */
  starts_at: string | null;
  ends_at: string | null;
  sections: Section[];
  combos: Combo[];
};

export type Portal = {
  storefront?: Storefront | null;
  /** The logo and name lettering from Settings. Shown whether or not the
   *  storefront is customized. */
  brand?: Brand;
  pickup_address?: string | null;
  restaurant_id: string;
  slug: string;
  name: string;
  tagline: string | null;
  currency: string;
  is_orderable: boolean;
  accepting_orders: boolean;
  stripe_publishable_key: string;
  stripe_account_id: string | null;
  /** Whether checkout offers delivery at all. Whether a particular address
   *  can be delivered to is the quote's answer. */
  delivery_offered: boolean;
  /** For the live delivery map: a browser key restricted to this site, and
   *  the Map ID its markers need. Null when there is no map to show. */
  maps_browser_key: string | null;
  /** How the delivery map colours itself, or null for Google's own look. */
  map_style_key?: string | null;
  /** Whether the map's pins take the restaurant's palette. */
  map_pins_themed?: boolean;
};

/** Who is ordering, as the server sees them. A guest is a real identity for
 *  every purpose except being recoverable: it lives in one browser's cookie
 *  and cannot be signed back into. */
export type CustomerSession = {
  email: string;
  full_name: string | null;
  /** What checkout saved last time, offered back so nobody types it twice. */
  phone: string | null;
  address: string | null;
  /** A signed-in customer whose email Clerk has not supplied yet. Checkout
   *  cannot place an order until it has. */
  email_pending: boolean;
  is_guest: boolean;
  /** Whether this customer has agreed to the terms in force. False for an
   *  account created through Google, Apple or Facebook without passing a
   *  consent step: Clerk finishes those itself whenever the provider gave it
   *  everything, and never asks us. */
  terms_accepted: boolean;
};

export type FulfillmentType = "PICKUP" | "DELIVERY";

export type MapPoint = { latitude: number; longitude: number };

/** What has happened so far, in order. Any step can be missing. */
export type TrackingStep = "PAID" | "DRIVER_ASSIGNED" | "READY" | "PICKED_UP" | "DELIVERED";

export type Tracking = {
  steps: { step: TrackingStep; at: string }[];
  /** First name only. */
  driver_name: string | null;
  restaurant: MapPoint | null;
  destination: MapPoint | null;
  /** Only while the order is on the road and the driver's phone is reporting. */
  driver_location: (MapPoint & { heading: number | null; recorded_at: string }) | null;
  eta_seconds: number | null;
  eta_computed_at: string | null;
};

/** A row of the customer's order history: enough to recognise an order, with
 *  a meal deal as one line under its own name. */
export type OrderSummary = {
  order_id: string;
  order_number: number;
  status: string;
  fulfillment_type: FulfillmentType;
  currency: string;
  total_minor: number;
  created_at: string;
  lines: string[];
};

/** A saved item. Price, photo and availability come from the menu, which is
 *  also how the page knows whether it is being served right now. */
export type Favourite = { item_id: string; name: string; saved_at: string };

/** Who is ordering, as checkout requires it. The email is not here: it is the
 *  account's, or the one a guest session began with. */
export type Contact = {
  full_name: string;
  phone: string;
  address: string;
};

export type CartModifier = {
  option_id: string;
  quantity: number;
  label: string;
  delta: number;
};

export type CartLine = {
  key: string;
  menu_item_id: string;
  name: string;
  quantity: number;
  note?: string;
  unitPreviewMinor: number; // display only; the server reprices everything
  modifiers: CartModifier[];
};

/** What fills one slot of a combo in the cart. The labels are carried so the
 *  cart and checkout can describe the deal without re-reading the menu. */
export type CartComboSelection = {
  slot_id: string;
  menu_item_id: string;
  slotLabel: string;
  itemName: string;
  modifiers: CartModifier[];
};

/** One combo in the cart, with a choice for every slot.
 *
 *  unitPreviewMinor is after the discount and is display only, like the one
 *  on a line. What the deal actually costs is the server's answer. */
export type CartComboLine = {
  key: string;
  combo_id: string;
  name: string;
  quantity: number;
  note?: string;
  unitPreviewMinor: number;
  selections: CartComboSelection[];
};

export type Amounts = {
  subtotal_minor: number;
  discount_minor: number;
  /** Zero on a collection. */
  delivery_fee_minor: number;
  tax_minor: number;
  total_minor: number;
};

/** One line of a placed order.
 *
 *  Lines that came from a meal deal carry its name and a group number, so the
 *  three rows of a combo can be shown as the one thing the customer bought
 *  rather than as unrelated food that happened to be cheap. */
export type OrderLine = {
  name: string;
  combo_name: string | null;
  combo_group: number | null;
  quantity: number;
  unit_price_minor: number;
  line_total_minor: number;
  item_note: string | null;
  modifiers: { group_name: string; option_name: string; unit_price_delta_minor: number; quantity: number }[];
};

export type Order = {
  order_id: string;
  order_number: number;
  status: string;
  payment_status: string;
  currency: string;
  amounts: Amounts;
  items: OrderLine[];
  fulfillment_type: FulfillmentType;
  pickup_pin: string | null;
  delivery_address: string | null;
  /** A paid delivery, as its customer follows it. Null for a collection. */
  tracking: Tracking | null;
  expires_at: string | null;
  created_at: string;
  /** The restaurant cancelled it after it was paid. */
  cancelled_by_restaurant: boolean;
  /** On its way back to the customer's card; null until a refund is issued. */
  refund_minor: number | null;
};
