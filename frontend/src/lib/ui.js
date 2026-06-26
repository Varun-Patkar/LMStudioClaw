/** Small shared UI helpers for the chat/new-task surfaces. */

/** Auto-grow a textarea to fit its content, capped so it never runs away. */
export function autoGrow(el, max = 220) {
  if (!el) return;
  el.style.height = "auto";
  el.style.height = Math.min(el.scrollHeight, max) + "px";
}

/** Starter prompts shown as chips on the new-task / fresh-session hero. */
export const SUGGESTIONS = [
  "Summarize the files in my workspace",
  "Review my code and list any bugs",
  "Draft a plan for a new feature",
];
