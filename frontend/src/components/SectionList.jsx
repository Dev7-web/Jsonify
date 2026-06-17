import ConfidenceBar from "./ConfidenceBar";
import StatusChip from "./StatusChip";

function getSectionStatus(section) {
  if (section.type === "ignored") {
    return "ignored";
  }

  return section.confidence < 70 ? "low_confidence" : "ready";
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
            <div className="section-card-meta">
              <span>{section.type}</span>
              <ConfidenceBar value={section.confidence} />
            </div>
          </button>
        ))}
      </div>
    </aside>
  );
}
