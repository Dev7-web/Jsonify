import { useEffect, useState } from "react";
import { getHealth, uploadDocument } from "./api";
import "./styles.css";

export default function App() {
  const [backendStatus, setBackendStatus] = useState("checking");
  const [selectedFile, setSelectedFile] = useState(null);
  const [uploadResult, setUploadResult] = useState(null);
  const [uploadError, setUploadError] = useState("");
  const [isUploading, setIsUploading] = useState(false);

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
    } catch (error) {
      setUploadError(error.message);
    } finally {
      setIsUploading(false);
    }
  }

  return (
    <main className="app-shell">
      <section className="status-panel" aria-labelledby="app-title">
        <p className="eyebrow">Document JSON Extractor</p>
        <h1 id="app-title">Upload document</h1>
        <p className="status-line">backend: {backendStatus}</p>

        <form className="upload-form" onSubmit={handleUpload}>
          <label className="file-label" htmlFor="document-file">
            Document file
          </label>
          <input
            accept=".xlsx,.pdf"
            id="document-file"
            name="file"
            onChange={(event) => {
              setSelectedFile(event.target.files?.[0] ?? null);
              setUploadError("");
              setUploadResult(null);
            }}
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
      </section>
    </main>
  );
}
