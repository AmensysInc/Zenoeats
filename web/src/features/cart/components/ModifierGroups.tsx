import { MenuImage } from "@/components/common/MenuImage";
import { signedMoney } from "@/utils/format";
import type { Item, Option } from "@/types";
import { isIncluded, type Selection } from "../modifiers";

/**
 * The modifier groups of one item, as a list of choices.
 *
 * Shared by the item sheet and the combo sheet so a group looks and behaves
 * the same whether the item is ordered alone or as part of a meal deal. A
 * combo does not relax the rules, and it should not read as though it might.
 *
 * `scope` disambiguates the radio inputs. Two slots of one combo can offer
 * the same item, and radios sharing a name would act as one group across
 * both, so choosing light ice for the drink would clear it for the other.
 *
 * `omit` names groups something else has already answered -- a combo sized
 * as a whole answers each course's size group -- so the choice is not put
 * twice, in two places, where the two could disagree. The group is still
 * selected and still priced; only this list leaves it out.
 */
export function ModifierGroups({
  item,
  currency,
  selected,
  scope,
  omit,
  onToggle,
}: {
  item: Item;
  currency: string;
  selected: Selection;
  scope: string;
  omit?: readonly string[];
  onToggle: (group: Item["modifier_groups"][number], option: Option) => void;
}) {
  const hidden = new Set(omit ?? []);
  return (
    <>
      {item.modifier_groups.filter((group) => !hidden.has(group.name)).map((group) => {
        const chosen = selected.get(group.name) ?? [];
        const atMax =
          group.selection_type === "MULTI" && group.max_select > 0 && chosen.length >= group.max_select;
        const helpId = `${scope}:${group.id}:help`;
        return (
          <fieldset key={group.id} aria-describedby={atMax ? helpId : undefined}>
            <legend className="mb-1 text-sm font-semibold">
              {group.name}
              <span className="text-caption font-normal text-muted">
                {" · "}
                {group.is_required
                  ? "Required"
                  : group.selection_type === "MULTI"
                    ? `Pick up to ${group.max_select}`
                    : "Optional"}
              </span>
            </legend>

            {group.options.map((option) => {
              const isChosen = chosen.some((o) => o.id === option.id);
              const included = isIncluded(item, option.id);
              return (
                <label
                  key={option.id}
                  className={`mt-2 flex min-h-[52px] items-center gap-3 rounded-button border px-3 py-[11px] transition-colors duration-color ease-standard ${
                    isChosen ? "border-brick bg-brickSoft/60" : "border-hairline"
                  } ${option.is_available ? "cursor-pointer" : "cursor-not-allowed opacity-[.45]"}`}
                >
                  <input
                    type={group.selection_type === "SINGLE" ? "radio" : "checkbox"}
                    name={`${scope}:${group.id}`}
                    checked={isChosen}
                    disabled={!option.is_available}
                    onChange={() => onToggle(group, option)}
                    className="h-5 w-5 shrink-0"
                  />
                  <MenuImage
                    src={option.image_url}
                    className="h-10 w-10 shrink-0 rounded-status bg-paper object-cover"
                  />
                  <span className="min-w-0 flex-1 text-sm">
                    {option.name}
                    {!option.is_available && <span className="text-danger"> · Sold out</span>}
                  </span>
                  {/* "Included" rather than a price, because on this item
                      there is no price. The same option on an item that
                      does not come with it still shows what it costs. */}
                  <span className={`tnum ml-auto whitespace-nowrap text-caption ${included ? "text-brick" : ""}`}>
                    {included ? "Included" : signedMoney(option.price_delta_minor, currency)}
                  </span>
                </label>
              );
            })}
            {/* A full group ignores another tick rather than dropping an
                earlier choice, so say why the tick did nothing. */}
            {atMax && (
              <p id={helpId} className="field-hint" aria-live="polite">
                Maximum selected. Untick an option to choose another.
              </p>
            )}
          </fieldset>
        );
      })}
    </>
  );
}
