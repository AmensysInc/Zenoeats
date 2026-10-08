import type { ReactNode } from "react";
import { useAppDispatch } from "@/app/hooks";
import { QuantityStepper } from "@/components/common/Sheet";
import { MenuImage } from "@/components/common/MenuImage";
import { money } from "@/utils/format";
import type { CartComboLine, CartLine } from "@/types";
import { comboQuantitySet, lineQuantitySet } from "../cartSlice";

/**
 * The cart, itemised and editable.
 *
 * One definition, drawn by the cart page and by checkout's own list: a
 * customer who changes a quantity in one place and finds a different row in
 * the other is looking at two carts, and only one of them is real.
 *
 * Combos come first, each as a single row. A meal deal listed as its three
 * items would read as three orders, and would let a customer take the drink
 * out of a deal that requires one.
 *
 * Each row is a photograph, what was chosen, and the price -- in that order.
 * It used to be three paragraphs of grey text with the choices run together
 * on one line, which read as a receipt for food already eaten rather than
 * food about to be made. The choices are chips now because they are a list
 * of discrete decisions, and a list set as prose is one a customer has to
 * parse rather than scan.
 */
export function CartLines({
  lines,
  combos,
  currency,
}: {
  lines: CartLine[];
  combos: CartComboLine[];
  currency: string;
}) {
  const dispatch = useAppDispatch();

  return (
    <ul className="divide-y divide-hairline">
      {combos.map((line, index) => (
        <Row
          key={line.key}
          index={index}
          name={line.name}
          imageUrl={line.imageUrl}
          badge="Meal deal"
          price={money(line.unitPreviewMinor * line.quantity, currency)}
          quantity={line.quantity}
          note={line.note}
          onQuantity={(quantity) => dispatch(comboQuantitySet({ key: line.key, quantity }))}
        >
          {/* A deal's slots stay a list: each line is one decision, and the
              slot's own label is what makes "Fries: Light" readable. */}
          <ul className="mt-2 space-y-1">
            {line.selections.map((sel) => (
              <li key={sel.slot_id} className="text-[13px] text-[rgb(var(--ze-item-description))]">
                <span className="text-[rgb(var(--ze-item-title))]">{sel.itemName}</span>
                {sel.modifiers.length > 0 && (
                  <span> · {sel.modifiers.map((m) => m.label).join(" · ")}</span>
                )}
              </li>
            ))}
          </ul>
        </Row>
      ))}

      {lines.map((line, index) => (
        <Row
          key={line.key}
          index={combos.length + index}
          name={line.name}
          imageUrl={line.imageUrl}
          price={money(line.unitPreviewMinor * line.quantity, currency)}
          quantity={line.quantity}
          note={line.note}
          onQuantity={(quantity) => dispatch(lineQuantitySet({ key: line.key, quantity }))}
        >
          {line.modifiers.length > 0 && (
            <ul className="mt-2 flex flex-wrap gap-1.5">
              {line.modifiers.map((m) => (
                <li
                  key={m.option_id}
                  className="rounded-full bg-[rgb(var(--ze-shortcut-ground))] px-[9px] py-[3px] text-[11px] text-[rgb(var(--ze-item-description))]"
                >
                  {m.label}
                </li>
              ))}
            </ul>
          )}
        </Row>
      ))}
    </ul>
  );
}

/**
 * One row, whichever kind of thing it is.
 *
 * The photo column collapses when there is no photo rather than holding an
 * empty square: a cart saved before photos were carried has none, and the
 * row has to read as well without one as the menu card does.
 */
function Row({
  index,
  name,
  imageUrl,
  badge,
  price,
  quantity,
  note,
  onQuantity,
  children,
}: {
  index: number;
  name: string;
  imageUrl?: string | null;
  badge?: string;
  price: string;
  quantity: number;
  note?: string;
  onQuantity: (quantity: number) => void;
  children?: ReactNode;
}) {
  return (
    <li
      className="animate-rise flex gap-[15px] py-[18px] first:pt-0 last:pb-0"
      // Rows arrive in order. Capped, as everywhere else: the delay is a
      // flourish on a short cart and a wait on a long one.
      style={{ animationDelay: `${Math.min(index, 6) * 45}ms` }}
    >
      {imageUrl && (
        <span className="relative h-[74px] w-[74px] shrink-0 overflow-hidden rounded-[12px] bg-[rgb(var(--ze-photo-ground))] sm:h-[86px] sm:w-[86px]">
          <MenuImage src={imageUrl} className="absolute inset-0 h-full w-full object-cover" />
        </span>
      )}

      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            {badge && (
              <span className="mb-1 block text-[9px] uppercase tracking-[1.2px] text-[rgb(var(--ze-menu-muted))]">
                {badge}
              </span>
            )}
            <p className="text-[16px] font-bold leading-[1.25] tracking-[-.2px] text-[rgb(var(--ze-item-title))] [overflow-wrap:anywhere] sm:text-[17px]">
              {name}
            </p>
          </div>
          <span className="tnum shrink-0 whitespace-nowrap text-[15px] font-semibold text-[rgb(var(--ze-item-title))]">
            {price}
          </span>
        </div>

        {children}

        {note && (
          <p className="mt-2 text-[13px] italic text-[rgb(var(--ze-item-description))]">{note}</p>
        )}

        {/* Minus at one removes the line: there is no separate remove
            button, and no upper limit here. */}
        <div className="mt-3">
          <QuantityStepper value={quantity} min={0} label={name} onChange={onQuantity} />
        </div>
      </div>
    </li>
  );
}
