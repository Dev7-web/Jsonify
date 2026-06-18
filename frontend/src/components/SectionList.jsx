import StatusChip from "./StatusChip";

function getSectionStatus(section) {
  if (section.type === "ignored") {
    return "ignored";
  }

  if (section.headers.length > 0 && section.headers.every(isApprovedHeader)) {
    return "approved";
  }

  if (
    section.headers.length > 0 &&
    section.headers.every((header) => isApprovedHeader(header) || isUnapprovedHeader(header))
  ) {
    return "reviewed";
  }

  return "needs_review";
}

export default function SectionList({ sections, selectedSectionId, onSelect }) {
  return (
    <aside className="panel section-list">
      <div className="panel-heading">
        <p className="eyebrow">Detected layout</p>
        <h2>Sections</h2>
      </div>

      <div className="section-stack">
        {sections.map((section) => (
          <button
            className={
              selectedSectionId === section.id
                ? "section-card section-card--selected"
                : "section-card"
            }
            key={section.id}
            onClick={() => onSelect(section.id)}
            type="button"
          >
            <div className="section-card-top">
              <strong>{section.title}</strong>
              <StatusChip status={getSectionStatus(section)} />
            </div>
          </button>
        ))}
      </div>
    </aside>
  );
}

function isApprovedHeader(header) {
  return header.approvalStatus === "approved" || (!header.approvalStatus && header.approved);
}

function isUnapprovedHeader(header) {
  return header.approvalStatus === "unapproved";
}
