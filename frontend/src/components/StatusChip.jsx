const LABELS = {
  approved: "Approved",
  needs_review: "Needs review",
  ready: "Ready",
  reviewed: "Reviewed",
  unapproved: "Unapproved",
  ignored: "Ignored",
};

export default function StatusChip({ status }) {
  return (
    <span className={`status-chip status-chip--${status}`}>
      {LABELS[status] ?? status}
    </span>
  );
}
