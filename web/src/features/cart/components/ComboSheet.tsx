import { useMemo, useState } from "react";
import { MenuImage } from "@/components/common/MenuImage";
import { QuantityStepper, Sheet } from "@/components/common/Sheet";
import { money, signedMoney } from "@/utils/format";
import { canAddCalories, comboCalories, kcal, selectionCalories } from "@/features/cart/calories";
import type { Combo, Item, Option } from "@/types";
import {
  defaultSelection,
  toggleOption,
  unitPrice,
  unmetGroups,
  type Selection,
} from "../modifiers";
import { ModifierGroups } from "./ModifierGroups";
import { mealSizeDelta, mealSizeGroupName, mealSizes, sizeGroupOf, withMealSize } from "../mealSize";
import { comboExtrasMinor, comboPreviewMinor, comboUnitMinor } from "../cartSlice";

/** What one slot has been filled with, if anything yet. */
type SlotChoice = { item: Item; selected: Selection };

/**
 * Build one combo: choose an item for every slot, then change it if you like.
 *
 * Every slot is required and takes exactly one item, which is what makes a
 * meal deal a meal deal rather than a discount on whatever you fancy. The
 * button stays disabled until each slot is filled and each chosen item's own
 * required modifiers are answered, and the footer says which is missing --
 * a combo has several ways to be incomplete and "not yet" is no use.
 *
 * The running total is a preview. Every price, rule and discount here is
 * enforced again by the server at checkout, and its answer is what is
 * charged.
 */
