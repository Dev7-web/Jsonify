import ConfidenceBar from "./ConfidenceBar";
import StatusChip from "./StatusChip";

function getHeaderStatus(header) {
  return header?.confidence < 70 ? "low_confidence" : "ready";
}

export default function HeaderEditor({ header, onChange, onRemove, onAdd }) {
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

  return (
    <aside className="panel editor-panel">
      <div className="panel-heading panel-heading-row">
        <div>
          <p className="eyebrow">Header editor</p>
          <h2>{header.name}</h2>
        </div>
        <StatusChip status={getHeaderStatus(header)} />
      </div>

      <ConfidenceBar value={header.confidence} />

      <label className="field">
        <span>Header name</span>
        <input
          onChange={(event) => onChange(header.id, { name: event.target.value })}
          type="text"
          value={header.name}
        />
      </label>

      <label className="field">
        <span>Cell coordinate</span>
        <input
          onChange={(event) => onChange(header.id, { coordinate: event.target.value })}
          type="text"
          value={header.coordinate}
        />
      </label>

      <div className="editor-actions">
        <button className="button button-secondary" onClick={onAdd} type="button">
          Add header
        </button>
        <button className="button button-danger" onClick={() => onRemove(header.id)} type="button">
          Remove
        </button>
      </div>
    </aside>
  );
}
