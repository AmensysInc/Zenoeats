import type { Contact } from "@/types";

/**
 * The rules checkout holds a customer's details to, mirrored from the API's
 * ContactIn so a mistake is named beside its field instead of after a round
 * trip. The server checks again and is the one that decides.
 */

export type ContactField = keyof Contact;
export type ContactErrors = Partial<Record<ContactField, string>>;

/** Runs of whitespace become one space, as the server stores them. */
export function collapse(value: string): string {
  return value.split(/\s+/).filter(Boolean).join(" ");
}

export function contactErrors(contact: Contact, addressRequired = true): ContactErrors {
  const errors: ContactErrors = {};

  if (!collapse(contact.full_name)) errors.full_name = "Enter your name.";

  const phone = collapse(contact.phone);
  const digits = phone.replace(/\D/g, "").length;
  if (!phone) {
    errors.phone = "Enter your phone number.";
  } else if (/[^0-9+\-(). ]/.test(phone) || phone.slice(1).includes("+")) {
    errors.phone = "Enter a phone number using digits only.";
  } else if (digits < 7 || digits > 15) {
    errors.phone = "Enter a phone number a driver could call.";
  }

  if (addressRequired && collapse(contact.address).length < 5) errors.address = "Enter your address.";

  return errors;
}

/** Format North American numbers while preserving valid international input. */
export function formatPhone(value: string): string {
  const raw = value.trim();
  const digits = raw.replace(/\D/g, "").slice(0, 15);
  if (!digits) return "";

  if (raw.startsWith("+") && !digits.startsWith("1")) return `+${digits}`;
  if (digits.length > 11 || (digits.length > 10 && !digits.startsWith("1"))) return `+${digits}`;
  const northAmerican = digits.length > 10 && digits.startsWith("1")
    ? digits.slice(1, 11)
    : digits.slice(0, 10);
  // +1 once the number is whole, not while it is being typed. A partial
  // string is as likely to be the start of an international number, and
  // announcing a country after four digits guesses out loud -- "+1 (447) 9"
  // for somebody halfway through a London number.
  const prefix = northAmerican.length === 10 ? "+1 " : "";
  if (northAmerican.length <= 3) return prefix + northAmerican;
  if (northAmerican.length <= 6)
    return `${prefix}(${northAmerican.slice(0, 3)}) ${northAmerican.slice(3)}`;
  return `${prefix}(${northAmerican.slice(0, 3)}) ${northAmerican.slice(3, 6)}-${northAmerican.slice(6)}`;
}

export function normalizeEmail(value: string): string {
  return value.trim().toLowerCase();
}

/** The order fields appear in, so the first mistake is the one focused. */
export const CONTACT_FIELDS: ContactField[] = ["full_name", "phone", "address"];

export function contactInputId(field: ContactField): string {
  return `contact-${field}`;
}
