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

export default function HeaderRow({ header, selected, onSelect, onApprovalChange }) {
  const approved = isApprovedHeader(header);
  const unapproved = isUnapprovedHeader(header);

  function handleRowKeyDown(event) {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      onSelect(header.id);
    }
  }

  function handleApprovalClick(event, nextStatus) {
    event.stopPropagation();
    onApprovalChange(header.id, nextStatus);
  }

  return (
    <div
      className={[
        "header-row",
        selected ? "header-row--selected" : "",
        unapproved ? "header-row--unapproved" : "",
      ]
        .filter(Boolean)
        .join(" ")}
    >
      <div
        className="header-row-main"
        onClick={() => onSelect(header.id)}
        onKeyDown={handleRowKeyDown}
        role="button"
        tabIndex={0}
      >
        <span className="header-name">{header.name}</span>
        <StatusChip status={getHeaderStatus(header)} />
        <div className="header-row-actions">
          {approved || unapproved ? (
            <button
              className="button-secondary-small"
              onClick={(event) => handleApprovalClick(event, "pending")}
              onKeyDown={(event) => event.stopPropagation()}
              type="button"
            >
              Reopen
            </button>
          ) : (
            <button
              className="button-success-small"
              onClick={(event) => handleApprovalClick(event, "approved")}
              onKeyDown={(event) => event.stopPropagation()}
              type="button"
            >
              Approve
            </button>
          )}

          {unapproved ? (
            <button
              className="button-success-small"
              onClick={(event) => handleApprovalClick(event, "approved")}
              onKeyDown={(event) => event.stopPropagation()}
              type="button"
            >
              Approve
            </button>
          ) : (
            <button
              className="button-danger-small"
              onClick={(event) => handleApprovalClick(event, "unapproved")}
              onKeyDown={(event) => event.stopPropagation()}
              type="button"
            >
              Unapprove
            </button>
          )}
        </div>
      </div>

      {header.subheaders?.length ? (
        <div className="subheader-list">
          {header.subheaders.map((subheader) => (
            <div className="subheader-row" key={subheader.id}>
              <span>{subheader.name}</span>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function isApprovedHeader(header) {
  return header.approvalStatus === "approved" || (!header.approvalStatus && header.approved);
}

function isUnapprovedHeader(header) {
  return header.approvalStatus === "unapproved";
}
