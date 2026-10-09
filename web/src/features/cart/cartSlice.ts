import { createSlice, createSelector, type PayloadAction } from "@reduxjs/toolkit";
import type { CartComboLine, CartLine, Combo, Item, Option } from "@/types";
import type { RootState } from "@/app/store";
import { modifierDelta, toCartModifiers, type Selection } from "./modifiers";

/**
 * The cart is one of only two things that genuinely belongs in Redux.
 *
 * It is written by the storefront, read by the cart bar, and read and edited
 * again by checkout -- three unrelated places in the tree. It also has to
 * survive a reload, because a customer who refreshes mid-order has not
 * changed their mind.
 *
 * Keyed by restaurant slug: subdomains share an origin and therefore share
 * localStorage, so one key would let a cart from one restaurant appear in
 * another, with menu item ids that mean nothing there.
 */

// v2 because the stored shape gained combos. A v1 cart read as v2 would
// arrive with `combos` undefined and crash the first selector that counts it,
// so the old key is simply not read: the worst case is one abandoned cart,
// against a menu that has changed underneath it anyway.
const STORAGE_PREFIX = "zenoeats.cart.v2";

type StoredCart = {
  lines: CartLine[];
  combos: CartComboLine[];
};

type CartState = StoredCart & {
  slug: string | null;
};

const initialState: CartState = { slug: null, lines: [], combos: [] };

function storageKey(slug: string): string {
  return `${STORAGE_PREFIX}.${slug}`;
}

/** Reads are wrapped because localStorage throws outright in some contexts --
 *  a private window with site data blocked, for instance -- and a cart that
 *  cannot be restored must not stop the menu rendering.
 *
 *  Both arrays are defaulted on the way out as well as on the way in: a cart
 *  half-written by an older tab is a shape this code has to survive, not one
 *  it can assume away. */
function load(slug: string): StoredCart {
  try {
    const raw = window.localStorage.getItem(storageKey(slug));
    if (!raw) return { lines: [], combos: [] };
    const parsed = JSON.parse(raw) as Partial<StoredCart>;
    return { lines: parsed.lines ?? [], combos: parsed.combos ?? [] };
  } catch {
    return { lines: [], combos: [] };
  }
}

function persist(state: CartState): void {
  // No slug means no key to write to, so there is nothing this can do. It
  // also means the caller never opened the cart (useOpenCart), and a write
  // that silently does nothing is how a paid cart survived checkout: the
  // page cleared the copy in memory and left storage untouched.
  if (!state.slug) return;
  try {
    const stored: StoredCart = { lines: state.lines, combos: state.combos };
    window.localStorage.setItem(storageKey(state.slug), JSON.stringify(stored));
  } catch {
    /* storage unavailable or full; the in-memory cart still works */
  }
}

/** What one combo costs before the server says so, discount included.
 *
 *  Display only, and deliberately the same arithmetic the server uses: round
 *  the percentage once, and never take off more than the food costs. A
 *  preview that disagreed with the receipt would look like a price change at
 *  checkout, which is the one thing this app refuses to do quietly. */
export function comboPreviewMinor(combo: Combo, itemsSubtotalMinor: number): number {
  if (itemsSubtotalMinor <= 0) return 0;
  let off = 0;
  if (combo.discount_kind === "PERCENT") {
    off = Math.round((itemsSubtotalMinor * combo.discount_value) / 10000);
  } else if (combo.discount_kind === "AMOUNT") {
    off = Math.max(0, combo.discount_value);
  }
  return Math.max(0, itemsSubtotalMinor - Math.min(off, itemsSubtotalMinor));
}

