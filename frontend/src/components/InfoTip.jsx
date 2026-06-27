import { Info } from "lucide-react";

/**
 * A small inline help affordance: an info icon that reveals an explanatory tooltip on
 * hover/focus. Used throughout the app so a newcomer can understand what every control
 * does without prior knowledge.
 *
 * Props:
 *  - text: the explanation to show in the tooltip (string).
 *  - side: "top" | "right" | "left" | "bottom" — where the bubble appears (default "top").
 */
export default function InfoTip({ text, side = "top" }) {
  if (!text) return null;
  return (
    <span className={"infotip " + side} tabIndex={0} role="note" aria-label={text}>
      <Info size={13} />
      <span className="infotip-bubble">{text}</span>
    </span>
  );
}
