import type { PlacesLibrary } from "@/services/googleMaps";

/**
 * Address suggestions under the checkout's delivery address, from Places (New).
 *
 * Google's ready-made suggestion box belongs to the legacy Places API, which a
 * Google Cloud project created since October 2026 can no longer switch on. So
 * the list is ours: Places answers the question, and this draws the answer
 * under the field. It keeps the class names the old box used (.pac-container,
 * .pac-item) so it looks the same, and it is a proper combobox -- arrow keys,
 * Enter and Escape, and what a screen reader needs to follow along.
 *
 * Nothing here is required. A customer who ignores the list, or whose browser
 * cannot reach Google, types the address and the server checks it as before.
 */

/** Letters before asking, and the pause after the last one. */
const MIN_CHARS = 3;
const DEBOUNCE_MS = 250;
const MAX_SUGGESTIONS = 5;

/** The kinds of result that are somewhere a driver can bring food. */
const ADDRESS_TYPES = ["street_address", "premise", "subpremise", "route"];

type Suggestion = {
  text: string;
  /** The full formatted address, which a suggestion's own text lacks (no ZIP). */
  resolve: () => Promise<string | null>;
};

let listCount = 0;

/**
 * Attach suggestions to `input`. `onPick` receives the chosen address.
 * Returns a function that removes everything this added.
 */
export function attachAddressSuggestions(
  input: HTMLInputElement,
  places: PlacesLibrary,
  onPick: (address: string) => void,
): () => void {
  const listId = `address-suggestions-${++listCount}`;
  const list = document.createElement("ul");
  list.id = listId;
  list.className = "pac-container";
  list.setAttribute("role", "listbox");
  list.hidden = true;
  list.style.position = "absolute";
  document.body.appendChild(list);

  input.setAttribute("role", "combobox");
  input.setAttribute("aria-autocomplete", "list");
  input.setAttribute("aria-controls", listId);
  input.setAttribute("aria-expanded", "false");

  // One session per address being typed: Google bills the typing and the
  // final details lookup together when they share a token.
  let session = new places.AutocompleteSessionToken();
  let suggestions: Suggestion[] = [];
  let active = -1;
  let timer: ReturnType<typeof setTimeout> | undefined;
  // Answers can arrive out of order; only the newest question's counts.
  let asked = 0;

  function place() {
    const box = input.getBoundingClientRect();
    list.style.left = `${box.left + window.scrollX}px`;
    list.style.top = `${box.bottom + window.scrollY}px`;
    list.style.width = `${box.width}px`;
  }

  function close() {
    list.hidden = true;
    list.replaceChildren();
    suggestions = [];
    active = -1;
    input.setAttribute("aria-expanded", "false");
    input.removeAttribute("aria-activedescendant");
  }

  function highlight(index: number) {
    active = index;
    list.querySelectorAll<HTMLElement>(".pac-item").forEach((item, i) => {
      const on = i === index;
      item.classList.toggle("pac-item-selected", on);
      item.setAttribute("aria-selected", on ? "true" : "false");
    });
    if (index >= 0) input.setAttribute("aria-activedescendant", `${listId}-${index}`);
    else input.removeAttribute("aria-activedescendant");
  }

  async function choose(index: number) {
    const picked = suggestions[index];
    if (!picked) return;
    close();
    const full = await picked.resolve().catch(() => null);
    session = new places.AutocompleteSessionToken();
    onPick((full ?? picked.text).trim());
  }

  function show(found: Suggestion[]) {
    suggestions = found;
    active = -1;
    list.replaceChildren(
      ...found.map((suggestion, index) => {
        const item = document.createElement("li");
        item.id = `${listId}-${index}`;
        item.className = "pac-item";
        item.setAttribute("role", "option");
        item.setAttribute("aria-selected", "false");
        const query = document.createElement("span");
        query.className = "pac-item-query";
        query.textContent = suggestion.text;
        item.append(query);
        // mousedown, not click: a click would blur the field first and close
        // the list before the choice landed.
        item.addEventListener("mousedown", (event) => {
          event.preventDefault();
          void choose(index);
        });
        item.addEventListener("mouseenter", () => highlight(index));
        return item;
      }),
    );
    if (found.length === 0) return close();
    place();
    list.hidden = false;
    input.setAttribute("aria-expanded", "true");
  }

  async function ask(text: string) {
    const question = ++asked;
    try {
      const { suggestions: found } = await places.AutocompleteSuggestion.fetchAutocompleteSuggestions({
        input: text,
        sessionToken: session,
        includedPrimaryTypes: ADDRESS_TYPES,
      });
      if (question !== asked || document.activeElement !== input) return;
      show(
        found
          .map((s) => s.placePrediction)
          .filter((p): p is NonNullable<typeof p> => !!p)
          .slice(0, MAX_SUGGESTIONS)
          .map((prediction) => ({
            text: prediction.text.toString(),
            resolve: async () => {
              const result = prediction.toPlace();
              await result.fetchFields({ fields: ["formattedAddress"] });
              return result.formattedAddress ?? null;
            },
          })),
      );
    } catch {
      // Google unreachable, or the key refused: typing still works.
      if (question === asked) close();
    }
  }

  function onInput() {
    clearTimeout(timer);
    const text = input.value.trim();
    if (text.length < MIN_CHARS) {
      asked++;
      return close();
    }
    timer = setTimeout(() => void ask(text), DEBOUNCE_MS);
  }

  function onKeyDown(event: KeyboardEvent) {
    if (list.hidden || suggestions.length === 0) return;
    if (event.key === "ArrowDown") {
      event.preventDefault();
      highlight((active + 1) % suggestions.length);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      highlight(active <= 0 ? suggestions.length - 1 : active - 1);
    } else if (event.key === "Enter" && active >= 0) {
      // Choosing, not submitting the checkout form.
      event.preventDefault();
      void choose(active);
    } else if (event.key === "Escape") {
      close();
    }
  }

  function onBlur() {
    clearTimeout(timer);
    asked++;
    close();
  }

  function onMove() {
    if (!list.hidden) place();
  }

  input.addEventListener("input", onInput);
  input.addEventListener("keydown", onKeyDown);
  input.addEventListener("blur", onBlur);
  window.addEventListener("resize", onMove);
  window.addEventListener("scroll", onMove, true);

  return () => {
    clearTimeout(timer);
    asked++;
    input.removeEventListener("input", onInput);
    input.removeEventListener("keydown", onKeyDown);
    input.removeEventListener("blur", onBlur);
    window.removeEventListener("resize", onMove);
    window.removeEventListener("scroll", onMove, true);
    for (const name of ["role", "aria-autocomplete", "aria-controls", "aria-expanded", "aria-activedescendant"]) {
      input.removeAttribute(name);
    }
    list.remove();
  };
}