const cartSlice = createSlice({
  name: "cart",
  initialState,
  reducers: {
    /** Bind the cart to a restaurant and restore anything saved for it. */
    cartOpened(state, action: PayloadAction<string>) {
      const slug = action.payload;
      if (state.slug === slug) return;
      const stored = load(slug);
      state.slug = slug;
      state.lines = stored.lines;
      state.combos = stored.combos;
    },

    itemAdded: {
      reducer(
        state,
        action: PayloadAction<{ line: CartLine }>,
      ) {
        const incoming = action.payload.line;
        const existing = state.lines.find((l) => l.key === incoming.key);
        // Identical configurations merge rather than stacking as separate rows.
        if (existing) existing.quantity += incoming.quantity;
        else state.lines.push(incoming);
        persist(state);
      },
      /** The line is built here rather than in the component so the merge key
       *  and the price preview are defined in exactly one place. */
      prepare(item: Item, selected: Map<string, Option[]>, quantity: number, note?: string) {
        // Built through the shared helper so an included option is priced at
        // nothing here exactly as it is everywhere else.
        const modifiers = toCartModifiers(item, selected);
        const unitPreviewMinor =
          item.base_price_minor + modifiers.reduce((sum, m) => sum + m.delta, 0);
        const key = [item.id, ...modifiers.map((m) => m.option_id).sort(), note ?? ""].join("|");

        return {
          payload: {
            line: {
              key,
              menu_item_id: item.id,
              name: item.name,
              imageUrl: item.image_url ?? null,
              quantity,
              note,
              unitPreviewMinor,
              modifiers,
            } satisfies CartLine,
          },
        };
      },
    },

    comboAdded: {
      reducer(state, action: PayloadAction<{ line: CartComboLine }>) {
        const incoming = action.payload.line;
        const existing = state.combos.find((c) => c.key === incoming.key);
        // Identical deals merge, exactly as identical items do.
        if (existing) existing.quantity += incoming.quantity;
        else state.combos.push(incoming);
        persist(state);
      },
      /** Built here rather than in the sheet so the merge key and the price
       *  preview have one definition, the same rule the item line follows. */
      prepare(
        combo: Combo,
        chosen: { slotId: string; slotLabel: string; item: Item; selected: Selection }[],
        quantity: number,
        note?: string,
      ) {
        const selections = chosen.map((c) => ({
          slot_id: c.slotId,
          menu_item_id: c.item.id,
          slotLabel: c.slotLabel,
          itemName: c.item.name,
          modifiers: toCartModifiers(c.item, c.selected),
        }));

        const itemsSubtotal = chosen.reduce(
          (sum, c) => sum + Math.max(0, c.item.base_price_minor + modifierDelta(c.item, c.selected)),
          0,
        );

        // Every choice and every option is in the key, so two differently
        // built deals stay two rows and two identical ones merge.
        const key = [
          combo.id,
          ...selections.map((s) =>
            [s.slot_id, s.menu_item_id, ...s.modifiers.map((m) => m.option_id).sort()].join("."),
          ),
          note ?? "",
        ].join("|");

        return {
          payload: {
            line: {
              key,
              combo_id: combo.id,
              // A deal has no photo of its own unless the restaurant took
              // one, so borrow from the first chosen item that has one --
              // the same rule the menu card follows.
              imageUrl:
                combo.image_url ?? chosen.find((c) => c.item.image_url)?.item.image_url ?? null,
              name: combo.name,
              quantity,
              note,
              unitPreviewMinor: comboPreviewMinor(combo, itemsSubtotal),
              selections,
            } satisfies CartComboLine,
          },
        };
      },
    },

    comboRemoved(state, action: PayloadAction<string>) {
      state.combos = state.combos.filter((c) => c.key !== action.payload);
      persist(state);
    },

    comboQuantitySet(state, action: PayloadAction<{ key: string; quantity: number }>) {
      const { key, quantity } = action.payload;
      if (quantity < 1) state.combos = state.combos.filter((c) => c.key !== key);
      else {
        const line = state.combos.find((c) => c.key === key);
        if (line) line.quantity = quantity;
      }
      persist(state);
    },

    lineRemoved(state, action: PayloadAction<string>) {
      state.lines = state.lines.filter((l) => l.key !== action.payload);
      persist(state);
    },

    lineQuantitySet(state, action: PayloadAction<{ key: string; quantity: number }>) {
      const { key, quantity } = action.payload;
      if (quantity < 1) state.lines = state.lines.filter((l) => l.key !== key);
      else {
        const line = state.lines.find((l) => l.key === key);
        if (line) line.quantity = quantity;
      }
      persist(state);
    },

    cartCleared(state) {
      state.lines = [];
      state.combos = [];
      persist(state);
    },
  },
});

export const {
  cartOpened,
  itemAdded,
  comboAdded,
  comboRemoved,
  comboQuantitySet,
  lineRemoved,
  lineQuantitySet,
  cartCleared,
} = cartSlice.actions;

export default cartSlice.reducer;

export const selectCartLines = (s: RootState) => s.cart.lines;
export const selectCartCombos = (s: RootState) => s.cart.combos;

/** A combo counts as one thing, whatever it is made of. Someone with a meal
 *  deal in their cart has one item in it, not three. */
export const selectCartCount = createSelector(
  selectCartLines,
  selectCartCombos,
  (lines, combos) =>
    lines.reduce((n, l) => n + l.quantity, 0) +
    combos.reduce((n, c) => n + c.quantity, 0),
);

export const selectCartEmpty = createSelector(
  selectCartCount,
  (count) => count === 0,
);

/** Display only. The server reprices everything at checkout and its answer is
 *  the one that counts. Combo previews are already net of their discount. */
export const selectCartPreviewSubtotal = createSelector(
  selectCartLines,
  selectCartCombos,
  (lines, combos) =>
    lines.reduce((n, l) => n + l.unitPreviewMinor * l.quantity, 0) +
    combos.reduce((n, c) => n + c.unitPreviewMinor * c.quantity, 0),
);
