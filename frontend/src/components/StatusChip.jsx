const LABELS = {
  needs_review: "Needs review",
  ready: "Ready",
  low_confidence: "Low confidence",
  ignored: "Ignored",
};

export default function StatusChip({ status }) {
  return (
    <span className={`status-chip status-chip--${status}`}>
      {LABELS[status] ?? status}
    </span>
  );
}
