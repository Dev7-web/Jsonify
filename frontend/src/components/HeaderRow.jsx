import ConfidenceBar from "./ConfidenceBar";
import StatusChip from "./StatusChip";

function getHeaderStatus(header) {
  return header.confidence < 70 ? "low_confidence" : "ready";
}

export default function HeaderRow({ header, selected, onSelect }) {
  return (
    <div className={selected ? "header-row header-row--selected" : "header-row"}>
      <button className="header-row-main" onClick={() => onSelect(header.id)} type="button">
        <span className="header-name">{header.name}</span>
        <span className="coordinate">{header.coordinate}</span>
        <ConfidenceBar value={header.confidence} />
        <StatusChip status={getHeaderStatus(header)} />
      </button>

      {header.subheaders?.length ? (
        <div className="subheader-list">
          {header.subheaders.map((subheader) => (
            <div className="subheader-row" key={subheader.id}>
              <span>{subheader.name}</span>
              <span className="coordinate">{subheader.coordinate}</span>
              <ConfidenceBar value={subheader.confidence} />
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}
