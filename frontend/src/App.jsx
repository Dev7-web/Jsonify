import { useEffect, useMemo, useState } from "react";
import { detectDocumentHeaders, getHealth, uploadDocument } from "./api";
import HeaderEditor from "./components/HeaderEditor";
import HeaderRow from "./components/HeaderRow";
import SchemaNamePanel from "./components/SchemaNamePanel";
import SectionList from "./components/SectionList";
import StatusChip from "./components/StatusChip";
import "./styles.css";

const DEFAULT_SCHEMA_NAME = "New layout schema";

export default function App() {
  const [backendStatus, setBackendStatus] = useState("checking");
  const [selectedFile, setSelectedFile] = useState(null);
  const [uploadResult, setUploadResult] = useState(null);
  const [uploadError, setUploadError] = useState("");
  const [isUploading, setIsUploading] = useState(false);
  const [detectionError, setDetectionError] = useState("");
  const [isDetecting, setIsDetecting] = useState(false);
  const [detectionResult, setDetectionResult] = useState(null);
  const [isReviewLoaded, setIsReviewLoaded] = useState(false);
  const [schemaName, setSchemaName] = useState(DEFAULT_SCHEMA_NAME);
  const [sections, setSections] = useState([]);
  const [selectedSectionId, setSelectedSectionId] = useState("");
  const [selectedHeaderId, setSelectedHeaderId] = useState("");

  useEffect(() => {
    let isMounted = true;

    getHealth()
      .then((data) => {
        if (isMounted) {
          setBackendStatus(data.status ?? "unknown");
        }
      })
      .catch(() => {
        if (isMounted) {
          setBackendStatus("unreachable");
        }
      });

    return () => {
      isMounted = false;
    };
  }, []);

  const activeDocument = useMemo(() => {
    return {
      id: uploadResult?.id ?? "",
      name: selectedFile?.name ?? "Uploaded document",
      status: detectionResult?.status ?? uploadResult?.status ?? "uploaded",
      confidence:
        detectionResult?.confidence != null
          ? Math.round(detectionResult.confidence * 100)
          : 0,
    };
  }, [detectionResult, selectedFile, uploadResult]);

  const selectedSection = sections.find((section) => section.id === selectedSectionId);
  const selectedHeader = selectedSection?.headers.find(
    (header) => header.id === selectedHeaderId,
  );
  const approvalPayload = useMemo(
    () => buildApprovalPayload(schemaName, sections),
    [schemaName, sections],
  );

  async function handleUpload(event) {
    event.preventDefault();

    if (!selectedFile) {
      return;
    }

    setIsUploading(true);
    setUploadError("");
    setUploadResult(null);

    try {
      const result = await uploadDocument(selectedFile);
      setUploadResult(result);
      setDetectionError("");
      setDetectionResult(null);
      setSchemaName(getDefaultSchemaName(selectedFile.name));
      setSections([]);
      setSelectedSectionId("");
      setSelectedHeaderId("");
      setIsReviewLoaded(false);
    } catch (error) {
      setUploadError(error.message);
    } finally {
      setIsUploading(false);
    }
  }

  async function loadReview() {
    if (!uploadResult) {
      setDetectionError("Upload an .xlsx document before loading detected headers.");
      return;
    }

    if (uploadResult.file_type !== "xlsx") {
      setDetectionError("Header review is currently wired for .xlsx documents only.");
      return;
    }

    setIsDetecting(true);
    setDetectionError("");

    try {
      const result = await detectDocumentHeaders(uploadResult.id);
      const nextSections = buildSectionsFromDetection(result.detected_headers);
      setDetectionResult(result);
      setSections(nextSections);
      setSelectedSectionId(nextSections[0]?.id ?? "");
      setSelectedHeaderId(nextSections[0]?.headers[0]?.id ?? "");
      setIsReviewLoaded(true);
    } catch (error) {
      setDetectionError(error.message);
    } finally {
      setIsDetecting(false);
    }
  }

  function selectSection(sectionId) {
    const nextSection = sections.find((section) => section.id === sectionId);
    setSelectedSectionId(sectionId);
    setSelectedHeaderId(nextSection?.headers[0]?.id ?? "");
  }

  function updateSelectedSection(changes) {
    setSections((currentSections) =>
      currentSections.map((section) =>
        section.id === selectedSectionId ? { ...section, ...changes } : section,
      ),
    );
  }

  function addSection() {
    const nextSection = {
      id: `section-${Date.now()}`,
      title: "New Section",
      type: "table",
      confidence: 100,
      headers: [],
    };

    setSections((currentSections) => [...currentSections, nextSection]);
    setSelectedSectionId(nextSection.id);
    setSelectedHeaderId("");
  }

  function removeSelectedSection() {
    if (sections.length === 1) {
      return;
    }

    const remainingSections = sections.filter((section) => section.id !== selectedSectionId);
    setSections(remainingSections);
    setSelectedSectionId(remainingSections[0].id);
    setSelectedHeaderId(remainingSections[0].headers[0]?.id ?? "");
  }

  function updateHeader(headerId, changes) {
    setSections((currentSections) =>
      currentSections.map((section) => {
        if (section.id !== selectedSectionId) {
          return section;
        }

        return {
          ...section,
          headers: section.headers.map((header) =>
            header.id === headerId ? { ...header, ...changes } : header,
          ),
        };
      }),
    );
  }

  function addHeader() {
    const nextHeader = {
      id: `header-${Date.now()}`,
      name: "New Header",
      coordinate: "",
      confidence: 100,
    };

    setSections((currentSections) =>
      currentSections.map((section) =>
        section.id === selectedSectionId
          ? { ...section, headers: [...section.headers, nextHeader] }
          : section,
      ),
    );
    setSelectedHeaderId(nextHeader.id);
  }

  function removeHeader(headerId) {
    const remainingHeaders =
      selectedSection?.headers.filter((header) => header.id !== headerId) ?? [];

    setSections((currentSections) =>
      currentSections.map((section) =>
        section.id === selectedSectionId
          ? { ...section, headers: remainingHeaders }
          : section,
      ),
    );
    setSelectedHeaderId(remainingHeaders[0]?.id ?? "");
  }

  return (
    <main className="app-shell">
      {!isReviewLoaded ? (
        <UploadAndLoadReview
          backendStatus={backendStatus}
          isUploading={isUploading}
          detectionError={detectionError}
          isDetecting={isDetecting}
          onLoadReview={loadReview}
          onSelectedFileChange={(file) => {
            setSelectedFile(file);
            setUploadError("");
            setUploadResult(null);
            setDetectionError("");
            setDetectionResult(null);
            setSchemaName(getDefaultSchemaName(file?.name));
            setSections([]);
            setSelectedSectionId("");
            setSelectedHeaderId("");
            setIsReviewLoaded(false);
          }}
          onSubmit={handleUpload}
          selectedFile={selectedFile}
          uploadError={uploadError}
          uploadResult={uploadResult}
        />
      ) : (
        <HeaderReviewScreen
          approvalPayload={approvalPayload}
          detectionResult={detectionResult}
          document={activeDocument}
          onAddHeader={addHeader}
          onAddSection={addSection}
          onBack={() => setIsReviewLoaded(false)}
          onRemoveHeader={removeHeader}
          onRemoveSection={removeSelectedSection}
          onSchemaNameChange={setSchemaName}
          onSectionChange={updateSelectedSection}
          onSelectHeader={setSelectedHeaderId}
          onSelectSection={selectSection}
          onUpdateHeader={updateHeader}
          schemaName={schemaName}
          sections={sections}
          selectedHeader={selectedHeader}
          selectedHeaderId={selectedHeaderId}
          selectedSection={selectedSection}
          selectedSectionId={selectedSectionId}
        />
      )}
    </main>
  );
}

