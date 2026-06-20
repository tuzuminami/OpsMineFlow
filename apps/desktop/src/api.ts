import type {
  AppSwitching,
  AppSettings,
  AutomationCandidate,
  AutomationReviewStatus,
  DiagnosticChecks,
  Diagnostics,
  EventRecord,
  ExportFormat,
  ExportPreview,
  ExportSaveResult,
  Health,
  ImportHistoryEntry,
  ImportPreview,
  ProcessMap,
  Summary
} from "./types";

const API_BASE = import.meta.env.VITE_API_BASE ?? "http://127.0.0.1:8765";

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`);
  if (!response.ok) {
    throw new Error(`Local API returned ${response.status}`);
  }
  return response.json() as Promise<T>;
}

async function postJson<T>(path: string, payload: unknown = {}): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload)
  });
  if (!response.ok) {
    let message = `Local API returned ${response.status}`;
    try {
      const errorPayload = (await response.json()) as { error?: string; detail?: string };
      message = errorPayload.error || errorPayload.detail || message;
    } catch {
      // Keep the status-based message.
    }
    throw new Error(message);
  }
  return response.json() as Promise<T>;
}

export async function loadDashboardData() {
  const [health, diagnostics, settings, importHistory, events, summary, processMap, candidates, appSwitching, report] = await Promise.all([
    getJson<Health>("/health"),
    getJson<Diagnostics>("/diagnostics"),
    getJson<AppSettings>("/settings"),
    getJson<ImportHistoryEntry[]>("/import/history"),
    getJson<EventRecord[]>("/events"),
    getJson<Summary>("/analytics/summary"),
    getJson<ProcessMap>("/analytics/process-map"),
    getJson<AutomationCandidate[]>("/analytics/automation-candidates"),
    getJson<AppSwitching>("/analytics/app-switching"),
    getJson<{ markdown: string }>("/reports/markdown")
  ]);

  return {
    health,
    diagnostics,
    settings,
    importHistory,
    events,
    summary,
    processMap,
    candidates,
    appSwitching,
    markdown: report.markdown
  };
}

export type ImportResult = {
  imported_events: number;
  source?: string;
  message?: string;
};

export async function importEvents(format: "csv" | "json", path: string) {
  return postJson<ImportResult>(`/import/${format}`, { path });
}

export async function previewImport(format: "csv" | "json", path: string) {
  return postJson<ImportPreview>("/import/preview", { format, path });
}

export async function importActivityWatchLocal(enabled: boolean) {
  return postJson<ImportResult>("/import/activitywatch-local", { enabled, base_url: "http://127.0.0.1:5600" });
}

export async function saveSettings(settings: Partial<AppSettings>) {
  return postJson<AppSettings>("/settings", settings);
}

export async function runDiagnosticChecks() {
  return postJson<DiagnosticChecks>("/diagnostics/checks");
}

export async function saveAutomationReview(activity: string, status: AutomationReviewStatus) {
  return postJson<{ activity: string; review_status: AutomationReviewStatus }>("/automation/review", { activity, status });
}

export async function deleteLocalData() {
  return postJson<{ deleted: boolean }>("/data/delete");
}

export async function exportArtifact(format: ExportFormat) {
  if (format === "markdown") return getJson<{ markdown: string }>("/reports/markdown");
  if (format === "json") return postJson<{ json: string }>("/export/json");
  if (format === "csv") return postJson<{ csv: string }>("/export/csv");
  if (format === "mermaid") return postJson<{ mermaid: string }>("/export/mermaid");
  return postJson<{ drawio: string }>("/export/drawio");
}

export async function previewExport(format: ExportFormat) {
  return postJson<ExportPreview>("/export/preview", { format });
}

export async function saveExport(format: ExportFormat, path: string) {
  return postJson<ExportSaveResult>("/export/save", { format, path });
}
