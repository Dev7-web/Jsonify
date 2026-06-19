import { useEffect, useMemo, useState } from "react";
import {
  approveDocumentHeaders,
  detectDocumentHeaders,
  extractDocumentJson,
  getDocument,
  getDocumentJson,
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
const DOCUMENT_REVIEW_PATH_PATTERN = /^\/documents\/([^/]+)\/review\/?$/;

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
  const [extractionError, setExtractionError] = useState("");
  const [extractionResult, setExtractionResult] = useState(null);
  const [isDownloadingJson, setIsDownloadingJson] = useState(false);
  const [isExtracting, setIsExtracting] = useState(false);
  const [isRestoringDocument, setIsRestoringDocument] = useState(false);
  const [restoreError, setRestoreError] = useState("");
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
      name: selectedFile?.name ?? uploadResult?.filename ?? "Uploaded document",
      status:
        extractionResult?.status ??
        approvalResult?.status ??
        detectionResult?.status ??
        uploadResult?.status ??
        "uploaded",
    };
  }, [approvalResult, detectionResult, extractionResult, selectedFile, uploadResult]);

  useEffect(() => {
    let isMounted = true;

    async function syncDocumentFromUrl() {
      const documentId = getDocumentIdFromUrl();
      if (!documentId) {
        if (isMounted) {
          setIsReviewLoaded(false);
          setRestoreError("");
        }
        return;
      }

      setIsRestoringDocument(true);
      setRestoreError("");

      try {
        const document = await getDocument(documentId);
        if (isMounted) {
          hydrateStoredDocument(document);
        }
      } catch (error) {
        if (isMounted) {
          setRestoreError(error.message);
        }
      } finally {
        if (isMounted) {
          setIsRestoringDocument(false);
        }
      }
    }

    syncDocumentFromUrl();
    window.addEventListener("popstate", syncDocumentFromUrl);

    return () => {
      isMounted = false;
      window.removeEventListener("popstate", syncDocumentFromUrl);
    };
  }, []);

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
      setDocumentReviewUrl(result.id);
      setDetectionError("");
      setDetectionResult(null);
      setApprovalError("");
      setApprovalResult(null);
      setExtractionError("");
      setExtractionResult(null);
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
    setExtractionError("");
    setExtractionResult(null);

    try {
      const result = await detectDocumentHeaders(uploadResult.id);
      setDocumentReviewUrl(uploadResult.id);
      setDetectionResult(result);
      setApprovalError("");
      setApprovalResult(null);
      setUploadResult((currentResult) =>
        currentResult ? { ...currentResult, status: result.status } : currentResult,
      );

      if (result.source === "schema") {
        setApprovalResult({
          status: result.status,
          schema_id: result.matched_schema_id,
          matched_schema_id: result.matched_schema_id,
          fingerprint: result.fingerprint,
        });
        setSections([]);
        setSelectedSectionId("");
        setSelectedHeaderId("");
        setIsReviewLoaded(false);
        return;
      }

      const nextSections = buildSectionsFromDetection(result.detected_headers);
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
      setExtractionError("");
      setExtractionResult(null);
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

  async function extractJson() {
    if (!uploadResult) {
      setExtractionError("Upload and approve an .xlsx document before extraction.");
      return;
    }

    if (!canExtractDocument(activeDocument)) {
      setExtractionError("Approve headers before extracting the final JSON.");
      return;
    }

    setIsExtracting(true);
    setExtractionError("");

    try {
      const result = await extractDocumentJson(uploadResult.id);
      setExtractionResult(result);
      setUploadResult((currentResult) =>
        currentResult ? { ...currentResult, status: result.status } : currentResult,
      );
      setDetectionResult((currentResult) =>
        currentResult ? { ...currentResult, status: result.status } : currentResult,
      );
      setApprovalResult((currentResult) =>
        currentResult ? { ...currentResult, status: result.status } : currentResult,
      );
    } catch (error) {
      setExtractionError(error.message);
    } finally {
      setIsExtracting(false);
    }
  }

  async function downloadJson() {
    if (!uploadResult) {
      setExtractionError("Extract JSON before downloading it.");
      return;
    }

    setIsDownloadingJson(true);
    setExtractionError("");

    try {
      const result = await getDocumentJson(uploadResult.id);
      setExtractionResult((currentResult) => ({
        ...(currentResult ?? {}),
        ...result,
      }));
      saveJsonFile(result.output_json, getJsonDownloadName(activeDocument.name));
    } catch (error) {
      setExtractionError(error.message);
    } finally {
      setIsDownloadingJson(false);
    }
  }

  function hydrateStoredDocument(document) {
    const storedDetectionResult = buildDetectionResultFromDocument(document);
    const storedExtractionResult = buildExtractionResultFromDocument(document);
    const storedApprovalResult = buildApprovalResultFromDocument(document);
    const shouldShowReview = shouldRestoreReviewScreen(document, storedDetectionResult);
    const restoredSections = shouldShowReview
      ? buildSectionsFromDetection(storedDetectionResult.detected_headers)
      : [];

    setSelectedFile(null);
    setUploadResult(buildUploadResultFromDocument(document));
    setUploadError("");
    setDetectionError("");
    setDetectionResult(storedDetectionResult);
    setApprovalError("");
    setApprovalResult(storedApprovalResult);
    setExtractionError("");
    setExtractionResult(storedExtractionResult);
    setSchemaName(getDefaultSchemaName(document.filename));
    setSections(restoredSections);
    setSelectedSectionId(restoredSections[0]?.id ?? "");
    setSelectedHeaderId(restoredSections[0]?.headers[0]?.id ?? "");
    setIsReviewLoaded(shouldShowReview && restoredSections.length > 0);
  }

  function clearSavedApproval() {
    setApprovalResult(null);
    setApprovalError("");
    setExtractionError("");
    setExtractionResult(null);
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

  function approveSectionHeaders(sectionId) {
    clearSavedApproval();
    setSections((currentSections) =>
      currentSections.map((section) => {
        if (section.id !== sectionId || section.type === "ignored") {
          return section;
        }

        return {
          ...section,
          headers: section.headers.map((header) => ({
            ...header,
            approvalStatus: "approved",
            approved: true,
            subheaders: header.subheaders?.map((subheader) => ({
              ...subheader,
              approvalStatus: "approved",
              approved: true,
            })),
          })),
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
      {isRestoringDocument ? (
        <section className="status-panel" aria-labelledby="restore-title">
          <p className="eyebrow">Document JSON Extractor</p>
          <h1 id="restore-title">Loading document</h1>
          <p className="status-line">Restoring saved review from MongoDB...</p>
        </section>
      ) : !isReviewLoaded ? (
        <UploadAndLoadReview
          backendStatus={backendStatus}
          isUploading={isUploading}
          detectionError={detectionError}
          isDetecting={isDetecting}
          restoreError={restoreError}
          onLoadReview={loadReview}
          onSelectedFileChange={(file) => {
            setSelectedFile(file);
            clearDocumentReviewUrl();
            setUploadError("");
            setUploadResult(null);
            setDetectionError("");
            setDetectionResult(null);
            setApprovalError("");
            setApprovalResult(null);
            setRestoreError("");
            setExtractionError("");
            setExtractionResult(null);
            setSchemaName(getDefaultSchemaName(file?.name));
            setSections([]);
            setSelectedSectionId("");
            setSelectedHeaderId("");
            setIsReviewLoaded(false);
          }}
          onSubmit={handleUpload}
          selectedFile={selectedFile}
          detectionResult={detectionResult}
          document={activeDocument}
          extractionError={extractionError}
          extractionResult={extractionResult}
          isDownloadingJson={isDownloadingJson}
          isExtracting={isExtracting}
          onDownloadJson={downloadJson}
          onExtractJson={extractJson}
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
          extractionError={extractionError}
          extractionResult={extractionResult}
          isDownloadingJson={isDownloadingJson}
          isExtracting={isExtracting}
          onAddHeader={addHeader}
          onAddSection={addSection}
          onApprove={approveHeaders}
          onBack={() => {
            clearDocumentReviewUrl();
            setIsReviewLoaded(false);
          }}
          onDownloadJson={downloadJson}
          onExtractJson={extractJson}
          onRemoveHeader={removeHeader}
          onRemoveSection={removeSelectedSection}
          onSchemaNameChange={updateSchemaName}
          onSectionChange={updateSelectedSection}
          onHeaderApprovalChange={setHeaderReviewStatus}
          onSectionApprove={approveSectionHeaders}
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
  detectionResult,
  document,
  extractionError,
  extractionResult,
  isUploading,
  detectionError,
  isDetecting,
  restoreError,
  isDownloadingJson,
  isExtracting,
  onDownloadJson,
  onExtractJson,
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
            {uploadResult.filename ? (
              <div>
                <dt>filename</dt>
                <dd>{uploadResult.filename}</dd>
              </div>
            ) : null}
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
        {detectionResult?.source === "schema" ? (
          <p className="success-message">
            Known schema matched: {detectionResult.matched_schema_id}. No human review
            needed.
          </p>
        ) : null}
        {restoreError ? <p className="error-message">{restoreError}</p> : null}
        <button
          className="secondary-button"
          disabled={!uploadResult || isDetecting}
          onClick={onLoadReview}
          type="button"
        >
          {isDetecting ? "Detecting headers..." : "Load detected headers"}
        </button>
      </div>

      {uploadResult && (canExtractDocument(document) || extractionResult) ? (
        <ExtractionPanel
          document={document}
          error={extractionError}
          extractionResult={extractionResult}
          isDownloading={isDownloadingJson}
          isExtracting={isExtracting}
          onDownload={onDownloadJson}
          onExtract={onExtractJson}
        />
      ) : null}
    </section>
  );
}

function HeaderReviewScreen({
  document,
  detectionResult,
  approvalError,
  approvalResult,
  extractionError,
  extractionResult,
  sections,
  selectedSection,
  selectedSectionId,
  selectedHeader,
  selectedHeaderId,
  schemaName,
  approvalPayload,
  isApproving,
  isDownloadingJson,
  isExtracting,
  onBack,
  onApprove,
  onDownloadJson,
  onExtractJson,
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
  onSectionApprove,
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
          onApproveSection={onSectionApprove}
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
                <button
                  className="button-success-small"
                  disabled={!canApproveSection(selectedSection)}
                  onClick={() => onSectionApprove(selectedSection.id)}
                  type="button"
                >
                  Approve section
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

      <ExtractionPanel
        document={document}
        error={extractionError}
        extractionResult={extractionResult}
        isDownloading={isDownloadingJson}
        isExtracting={isExtracting}
        onDownload={onDownloadJson}
        onExtract={onExtractJson}
      />

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

function ExtractionPanel({
  document,
  error,
  extractionResult,
  isDownloading,
  isExtracting,
  onDownload,
  onExtract,
}) {
  const canExtract = canExtractDocument(document);
  const hasJson = Boolean(extractionResult?.output_json);

  return (
    <section className="panel extraction-panel" aria-labelledby="extraction-title">
      <div className="panel-heading panel-heading-row">
        <div>
          <p className="eyebrow">Extraction</p>
          <h2 id="extraction-title">Final JSON</h2>
        </div>
        <StatusChip status={document.status} />
      </div>

      <p className="panel-copy">
        Run extraction after approval to save the final JSON on this document.
      </p>

      {error ? <p className="error-message">{error}</p> : null}

      <div className="extraction-actions">
        <button
          className="primary-button"
          disabled={!canExtract || isExtracting}
          onClick={onExtract}
          type="button"
        >
          {isExtracting ? "Extracting JSON..." : hasJson ? "Run extraction again" : "Extract JSON"}
        </button>
        <button
          className="button-secondary"
          disabled={!hasJson || isDownloading}
          onClick={onDownload}
          type="button"
        >
          {isDownloading ? "Preparing download..." : "Download JSON"}
        </button>
      </div>

      {!canExtract ? (
        <p className="muted-message">Approve headers before running extraction.</p>
      ) : null}

      {hasJson ? (
        <div className="json-result">
          <div className="json-result-header">
            <strong>Stored output JSON</strong>
            <span>
              {extractionResult.schema_id
                ? `Schema ${extractionResult.schema_id}`
                : "Fetched from document output_json"}
            </span>
          </div>
          <pre>{JSON.stringify(extractionResult.output_json, null, 2)}</pre>
        </div>
      ) : null}
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

function canApproveSection(section) {
  return (
    section?.type !== "ignored" &&
    Array.isArray(section?.headers) &&
    section.headers.length > 0 &&
    !section.headers.every(isApprovedHeader)
  );
}

function canExtractDocument(document) {
  return document?.status === "approved" || document?.status === "extracted";
}

function saveJsonFile(outputJson, fileName) {
  const blob = new Blob([JSON.stringify(outputJson, null, 2)], {
    type: "application/json",
  });
  const objectUrl = URL.createObjectURL(blob);
  const link = document.createElement("a");

  link.href = objectUrl;
  link.download = fileName;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(objectUrl);
}

function getJsonDownloadName(documentName) {
  const baseName = String(documentName || "document")
    .replace(/\.[^.]+$/, "")
    .trim();

  return `${baseName || "document"}.json`;
}

function buildUploadResultFromDocument(document) {
  return {
    id: document.id,
    filename: document.filename,
    file_type: document.file_type,
    status: document.status,
  };
}

function buildDetectionResultFromDocument(document) {
  if (!document.detected_headers) {
    return null;
  }

  return {
    id: document.id,
    status: document.status,
    source:
      document.detected_headers.source ??
      (document.matched_schema_id ? "schema" : "llm"),
    confidence: document.confidence ?? document.detected_headers.confidence ?? 0,
    detected_headers: document.detected_headers,
    fingerprint: document.fingerprint,
    matched_schema_id: document.matched_schema_id,
  };
}

function buildApprovalResultFromDocument(document) {
  if (
    !document.matched_schema_id ||
    (document.status !== "approved" && document.status !== "extracted")
  ) {
    return null;
  }

  return {
    id: document.id,
    status: document.status,
    schema_id: document.matched_schema_id,
    matched_schema_id: document.matched_schema_id,
    fingerprint: document.fingerprint,
  };
}

function buildExtractionResultFromDocument(document) {
  if (!document.output_json) {
    return null;
  }

  return {
    id: document.id,
    status: document.status,
    schema_id: document.matched_schema_id,
    output_json: document.output_json,
  };
}

function shouldRestoreReviewScreen(document, detectionResult) {
  return (
    document.status === "needs_review" &&
    detectionResult?.source !== "schema" &&
    Boolean(detectionResult?.detected_headers?.header_structure)
  );
}

function getDocumentIdFromUrl() {
  const match = window.location.pathname.match(DOCUMENT_REVIEW_PATH_PATTERN);
  return match ? decodeURIComponent(match[1]) : "";
}

function setDocumentReviewUrl(documentId) {
  if (!documentId) {
    return;
  }

  const nextPath = `/documents/${encodeURIComponent(documentId)}/review`;
  if (window.location.pathname !== nextPath) {
    window.history.pushState(null, "", nextPath);
  }
}

function clearDocumentReviewUrl() {
  if (getDocumentIdFromUrl()) {
    window.history.pushState(null, "", "/");
  }
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