function UploadAndLoadReview({
  backendStatus,
  selectedFile,
  uploadResult,
  uploadError,
  isUploading,
  detectionError,
  isDetecting,
  onSelectedFileChange,
  onSubmit,
  onLoadReview,
}) {
  return (
    <section className="status-panel" aria-labelledby="app-title">
      <p className="eyebrow">Document JSON Extractor</p>
      <h1 id="app-title">Upload document</h1>
      <p className="status-line">backend: {backendStatus}</p>

      <form className="upload-form" onSubmit={onSubmit}>
        <label className="file-label" htmlFor="document-file">
          Document file
        </label>
        <input
          accept=".xlsx,.pdf"
          id="document-file"
          name="file"
          onChange={(event) => onSelectedFileChange(event.target.files?.[0] ?? null)}
          type="file"
        />
        <button disabled={!selectedFile || isUploading} type="submit">
          {isUploading ? "Uploading..." : "Upload"}
        </button>
      </form>

      <div aria-live="polite" className="upload-feedback">
        {uploadError ? <p className="error-message">{uploadError}</p> : null}
        {uploadResult ? (
          <dl className="result-list">
            <div>
              <dt>id</dt>
              <dd>{uploadResult.id}</dd>
            </div>
            <div>
              <dt>file type</dt>
              <dd>{uploadResult.file_type}</dd>
            </div>
            <div>
              <dt>status</dt>
              <dd>{uploadResult.status}</dd>
            </div>
          </dl>
        ) : null}
      </div>

      <div className="review-entry">
        <p>
          Upload an Excel document, then run backend header detection to review and
          edit the returned schema draft.
        </p>
        {detectionError ? <p className="error-message">{detectionError}</p> : null}
        <button
          className="secondary-button"
          disabled={!uploadResult || isDetecting}
          onClick={onLoadReview}
          type="button"
        >
          {isDetecting ? "Detecting headers..." : "Load detected headers"}
        </button>
      </div>
    </section>
  );
}

