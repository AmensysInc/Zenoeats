import { useState } from "react";
import { Empty, Panel, Spinner } from "@/components/common/Feedback";
import { PencilIcon } from "@/components/common/icons";
import { deltaToMinor, minorToDeltaInput, money } from "@/utils/format";
import type { DiscountKind, Meal } from "@/types";
import { rootIdOf, topLevel } from "../itemTypes";
import { ImagePicker, NO_IMAGE, type ImageDraft } from "./ImagePicker";
import { errorMessage } from "@/services/apiClient";
import {
  useCreateComboMutation,
  useDeleteComboMutation,
  useUpdateComboMutation,
  type BuilderCombo,
  type ComboSlotDraft,
  type ItemTypeRow,
  type LibraryItem,
} from "../restaurantApi";

/**
 * Combos: one item from each of several types, sold together for less.
 *
 * A combo belongs to one meal period, and the choices it offers can only be
 * items that period serves. That is why the period is chosen first and the
 * item lists appear afterwards: there is nothing sensible to tick until the
 * builder has said when this deal is on sale.
 *
 * The types in a combo are not chosen separately. A type with items ticked
 * is a required choice; a type with none is not part of the deal. One step
 * instead of two, and no way to leave behind a type that asks for something
 * from an empty list.
 */
export function ComboBuilder({
  combos,
  meals,
  items,
  types,
  onError,
}: {
  combos: BuilderCombo[];
  meals: Meal[];
  items: LibraryItem[];
  types: ItemTypeRow[];
  onError: (message: string | null) => void;
}) {
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);
  const [createCombo] = useCreateComboMutation();

  const mealName = new Map(meals.map((m) => [m.id, m.name]));
  const typeName = new Map(types.map((t) => [t.id, t.name]));

  return (
    <>
      <Panel
        title="Combos"
        action={
          <button
            type="button"
            className="link"
            aria-expanded={adding}
            onClick={() => {
              onError(null);
              setAdding((open) => !open);
            }}
          >
            {adding ? "cancel" : "add a combo"}
          </button>
        }
      >
        {!meals.length ? (
          <Empty>
            A combo is sold during one meal period and offers items that period serves. Add a meal
            period first, and put some items on it.
          </Empty>
        ) : (
          <p className="-mt-2 mb-6 max-w-prose text-caption text-muted">
            One item from each type you include, every one required. The saving comes off what
            those items cost separately.
          </p>
        )}

        {adding && (
          <div className="editor mb-6 animate-disclose">
            <ComboForm
              meals={meals}
              items={items}
              types={types}
              submitLabel="Create combo"
              onCancel={() => setAdding(false)}
              onSubmit={async (draft) => {
                await createCombo(draft).unwrap();
                setAdding(false);
              }}
              onError={onError}
            />
          </div>
        )}

        {!combos.length && !adding && meals.length > 0 && <Empty>No combos yet. Add one above.</Empty>}

        <div className="flex flex-col gap-5">
          {combos.map((combo) =>
            editing === combo.id ? (
              <ComboEditor
                key={combo.id}
                combo={combo}
                meals={meals}
                items={items}
                types={types}
                onDone={() => setEditing(null)}
                onError={onError}
              />
            ) : (
              <article key={combo.id} className="card">
                <header className="flex items-center justify-between gap-4">
                  {combo.image_url && (
                    <img
                      src={combo.image_url}
                      alt=""
                      className="h-14 w-14 shrink-0 rounded-field object-cover"
                    />
                  )}
                  <h3 className="mr-auto font-display text-[26px] leading-tight tracking-[-.5px]">{combo.name}</h3>
                  <button
                    type="button"
                    aria-label={`Edit ${combo.name}`}
                    className="link min-h-[32px] px-1 text-base no-underline"
                    onClick={() => {
                      onError(null);
                      setEditing(combo.id);
                    }}
                  >
                    <PencilIcon />
                  </button>
                </header>
                <p className="mt-3 text-caption">
                  {mealName.get(combo.meal_id) ?? "no meal period"} ·{" "}
                  <span className="font-[650] text-brick">{savingWords(combo)}</span>
                  {!combo.is_available && <span className="text-danger"> · hidden</span>}
                </p>

                {!combo.slots.length ? (
                  <p className="field-hint mt-6">
                    Nothing in this combo yet. Open it and tick the items it includes.
                  </p>
                ) : (
                  <ul className="mt-5 flex flex-col gap-2">
                    {combo.slots.map((slot) => (
                      <li key={slot.id} className="text-sm">
                        <strong className="font-semibold">
                          {typeName.get(slot.item_type_id) ?? "unknown type"}
                        </strong>{" "}
                        <span className="text-muted">
                          {slot.item_ids.map((id, index) => {
                            const item = items.find((i) => i.id === id);
                            // Still ticked, but taken off this combo's period since:
                            // the storefront and checkout skip it until it is served
                            // there again, so the list says why it is missing.
                            const offPeriod = item && !item.meal_ids.includes(combo.meal_id);
                            return (
                              <span key={id}>
                                {index > 0 && " · "}
                                <span className={offPeriod ? "line-through" : undefined}>
                                  {item?.name ?? "?"}
                                </span>
                                {offPeriod && (
                                  <span className="text-caption text-danger">
                                    {" "}
                                    (not on {mealName.get(combo.meal_id) ?? "this period"})
                                  </span>
                                )}
                              </span>
                            );
                          })}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </article>
            ),
          )}
        </div>
      </Panel>
    </>
  );
}

function ComboEditor({
  combo,
  meals,
  items,
  types,
  onDone,
  onError,
}: {
  combo: BuilderCombo;
  meals: Meal[];
  items: LibraryItem[];
  types: ItemTypeRow[];
  onDone: () => void;
  onError: (message: string | null) => void;
}) {
  const [removing, setRemoving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [updateCombo] = useUpdateComboMutation();
  const [deleteCombo] = useDeleteComboMutation();

  if (removing) {
    return (
      <section className="inline-confirm">
        <p>
          <strong className="font-semibold text-danger">{combo.name} comes off the menu.</strong> The
          items it offered are untouched and stay on sale on their own.
        </p>
        <div className="mt-3.5 flex flex-wrap items-center gap-4">
          <button
            type="button"
            className="btn-danger"
            disabled={deleting}
            onClick={async () => {
              setDeleting(true);
              try {
                await deleteCombo(combo.id).unwrap();
                onError(null);
                onDone();
              } catch (e) {
                onError(errorMessage(e));
              } finally {
                setDeleting(false);
              }
            }}
          >
            {deleting && <Spinner />}
            Delete it
          </button>
          <button type="button" className="link" disabled={deleting} onClick={() => setRemoving(false)}>
            keep it
          </button>
        </div>
      </section>
    );
  }

  return (
    <section className="editor animate-disclose">
      <ComboForm
        initial={combo}
        meals={meals}
        items={items}
        types={types}
        submitLabel="Save changes"
        onCancel={onDone}
        onDelete={() => setRemoving(true)}
        onSubmit={async (draft) => {
          const { meal_id: _meal, ...changes } = draft;
          await updateCombo({ comboId: combo.id, changes }).unwrap();
          onDone();
        }}
        onError={onError}
      />
    </section>
  );
}

/* A combo card is wide on a laptop and stacked on a phone, and the photo
 * fills half of it either way -- so the advice is the item advice, with the
 * tray rather than one dish in the middle of it. */
const COMBO_GUIDE = {
  size: "Square, 1200 × 1200 px or larger",
  tip: "Shoot the whole tray, centred: the card crops the edges differently on a phone and a laptop.",
};

type Draft = {
  mealId: string;
  name: string;
  description: string;
  discountKind: DiscountKind;
  /** Typed as the manager thinks of it: "10" for ten percent, "1.50" for a
   *  pound fifty. Converted once, on save. */
  discountInput: string;
  /** Ticked items, keyed by item type id. A type with none is not part of
   *  the combo. */
  picked: Record<string, string[]>;
  /** Fixed-price combos only: what choosing an item adds, typed as "1.00",
   *  keyed by item id. Blank means nothing extra. */
  upcharges: Record<string, string>;
  /** Courses the customer may leave out, keyed by item type id. */
  optional: Record<string, boolean>;
  /** Fixed-price combos only: what leaving a course out takes off, typed as
   *  "1.00", keyed by item type id. Blank means nothing off. */
  skipCredits: Record<string, string>;
  /** The deal's own photo. Uploaded as it is chosen, attached on save, so
   *  cancelling the form leaves the menu as it was. */
  image: ImageDraft;
};

function seed(combo: BuilderCombo | undefined, meals: Meal[]): Draft {
  const picked: Record<string, string[]> = {};
  const upcharges: Record<string, string> = {};
  const optional: Record<string, boolean> = {};
  const skipCredits: Record<string, string> = {};
  for (const slot of combo?.slots ?? []) {
    picked[slot.item_type_id] = [...slot.item_ids];
    for (const [itemId, minor] of Object.entries(slot.upcharges ?? {})) {
      upcharges[itemId] = minorToDeltaInput(minor);
    }
    if (slot.is_optional) optional[slot.item_type_id] = true;
    if (slot.skip_credit_minor) {
      skipCredits[slot.item_type_id] = minorToDeltaInput(slot.skip_credit_minor);
    }
  }

  return {
    mealId: combo?.meal_id ?? meals[0]?.id ?? "",
    name: combo?.name ?? "",
    description: combo?.description ?? "",
    discountKind: combo?.discount_kind ?? "FIXED_PRICE",
    image: combo?.image_path ? { path: combo.image_path, url: combo.image_url } : NO_IMAGE,
    discountInput: !combo || combo.discount_kind === "NONE"
      ? ""
      : combo.discount_kind === "PERCENT"
        ? String(combo.discount_value / 100)
        : minorToDeltaInput(combo.discount_value),
    picked,
    upcharges,
    optional,
    skipCredits,
  };
}

/**
 * The combo form, used to add and to edit.
 *
 * The meal period is fixed once a combo exists. Every choice in it is an item
 * that period serves, so moving the combo would invalidate all of them at
 * once -- that is a new combo, and building it as one is clearer than a rule
 * about which choices survive.
 */
function ComboForm({
  initial,
  meals,
  items,
  types,
  submitLabel,
  onSubmit,
  onCancel,
  onDelete,
  onError,
}: {
  initial?: BuilderCombo;
  meals: Meal[];
  items: LibraryItem[];
  types: ItemTypeRow[];
  submitLabel: string;
  onSubmit: (draft: {
    meal_id: string;
    name: string;
    description: string | null;
    discount_kind: DiscountKind;
    discount_value: number;
    image_path: string | null;
    slots: ComboSlotDraft[];
  }) => Promise<void>;
  onCancel: () => void;
  onDelete?: () => void;
  onError: (message: string | null) => void;
}) {
  const [draft, setDraft] = useState<Draft>(() => seed(initial, meals));
  const [saving, setSaving] = useState(false);
  // A photo still uploading holds Save: saving now would store the combo
  // without the picture that was just chosen.
  const [uploading, setUploading] = useState(0);

  // Only what this period serves. An item taken off the period is no longer
  // offerable in a combo on it, and the server refuses one that is.
  const servedHere = items.filter((item) => item.meal_ids.includes(draft.mealId));

  // A slot asks for a heading, and everything inside it can fill the slot.
  // That is what lets one meal deal offer burgers or nuggets where two
  // top-level types would have forced it into two deals.
  const byType = (typeId: string) =>
    servedHere.filter((item) => rootIdOf(types, item.item_type_id) === typeId);

  // Combos are built from headings only. A slot asking for a subcategory
  // would narrow the deal every time the menu was subdivided further.
  const headings = topLevel(types);

  function togglePick(typeId: string, itemId: string) {
    setDraft((d) => {
      const current = d.picked[typeId] ?? [];
      const next = current.includes(itemId)
        ? current.filter((x) => x !== itemId)
        : [...current, itemId];
      return { ...d, picked: { ...d.picked, [typeId]: next } };
    });
  }

  async function save() {
    const name = draft.name.trim();
    if (!name) {
      onError("Enter a name for the combo, like Burger Meal.");
      return;
    }
    if (!draft.mealId) {
      onError("Choose the meal period this combo is sold during.");
      return;
    }

    // In the restaurant's own type order, so the slots a customer works
    // through read the same way down the page as the menu does.
    const slots: ComboSlotDraft[] = headings
      .filter((t) => (draft.picked[t.id] ?? []).length > 0)
      .map((t) => ({
        item_type_id: t.id,
        is_optional: draft.optional[t.id] === true,
        // Only choices this period still serves. A tick left over from before
        // an item was taken off the period is not on screen to untick, and the
        // server refuses it, so it is dropped here rather than failing a save
        // over something the manager cannot see.
        item_ids: (draft.picked[t.id] ?? []).filter((id) =>
          servedHere.some((item) => item.id === id),
        ),
      }))
      .filter((slot) => slot.item_ids.length > 0);

    // Upcharges only mean something on a fixed-price deal; on the other kinds
    // the dishes' own price difference already does the job.
    if (draft.discountKind === "FIXED_PRICE") {
      for (const slot of slots) {
        const charged: Record<string, number> = {};
        for (const id of slot.item_ids) {
          const typed = (draft.upcharges[id] ?? "").trim();
          if (!typed) continue;
          const minor = deltaToMinor(typed);
          if (minor === null || minor < 0) {
            const name = items.find((item) => item.id === id)?.name ?? "an item";
            onError(`Enter the extra for ${name} as a plain amount, like 1.00.`);
            return;
          }
          if (minor > 0) charged[id] = minor;
        }
        slot.upcharges = charged;

        // What leaving this course out gives back. Only a set price needs
        // it: the other kinds lose the dish's own price from the subtotal
        // and need no figure stated.
        if (slot.is_optional) {
          const typed = (draft.skipCredits[slot.item_type_id] ?? "").trim();
          if (typed) {
            const minor = deltaToMinor(typed);
            if (minor === null || minor < 0) {
              const name = headings.find((t) => t.id === slot.item_type_id)?.name ?? "that course";
              onError(`Enter what leaving ${name} out takes off as a plain amount, like 1.00.`);
              return;
            }
            slot.skip_credit_minor = minor;
          }
        }
      }
    }

    // Something has to be ordered. The server refuses such a combo too, but
    // saying so here names the fix while the form is still open.
    if (slots.length > 0 && slots.every((slot) => slot.is_optional)) {
      onError("At least one course has to be one the customer must take.");
      return;
    }

    if (!slots.length) {
      onError("Tick the items this combo includes. Each type ticked becomes a choice.");
      return;
    }
    if (slots.length < 2) {
      // Not a rule the server enforces, because one is a coherent thing to
      // store. It is almost always a half-finished combo, though, and saying
      // so costs nothing.
      onError("A combo needs at least two types. One item on its own is just an item.");
      return;
    }

    let discountValue = 0;
    if (draft.discountKind !== "NONE") {
      const typed = draft.discountInput.trim();
      if (!typed) {
        onError(
          draft.discountKind === "PERCENT"
            ? "Enter the discount as a percentage, like 10."
            : draft.discountKind === "FIXED_PRICE"
              ? "Enter the combo's price, like 8.99."
              : "Enter the discount as an amount, like 1.50.",
        );
        return;
      }
      if (draft.discountKind === "PERCENT") {
        const percent = Number(typed);
        if (!Number.isFinite(percent) || percent < 0 || percent > 100) {
          onError("Enter the discount as a percentage between 0 and 100.");
          return;
        }
        // Basis points, so 12.5% is 1250 and nothing is lost to a float.
        discountValue = Math.round(percent * 100);
      } else if (draft.discountKind === "FIXED_PRICE") {
        const minor = deltaToMinor(typed);
        if (minor === null || minor <= 0) {
          onError("Enter the combo's price as a plain amount, like 8.99.");
          return;
        }
        discountValue = minor;
      } else {
        const minor = deltaToMinor(typed);
        if (minor === null || minor < 0) {
          onError("Enter the discount as a plain amount, like 1.50.");
          return;
        }
        discountValue = minor;
      }
    }

    setSaving(true);
    try {
      await onSubmit({
        meal_id: draft.mealId,
        name,
        description: draft.description.trim() || null,
        discount_kind: draft.discountKind,
        discount_value: discountValue,
        image_path: draft.image.path,
        slots,
      });
      onError(null);
    } catch (e) {
      onError(errorMessage(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <form
      className="flex flex-col gap-[17px]"
      onSubmit={(e) => {
        e.preventDefault();
        if (!saving) void save();
      }}
    >
      <div className="grid grid-cols-1 gap-[18px] sm:grid-cols-2">
        <label className="block">
          <span className="label">Combo name</span>
          <input
            className="field mt-[7px]"
            placeholder="Burger Meal"
            value={draft.name}
            autoFocus
            disabled={saving}
            onChange={(e) => setDraft((d) => ({ ...d, name: e.target.value }))}
          />
        </label>

        <label className="block">
          <span className="label">Sold during</span>
          <select
            className="field mt-[7px]"
            value={draft.mealId}
            // Fixed after creation: every ticked item belongs to this period.
            disabled={saving || !!initial}
            aria-describedby={initial ? "combo-period-lock" : undefined}
            onChange={(e) =>
              // The old ticks belong to the old period's menu, so they go.
              setDraft((d) => ({ ...d, mealId: e.target.value, picked: {} }))
            }
          >
            {!meals.length && <option value="">No meal periods yet</option>}
            {meals.map((meal) => (
              <option key={meal.id} value={meal.id}>
                {meal.name}
              </option>
            ))}
          </select>
          {initial && (
            <span id="combo-period-lock" className="field-hint block">
              The meal period is locked once the combo exists.
            </span>
          )}
        </label>

        <div className="block sm:col-span-2">
          <span className="label">Photo of the deal</span>
          <div className="mt-[7px]">
            <ImagePicker
              kind="items"
              label={draft.name.trim() || "this combo"}
              image={draft.image}
              disabled={saving}
              guide={COMBO_GUIDE}
              onChange={(image) => setDraft((d) => ({ ...d, image }))}
              onBusyChange={(busy) => setUploading((n) => Math.max(0, n + (busy ? 1 : -1)))}
              onError={onError}
            />
          </div>
          <span className="field-hint block">
            Optional. Without one, customers see a photo of an item inside the combo.
          </span>
        </div>

        <label className="block sm:col-span-2">
          <span className="label">Description</span>
          <input
            className="field mt-[7px]"
            value={draft.description}
            disabled={saving}
            onChange={(e) => setDraft((d) => ({ ...d, description: e.target.value }))}
          />
        </label>

        <label className="block">
          <span className="label">Pricing</span>
          <select
            className="field mt-[7px]"
            value={draft.discountKind}
            disabled={saving}
            onChange={(e) => setDraft((d) => ({ ...d, discountKind: e.target.value as DiscountKind }))}
          >
            <option value="FIXED_PRICE">Fixed price</option>
            <option value="PERCENT">Percentage off</option>
            <option value="AMOUNT">Amount off</option>
            <option value="NONE">No discount</option>
          </select>
        </label>

        {draft.discountKind !== "NONE" && (
          <label className="block">
            <span className="label">
              {draft.discountKind === "PERCENT"
                ? "Percent off the total"
                : draft.discountKind === "FIXED_PRICE"
                  ? "Combo price"
                  : "Amount off the total"}
            </span>
            <input
              className="field tnum mt-[7px]"
              inputMode="decimal"
              placeholder={
                draft.discountKind === "PERCENT"
                  ? "10"
                  : draft.discountKind === "FIXED_PRICE"
                    ? "8.99"
                    : "1.50"
              }
              value={draft.discountInput}
              disabled={saving}
              onChange={(e) => setDraft((d) => ({ ...d, discountInput: e.target.value }))}
            />
          </label>
        )}
      </div>

      <fieldset>
        <legend className="mb-1 text-sm font-semibold">What this combo includes</legend>
        <p className="max-w-prose text-caption text-muted">
          Tick the items a customer may choose from. Every type you tick becomes one
          choice, required unless you let the customer leave it out.
        </p>

        {!draft.mealId ? (
          <p className="field-hint">Choose a meal period first.</p>
        ) : !servedHere.length ? (
          <div className="mt-3">
            <Empty>
              That meal period serves nothing yet. Put items on it first, on the Meal periods tab.
            </Empty>
          </div>
        ) : (
          <div className="mt-3 flex flex-col gap-4">
            {headings.map((type) => {
              const available = byType(type.id);
              if (!available.length) return null;
              const picked = draft.picked[type.id] ?? [];
              return (
                <div key={type.id}>
                  <h4 className="flex items-center gap-2 text-sm font-semibold">
                    {type.name}
                    {picked.length > 0 && (
                      <span className="h-1.5 w-1.5 rounded-full bg-brick" aria-label="included" />
                    )}
                  </h4>
                  <div className="mt-2 flex flex-wrap gap-2">
                    {available.map((item) => {
                      const on = picked.includes(item.id);
                      return (
                        <button
                          key={item.id}
                          type="button"
                          aria-pressed={on}
                          disabled={saving}
                          onClick={() => togglePick(type.id, item.id)}
                          className="chip"
                        >
                          {item.name}
                          <span className="tnum opacity-70">· {money(item.base_price_minor, item.currency)}</span>
                        </button>
                      );
                    })}
                  </div>

                  {/* Only once the course is part of the combo: there is
                      nothing to make optional until something is ticked. */}
                  {picked.length > 0 && (
                    <div className="mt-2 flex flex-wrap items-center gap-3">
                      <label className="flex items-center gap-2 text-caption">
                        <input
                          type="checkbox"
                          className="h-4 w-4"
                          disabled={saving}
                          checked={draft.optional[type.id] === true}
                          onChange={(e) =>
                            setDraft((d) => ({
                              ...d,
                              optional: { ...d.optional, [type.id]: e.target.checked },
                            }))
                          }
                        />
                        A customer may leave this out
                      </label>
                      {/* A credit only means something on a set price, and
                          only on a course that can actually be declined. */}
                      {draft.optional[type.id] === true &&
                        draft.discountKind === "FIXED_PRICE" && (
                          <label className="flex items-center gap-2 text-caption">
                            Leaving it out takes off
                            <input
                              className="field w-[110px]"
                              inputMode="decimal"
                              placeholder="1.00"
                              disabled={saving}
                              value={draft.skipCredits[type.id] ?? ""}
                              onChange={(e) =>
                                setDraft((d) => ({
                                  ...d,
                                  skipCredits: { ...d.skipCredits, [type.id]: e.target.value },
                                }))
                              }
                            />
                          </label>
                        )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </fieldset>

      {draft.discountKind === "FIXED_PRICE" && (
        <UpchargeFields
          headings={headings}
          picked={draft.picked}
          items={servedHere}
          upcharges={draft.upcharges}
          disabled={saving}
          onChange={(itemId, value) =>
            setDraft((d) => ({ ...d, upcharges: { ...d.upcharges, [itemId]: value } }))
          }
        />
      )}

      <div className="flex flex-wrap items-center gap-4">
        <button type="submit" className="btn-primary" disabled={saving || uploading > 0}>
          {saving && <Spinner />}
          {saving ? "Saving…" : uploading > 0 ? "Uploading photo…" : submitLabel}
        </button>
        <button type="button" className="link" disabled={saving} onClick={onCancel}>
          cancel
        </button>
        {onDelete && (
          <button type="button" className="link-danger sm:ml-auto" disabled={saving} onClick={onDelete}>
            delete combo
          </button>
        )}
      </div>
    </form>
  );
}

/**
 * What each ticked item adds to a fixed-price combo. Blank is nothing extra:
 * the usual dish in each slot. A dearer swap -- cheese fries for fries --
 * gets the amount the counter would charge for it inside the meal.
 */
function UpchargeFields({
  headings,
  picked,
  items,
  upcharges,
  disabled,
  onChange,
}: {
  headings: ItemTypeRow[];
  picked: Record<string, string[]>;
  items: LibraryItem[];
  upcharges: Record<string, string>;
  disabled: boolean;
  onChange: (itemId: string, value: string) => void;
}) {
  const rows = headings.flatMap((type) =>
    (picked[type.id] ?? [])
      .map((id) => items.find((item) => item.id === id))
      .filter((item): item is LibraryItem => item !== undefined)
      .map((item) => ({ type, item })),
  );
  if (!rows.length) return null;

  return (
    <fieldset>
      <legend className="mb-1 text-sm font-semibold">Extra charge in this combo</legend>
      <p className="max-w-prose text-caption text-muted">
        Leave blank for the usual choices. For a dearer swap, enter what it adds to the combo
        price, like 1.00 for cheese fries. Sizes and options still add their own price.
      </p>
      <div className="mt-3 grid grid-cols-1 gap-x-[18px] gap-y-3 sm:grid-cols-2">
        {rows.map(({ type, item }) => (
          <label key={item.id} className="flex items-center gap-3">
            <span className="min-w-0 flex-1 text-sm">
              {item.name}
              <span className="block text-caption text-muted">
                {type.name} · {money(item.base_price_minor, item.currency)} on its own
              </span>
            </span>
            <input
              className="field tnum w-[96px]"
              inputMode="decimal"
              placeholder="0.00"
              aria-label={`Extra charge for ${item.name}`}
              value={upcharges[item.id] ?? ""}
              disabled={disabled}
              onChange={(e) => onChange(item.id, e.target.value)}
            />
          </label>
        ))}
      </div>
    </fieldset>
  );
}

/** How the saving reads in the list. */
function savingWords(combo: BuilderCombo): string {
  if (combo.discount_kind === "FIXED_PRICE" && combo.discount_value > 0) {
    return `${money(combo.discount_value)} fixed price`;
  }
  if (combo.discount_kind === "PERCENT" && combo.discount_value > 0) {
    return `${Number((combo.discount_value / 100).toFixed(2))}% off`;
  }
  if (combo.discount_kind === "AMOUNT" && combo.discount_value > 0) {
    return `${money(combo.discount_value)} off`;
  }
  return "no discount";
}
