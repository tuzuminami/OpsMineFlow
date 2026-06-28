import type {
  AppSwitching,
  AppSettings,
  AutomationCandidate,
  AutomationReviewStatus,
  CsvMapping,
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
  RecordingStatus,
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
  const [health, diagnostics, recording, settings, importHistory, events, summary, processMap, candidates, appSwitching, report] = await Promise.all([
    getJson<Health>("/health"),
    getJson<Diagnostics>("/diagnostics"),
    getJson<RecordingStatus>("/recording/status"),
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
    recording,
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

export async function startRecording(caseId: string, activityLabel: string) {
  return postJson<RecordingStatus>("/recording/start", { case_id: caseId, activity_label: activityLabel, consent: true });
}

export async function stopRecording() {
  return postJson<RecordingStatus>("/recording/stop");
}

export type ImportResult = {
  imported_events: number;
  source?: string;
  message?: string;
};

function importPayload(path: string, mapping?: CsvMapping, dateFormat = "", timezone = "UTC") {
  const payload: { path: string; mapping?: CsvMapping; date_format?: string; timezone?: string } = { path };
  if (mapping && Object.values(mapping).some((value) => value.trim())) payload.mapping = mapping;
  if (dateFormat.trim()) payload.date_format = dateFormat;
  if (timezone.trim()) payload.timezone = timezone;
  return payload;
}

export async function importEvents(format: "csv" | "json", path: string, mapping?: CsvMapping, dateFormat = "", timezone = "UTC") {
  return postJson<ImportResult>(`/import/${format}`, importPayload(path, format === "csv" ? mapping : undefined, dateFormat, timezone));
}

export async function previewImport(format: "csv" | "json", path: string, mapping?: CsvMapping, dateFormat = "", timezone = "UTC") {
  return postJson<ImportPreview>("/import/preview", {
    format,
    ...importPayload(path, format === "csv" ? mapping : undefined, dateFormat, timezone)
  });
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

export async function updateEventActivity(eventId: string, activity: string) {
  return postJson<{ event: EventRecord }>("/events/activity", { event_id: eventId, activity });
}

export async function excludeEvent(eventId: string) {
  return postJson<{ excluded: boolean; event_id: string }>("/events/exclude", { event_id: eventId });
}

export async function splitEvent(eventId: string, splitAfterSeconds: number, firstActivity = "", secondActivity = "") {
  return postJson<{ split: boolean; events: EventRecord[] }>("/events/split", {
    event_id: eventId,
    split_after_seconds: splitAfterSeconds,
    first_activity: firstActivity,
    second_activity: secondActivity
  });
}

export async function mergeEvents(firstEventId: string, secondEventId: string, activity = "") {
  return postJson<{ merged: boolean; event: EventRecord }>("/events/merge", {
    first_event_id: firstEventId,
    second_event_id: secondEventId,
    activity
  });
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
