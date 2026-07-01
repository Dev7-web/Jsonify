const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000";

async function parseApiResponse(response, fallbackMessage) {
  const contentType = response.headers.get("content-type") ?? "";
  const text = await response.text();
  const isJson = contentType.includes("application/json");
  let data = null;

  if (text && isJson) {
    try {
      data = JSON.parse(text);
    } catch {
      throw new Error("Backend returned invalid JSON.");
    }
  } else if (text) {
    const trimmed = text.trimStart().toLowerCase();
    data = {
      detail:
        trimmed.startsWith("<!doctype") || trimmed.startsWith("<html")
          ? "Server returned an HTML timeout response. Detection may still be running; refresh status."
          : text,
    };
  } else {
    data = {};
  }

  if (!response.ok) {
    throw new Error(data?.detail ?? fallbackMessage);
  }

  return data;
}

export async function getHealth() {
  const response = await fetch(`${API_BASE_URL}/health`);

  if (!response.ok) {
    throw new Error(`Health check failed with status ${response.status}`);
  }

  return parseApiResponse(response, `Health check failed with status ${response.status}`);
}

export async function uploadDocument(file) {
  const formData = new FormData();
  formData.append("file", file);

  const response = await fetch(`${API_BASE_URL}/documents`, {
    method: "POST",
    body: formData,
  });

  return parseApiResponse(response, `Upload failed with status ${response.status}`);
}

export async function getDocument(documentId) {
  const response = await fetch(`${API_BASE_URL}/documents/${documentId}`);

  return parseApiResponse(response, `Document fetch failed with status ${response.status}`);
}

export async function detectDocumentHeaders(documentId) {
  const response = await fetch(`${API_BASE_URL}/documents/${documentId}/detect-headers`, {
    method: "POST",
  });

  return parseApiResponse(
    response,
    `Header detection failed with status ${response.status}`,
  );
}

export async function approveDocumentHeaders(documentId, payload) {
  const response = await fetch(`${API_BASE_URL}/documents/${documentId}/approve-headers`, {
    body: JSON.stringify(payload),
    headers: {
      "Content-Type": "application/json",
    },
    method: "POST",
  });

  return parseApiResponse(
    response,
    `Header approval failed with status ${response.status}`,
  );
}

export async function saveDocumentReviewDraft(documentId, payload) {
  const response = await fetch(`${API_BASE_URL}/documents/${documentId}/review-draft`, {
    body: JSON.stringify(payload),
    headers: {
      "Content-Type": "application/json",
    },
    method: "PATCH",
  });

  return parseApiResponse(
    response,
    `Review draft save failed with status ${response.status}`,
  );
}

export async function extractDocumentJson(documentId) {
  const response = await fetch(`${API_BASE_URL}/documents/${documentId}/extract`, {
    method: "POST",
  });

  return parseApiResponse(
    response,
    `JSON extraction failed with status ${response.status}`,
  );
}

export async function getDocumentJson(documentId) {
  const response = await fetch(`${API_BASE_URL}/documents/${documentId}/json`);

  return parseApiResponse(response, `JSON fetch failed with status ${response.status}`);
}

export function createDetectionEventSource(eventUrl) {
  const url = eventUrl.startsWith("http") ? eventUrl : `${API_BASE_URL}${eventUrl}`;
  return new EventSource(url);
}
