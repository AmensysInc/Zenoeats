import type { Combo, Item } from "@/types";
import { isIncluded, toggleOption, type Selection } from "./modifiers";

/**
 * One size for the whole meal, the way a drive-through asks it.
 *
 * A combo is sized once -- "make it a large" -- and the fries and the drink
 * both follow. The alternative, asking for a size inside each course, makes
 * the customer answer the same question twice and lets them answer it two
 * different ways, which is not an order anyone means to place.
 *
 * Nothing new is stored for this. A meal size is the restaurant's own size
 * group, set on every course that has one, so the price, the kitchen ticket
 * and the receipt are built from exactly the modifiers they were before. The
 * server prices and re-checks each of them as it always did; this only
 * decides which option is ticked on each course.
 */

/** The group name a restaurant uses for portion size.
 *
 *  Matched by name because nothing in the model marks a group as being about
 *  size -- a group is a name, a selection rule and some options. That makes
 *  this a convention rather than a guarantee: a restaurant whose group is
 *  called "Portion" gets per-course sizes and no meal size, which is the old
 *  behaviour rather than a broken one. */
const SIZE_GROUP = "size";

type Group = Item["modifier_groups"][number];

/** The size group of one item, if it has one.
 *
 *  Single-choice only: a size is one answer. A multi-choice group that
 *  happened to be called Size would be something else, and sizing a meal by
 *  it would tick options the customer never chose. */
export function sizeGroupOf(item: Item): Group | undefined {
  return item.modifier_groups.find(
    (g) => g.name.trim().toLowerCase() === SIZE_GROUP && g.selection_type === "SINGLE",
  );
}

/** What this combo's meal size is called, spelled as the restaurant spells
 *  it, or null where no course has sizes. Used to hide the per-course group
 *  that the meal size now answers. */
export function mealSizeGroupName(combo: Combo): string | null {
  for (const slot of combo.slots) {
    for (const item of slot.items) {
      const group = sizeGroupOf(item);
      if (group) return group.name;
    }
  }
  return null;
}

/** The sizes offerable for the whole meal: those every sizeable course has.
 *
 *  An intersection rather than a union, because a meal size has to mean the
 *  same thing on every course that takes it. If the drink went to Extra
 *  large and the fries had no such size, "extra large meal" would name
 *  something the kitchen cannot make. A size only one course offers stays
 *  available the old way -- on that course, when the combo has no meal size
 *  at all. Order follows the first sizeable course, which is the
 *  restaurant's own sort order, so Small reads before Large. */
export function mealSizes(combo: Combo): string[] {
  const perCourse: string[][] = [];
  for (const slot of combo.slots) {
    for (const item of slot.items) {
      const group = sizeGroupOf(item);
      if (group) perCourse.push(group.options.filter((o) => o.is_available).map((o) => o.name));
    }
  }
  const [first, ...rest] = perCourse;
  if (!first) return [];
  return first.filter((name) => rest.every((names) => names.includes(name)));
}

/** That size ticked on one item, if the item is sizeable.
 *
 *  Left untouched otherwise: a burger has no size and must not acquire one.
 *  Goes through toggleOption so a single-choice group replaces rather than
 *  accumulates, by the same rule every other modifier obeys. */
export function withMealSize(item: Item, selected: Selection, size: string | null): Selection {
  if (!size) return selected;
  const group = sizeGroupOf(item);
  if (!group) return selected;
  const option = group.options.find((o) => o.name === size && o.is_available);
  if (!option) return selected;
  return toggleOption(selected, group, option);
}

type Option = Group["options"][number];

/** The size option each course would take, as the meal currently stands.
 *
 *  A course not yet chosen is read as the first sizeable thing its slot
 *  offers. Reading only the chosen courses would leave every size blank
 *  until the meal was already built, which is exactly when the customer no
 *  longer needs to compare them -- the figures have to be there while the
 *  choice is being made. They then settle onto the real courses as those are
 *  picked, and a course with no size of its own contributes nothing. */
function sizedCourses(
  combo: Combo,
  chosen: Readonly<Record<string, { item: Item } | undefined>>,
  size: string,
): { item: Item; option: Option }[] {
  const courses: { item: Item; option: Option }[] = [];
  for (const slot of combo.slots) {
    const item = chosen[slot.id]?.item ?? slot.items.find((i) => sizeGroupOf(i));
    if (!item) continue;
    const option = sizeGroupOf(item)?.options.find((o) => o.name === size);
    if (option) courses.push({ item, option });
  }
  return courses;
}

/** What a size adds to the price of the whole meal.
 *
 *  Summed over the courses rather than read off one, because sizing a meal
 *  sizes the fries and the drink and the customer is owed the total of both.
 *  Each course prices its own size: a large fries and a large drink are not
 *  the same upcharge. An included size costs nothing, by the same rule
 *  modifierDelta follows. */
export function mealSizeDelta(
  combo: Combo,
  chosen: Readonly<Record<string, { item: Item } | undefined>>,
  size: string,
): number {
  return sizedCourses(combo, chosen, size).reduce(
    (sum, { item, option }) =>
      sum + (isIncluded(item, option.id) ? 0 : option.price_delta_minor),
    0,
  );
}
