import { useEffect, useMemo, useState } from "react";
import {
  approveDocumentHeaders,
  detectDocumentHeaders,
  getHealth,
  uploadDocument,
} from "./api";
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
  const [approvalError, setApprovalError] = useState("");
  const [approvalResult, setApprovalResult] = useState(null);
  const [isApproving, setIsApproving] = useState(false);
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
      status:
        approvalResult?.status ??
        detectionResult?.status ??
        uploadResult?.status ??
        "uploaded",
    };
  }, [approvalResult, detectionResult, selectedFile, uploadResult]);

  const selectedSection = sections.find((section) => section.id === selectedSectionId);
  const selectedHeader = selectedSection?.headers.find(
    (header) => header.id === selectedHeaderId,
  );
  const reviewProgress = useMemo(() => getReviewProgress(sections), [sections]);
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
      setApprovalError("");
      setApprovalResult(null);
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
      setApprovalError("");
      setApprovalResult(null);
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

  async function approveHeaders() {
    if (!uploadResult) {
      setApprovalError("Upload and detect an .xlsx document before approval.");
      return;
    }

    const trimmedSchemaName = schemaName.trim();
    if (!trimmedSchemaName) {
      setApprovalError("Schema name must not be empty.");
      return;
    }

    if (reviewProgress.total === 0) {
      setApprovalError("Add at least one included header before saving the schema.");
      return;
    }

    if (reviewProgress.pending > 0) {
      setApprovalError(
        `Resolve ${reviewProgress.pending} pending header${
          reviewProgress.pending === 1 ? "" : "s"
        } by approving or unapproving them before saving the schema.`,
      );
      return;
    }

    if (reviewProgress.approved === 0) {
      setApprovalError("Approve at least one header before saving the schema.");
      return;
    }

    setIsApproving(true);
    setApprovalError("");

    try {
      const result = await approveDocumentHeaders(uploadResult.id, {
        ...approvalPayload,
        name: trimmedSchemaName,
      });
      setApprovalResult(result);
      setUploadResult((currentResult) =>
        currentResult ? { ...currentResult, status: result.status } : currentResult,
      );
      setDetectionResult((currentResult) =>
        currentResult ? { ...currentResult, status: result.status } : currentResult,
      );
    } catch (error) {
      setApprovalError(error.message);
    } finally {
      setIsApproving(false);
    }
  }

  function clearSavedApproval() {
    setApprovalResult(null);
    setApprovalError("");
    setDetectionResult((currentResult) =>
      currentResult ? { ...currentResult, status: "needs_review" } : currentResult,
    );
    setUploadResult((currentResult) =>
      currentResult ? { ...currentResult, status: "needs_review" } : currentResult,
    );
  }

  function selectSection(sectionId) {
    const nextSection = sections.find((section) => section.id === sectionId);
    setSelectedSectionId(sectionId);
    setSelectedHeaderId(nextSection?.headers[0]?.id ?? "");
  }

  function updateSelectedSection(changes) {
    clearSavedApproval();
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
      headers: [],
    };

    clearSavedApproval();
    setSections((currentSections) => [...currentSections, nextSection]);
    setSelectedSectionId(nextSection.id);
    setSelectedHeaderId("");
  }

  function removeSelectedSection() {
    if (sections.length === 1) {
      return;
    }

    const remainingSections = sections.filter((section) => section.id !== selectedSectionId);
    clearSavedApproval();
    setSections(remainingSections);
    setSelectedSectionId(remainingSections[0].id);
    setSelectedHeaderId(remainingSections[0].headers[0]?.id ?? "");
  }

  function updateHeader(headerId, changes) {
    clearSavedApproval();
    setSections((currentSections) =>
      currentSections.map((section) => {
        if (section.id !== selectedSectionId) {
          return section;
        }

        return {
          ...section,
          headers: section.headers.map((header) =>
            header.id === headerId
              ? {
                  ...header,
                  ...changes,
                  approved: false,
                  approvalStatus: "pending",
                }
              : header,
          ),
        };
      }),
    );
  }

  function setHeaderReviewStatus(headerId, approvalStatus) {
    clearSavedApproval();
    setSections((currentSections) =>
      currentSections.map((section) => {
        if (section.id !== selectedSectionId) {
          return section;
        }

        return {
          ...section,
          headers: section.headers.map((header) =>
            header.id === headerId
              ? {
                  ...header,
                  approvalStatus,
                  approved: approvalStatus === "approved",
                  subheaders: header.subheaders?.map((subheader) => ({
                    ...subheader,
                    approvalStatus,
                    approved: approvalStatus === "approved",
                  })),
                }
              : header,
          ),
        };
      }),
    );
  }

  function addHeader() {
    const nextHeader = {
      id: `header-${Date.now()}`,
      name: "New Header",
      approved: false,
      approvalStatus: "pending",
    };

    clearSavedApproval();
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
    setHeaderReviewStatus(headerId, "unapproved");
  }

  function updateSchemaName(nextSchemaName) {
    clearSavedApproval();
    setSchemaName(nextSchemaName);
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
            setApprovalError("");
            setApprovalResult(null);
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
          approvalError={approvalError}
          approvalResult={approvalResult}
          detectionResult={detectionResult}
          document={activeDocument}
          isApproving={isApproving}
          onAddHeader={addHeader}
          onAddSection={addSection}
          onApprove={approveHeaders}
          onBack={() => setIsReviewLoaded(false)}
          onRemoveHeader={removeHeader}
          onRemoveSection={removeSelectedSection}
          onSchemaNameChange={updateSchemaName}
          onSectionChange={updateSelectedSection}
          onHeaderApprovalChange={setHeaderReviewStatus}
          onSelectHeader={setSelectedHeaderId}
          onSelectSection={selectSection}
          onUpdateHeader={updateHeader}
          reviewProgress={reviewProgress}
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
  approvalError,
  approvalResult,
  sections,
  selectedSection,
  selectedSectionId,
  selectedHeader,
  selectedHeaderId,
  schemaName,
  approvalPayload,
  isApproving,
  onBack,
  onApprove,
  onSelectSection,
  onSelectHeader,
  onSectionChange,
  onAddSection,
  onRemoveSection,
  onUpdateHeader,
  onAddHeader,
  onRemoveHeader,
  onSchemaNameChange,
  onHeaderApprovalChange,
  reviewProgress,
}) {
  const canApproveSchema =
    reviewProgress.total > 0 && reviewProgress.pending === 0 && reviewProgress.approved > 0;
  const selectedSectionCounts = selectedSection
    ? getHeaderReviewCounts(selectedSection.headers)
    : null;

  return (
    <section className="review-shell" aria-labelledby="review-title">
      <header className="review-header">
        <div>
          <p className="eyebrow">Human-in-the-loop review</p>
          <h1 id="review-title">Review detected headers</h1>
          <p>{document.name}</p>
        </div>
        <div className="review-header-status">
          <div className="review-status-line">
            <StatusChip status={document.status} />
            {detectionResult?.detected_headers?.flags?.length ? (
              <span className="review-flag-count">
                {detectionResult.detected_headers.flags.length} review flags
              </span>
            ) : null}
          </div>
          <p className="approval-progress">
            {reviewProgress.reviewed}/{reviewProgress.total} reviewed ·{" "}
            {reviewProgress.approved} approved · {reviewProgress.unapproved} unapproved
          </p>
          <button
            className="primary-button"
            disabled={isApproving || Boolean(approvalResult) || !canApproveSchema}
            onClick={onApprove}
            type="button"
          >
            {approvalResult
              ? "Schema approved"
              : isApproving
                ? "Approving..."
                : "Approve headers"}
          </button>
          {approvalError ? <p className="inline-error">{approvalError}</p> : null}
          {approvalResult ? (
            <p className="inline-success">Schema approved: {approvalResult.schema_id}</p>
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
              </div>

              <div className="headers-toolbar">
                <strong>
                  {selectedSection.headers.length} headers
                  {selectedSection.type !== "ignored" && selectedSectionCounts ? (
                    <>
                      {" "}
                      · {selectedSectionCounts.approved} approved
                      {" "}
                      · {selectedSectionCounts.unapproved} unapproved
                      {" "}
                      · {selectedSectionCounts.pending} pending
                    </>
                  ) : null}
                </strong>
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
                      onApprovalChange={onHeaderApprovalChange}
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
          onApprovalChange={onHeaderApprovalChange}
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
        <div className="approval-feedback" aria-live="polite">
          <button className="button-secondary" onClick={onBack} type="button">
            Back to upload
          </button>
          {approvalError ? <p className="error-message">{approvalError}</p> : null}
          {approvalResult ? (
            <p className="success-message">
              Schema approved: {approvalResult.schema_id}
            </p>
          ) : null}
        </div>
        <button
          className="primary-button"
          disabled={isApproving || Boolean(approvalResult) || !canApproveSchema}
          onClick={onApprove}
          type="button"
        >
          {approvalResult
            ? "Schema approved"
            : isApproving
              ? "Approving..."
              : "Approve headers"}
        </button>
      </footer>
    </section>
  );
}

function buildApprovalPayload(schemaName, sections) {
  const sectionsBySheet = sections.reduce((groups, section) => {
    if (section.type === "ignored") {
      return groups;
    }

    const approvedHeaders = section.headers.filter(isApprovedHeader);
    if (!approvedHeaders.length) {
      return groups;
    }

    const sheetName = section.sheetName || "Workbook";
    const approvedSection =
      section.type === "key_value"
        ? {
            type: section.type,
            title: section.title,
            fields: approvedHeaders.map((header) => header.name),
          }
        : {
            type: section.type,
            title: section.title,
            headers: approvedHeaders.map((header) => {
              const approvedSubheaders = header.subheaders?.filter(isApprovedHeader) ?? [];
              const approvedHeader = { name: header.name };

              if (approvedSubheaders.length) {
                approvedHeader.subheaders = approvedSubheaders.map((subheader) => ({
                  name: subheader.name,
                }));
              }

              return approvedHeader;
            }),
          };

    return {
      ...groups,
      [sheetName]: [...(groups[sheetName] ?? []), approvedSection],
    };
  }, {});

  return {
    name: schemaName,
    header_structure: {
      sheets: Object.entries(sectionsBySheet).map(([sheetName, sheetSections]) => ({
        name: sheetName,
        sections: sheetSections,
      })),
    },
  };
}

function getReviewProgress(sections) {
  const reviewableHeaders = sections
    .filter((section) => section.type !== "ignored")
    .flatMap((section) => section.headers);

  const counts = getHeaderReviewCounts(reviewableHeaders);

  return {
    ...counts,
    reviewed: counts.approved + counts.unapproved,
    total: reviewableHeaders.length,
    remaining: counts.pending,
  };
}

function countApprovedHeaders(headers) {
  return headers.filter(isApprovedHeader).length;
}

function getHeaderReviewCounts(headers) {
  const approved = countApprovedHeaders(headers);
  const unapproved = headers.filter(isUnapprovedHeader).length;

  return {
    approved,
    unapproved,
    pending: headers.length - approved - unapproved,
  };
}

function isApprovedHeader(header) {
  return header.approvalStatus === "approved" || (!header.approvalStatus && header.approved);
}

function isUnapprovedHeader(header) {
  return header.approvalStatus === "unapproved";
}

function buildSectionsFromDetection(detectedHeaders) {
  const headerStructure = detectedHeaders?.header_structure;
  const deterministicHeaders = detectedHeaders?.deterministic_headers ?? {};
  const sections = [];

  headerStructure?.sheets?.forEach((sheet) => {
    sheet.sections?.forEach((section, sectionIndex) => {
      const title = section.title || `${sheet.name} section ${sectionIndex + 1}`;

      sections.push({
        id: createId(`${sheet.name}-${title}-${sectionIndex}`),
        sheetName: sheet.name,
        title,
        type: section.type,
        headers:
          section.type === "key_value"
            ? buildKeyValueHeaders(section.fields ?? [])
            : buildTableHeaders(section.headers ?? [], {
                sheetName: sheet.name,
                title,
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
        headers: table.columns.map((column) => ({
          id: createId(`${sheetName}-${table.title}-${column.name}`),
          name: column.name,
          approved: false,
          approvalStatus: "pending",
        })),
      });
    });
  });

  return sections;
}

function buildKeyValueHeaders(fields) {
  return fields.map((field, index) => ({
    id: createId(`${field}-${index}`),
    name: field,
    approved: false,
    approvalStatus: "pending",
  }));
}

function buildTableHeaders(headers, context) {
  return headers.map((header, index) => {
    return {
      id: createId(`${context.sheetName}-${context.title}-${header.name}-${index}`),
      name: header.name,
      approved: false,
      approvalStatus: "pending",
      subheaders: header.subheaders?.map((subheader, subIndex) => ({
        id: createId(
          `${context.sheetName}-${context.title}-${header.name}-${subheader.name}-${subIndex}`,
        ),
        name: subheader.name,
        approved: false,
        approvalStatus: "pending",
      })),
    };
  });
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
