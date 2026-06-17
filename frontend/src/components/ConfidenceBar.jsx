export default function ConfidenceBar({ value }) {
  const level = value < 70 ? "low" : value < 85 ? "medium" : "high";

  return (
    <div className={`confidence confidence--${level}`}>
      <div className="confidence-track" aria-hidden="true">
        <div className="confidence-fill" style={{ width: `${value}%` }} />
      </div>
      <span>{value}%</span>
    </div>
  );
}