export function ComboSheet({
  combo,
  currency,
  onClose,
  onAdd,
}: {
  combo: Combo;
  currency: string;
  onClose: () => void;
  onAdd: (
    chosen: { slotId: string; slotLabel: string; item: Item; selected: Selection }[],
    quantity: number,
    note?: string,
  ) => void;
}) {
  // Keyed by slot id, not by index: two slots can offer the same item, and a
  // combo can be edited to have fewer slots than a stale render expects.
  const [choices, setChoices] = useState<Record<string, SlotChoice>>({});
  // Courses the customer has actively left out. Separate from `choices`
  // because an absent entry there means "not answered yet", and the footer
  // has to tell that apart from "no drink, thanks" -- one blocks the order
  // and the other is the order.
  const [skipped, setSkipped] = useState<Record<string, boolean>>({});
  const [quantity, setQuantity] = useState(1);
  const [note, setNote] = useState("");

  // One size for the meal, where every sizeable course offers the same ones.
  // Starts at the smallest rather than unset: the course size groups are
  // required, and this answers them, so an unset meal size would refuse to
  // add a combo while hiding the control that fixes it.
  const sizes = useMemo(() => mealSizes(combo), [combo]);
  const sizeGroup = useMemo(() => mealSizeGroupName(combo), [combo]);
  const [mealSize, setMealSize] = useState<string | null>(() => mealSizes(combo)[0] ?? null);
  // Hidden per course only while a meal size actually governs it.
  const omitPerCourse = useMemo(
    () => (sizes.length > 0 && sizeGroup ? [sizeGroup] : []),
    [sizes.length, sizeGroup],
  );

  function chooseItem(slotId: string, item: Item) {
    setChoices((prev) => {
      // Re-picking the same item leaves its modifiers alone. Choosing a
      // different one starts that item's own defaults, because the previous
      // item's options mean nothing on it.
      if (prev[slotId]?.item.id === item.id) return prev;
      // A course joining a meal that is already a large arrives large: the
      // size was chosen for the meal, not for whatever was in it at the time.
      return {
        ...prev,
        [slotId]: { item, selected: withMealSize(item, defaultSelection(item), mealSize) },
      };
    });
    setSkipped((prev) => (prev[slotId] ? { ...prev, [slotId]: false } : prev));
  }

  /** Leave a course out: the choice is dropped, not merely unticked. */
  function skipSlot(slotId: string) {
    setChoices((prev) => {
      if (!(slotId in prev)) return prev;
      const next = { ...prev };
      delete next[slotId];
      return next;
    });
    setSkipped((prev) => ({ ...prev, [slotId]: true }));
  }

  /** Size the whole meal: every course already chosen follows. */
  function chooseMealSize(size: string) {
    setMealSize(size);
    setChoices((prev) => {
      const next: Record<string, SlotChoice> = {};
      for (const [slotId, choice] of Object.entries(prev)) {
        next[slotId] = { ...choice, selected: withMealSize(choice.item, choice.selected, size) };
      }
      return next;
    });
  }

  function toggle(slotId: string, group: Item["modifier_groups"][number], option: Option) {
    setChoices((prev) => {
      const current = prev[slotId];
      if (!current) return prev;
      return {
        ...prev,
        [slotId]: { ...current, selected: toggleOption(current.selected, group, option) },
      };
    });
  }

  const filled = combo.slots
    .map((slot) => {
      const choice = choices[slot.id];
      return choice ? { slot, choice } : null;
    })
    .filter((x): x is { slot: Combo["slots"][number]; choice: SlotChoice } => x !== null);

  const emptySlots = combo.slots
    .filter((slot) => !choices[slot.id] && !skipped[slot.id])
    .map((slot) => slot.label);

  const unanswered = filled.flatMap(({ choice }) =>
    unmetGroups(choice.item, choice.selected),
  );

  const itemsSubtotal = filled.reduce(
    (sum, { choice }) => sum + unitPrice(choice.item, choice.selected),
    0,
  );

  // The same arithmetic the server runs, so the preview and the receipt
  // agree: round the percentage once, never take off more than the food,
  // and a set price plus its upcharges and options for a fixed-price deal.
  const fixedPrice = combo.discount_kind === "FIXED_PRICE";
  const extras = comboExtrasMinor(
    combo,
    filled.map(({ slot, choice }) => ({ slotId: slot.id, item: choice.item, selected: choice.selected })),
  );
  const unit = comboPreviewMinor(combo, itemsSubtotal, extras);
  const discount = Math.max(0, itemsSubtotal - unit);

  // At least one course has to be taken. The builder refuses a combo whose
  // every course is optional, so this cannot normally be reached -- but the
  // pricing refuses an empty deal too, and the button must not offer what
  // checkout would reject.
  const ready = filled.length > 0 && emptySlots.length === 0 && unanswered.length === 0;

  const total = comboCalories(filled.map(({ choice }) => choice));
  const helper =
    emptySlots.length > 0
      ? `Still to choose: ${emptySlots.join(", ")}.`
      : unanswered.length > 0
        ? `Choose an option for ${unanswered.join(", ")}.`
        : null;

  return (
    <Sheet
      title={combo.name}
      description={combo.description}
      eyebrow={
        <p className="mt-3 text-[13px] font-[650] text-brick">{savingLabel(combo, currency)}</p>
      }
      // Only the deal's own photograph. One borrowed from an item inside it
      // would fill the header with a single dish while the customer is
      // choosing between several.
      hero={
        combo.image_url ? (
          <MenuImage
            src={combo.image_url}
            className="block h-[175px] w-full bg-paper object-cover sm:h-[200px]"
          />
        ) : undefined
      }
      onClose={onClose}
      footer={
        <>
          {/* The deal as it now stands: only once every slot's choice states
              a figure, because a total missing one dish is a smaller number
              presented as the whole meal. */}
          {total !== null && (
            <div className="mb-[13px] flex items-baseline justify-between text-caption">
              <span className="text-muted">Calories, as chosen</span>
              <span className="tnum font-[650]">{kcal(total)}</span>
            </div>
          )}
          {discount > 0 && (
            <div className="mb-[13px] flex items-baseline justify-between text-caption">
              <span className="text-muted">{money(itemsSubtotal, currency)} separately</span>
              {/* Replaced, never counted up: a preview must not flash an
                  amount it is not. */}
              <span className="tnum font-[650] text-brick">{signedMoney(-discount, currency)}</span>
            </div>
          )}
          {/* Named, not merely counted. A combo has several ways to be
              unfinished and the customer should not have to hunt for which. */}
          {helper && (
            <p id="combo-helper" className="mb-2.5 text-caption text-danger">
              {helper}
            </p>
          )}
          <div className="flex items-center gap-2.5 sm:gap-3">
            <QuantityStepper value={quantity} min={1} max={20} label="items" onChange={setQuantity} />
            <button
              type="button"
              className="btn-primary flex-1 rounded-full px-[11px] sm:px-[19px]"
              disabled={!ready}
              aria-describedby={helper ? "combo-helper" : undefined}
              onClick={() => {
                onAdd(
                  filled.map(({ slot, choice }) => ({
                    slotId: slot.id,
                    slotLabel: slot.label,
                    item: choice.item,
                    selected: choice.selected,
                  })),
                  quantity,
                  note.trim() || undefined,
                );
                onClose();
              }}
            >
              Add {quantity} · <span className="tnum">{money(unit * quantity, currency)}</span>
            </button>
          </div>
        </>
      }
    >
      {/* Asked once, before the courses: the answer applies to all of them,
          and a control that governs several sections belongs above them
          rather than inside one. Segments rather than a stacked list --
          sizes are one short axis the eye compares across, and the price of
          each is the thing being compared. */}
      {sizes.length > 0 && (
        <fieldset>
          <legend className="mb-1 text-sm font-semibold">
            Meal size
            <span className="text-caption font-normal text-muted"> · Sizes the whole meal</span>
          </legend>
          <div className="mt-2 flex flex-wrap gap-2">
            {sizes.map((size) => {
              const checked = mealSize === size;
              // What it adds to the whole meal, stated from the moment the
              // sheet opens so the sizes can be compared on price. The
              // calories stay on the courses and the total, where they move
              // as the meal is actually chosen.
              const delta = mealSizeDelta(combo, choices, size);
              return (
                <label
                  key={size}
                  className={`flex min-w-[calc(50%-4px)] flex-1 cursor-pointer flex-col items-center justify-center gap-0.5 rounded-button border px-3 py-[11px] text-center transition-colors duration-color ease-standard sm:min-w-0 ${
                    checked ? "border-brick bg-brickSoft/60" : "border-hairline"
                  }`}
                >
                  <input
                    type="radio"
                    name={`meal-size:${combo.id}`}
                    checked={checked}
                    onChange={() => chooseMealSize(size)}
                    className="sr-only"
                  />
                  <span
                    className={`text-[13px] font-bold uppercase tracking-[.4px] ${
                      checked ? "text-brick" : "text-ink"
                    }`}
                  >
                    {size}
                  </span>
                  {/* "Included" for the size the meal already is, the same
                      word an option that costs nothing uses elsewhere in
                      this sheet. Never blank: a price missing from one
                      segment reads as a price still loading. */}
                  <span className={`tnum text-caption ${checked ? "text-brick" : "text-muted"}`}>
                    {delta === 0 ? "Included" : signedMoney(delta, currency)}
                  </span>
                </label>
              );
            })}
          </div>
        </fieldset>
      )}

      {combo.slots.map((slot) => {
        const choice = choices[slot.id];
        return (
          <section key={slot.id}>
            <fieldset>
              {/* The type name as a heading, with no article in front: it is
                  the restaurant's own word and may be plural, so "Choose a
                  Tiffins" is a sentence this cannot write. */}
              <legend className="mb-1 text-sm font-semibold">
                {slot.label}
                <span className="text-caption font-normal text-muted"> · Choose one</span>
              </legend>
              {slot.items.map((item) => {
                const checked = choice?.item.id === item.id;
                const upcharge = slot.upcharges?.[item.id] ?? 0;
                // A sizeable course has its size settled by the meal, chosen
                // or not, so its figure is known before it is picked and
                // moves when the meal size does. Without this the row sat at
                // the small portion's floor until it was selected, and
                // switching the meal size appeared to do nothing.
                const sized = mealSize !== null && sizeGroupOf(item) !== undefined;
                const figure = selectionCalories(
                  item,
                  checked
                    ? choice.selected
                    : withMealSize(item, defaultSelection(item), mealSize),
                );
                return (
                  <label
                    key={item.id}
                    className={`mt-2 flex min-h-[52px] items-center gap-3 rounded-button border px-3 py-[11px] transition-colors duration-color ease-standard ${
                      checked ? "border-brick bg-brickSoft/60" : "border-hairline"
                    } ${item.is_available ? "cursor-pointer" : "cursor-not-allowed opacity-[.45]"}`}
                  >
                    <input
                      type="radio"
                      name={`slot:${slot.id}`}
                      checked={checked}
                      disabled={!item.is_available}
                      onChange={() => chooseItem(slot.id, item)}
                      className="h-5 w-5 shrink-0"
                    />
                    <MenuImage
                      src={item.image_url}
                      className="h-10 w-10 shrink-0 rounded-status bg-paper object-cover"
                    />
                    <span className="min-w-0 flex-1 text-sm">
                      {item.name}
                      {!item.is_available && <span className="text-danger"> · Sold out</span>}
                      {/* Chosen, or sized by the meal: either way its size
                          is settled, so it states a figure rather than a
                          floor. Still a floor where something else on it
                          could add more. */}
                      {kcal(item.calories) && (
                        <span className="block text-caption text-muted">
                          {checked || sized
                            ? `${!checked && canAddCalories(item, omitPerCourse) ? "from " : ""}${kcal(figure)}`
                            : `${canAddCalories(item) ? "from " : ""}${kcal(item.calories)}`}
                        </span>
                      )}
                    </span>
                    {/* In a fixed-price deal the dish's own price is not what
                        it costs here; only what it adds to the deal is. */}
                    <span className="tnum ml-auto whitespace-nowrap text-caption">
                      {fixedPrice
                        ? upcharge > 0
                          ? signedMoney(upcharge, currency)
                          : ""
                        : money(item.base_price_minor, currency)}
                    </span>
                  </label>
                );
              })}

              {/* A course the customer may leave out. Last, after the things
                  they might want, because it is the answer to "none of
                  these" rather than one of them. */}
              {slot.is_optional && (() => {
                const left = filled
                  .filter(({ slot: s }) => s.id !== slot.id)
                  .map(({ slot: s, choice: c }) => ({
                    slotId: s.id, item: c.item, selected: c.selected,
                  }));
                // Priced against the course they have, or the cheapest one
                // they could take: a saving has to be measured against
                // something, and the cheapest is the only figure that is
                // never an overstatement.
                const reference =
                  choice?.item ??
                  slot.items
                    .filter((i) => i.is_available)
                    .reduce<Item | undefined>(
                      (cheapest, i) =>
                        !cheapest || i.base_price_minor < cheapest.base_price_minor ? i : cheapest,
                      undefined,
                    );
                const taken = reference
                  ? [...left, {
                      slotId: slot.id,
                      item: reference,
                      selected:
                        choice?.item.id === reference.id
                          ? choice.selected
                          : withMealSize(reference, defaultSelection(reference), mealSize),
                    }]
                  : left;
                // Both sides through one helper, so the drop shown and the
                // total below cannot be arrived at by different arithmetic.
                const drop = Math.max(0, comboUnitMinor(combo, taken) - comboUnitMinor(combo, left));
                const off = skipped[slot.id] === true;
                return (
                  <label
                    className={`mt-2 flex min-h-[52px] items-center gap-3 rounded-button border px-3 py-[11px] transition-colors duration-color ease-standard cursor-pointer ${
                      off ? "border-brick bg-brickSoft/60" : "border-hairline"
                    }`}
                  >
                    <input
                      type="radio"
                      name={`slot:${slot.id}`}
                      checked={off}
                      onChange={() => skipSlot(slot.id)}
                      className="h-5 w-5 shrink-0"
                    />
                    {/* The restaurant's own word, unchanged: it may be plural
                        and it may be a name, and neither survives being
                        lowercased or made singular here. */}
                    <span className="min-w-0 flex-1 text-sm">No {slot.label}</span>
                    <span className="tnum ml-auto whitespace-nowrap text-caption text-brick">
                      {drop > 0 ? signedMoney(-drop, currency) : ""}
                    </span>
                  </label>
                );
              })()}
            </fieldset>

            {/* The chosen item's own modifiers, in place. A combo does not
                relax them: a required choice is still required inside one.
                Counted after the meal size is taken out, so a course whose
                only group was its size does not leave an empty rule behind
                it. */}
            {choice &&
              choice.item.modifier_groups.some((g) => !omitPerCourse.includes(g.name)) && (
              <div className="ml-5 mt-4 flex animate-disclose flex-col gap-4 border-l-2 border-brickSoft pl-4">
                <ModifierGroups
                  item={choice.item}
                  currency={currency}
                  selected={choice.selected}
                  scope={slot.id}
                  omit={omitPerCourse}
                  onToggle={(group, option) => toggle(slot.id, group, option)}
                />
              </div>
            )}
          </section>
        );
      })}

      <label className="block">
        <span className="label">Note for the kitchen</span>
        <textarea
          className="field mt-[7px]"
          value={note}
          maxLength={280}
          placeholder="Allergies, how you'd like it cooked"
          onChange={(e) => setNote(e.target.value)}
        />
      </label>
    </Sheet>
  );
}

/** What the deal saves, in the words the customer reads on the menu too. */
export function savingLabel(combo: Combo, currency: string): string {
  if (combo.discount_kind === "FIXED_PRICE" && combo.discount_value > 0) {
    return money(combo.discount_value, currency);
  }
  if (combo.discount_kind === "PERCENT" && combo.discount_value > 0) {
    // Basis points, so 1250 is 12.5%. Trailing zeros are dropped: "10% off"
    // rather than "10.0% off".
    const percent = combo.discount_value / 100;
    return `${Number(percent.toFixed(2))}% off`;
  }
  if (combo.discount_kind === "AMOUNT" && combo.discount_value > 0) {
    return `${money(combo.discount_value, currency)} off`;
  }
  return "Pick one of each";
}
