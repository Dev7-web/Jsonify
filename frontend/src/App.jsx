import { useEffect, useMemo, useRef, useState } from "react";
import { Navigate, Route, Routes, useNavigate, useParams } from "react-router-dom";
import {
  approveDocumentHeaders,
  createDetectionEventSource,
  detectDocumentHeaders,
  extractDocumentJson,
  getDocument,
  getDocumentJson,
  getHealth,
  saveDocumentReviewDraft,
  uploadDocument,
  uploadSupportingFile,
  deleteSupportingFile,
  runVerification,
  getVerificationReport,
} from "./api";
import HeaderEditor from "./components/HeaderEditor";
import HeaderRow from "./components/HeaderRow";
import SchemaNamePanel from "./components/SchemaNamePanel";
import SectionList from "./components/SectionList";
import StatusChip from "./components/StatusChip";
import "./styles.css";


const DEFAULT_SCHEMA_NAME = "New layout schema";
const DRAFT_AUTOSAVE_DELAY_MS = 700;
const DETECTION_POLL_INTERVAL_MS = 3000;
const DETECTION_TERMINAL_STATUSES = new Set([
  "needs_review",
  "approved",
  "extracted",
]);

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<DocumentWorkflow />} />
      <Route path="/documents/:documentId/review" element={<DocumentWorkflow />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

function DocumentWorkflow() {
  const { documentId: routeDocumentId = "" } = useParams();
  const navigate = useNavigate();
  const [backendStatus, setBackendStatus] = useState("checking");
  const [selectedFile, setSelectedFile] = useState(null);
  const [selectedSupportingFiles, setSelectedSupportingFiles] = useState([]);
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
  const [detectionProgress, setDetectionProgress] = useState(null);
  const [supportingFiles, setSupportingFiles] = useState([]);
  const [isUploadingSupporting, setIsUploadingSupporting] = useState(false);
  const [supportingUploadError, setSupportingUploadError] = useState("");
  const [verificationReport, setVerificationReport] = useState(null);
  const [isVerifying, setIsVerifying] = useState(false);
  const [verificationError, setVerificationError] = useState("");
  const detectionEventSourceRef = useRef(null);
  const detectionPollTimerRef = useRef(null);
  const draftSaveTimerRef = useRef(null);

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

  useEffect(() => {
    return () => {
      stopDetectionWatch();
      clearDraftSaveTimer();
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

    async function syncDocumentFromRoute() {
      if (!routeDocumentId) {
        if (isMounted) {
          setIsReviewLoaded(false);
          setRestoreError("");
          setIsRestoringDocument(false);
          stopDetectionWatch();
        }
        return;
      }

      setIsRestoringDocument(true);
      setRestoreError("");

      try {
        const document = await getDocument(routeDocumentId);
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

    syncDocumentFromRoute();

    return () => {
      isMounted = false;
    };
  }, [routeDocumentId]);

  const selectedSection = sections.find((section) => section.id === selectedSectionId);
  const selectedHeader = selectedSection?.headers.find(
    (header) => header.id === selectedHeaderId,
  );
  const reviewProgress = useMemo(() => getReviewProgress(sections), [sections]);
  const approvalPayload = useMemo(
    () => buildApprovalPayload(schemaName, sections),
    [schemaName, sections],
  );
  const reviewDraftPayload = useMemo(() => {
    if (!isReviewLoaded || !uploadResult?.id || sections.length === 0) {
      return null;
    }

    return {
      schema_name: schemaName.trim() || DEFAULT_SCHEMA_NAME,
      header_structure: approvalPayload.header_structure,
      review_state: {
        sections,
      },
    };
  }, [approvalPayload, isReviewLoaded, schemaName, sections, uploadResult?.id]);

  useEffect(() => {
    clearDraftSaveTimer();

    if (
      !reviewDraftPayload ||
      !uploadResult?.id ||
      activeDocument.status !== "needs_review"
    ) {
      return;
    }

    draftSaveTimerRef.current = window.setTimeout(() => {
      saveDocumentReviewDraft(uploadResult.id, reviewDraftPayload).catch((error) => {
        setRestoreError(error.message);
      });
    }, DRAFT_AUTOSAVE_DELAY_MS);

    return () => {
      clearDraftSaveTimer();
    };
  }, [activeDocument.status, reviewDraftPayload, uploadResult?.id]);

  async function handleUpload(event) {
    event.preventDefault();

    if (!selectedFile) {
      return;
    }

    setIsUploading(true);
    setUploadError("");
    setUploadResult(null);
    setDetectionError("");
    setDetectionResult(null);
    setDetectionProgress(null);
    setApprovalError("");
    setApprovalResult(null);
    setExtractionError("");
    setExtractionResult(null);
    setSchemaName(getDefaultSchemaName(selectedFile.name));
    setSections([]);
    setSelectedSectionId("");
    setSelectedHeaderId("");
    setIsReviewLoaded(false);
    setSupportingFiles([]);
    setSupportingUploadError("");
    setVerificationReport(null);
    setVerificationError("");

    try {
      const result = await uploadDocument(selectedFile, selectedSupportingFiles);
      setUploadResult(result);
      navigate(getDocumentReviewPath(result.id));
      
      await loadReview(result.id, result.file_type);
    } catch (error) {
      setUploadError(error.message);
    } finally {
      setIsUploading(false);
    }
  }

  async function handleSupportingUpload(event) {
    const file = event.target.files?.[0];
    if (!file || !uploadResult?.id) return;

    setIsUploadingSupporting(true);
    setSupportingUploadError("");

    try {
      const updatedFiles = await uploadSupportingFile(uploadResult.id, file);
      setSupportingFiles(updatedFiles);
    } catch (error) {
      setSupportingUploadError(error.message);
    } finally {
      setIsUploadingSupporting(false);
      event.target.value = "";
    }
  }

  async function handleSupportingDelete(fileId) {
    if (!uploadResult?.id) return;

    try {
      const updatedFiles = await deleteSupportingFile(uploadResult.id, fileId);
      setSupportingFiles(updatedFiles);
    } catch (error) {
      setSupportingUploadError(error.message);
    }
  }

  async function handleVerify() {
    if (!uploadResult?.id) return;

    setIsVerifying(true);
    setVerificationError("");

    try {
      const response = await runVerification(uploadResult.id);
      setVerificationReport(response.verification_report);
      setUploadResult((curr) => (curr ? { ...curr, status: response.status } : curr));
    } catch (error) {
      setVerificationError(error.message);
    } finally {
      setIsVerifying(false);
    }
  }


  async function loadReview(overrideId, overrideFileType) {
    const docId = overrideId || uploadResult?.id;
    const fileType = overrideFileType || uploadResult?.file_type;
    
    if (!docId) {
      setDetectionError("Upload an .xlsx document before loading detected headers.");
      return;
    }

    if (fileType !== "xlsx") {
      setDetectionError("Header review is currently wired for .xlsx documents only.");
      return;
    }

    setIsDetecting(true);
    setDetectionProgress({
      percent: 0,
      stage: "queued",
      message: "Header detection queued.",
    });
    setDetectionError("");
    setExtractionError("");
    setExtractionResult(null);

    try {
      const result = await detectDocumentHeaders(docId);
      navigate(getDocumentReviewPath(docId));
      setApprovalError("");
      setApprovalResult(null);
      setUploadResult((currentResult) =>
        currentResult ? { ...currentResult, status: result.status } : currentResult,
      );

      if (result.status === "detecting") {
        setDetectionProgress(
          result.detection_progress ?? {
            percent: 0,
            stage: "detecting",
            message: "Header detection is running.",
          },
        );
        const completedDocument = await waitForDetectionCompletion(
          docId,
          result.event_url,
        );
        applyCompletedDetectionDocument(completedDocument);
        return;
      }

      applyCompletedDetectionResult(result);
    } catch (error) {
      setDetectionError(error.message);
    } finally {
      stopDetectionWatch();
      setIsDetecting(false);
    }
  }

  function applyCompletedDetectionResult(result) {
    setDetectionResult(result);
    setDetectionProgress(null);
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
      
      extractJson(result.id);
      
      getDocument(result.id).then((doc) => {
        setSupportingFiles(doc.supporting_files ?? []);
      }).catch(console.error);
      
      return;
    }

    const nextSections = buildSectionsFromDetection(result.detected_headers);
    setSections(nextSections);
    setSelectedSectionId(nextSections[0]?.id ?? "");
    setSelectedHeaderId(nextSections[0]?.headers[0]?.id ?? "");
    setIsReviewLoaded(true);
  }

  function applyCompletedDetectionDocument(document) {
    if (document.status === "failed") {
      throw new Error(document.failure_reason || "Header detection failed.");
    }

    const storedDetectionResult = buildDetectionResultFromDocument(document);
    if (!storedDetectionResult) {
      throw new Error("Header detection finished without detected headers.");
    }

    setUploadResult(buildUploadResultFromDocument(document));
    applyCompletedDetectionResult(storedDetectionResult);
  }

  function waitForDetectionCompletion(documentId, eventUrl) {
    return new Promise((resolve, reject) => {
      let settled = false;

      const settle = (callback, value) => {
        if (settled) {
          return;
        }

        settled = true;
        stopDetectionWatch();
        callback(value);
      };

      const resolveFromDocument = async () => {
        try {
          const document = await getDocument(documentId);
          if (document.detection_progress) {
            setDetectionProgress(document.detection_progress);
          }

          if (document.status === "failed") {
            settle(
              reject,
              new Error(document.failure_reason || "Header detection failed."),
            );
            return true;
          }

          if (DETECTION_TERMINAL_STATUSES.has(document.status)) {
            settle(resolve, document);
            return true;
          }

          return false;
        } catch (error) {
          settle(reject, error);
          return true;
        }
      };

      const startPolling = () => {
        if (settled) {
          return;
        }

        if (detectionPollTimerRef.current) {
          return;
        }

        detectionPollTimerRef.current = window.setInterval(() => {
          resolveFromDocument();
        }, DETECTION_POLL_INTERVAL_MS);
        resolveFromDocument();
      };

      if (eventUrl && typeof EventSource !== "undefined") {
        try {
          const eventSource = createDetectionEventSource(eventUrl);
          detectionEventSourceRef.current = eventSource;

          eventSource.addEventListener("progress", (event) => {
            setDetectionProgress(parseDetectionEventData(event.data));
          });
          eventSource.addEventListener("complete", (event) => {
            setDetectionProgress(parseDetectionEventData(event.data));
            resolveFromDocument();
          });
          eventSource.addEventListener("failed", (event) => {
            const data = parseDetectionEventData(event.data);
            setDetectionProgress(data);
            settle(
              reject,
              new Error(data.failure_reason || data.message || "Header detection failed."),
            );
          });
          eventSource.onerror = () => {
            if (settled) {
              return;
            }

            if (detectionEventSourceRef.current) {
              detectionEventSourceRef.current.close();
              detectionEventSourceRef.current = null;
            }
            startPolling();
          };
        } catch {
          startPolling();
        }
      } else {
        startPolling();
      }
    });
  }

  function stopDetectionWatch() {
    if (detectionEventSourceRef.current) {
      detectionEventSourceRef.current.close();
      detectionEventSourceRef.current = null;
    }

    if (detectionPollTimerRef.current) {
      window.clearInterval(detectionPollTimerRef.current);
      detectionPollTimerRef.current = null;
    }
  }

  function clearDraftSaveTimer() {
    if (draftSaveTimerRef.current) {
      window.clearTimeout(draftSaveTimerRef.current);
      draftSaveTimerRef.current = null;
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

  async function extractJson(overrideId = null) {
    const targetId = typeof overrideId === "string" ? overrideId : uploadResult?.id;
    if (!targetId) {
      setExtractionError("Upload and approve an .xlsx document before extraction.");
      return;
    }

    if (!overrideId && !canExtractDocument(activeDocument)) {
      setExtractionError("Approve headers before extracting the final JSON.");
      return;
    }

    setIsExtracting(true);
    setExtractionError("");

    try {
      const result = await extractDocumentJson(targetId);
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
    const storedDraft = buildReviewDraftFromDocument(document);
    const restoredSections = shouldShowReview
      ? (storedDraft?.sections ?? buildSectionsFromDetection(storedDetectionResult.detected_headers))
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
    setSchemaName(storedDraft?.schemaName ?? getDefaultSchemaName(document.filename));
    setSections(restoredSections);
    setSelectedSectionId(restoredSections[0]?.id ?? "");
    setSelectedHeaderId(restoredSections[0]?.headers[0]?.id ?? "");
    setIsReviewLoaded(shouldShowReview && restoredSections.length > 0);
    setSupportingFiles(document.supporting_files ?? []);
    setVerificationReport(document.verification_report ?? null);

    if (document.status === "detecting") {
      setIsDetecting(true);
      setDetectionProgress(document.detection_progress ?? null);
      waitForDetectionCompletion(
        document.id,
        getDetectionEventUrl(document.id),
      )
        .then((completedDocument) => {
          applyCompletedDetectionDocument(completedDocument);
        })
        .catch((error) => {
          setDetectionError(error.message);
        })
        .finally(() => {
          stopDetectionWatch();
          setIsDetecting(false);
        });
    }
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
      sheetName: selectedSection?.sheetName ?? sections[0]?.sheetName ?? "Workbook",
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
          detectionProgress={detectionProgress}
          isDetecting={isDetecting}
          restoreError={restoreError}
          onLoadReview={loadReview}
          onSelectedFileChange={(file) => {
            setSelectedFile(file);
            navigate("/");
            setUploadError("");
            setUploadResult(null);
            setDetectionError("");
            setDetectionResult(null);
            setDetectionProgress(null);
            stopDetectionWatch();
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
          selectedSupportingFiles={selectedSupportingFiles}
          onSelectedSupportingFilesChange={setSelectedSupportingFiles}
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
            navigate("/");
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
      
      {uploadResult && (
        <VerificationPanel
          documentId={uploadResult.id}
          documentStatus={activeDocument.status}
          hasJson={Boolean(extractionResult?.output_json)}
          supportingFiles={supportingFiles}
          isUploadingSupporting={isUploadingSupporting}
          supportingUploadError={supportingUploadError}
          verificationReport={verificationReport}
          isVerifying={isVerifying}
          verificationError={verificationError}
          onSupportingUpload={handleSupportingUpload}
          onSupportingDelete={handleSupportingDelete}
          onVerify={handleVerify}
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
  detectionProgress,
  isDetecting,
  restoreError,
  isDownloadingJson,
  isExtracting,
  onDownloadJson,
  onExtractJson,
  onSelectedFileChange,
  selectedSupportingFiles,
  onSelectedSupportingFilesChange,
  onSubmit,
  onLoadReview,
}) {
  return (
    <>
      <section className="status-panel" aria-labelledby="app-title">
      <p className="eyebrow">Document JSON Extractor</p>
      <h1 id="app-title">Upload document</h1>
      <p className="status-line">backend: {backendStatus}</p>

      <form className="upload-form" onSubmit={onSubmit}>
        <div className="upload-form-group">
          <label className="file-label" htmlFor="document-file">
            Excel Document (Required)
          </label>
          <input
            accept=".xlsx"
            id="document-file"
            name="file"
            onChange={(event) => onSelectedFileChange(event.target.files?.[0] ?? null)}
            type="file"
            required
          />
        </div>
        <div className="upload-form-group">
          <label className="file-label" htmlFor="supporting-documents">
            Supporting PDFs (Optional)
          </label>
          <input
            accept=".pdf"
            id="supporting-documents"
            name="supporting_files"
            multiple
            onChange={(event) => {
              if (onSelectedSupportingFilesChange) {
                onSelectedSupportingFilesChange(Array.from(event.target.files));
              }
            }}
            type="file"
          />
        </div>
        <button disabled={!selectedFile || isUploading} type="submit">
          {isUploading ? "Uploading & Processing..." : "Upload & Process"}
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
        {isDetecting && detectionProgress ? (
          <DetectionProgress progress={detectionProgress} />
        ) : null}
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
    </>
  );
}

function DetectionProgress() {
  return (
    <div className="detection-loader" aria-live="polite" role="status">
      <span className="loader-spinner" aria-hidden="true" />
      <span>Detecting headers...</span>
    </div>
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
              {selectedSection?.sheetName ? (
                <p className="selected-sheet-label">{selectedSection.sheetName}</p>
              ) : null}
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
            ...(section.type === "matrix" && section.rowHeader
              ? { row_header: section.rowHeader }
              : {}),
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

function buildReviewDraftFromDocument(document) {
  const draft = document.review_draft;
  const sections = draft?.review_state?.sections;
  const schemaName = draft?.schema_name;

  if (!Array.isArray(sections)) {
    return null;
  }

  return {
    schemaName: typeof schemaName === "string" && schemaName.trim()
      ? schemaName
      : getDefaultSchemaName(document.filename),
    sections,
  };
}

function shouldRestoreReviewScreen(document, detectionResult) {
  return (
    document.status === "needs_review" &&
    detectionResult?.source !== "schema" &&
    Boolean(detectionResult?.detected_headers?.header_structure)
  );
}

function getDocumentReviewPath(documentId) {
  return `/documents/${encodeURIComponent(documentId)}/review`;
}

function getDetectionEventUrl(documentId) {
  return `/documents/${encodeURIComponent(documentId)}/detect-headers/events`;
}

function parseDetectionEventData(rawData) {
  try {
    return JSON.parse(rawData);
  } catch {
    return {
      percent: 0,
      stage: "unknown",
      message: "Waiting for detection status...",
    };
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
        rowHeader: section.row_header ?? "",
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

function VerificationPanel({
  documentId,
  documentStatus,
  hasJson,
  supportingFiles,
  isUploadingSupporting,
  supportingUploadError,
  verificationReport,
  isVerifying,
  verificationError,
  onSupportingUpload,
  onSupportingDelete,
  onVerify,
}) {
  const [statusFilter, setStatusFilter] = useState("all");
  const [searchTerm, setSearchTerm] = useState("");

  const canVerify = hasJson && supportingFiles.length > 0;

  const filteredFields = useMemo(() => {
    if (!verificationReport?.fields) return [];
    return verificationReport.fields.filter((field) => {
      const matchesFilter = statusFilter === "all" || field.status === statusFilter;
      const matchesSearch =
        field.field_path.toLowerCase().includes(searchTerm.toLowerCase()) ||
        String(field.extracted_value).toLowerCase().includes(searchTerm.toLowerCase()) ||
        String(field.found_value || "").toLowerCase().includes(searchTerm.toLowerCase()) ||
        String(field.citation || "").toLowerCase().includes(searchTerm.toLowerCase());
      return matchesFilter && matchesSearch;
    });
  }, [verificationReport, statusFilter, searchTerm]);

  return (
    <section className="panel verification-panel" aria-labelledby="verification-title">
      <div className="panel-heading panel-heading-row">
        <div>
          <p className="eyebrow">Verification</p>
          <h2 id="verification-title">Supporting Documents & Verification</h2>
        </div>
      </div>

      <div className="verification-layout">
        {/* Left Column: Upload and Trigger */}
        <div className="verification-sidebar">
          <h3>Supporting Documents (PDFs)</h3>
          <p className="panel-copy">
            Upload PDF files (such as invoices, purchase orders, or contracts) to verify the extracted Excel data.
          </p>

          <div className="supporting-upload-zone">
            <input
              type="file"
              accept=".pdf"
              id="supporting-file-input"
              style={{ display: "none" }}
              onChange={onSupportingUpload}
              disabled={isUploadingSupporting}
            />
            <label htmlFor="supporting-file-input" className="button-secondary supporting-upload-btn">
              {isUploadingSupporting ? "Uploading..." : "Upload Supporting PDF"}
            </label>
            {supportingUploadError && <p className="error-message">{supportingUploadError}</p>}
          </div>

          <div className="supporting-files-list">
            {supportingFiles.length === 0 ? (
              <p className="empty-state-mini">No supporting PDFs uploaded yet.</p>
            ) : (
              <ul>
                {supportingFiles.map((file) => (
                  <li key={file.id} className="supporting-file-item">
                    <span className="file-name" title={file.filename}>
                      📄 {file.filename}
                    </span>
                    <button
                      type="button"
                      className="delete-file-btn"
                      onClick={() => onSupportingDelete(file.id)}
                      title="Remove document"
                    >
                      ✕
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>

          <div className="verification-trigger-zone">
            <button
              type="button"
              className="primary-button verify-btn"
              disabled={!canVerify || isVerifying}
              onClick={onVerify}
            >
              {isVerifying ? "Verifying Extracted Data..." : "Verify Extracted Data"}
            </button>

            {!hasJson && (
              <p className="helper-text warning">⚠️ You must extract JSON from the Excel document first.</p>
            )}
            {hasJson && supportingFiles.length === 0 && (
              <p className="helper-text info">ℹ️ Upload at least one supporting PDF to enable verification.</p>
            )}
            {verificationError && <p className="error-message">{verificationError}</p>}
          </div>
        </div>

        {/* Right Column: Verification Results */}
        <div className="verification-content">
          {isVerifying ? (
            <div className="verification-loader-container">
              <span className="loader-spinner" aria-hidden="true" />
              <p>Analyzing supporting PDFs and verifying extracted JSON values using Gemini...</p>
            </div>
          ) : verificationReport ? (
            <div className="verification-results">
              {/* Summary Cards */}
              <div className="verification-summary-card">
                <div className="metric-container">
                  <div className="metric-ring">
                    <span className="metric-value">{Math.round(verificationReport.summary?.percentage ?? 0)}%</span>
                    <span className="metric-label">Verified</span>
                  </div>
                  <div className="metric-stats">
                    <div className="stat-row">
                      <span className="badge status-verified">Verified</span>
                      <span className="stat-val">{verificationReport.summary?.verified_fields ?? 0} fields</span>
                    </div>
                    <div className="stat-row">
                      <span className="badge status-mismatch">Mismatch</span>
                      <span className="stat-val">{verificationReport.summary?.mismatched_fields ?? 0} fields</span>
                    </div>
                    <div className="stat-row">
                      <span className="badge status-not_found">Not Found</span>
                      <span className="stat-val">{verificationReport.summary?.not_found_fields ?? 0} fields</span>
                    </div>
                  </div>
                </div>
                <div className="summary-text-block">
                  <h4>Verification Overview</h4>
                  <p>{verificationReport.summary?.textual_summary}</p>
                </div>
              </div>

              {/* Filters & Search */}
              <div className="results-toolbar">
                <div className="filter-buttons">
                  <button
                    className={`filter-btn ${statusFilter === "all" ? "active" : ""}`}
                    onClick={() => setStatusFilter("all")}
                  >
                    All ({verificationReport.summary?.total_fields ?? 0})
                  </button>
                  <button
                    className={`filter-btn ${statusFilter === "verified" ? "active" : ""}`}
                    onClick={() => setStatusFilter("verified")}
                  >
                    Verified ({verificationReport.summary?.verified_fields ?? 0})
                  </button>
                  <button
                    className={`filter-btn ${statusFilter === "mismatch" ? "active" : ""}`}
                    onClick={() => setStatusFilter("mismatch")}
                  >
                    Mismatch ({verificationReport.summary?.mismatched_fields ?? 0})
                  </button>
                  <button
                    className={`filter-btn ${statusFilter === "not_found" ? "active" : ""}`}
                    onClick={() => setStatusFilter("not_found")}
                  >
                    Not Found ({verificationReport.summary?.not_found_fields ?? 0})
                  </button>
                </div>
                <input
                  type="text"
                  placeholder="Search verified fields..."
                  value={searchTerm}
                  onChange={(e) => setSearchTerm(e.target.value)}
                  className="search-input"
                />
              </div>

              {/* Table of Fields */}
              <div className="verification-table-container">
                {filteredFields.length === 0 ? (
                  <p className="empty-results">No fields match the selected filter/search criteria.</p>
                ) : (
                  <table className="verification-table">
                    <thead>
                      <tr>
                        <th>Field Path</th>
                        <th>Extracted Value (Excel)</th>
                        <th>Status</th>
                        <th>Found Value (PDF)</th>
                        <th>Citation & Context</th>
                      </tr>
                    </thead>
                    <tbody>
                      {filteredFields.map((field, idx) => (
                        <tr key={idx} className={`row-status-${field.status}`}>
                          <td className="field-path"><code>{field.field_path}</code></td>
                          <td className="field-value">
                            {typeof field.extracted_value === "object" && field.extracted_value !== null
                              ? JSON.stringify(field.extracted_value)
                              : String(field.extracted_value ?? "")}
                          </td>
                          <td>
                            <span className={`badge status-${field.status}`}>{field.status}</span>
                          </td>
                          <td className="field-value font-mismatch">
                            {field.status === "mismatch"
                              ? (typeof field.found_value === "object" && field.found_value !== null
                                ? JSON.stringify(field.found_value)
                                : String(field.found_value ?? ""))
                              : field.status === "verified"
                              ? "—"
                              : "Not found"}
                          </td>
                          <td className="field-citation">
                            {field.citation ? (
                              <span title={field.citation}>{field.citation}</span>
                            ) : (
                              <span className="muted">—</span>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            </div>
          ) : (
            <div className="verification-empty-prompt">
              <div className="prompt-content">
                <span className="prompt-icon">🔍</span>
                <h4>Ready for Verification</h4>
                <p>Upload supporting PDF documents and click "Verify Extracted Data" to run verification.</p>
              </div>
            </div>
          )}
        </div>
      </div>
    </section>
  );
}

