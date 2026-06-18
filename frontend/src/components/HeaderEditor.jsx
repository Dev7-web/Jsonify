import StatusChip from "./StatusChip";

function getHeaderStatus(header) {
  if (isApprovedHeader(header)) {
    return "approved";
  }

  if (isUnapprovedHeader(header)) {
    return "unapproved";
  }

  return "needs_review";
}

export default function HeaderEditor({
  header,
  onChange,
  onRemove,
  onAdd,
  onApprovalChange,
}) {
  if (!header) {
    return (
      <aside className="panel editor-panel">
        <div className="panel-heading">
          <p className="eyebrow">Header editor</p>
          <h2>No header selected</h2>
        </div>
        <button className="button button-secondary" onClick={onAdd} type="button">
          Add header
        </button>
      </aside>
    );
  }

  const approved = isApprovedHeader(header);
  const unapproved = isUnapprovedHeader(header);
  const approvalLabel = approved
    ? "Header approved"
    : unapproved
      ? "Header unapproved"
      : "Header needs approval";

  return (
    <aside className="panel editor-panel">
      <div className="panel-heading panel-heading-row">
        <div>
          <p className="eyebrow">Header editor</p>
          <h2>{header.name}</h2>
        </div>
        <StatusChip status={getHeaderStatus(header)} />
      </div>

      <div className="header-approval-panel">
        <div>
          <strong>{approvalLabel}</strong>
        </div>
        <div className="header-editor-review-actions">
          {approved || unapproved ? (
            <button
              className="button-secondary-small"
              onClick={() => onApprovalChange(header.id, "pending")}
              type="button"
            >
              Reopen
            </button>
          ) : null}
          {!approved ? (
            <button
              className="button-success-small"
              onClick={() => onApprovalChange(header.id, "approved")}
              type="button"
            >
              Approve header
            </button>
          ) : null}
          {!unapproved ? (
            <button
              className="button-danger-small"
              onClick={() => onApprovalChange(header.id, "unapproved")}
              type="button"
            >
              Unapprove
            </button>
          ) : null}
        </div>
      </div>

      <label className="field">
        <span>Header name</span>
        <input
          onChange={(event) => onChange(header.id, { name: event.target.value })}
          type="text"
          value={header.name}
        />
      </label>

      <div className="editor-actions">
        <button className="button button-secondary" onClick={onAdd} type="button">
          Add header
        </button>
        <button
          className="button button-danger"
          disabled={unapproved}
          onClick={() => onRemove(header.id)}
          type="button"
        >
          Unapprove
        </button>
      </div>
    </aside>
  );
}

function isApprovedHeader(header) {
  return header?.approvalStatus === "approved" || (!header?.approvalStatus && header?.approved);
}

function isUnapprovedHeader(header) {
  return header?.approvalStatus === "unapproved";
}
