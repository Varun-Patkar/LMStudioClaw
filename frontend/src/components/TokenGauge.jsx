// Circular token gauge with a hover breakdown tooltip. `budget` is the latest
// `budget` event from the engine: { used, total, threshold, limit, breakdown }.
export default function TokenGauge({ budget }) {
  const R = 13, C = 2 * Math.PI * R;
  const total = (budget && budget.total) || 0;
  const used = (budget && budget.used) || 0;
  const frac = total ? Math.min(1, used / total) : 0;
  const pct = Math.round(frac * 100);
  const warn = budget && budget.threshold && frac >= budget.threshold;
  const b = (budget && budget.breakdown) || {};
  const rows = ["system", "user", "assistant", "tool"].filter((k) => b[k]);

  return (
    <div className="gauge-wrap">
      <svg className="token-gauge" viewBox="0 0 32 32" width="32" height="32">
        <circle className="gauge-track" cx="16" cy="16" r={R} />
        <circle className={"gauge-fill" + (warn ? " warn" : "")} cx="16" cy="16" r={R}
          strokeDasharray={C.toFixed(1)} strokeDashoffset={(C * (1 - frac)).toFixed(1)}
          transform="rotate(-90 16 16)" />
        <text className="gauge-text" x="16" y="16">{pct}%</text>
      </svg>
      <div className="gauge-tip">
        <div className="grow ghead">
          <span>{used.toLocaleString()} / {total.toLocaleString()}</span><span>{pct}%</span>
        </div>
        {rows.map((k) => (
          <div className="grow" key={k}><span>{k}</span><span>{b[k].toLocaleString()}</span></div>
        ))}
        {budget && budget.limit ? (
          <div className="grow"><span>compaction at</span><span>{budget.limit.toLocaleString()}</span></div>
        ) : null}
      </div>
    </div>
  );
}
