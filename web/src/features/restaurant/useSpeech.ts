import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Speaking to the assistant, and being spoken back to.
 *
 * Both halves are the browser's own: SpeechRecognition for listening,
 * speechSynthesis for reading a reply aloud. No key, no vendor, no audio
 * leaving the page to anywhere we chose -- which is the same reason the
 * assistant reads sentences on our own server rather than at a model.
 *
 * WHY THIS IS WORTH HAVING AT ALL: the person who knows the fryer is empty
 * has their hands full. "86 the fries" said out loud, with the change still
 * confirmed by a tap, is the difference between updating the menu now and
 * updating it after the lunch rush.
 *
 * SUPPORT IS NOT UNIVERSAL and the caller must cope. Chrome and Safari
 * implement it under different names and Firefox does not implement
 * recognition at all, so `supported` is false there and the screen simply
 * has no microphone button -- never a button that does nothing.
 *
 * IT NEEDS HTTPS, which every storefront and portal already is, and it
 * needs permission, which the browser asks for once. A refusal is reported
 * rather than retried: a page that re-prompted would be a page nobody could
 * get rid of.
 */

// Chrome ships it prefixed and has for years; Safari 14.1+ ships it plain.
type Recogniser = {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  start: () => void;
  stop: () => void;
  abort: () => void;
  onresult: ((event: SpeechEvent) => void) | null;
  onerror: ((event: { error: string }) => void) | null;
  onend: (() => void) | null;
};
type SpeechEvent = {
  resultIndex: number;
  results: ArrayLike<ArrayLike<{ transcript: string }> & { isFinal: boolean }>;
};

function recogniser(): Recogniser | null {
  const holder = window as unknown as {
    SpeechRecognition?: new () => Recogniser;
    webkitSpeechRecognition?: new () => Recogniser;
  };
  const Ctor = holder.SpeechRecognition ?? holder.webkitSpeechRecognition;
  return Ctor ? new Ctor() : null;
}

export type Speech = {
  /** False where the browser has no recognition at all. Hide the button. */
  supported: boolean;
  listening: boolean;
  /** What has been heard so far, including the part still being revised. */
  heard: string;
  /** A sentence to show the admin, or null. Permission refusals live here. */
  problem: string | null;
  start: () => void;
  stop: () => void;
};

/**
 * Listen until the speaker stops, then hand over what was said.
 *
 * `onSaid` fires once, with the final transcript. Interim results are
 * exposed separately so the box can fill in as somebody talks -- watching
 * the words appear is most of what makes dictation feel reliable.
 */
export function useSpeech(onSaid: (text: string) => void): Speech {
  const [supported] = useState(() => typeof window !== "undefined" && !!recogniser());
  const [listening, setListening] = useState(false);
  const [heard, setHeard] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const active = useRef<Recogniser | null>(null);
  // The latest callback, so a recogniser started earlier does not call a
  // stale closure holding yesterday's state.
  const said = useRef(onSaid);
  said.current = onSaid;

  const stop = useCallback(() => {
    active.current?.stop();
    active.current = null;
    setListening(false);
  }, []);

  const start = useCallback(() => {
    if (!supported || active.current) return;
    const engine = recogniser();
    if (!engine) return;

    setProblem(null);
    setHeard("");
    engine.lang = navigator.language || "en-US";
    // One utterance at a time: this is a command, not a dictation session,
    // and a recogniser left running is a microphone left on.
    engine.continuous = false;
    engine.interimResults = true;

    engine.onresult = (event) => {
      let interim = "";
      let settled = "";
      for (let i = event.resultIndex; i < event.results.length; i += 1) {
        const result = event.results[i];
        if (!result) continue;
        const text = result[0]?.transcript ?? "";
        if (result.isFinal) settled += text;
        else interim += text;
      }
      setHeard((settled || interim).trim());
      if (settled.trim()) {
        said.current(settled.trim());
        stop();
      }
    };

    engine.onerror = (event) => {
      // "aborted" is what stopping deliberately looks like; not a problem.
      if (event.error === "aborted") return;
      setProblem(
        event.error === "not-allowed" || event.error === "service-not-allowed"
          ? "I need permission to use the microphone. Allow it in your browser, then try again."
          : event.error === "no-speech"
            ? "I did not hear anything."
            : "The microphone did not work. You can type instead.",
      );
      setListening(false);
      active.current = null;
    };

    engine.onend = () => {
      setListening(false);
      active.current = null;
    };

    active.current = engine;
    setListening(true);
    engine.start();
  }, [supported, stop]);

  // A page left while listening must not leave the microphone open.
  useEffect(() => () => active.current?.abort(), []);

  return { supported, listening, heard, problem, start, stop };
}

/**
 * Read a reply aloud, if the admin asked for that.
 *
 * Off unless switched on, and remembered per browser: a dashboard that
 * started talking by itself would be switched off within the minute and
 * never switched back on.
 */
export function speak(text: string): void {
  if (typeof window === "undefined" || !("speechSynthesis" in window)) return;
  try {
    // Whatever was queued is stale the moment there is a newer answer.
    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = navigator.language || "en-US";
    window.speechSynthesis.speak(utterance);
  } catch {
    /* A browser that has the object but refuses to use it. Nothing to say. */
  }
}

export function speechOutSupported(): boolean {
  return typeof window !== "undefined" && "speechSynthesis" in window;
}
