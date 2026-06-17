export default function SchemaNamePanel({ schemaName, onSchemaNameChange, payload }) {
  return (
    <aside className="panel schema-panel">
      <div className="panel-heading">
        <p className="eyebrow">Schema approval</p>
        <h2>Ready to submit</h2>
      </div>

      <label className="field">
        <span>Schema name</span>
        <input
          onChange={(event) => onSchemaNameChange(event.target.value)}
          type="text"
          value={schemaName}
        />
      </label>

      <div className="payload-preview">
        <p>Approval payload preview</p>
        <pre>{JSON.stringify(payload, null, 2)}</pre>
      </div>
    </aside>
  );
}