function HeaderReviewScreen({
  document,
  detectionResult,
  sections,
  selectedSection,
  selectedSectionId,
  selectedHeader,
  selectedHeaderId,
  schemaName,
  approvalPayload,
  onBack,
  onSelectSection,
  onSelectHeader,
  onSectionChange,
  onAddSection,
  onRemoveSection,
  onUpdateHeader,
  onAddHeader,
  onRemoveHeader,
  onSchemaNameChange,
}) {
  return (
    <section className="review-shell" aria-labelledby="review-title">
      <header className="review-header">
        <div>
          <p className="eyebrow">Human-in-the-loop review</p>
          <h1 id="review-title">Review detected headers</h1>
          <p>{document.name}</p>
        </div>
        <div className="review-header-status">
          <StatusChip status="needs_review" />
          {detectionResult?.detected_headers?.flags?.length ? (
            <span>{detectionResult.detected_headers.flags.length} review flags</span>
          ) : null}
        </div>
      </header>

      <div className="review-grid">
        <SectionList
          onSelect={onSelectSection}
          sections={sections}
          selectedSectionId={selectedSectionId}
        />

        <section className="panel section-editor">
          <div className="panel-heading panel-heading-row">
            <div>
              <p className="eyebrow">Selected section</p>
              <h2>{selectedSection?.title ?? "No section selected"}</h2>
            </div>
            <div className="toolbar">
              <button className="button-secondary-small" onClick={onAddSection} type="button">
                Add section
              </button>
              <button
                className="button-danger-small"
                disabled={sections.length === 1}
                onClick={onRemoveSection}
                type="button"
              >
                Remove
              </button>
            </div>
          </div>

          {selectedSection ? (
            <>
              <div className="section-fields">
                <label className="field">
                  <span>Section name</span>
                  <input
                    onChange={(event) => onSectionChange({ title: event.target.value })}
                    type="text"
                    value={selectedSection.title}
                  />
                </label>

                <label className="field">
                  <span>Section type</span>
                  <select
                    onChange={(event) => onSectionChange({ type: event.target.value })}
                    value={selectedSection.type}
                  >
                    <option value="key_value">key_value</option>
                    <option value="table">table</option>
                    <option value="ignored">ignored</option>
                  </select>
                </label>
              </div>

              <div className="headers-toolbar">
                <strong>{selectedSection.headers.length} headers</strong>
                <button className="button-secondary-small" onClick={onAddHeader} type="button">
                  Add header
                </button>
              </div>

              <div className="headers-list">
                {selectedSection.headers.length ? (
                  selectedSection.headers.map((header) => (
                    <HeaderRow
                      header={header}
                      key={header.id}
                      onSelect={onSelectHeader}
                      selected={selectedHeaderId === header.id}
                    />
                  ))
                ) : (
                  <p className="empty-state">This section has no headers.</p>
                )}
              </div>
            </>
          ) : null}
        </section>

        <HeaderEditor
          header={selectedHeader}
          onAdd={onAddHeader}
          onChange={onUpdateHeader}
          onRemove={onRemoveHeader}
        />

        <SchemaNamePanel
          onSchemaNameChange={onSchemaNameChange}
          payload={approvalPayload}
          schemaName={schemaName}
        />
      </div>

      <footer className="review-footer">
        <button className="button-secondary" onClick={onBack} type="button">
          Back to upload
        </button>
        <button className="primary-button" type="button">
          Ready to submit later
        </button>
      </footer>
    </section>
  );
}

function buildApprovalPayload(schemaName, sections) {
  const includedSections = sections.filter((section) => section.type !== "ignored");
  const sectionsBySheet = includedSections.reduce((groups, section) => {
    const sheetName = section.sheetName || "Workbook";
    return {
      ...groups,
      [sheetName]: [...(groups[sheetName] ?? []), section],
    };
  }, {});

  return {
    name: schemaName,
    header_structure: {
      sheets: Object.entries(sectionsBySheet).map(([sheetName, sheetSections]) => ({
        name: sheetName,
        sections: sheetSections.map((section) => {
            if (section.type === "key_value") {
              return {
                type: section.type,
                title: section.title,
                fields: section.headers.map((header) => header.name),
              };
            }

            return {
              type: section.type,
              title: section.title,
              headers: section.headers.map((header) => ({
                name: header.name,
                subheaders: header.subheaders?.map((subheader) => ({
                  name: subheader.name,
                })),
              })),
            };
          }),
      })),
    },
  };
}

