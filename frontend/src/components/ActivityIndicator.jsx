/**
 * ActivityIndicator — a single, plain-English line telling the user exactly what
 * the agent is doing right now (so a newcomer is never left guessing): loading a
 * model, thinking, running a specific tool, queued, stopping, or idle/ready.
 *
 * Props:
 *  - variant: "idle" | "busy" | "loading" | "stopping"  (drives colour + spinner)
 *  - text:    the human-readable status line
 */
export default function ActivityIndicator({ variant = "idle", text }) {
  const spinning = variant === "busy" || variant === "loading" || variant === "stopping";
  return (
    <div className={"activity " + variant}>
      {spinning ? <span className="act-spin" /> : <span className="act-dot" />}
      <span className="act-text">{text}</span>
    </div>
  );
}
