/**
 * What a dish contains, as the restaurant states it.
 *
 * Optional everywhere: a restaurant that has not worked out a figure states
 * none, and a menu must then say nothing rather than imply zero.
 *
 * An item's figure is what it contains as it comes. A choice states what it
 * changes that by -- large fries are the same dish with more calories, not a
 * second dish -- so the arithmetic mirrors the price exactly, including the
 * part where what the item already comes with adds nothing.
 */

import type { Item, Option } from "@/types";
import type { Selection } from "./modifiers";

/** "540 kcal", or null where there is no figure to show. */
export function kcal(value: number | null | undefined): string | null {
  return typeof value === "number" ? `${value.toLocaleString("en-US")} kcal` : null;
}

/** What the chosen options add. An option the item comes with adds nothing:
 *  it is already part of the figure the item states. An option stating no
 *  change adds nothing either -- "no change stated" is counted as none. */
export function modifierCalories(item: Item, selected: Selection): number {
  const included = new Set(item.included_option_ids);
  return [...selected.values()]
    .flat()
    .reduce((sum, o: Option) => sum + (included.has(o.id) ? 0 : o.calories_delta ?? 0), 0);
}

/** This item as configured, or null where the item states no figure at all.
 *  Floored at zero, as the price is: a choice may take calories off, a dish
 *  may not contain fewer than none. */
export function selectionCalories(item: Item, selected: Selection): number | null {
  if (typeof item.calories !== "number") return null;
  return Math.max(0, item.calories + modifierCalories(item, selected));
}

/**
 * Whether choosing could raise this item's figure, so the menu card says
 * "from 310 kcal" rather than stating 310 for a large portion too.
 *
 * `settled` names groups whose answer is already known -- a combo sized as a
 * whole has settled each course's size -- so they no longer make the figure
 * a floor. Without this a sized course would read "from 680 kcal" when 680
 * is exactly what it is.
 */
export function canAddCalories(item: Item, settled: readonly string[] = []): boolean {
  const included = new Set(item.included_option_ids);
  return item.modifier_groups.some(
    (group) =>
      !settled.includes(group.name) &&
      group.options.some((o) => !included.has(o.id) && (o.calories_delta ?? 0) > 0),
  );
}

/**
 * A combo's total, added up from the items chosen for it so far, each with
 * its own choices.
 *
 * Null unless every chosen item states a figure. A total that quietly left
 * out the one dish with no figure would be a smaller number than the meal,
 * presented as if it were the meal -- worse than showing nothing, because a
 * customer counting calories would believe it.
 */
export function comboCalories(chosen: { item: Item; selected: Selection }[]): number | null {
  if (!chosen.length) return null;
  let total = 0;
  for (const { item, selected } of chosen) {
    const figure = selectionCalories(item, selected);
    if (figure === null) return null;
    total += figure;
  }
  return total;
}
