/**
 * Reading the numbers an operator types into a promotion form.
 *
 * Shared by the coupon form and the group form because they ask for the same
 * figures, and two copies of "what does an empty field mean" is how one of
 * them ends up sending 0 where the other sends null -- which the API reads
 * as a real limit of zero rather than as no limit at all.
 */

/** A typed amount as minor units, or null for an empty field.
 *
 *  Also does duty for a percentage, where the basis points happen to be the
 *  same conversion: 10 typed as a percent is 1000 bps, exactly as $10.00 is
 *  1000 cents.
 *
 *  Zero reads as null here, and that is right for the fields this serves:
 *  a ceiling of nothing is no ceiling, a minimum of nothing is no minimum.
 *  The flat discount itself is the one field where zero is a real answer,
 *  and it uses centsOrZero below. */
export function cents(typed: string): number | null {
  const value = Number(typed.trim());
  if (!typed.trim() || !Number.isFinite(value) || value <= 0) return null;
  return Math.round(value * 100);
}

/** A typed amount as minor units, where zero is a value rather than an
 *  absence. Only an empty field is null.
 *
 *  For the flat discount, which may be anything from nothing to more than
 *  the order is worth -- the server caps it at what is being charged, so an
 *  amount larger than the bill simply makes that order free. */
export function centsOrZero(typed: string): number | null {
  const value = Number(typed.trim());
  if (!typed.trim() || !Number.isFinite(value) || value < 0) return null;
  return Math.round(value * 100);
}

/** A typed whole number, or null for an empty field. */
export function count(typed: string): number | null {
  const value = Number(typed.trim());
  if (!typed.trim() || !Number.isInteger(value) || value <= 0) return null;
  return value;
}
