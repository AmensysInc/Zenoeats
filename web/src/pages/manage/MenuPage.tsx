import { useState } from "react";
import { useAppSelector } from "@/app/hooks";
import { ErrorNote, Loading } from "@/components/common/Feedback";
import { PageTitle } from "@/components/layout/Shell";
import { selectSession } from "@/features/session/sessionSlice";
import { canEditMenu } from "@/features/restaurant/nav";
import { ManageShell } from "@/features/restaurant/components/ManageShell";
import { ItemLibrary } from "@/features/restaurant/components/ItemLibrary";
import { MenuPreview } from "@/features/restaurant/components/MenuPreview";
import { MealPeriods } from "@/features/restaurant/components/MealPeriods";
import { ComboBuilder } from "@/features/restaurant/components/ComboBuilder";
import { ModifierLibrary } from "@/features/restaurant/components/ModifierLibrary";
import {
  useCombosQuery,
  useItemTypesQuery,
  useItemsQuery,
  useMenuQuery,
  useModifierGroupsQuery,
} from "@/features/restaurant/restaurantApi";
import { errorMessage } from "@/services/apiClient";

const TABS = [
  { id: "preview", label: "Preview" },
  { id: "items", label: "Items" },
  { id: "periods", label: "Meal periods" },
  { id: "combos", label: "Combos" },
  { id: "groups", label: "Categories" },
] as const;

type Tab = (typeof TABS)[number]["id"];

/**
 * Menu builder.
 *
 * Preview first, then four tabs in the order a menu is built: what do we
 * sell, when do we serve it, what do we bundle, and what can be changed about
 * any of it. Combos come after meal periods because a combo can only offer
 * items a period already serves, so there is nothing to build until that is
 * done.
 *
 * Preview leads and is where the page opens, because it is the only tab that
 * answers what the menu now says rather than asking another question about
 * it. It edits nothing, so landing there costs a click at worst.
 *
 * Only routing between the tabs and surfacing errors lives here; the tabs
 * themselves own their forms and mutations. RTK Query tags handle the
 * refresh, so the children need no manual onChange threading. Every tab's
 * errors land in the one banner above the tabs, and switching tab clears it.
 *
 * IT support gets the preview and nothing else. The API lets the role read
 * the menu -- "why is this item not on the storefront" is a support question
 * and the answer is in here -- and refuses every edit, so the four editing
 * tabs would be four screens of controls that 403. Preview is already
 * strictly read-only, which is why there is a read-only menu to offer at all.
 */
export function MenuPage() {
  const [tab, setTab] = useState<Tab>("preview");
  const [error, setError] = useState<string | null>(null);
  const { roleCode } = useAppSelector(selectSession);
  const editable = canEditMenu(roleCode);
  // Preview leads, so a read-only visitor keeps the tab they landed on and
  // the nav below simply has nothing else in it.
  const tabs = editable ? TABS : TABS.filter((t) => t.id === "preview");

  const menu = useMenuQuery();
  const items = useItemsQuery();
  const itemTypes = useItemTypesQuery();
  const combos = useCombosQuery();
  const groups = useModifierGroupsQuery();

  const loadError =
    (menu.error ? errorMessage(menu.error) : null) ??
    (items.error ? errorMessage(items.error) : null) ??
    (itemTypes.error ? errorMessage(itemTypes.error) : null) ??
    (combos.error ? errorMessage(combos.error) : null) ??
    (groups.error ? errorMessage(groups.error) : null);

  // Until the first answers land, every tab would describe an empty menu.
  const loading =
    menu.isLoading || items.isLoading || itemTypes.isLoading || combos.isLoading || groups.isLoading;

  const meals = menu.data?.meals ?? [];
  const types = itemTypes.data ?? [];

  return (
    <ManageShell>
      <PageTitle
        title="Your menu"
        subtitle={editable ? "One library. Every meal period." : "The menu as it currently reads."}
      />

      <ErrorNote message={error ?? loadError} />

      {/* One tab is not a choice, so a read-only visitor gets the menu
          straight away rather than a row with a single thing in it. */}
      {tabs.length > 1 && (
        <nav
          aria-label="Menu builder"
          className="mb-6 flex gap-6 overflow-x-auto border-b border-hairline sm:mb-[30px] sm:gap-7"
        >
          {tabs.map((t) => (
            <button
              key={t.id}
              type="button"
              aria-current={tab === t.id ? "page" : undefined}
              onClick={() => {
                setError(null);
                setTab(t.id);
              }}
              className={`-mb-px whitespace-nowrap border-b-2 py-3.5 text-[13px] transition-colors duration-tab ease-standard ${
                tab === t.id
                  ? "border-brick font-semibold text-ink"
                  : "border-transparent text-muted hover:text-ink"
              }`}
            >
              {t.label}
            </button>
          ))}
        </nav>
      )}

      {loading ? (
        <Loading />
      ) : (
        <div key={tab} className="animate-fade">
          {tab === "preview" && <MenuPreview meals={meals} types={types} />}
          {tab === "items" && (
            <ItemLibrary
              items={items.data ?? []}
              meals={meals}
              types={types}
              groups={groups.data ?? []}
              onError={setError}
            />
          )}
          {tab === "periods" && (
            <MealPeriods meals={meals} items={items.data ?? []} types={types} onError={setError} />
          )}
          {tab === "combos" && (
            <ComboBuilder
              combos={combos.data ?? []}
              meals={meals}
              items={items.data ?? []}
              types={types}
              onError={setError}
            />
          )}
          {tab === "groups" && (
            <ModifierLibrary groups={groups.data ?? []} types={types} onError={setError} />
          )}
        </div>
      )}
    </ManageShell>
  );
}