function buildSectionsFromDetection(detectedHeaders) {
  const headerStructure = detectedHeaders?.header_structure;
  const deterministicHeaders = detectedHeaders?.deterministic_headers ?? {};
  const confidence = detectedHeaders?.confidence ?? 0.8;
  const flags = detectedHeaders?.flags ?? [];
  const sections = [];

  headerStructure?.sheets?.forEach((sheet) => {
    sheet.sections?.forEach((section, sectionIndex) => {
      const title = section.title || `${sheet.name} section ${sectionIndex + 1}`;
      const deterministicTable = findDeterministicTable(
        deterministicHeaders[sheet.name] ?? [],
        title,
      );

      sections.push({
        id: createId(`${sheet.name}-${title}-${sectionIndex}`),
        sheetName: sheet.name,
        title,
        type: section.type,
        confidence: getSectionConfidence(confidence, flags, sheet.name, title),
        headers:
          section.type === "key_value"
            ? buildKeyValueHeaders(section.fields ?? [], confidence)
            : buildTableHeaders(section.headers ?? [], deterministicTable, flags, {
                sheetName: sheet.name,
                title,
                confidence,
              }),
      });
    });
  });

  if (sections.length) {
    return sections;
  }

  Object.entries(deterministicHeaders).forEach(([sheetName, tables]) => {
    tables.forEach((table, index) => {
      sections.push({
        id: createId(`${sheetName}-${table.title ?? "table"}-${index}`),
        sheetName,
        title: table.title || "Untitled table",
        type: "table",
        confidence: Math.round(confidence * 100),
        headers: table.columns.map((column) => ({
          id: createId(`${sheetName}-${table.title}-${column.name}`),
          name: column.name,
          coordinate: column.coordinate,
          confidence: Math.round(confidence * 100),
        })),
      });
    });
  });

  return sections;
}

function buildKeyValueHeaders(fields, confidence) {
  const value = Math.round(confidence * 100);

  return fields.map((field, index) => ({
    id: createId(`${field}-${index}`),
    name: field,
    coordinate: "",
    confidence: value,
  }));
}

function buildTableHeaders(headers, deterministicTable, flags, context) {
  return headers.map((header, index) => {
    const deterministicColumn = deterministicTable?.columns?.find(
      (column) => normalizeLabel(column.name) === normalizeLabel(header.name),
    );
    const isFlagged = flags.some(
      (flag) =>
        flag.type === "deterministic_column_missing" &&
        normalizeLabel(flag.sheet) === normalizeLabel(context.sheetName) &&
        normalizeLabel(flag.title ?? "") === normalizeLabel(context.title) &&
        normalizeLabel(flag.column ?? "") === normalizeLabel(header.name),
    );

    return {
      id: createId(`${context.sheetName}-${context.title}-${header.name}-${index}`),
      name: header.name,
      coordinate: deterministicColumn?.coordinate ?? "",
      confidence: isFlagged ? 62 : Math.round(context.confidence * 100),
      subheaders: header.subheaders?.map((subheader, subIndex) => ({
        id: createId(`${context.sheetName}-${context.title}-${header.name}-${subheader.name}-${subIndex}`),
        name: subheader.name,
        coordinate: "",
        confidence: Math.round(context.confidence * 100),
      })),
    };
  });
}

function findDeterministicTable(tables, title) {
  return tables.find((table) => normalizeLabel(table.title ?? "") === normalizeLabel(title));
}

function getSectionConfidence(confidence, flags, sheetName, title) {
  const hasFlag = flags.some(
    (flag) =>
      normalizeLabel(flag.sheet) === normalizeLabel(sheetName) &&
      normalizeLabel(flag.title ?? "") === normalizeLabel(title),
  );

  return hasFlag ? 65 : Math.round(confidence * 100);
}

function normalizeLabel(value) {
  return String(value ?? "").trim().replace(/\s+/g, " ").toLowerCase();
}

function createId(value) {
  return normalizeLabel(value).replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
}

function getDefaultSchemaName(fileName) {
  if (!fileName) {
    return DEFAULT_SCHEMA_NAME;
  }

  return fileName.replace(/\.[^.]+$/, "") || DEFAULT_SCHEMA_NAME;
}
