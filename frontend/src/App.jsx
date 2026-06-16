import { useEffect, useState } from "react";
import { getHealth } from "./api";
import "./styles.css";

export default function App() {
  const [backendStatus, setBackendStatus] = useState("checking");

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

  return (
    <main className="app-shell">
      <section className="status-panel" aria-labelledby="app-title">
        <p className="eyebrow">Document JSON Extractor</p>
        <h1 id="app-title">Backend connection</h1>
        <p className="status-line">backend: {backendStatus}</p>
      </section>
    </main>
  );
}
