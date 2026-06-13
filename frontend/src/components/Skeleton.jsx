// A shimmering placeholder shown while a view's data loads.
export default function Skeleton({ cards = 3 }) {
  return (
    <div className="skeleton-wrap">
      {Array.from({ length: cards }).map((_, i) => (
        <div className="card skeleton-card" key={i}>
          <div className="skeleton-bar" />
          <div className="skeleton-line" />
          <div className="skeleton-line short" />
        </div>
      ))}
    </div>
  );
}
