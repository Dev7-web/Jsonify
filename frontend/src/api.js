const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000";

export async function getHealth() {
  const response = await fetch(`${API_BASE_URL}/health`);

  if (!response.ok) {
    throw new Error(`Health check failed with status ${response.status}`);
  }

  return response.json();
}

export async function uploadDocument(file) {
  const formData = new FormData();
  formData.append("file", file);

  const response = await fetch(`${API_BASE_URL}/documents`, {
    method: "POST",
    body: formData,
  });

  const data = await response.json();

  if (!response.ok) {
    throw new Error(data.detail ?? `Upload failed with status ${response.status}`);
  }

  return data;
}

export async function detectDocumentHeaders(documentId) {
  const response = await fetch(`${API_BASE_URL}/documents/${documentId}/detect-headers`, {
    method: "POST",
  });

  const data = await response.json();

  if (!response.ok) {
    throw new Error(
      data.detail ?? `Header detection failed with status ${response.status}`,
    );
  }

  return data;
}
