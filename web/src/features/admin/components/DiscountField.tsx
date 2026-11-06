import { useId } from "react";
import type { DiscountKind } from "@/types";

/**
 * How much a promotion takes off: a switch between dollars and percent, and
 * one box.
 *
 * It replaced two boxes side by side ("Percentage off", "Amount off", fill in
 * one), which restaurant teams read as two discounts at once. Dollars come
 * first because "$2 off" is what most of them run.
 *
 * Shared by every promotion form -- plain coupons, coupon groups and the
 * sign-on offer -- on the platform admin and in a restaurant's own Settings,
 * so the question is asked the same way everywhere.
 */
export function DiscountField({
  kind,
  onKindChange,
  value,
  onValueChange,
  currency,
  className = "",
}: {
  kind: DiscountKind;
  onKindChange: (kind: DiscountKind) => void;
  value: string;
  onValueChange: (value: string) => void;
  currency: string;
  className?: string;
}) {
  const labelId = useId();
  const percent = kind === "PERCENT";
  const symbol = currency === "USD" ? "$" : currency;
  const options: [DiscountKind, string][] = [
    ["AMOUNT", `${symbol} Amount off`],
    ["PERCENT", "% Percentage off"],
  ];

  return (
    <div className={className}>
      <span className="label" id={labelId}>Discount</span>
      <div
        role="radiogroup"
        aria-labelledby={labelId}
        className="mt-[7px] inline-flex rounded-status border border-hairline p-[3px]"
      >
        {options.map(([option, label]) => (
          <button
            key={option}
            type="button"
            role="radio"
            aria-checked={kind === option}
            onClick={() => onKindChange(option)}
            className={`rounded-[7px] px-3.5 py-[7px] text-caption font-semibold transition-colors duration-color ${
              kind === option ? "bg-ink text-white" : "text-ink hover:bg-paper"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      <div className="mt-2.5 flex max-w-[260px] items-center gap-2">
        {!percent && <span className="text-[15px] text-muted">{symbol}</span>}
        <input
          className="field tnum"
          value={value}
          onChange={(e) => onValueChange(e.target.value)}
          inputMode="decimal"
          placeholder={percent ? "10" : "2.00"}
          aria-label={percent ? "Percentage off" : `Amount off in ${currency}`}
        />
        {percent && <span className="text-[15px] text-muted">%</span>}
      </div>
      <span className="mt-1 block text-caption text-muted">
        {percent
          ? "For example 10 for 10% off."
          : "For example 2 for $2 off. More than the order total makes it free."}
      </span>
    </div>
  );
}
