import type {
  AppSwitching,
  AppSettings,
  ActivityWatchImportMode,
  ActivityWatchPreview,
  AutomationCandidate,
  AutomationReviewStatus,
  CsvMapping,
  DiagnosticChecks,
  Diagnostics,
  EventPage,
  EventRecord,
  EventQualityReport,
  ExportFormat,
  ExportPreview,
  ExportSaveResult,
  Health,
  ImportHistoryEntry,
  ImportPreview,
  ProcessMap,
  RecordingStatus,
  RuntimeStatus,
  Summary
} from "./types";
import { invoke, isTauri } from "@tauri-apps/api/core";

const DEV_API_BASE = import.meta.env.VITE_API_BASE ?? "http://127.0.0.1:8765";
const DIRECT_DEVELOPMENT_API = import.meta.env.DEV && isApprovedDevelopmentApiBase(import.meta.env.VITE_API_BASE);

function isApprovedDevelopmentApiBase(value: string | undefined): boolean {
  if (!value) return false;
  try {
    const url = new URL(value);
    return url.protocol === "http:" && (url.hostname === "127.0.0.1" || url.hostname === "localhost") && Boolean(url.port);
  } catch {
    return false;
  }
}

const DEVELOPMENT_ROUTES: Record<string, { method: "GET" | "POST"; path: string }> = {
  health: { method: "GET", path: "/health" },
  diagnostics: { method: "GET", path: "/diagnostics" },
  recording_status: { method: "GET", path: "/recording/status" },
  settings: { method: "GET", path: "/settings" },
  import_history: { method: "GET", path: "/import/history" },
  events_page: { method: "POST", path: "/events/page" },
  event_quality: { method: "GET", path: "/analytics/event-quality" },
  summary: { method: "GET", path: "/analytics/summary" },
  process_map: { method: "GET", path: "/analytics/process-map" },
  automation_candidates: { method: "GET", path: "/analytics/automation-candidates" },
  app_switching: { method: "GET", path: "/analytics/app-switching" },
  report_markdown: { method: "GET", path: "/reports/markdown" },
  diagnostics_checks: { method: "POST", path: "/diagnostics/checks" },
  recording_start: { method: "POST", path: "/recording/start" },
  recording_stop: { method: "POST", path: "/recording/stop" },
  recording_pause: { method: "POST", path: "/recording/pause" },
  recording_resume: { method: "POST", path: "/recording/resume" },
  import_preview: { method: "POST", path: "/import/preview" },
  import_csv: { method: "POST", path: "/import/csv" },
  import_json: { method: "POST", path: "/import/json" },
  activitywatch_preview: { method: "POST", path: "/import/activitywatch-preview" },
  activitywatch_import: { method: "POST", path: "/import/activitywatch-local" },
  settings_update: { method: "POST", path: "/settings" },
  automation_review: { method: "POST", path: "/automation/review" },
  event_activity: { method: "POST", path: "/events/activity" },
  event_exclude: { method: "POST", path: "/events/exclude" },
  event_quality_review: { method: "POST", path: "/events/quality-review" },
  event_split: { method: "POST", path: "/events/split" },
  event_merge: { method: "POST", path: "/events/merge" },
  delete_challenge: { method: "POST", path: "/data/delete/challenge" },
  delete_data: { method: "POST", path: "/data/delete" },
  export_mermaid: { method: "POST", path: "/export/mermaid" },
  export_drawio: { method: "POST", path: "/export/drawio" },
  export_csv: { method: "POST", path: "/export/csv" },
  export_json: { method: "POST", path: "/export/json" },
  export_llm_handoff: { method: "POST", path: "/export/llm-handoff" },
  export_preview: { method: "POST", path: "/export/preview" },
  export_save: { method: "POST", path: "/export/save" }
};

export async function getNativeRuntimeStatus(): Promise<RuntimeStatus | null> {
  if (!isTauri()) return null;
  return invoke<RuntimeStatus>("runtime_status");
}

export async function repairNativeRuntimeState(): Promise<RuntimeStatus | null> {
  if (!isTauri()) return null;
  return invoke<RuntimeStatus>("repair_runtime_state");
}

async function localApiOperation<T>(operation: string, payload?: unknown): Promise<T> {
  if (isTauri()) return invoke<T>("local_api_operation", { operation, payload: payload ?? null });
  if (!DIRECT_DEVELOPMENT_API) throw new Error("The packaged app requires its managed local runtime.");
  const route = DEVELOPMENT_ROUTES[operation];
  if (!route) throw new Error("Local API operation is not available in development.");
  const response = await fetch(`${DEV_API_BASE}${route.path}`, {
    method: route.method,
    headers: route.method === "POST" ? { "content-type": "application/json" } : undefined,
    body: route.method === "POST" ? JSON.stringify(payload ?? {}) : undefined
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

function getJson<T>(operation: string): Promise<T> {
  return localApiOperation<T>(operation);
}

function postJson<T>(operation: string, payload: unknown = {}): Promise<T> {
  return localApiOperation<T>(operation, payload);
}

export async function loadDashboardData() {
  const [health, diagnostics, recording, settings, importHistory, eventPage, quality, summary, processMap, candidates, appSwitching, report] = await Promise.all([
    getJson<Health>("health"),
    getJson<Diagnostics>("diagnostics"),
    getJson<RecordingStatus>("recording_status"),
    getJson<AppSettings>("settings"),
    getJson<ImportHistoryEntry[]>("import_history"),
    postJson<EventPage>("events_page", { offset: 0, limit: 500 }),
    getJson<EventQualityReport>("event_quality"),
    getJson<Summary>("summary"),
    getJson<ProcessMap>("process_map"),
    getJson<AutomationCandidate[]>("automation_candidates"),
    getJson<AppSwitching>("app_switching"),
    getJson<{ markdown: string }>("report_markdown")
  ]);

  return {
    health,
    diagnostics,
    recording,
    settings,
    importHistory,
    events: eventPage.events,
    eventTotal: eventPage.total,
    quality,
    summary,
    processMap,
    candidates,
    appSwitching,
    markdown: report.markdown
  };
}

export async function loadEventPage(offset: number, limit = 500) {
  return postJson<EventPage>("events_page", { offset, limit });
}

export async function getRecordingStatus() {
  return getJson<RecordingStatus>("recording_status");
}

export async function startRecording(caseId: string, activityLabel: string) {
  return postJson<RecordingStatus>("recording_start", { case_id: caseId, activity_label: activityLabel, consent: true });
}

export async function stopRecording() {
  return postJson<RecordingStatus>("recording_stop");
}

export async function pauseRecording(reason = "") {
  return postJson<RecordingStatus>("recording_pause", { reason });
}

export async function resumeRecording() {
  return postJson<RecordingStatus>("recording_resume");
}

export type ImportResult = {
  imported_events: number;
  source?: string;
  mode?: ActivityWatchImportMode;
  fetched_events?: number;
  skipped_duplicates?: number;
  excluded_events?: number;
  message?: string;
};

export type SelectedImportFile = {
  handle: string;
  display_name: string;
};

export function isManagedDesktop(): boolean {
  return isTauri();
}

export async function chooseImportFile(format: "csv" | "json"): Promise<SelectedImportFile> {
  if (!isTauri()) throw new Error("The browser development helper requires a direct test file path.");
  return invoke<SelectedImportFile>("choose_import_file", { format });
}

function importPayload(path: string, mapping?: CsvMapping, dateFormat = "", timezone = "UTC") {
  const payload: { path: string; mapping?: CsvMapping; date_format?: string; timezone?: string } = { path };
  if (mapping && Object.values(mapping).some((value) => value.trim())) payload.mapping = mapping;
  if (dateFormat.trim()) payload.date_format = dateFormat;
  if (timezone.trim()) payload.timezone = timezone;
  return payload;
}

export async function importEvents(format: "csv" | "json", path: string, mapping?: CsvMapping, dateFormat = "", timezone = "UTC") {
  if (isTauri()) {
    return invoke<ImportResult>("import_selected_file", {
      handle: path,
      payload: importPayload(path, format === "csv" ? mapping : undefined, dateFormat, timezone)
    });
  }
  return postJson<ImportResult>(`import_${format}`, importPayload(path, format === "csv" ? mapping : undefined, dateFormat, timezone));
}

export async function previewImport(format: "csv" | "json", path: string, mapping?: CsvMapping, dateFormat = "", timezone = "UTC") {
  if (isTauri()) {
    return invoke<ImportPreview>("preview_selected_import", {
      handle: path,
      payload: { format, ...importPayload(path, format === "csv" ? mapping : undefined, dateFormat, timezone) }
    });
  }
  return postJson<ImportPreview>("import_preview", {
    format,
    ...importPayload(path, format === "csv" ? mapping : undefined, dateFormat, timezone)
  });
}

export async function previewActivityWatchLocal(enabled: boolean) {
  return postJson<ActivityWatchPreview>("activitywatch_preview", { enabled, base_url: "http://127.0.0.1:5600" });
}

export async function importActivityWatchLocal(enabled: boolean, mode: ActivityWatchImportMode = "replace") {
  return postJson<ImportResult>("activitywatch_import", { enabled, mode, base_url: "http://127.0.0.1:5600" });
}

export async function saveSettings(settings: Partial<AppSettings>) {
  return postJson<AppSettings>("settings_update", settings);
}

export async function runDiagnosticChecks() {
  return postJson<DiagnosticChecks>("diagnostics_checks");
}

export async function saveAutomationReview(activity: string, status: AutomationReviewStatus, note = "") {
  return postJson<{ activity: string; review_status: AutomationReviewStatus; review_note: string }>("automation_review", { activity, status, note });
}

export async function updateEventActivity(eventId: string, activity: string) {
  return postJson<{ event: EventRecord }>("event_activity", { event_id: eventId, activity });
}

export async function excludeEvent(eventId: string) {
  return postJson<{ excluded: boolean; event_id: string }>("event_exclude", { event_id: eventId });
}

export async function approveEventQuality(eventId: string) {
  return postJson<{ event_id: string; quality_review_status: string }>("event_quality_review", { event_id: eventId, status: "approved" });
}

export async function splitEvent(eventId: string, splitAfterSeconds: number, firstActivity = "", secondActivity = "") {
  return postJson<{ split: boolean; events: EventRecord[] }>("event_split", {
    event_id: eventId,
    split_after_seconds: splitAfterSeconds,
    first_activity: firstActivity,
    second_activity: secondActivity
  });
}

export async function mergeEvents(firstEventId: string, secondEventId: string, activity = "") {
  return postJson<{ merged: boolean; event: EventRecord }>("event_merge", {
    first_event_id: firstEventId,
    second_event_id: secondEventId,
    activity
  });
}

export async function deleteLocalData() {
  if (isTauri()) return invoke<{ deleted: boolean }>("delete_local_data");
  const challenge = await postJson<{ challenge: string }>("delete_challenge");
  if (!DIRECT_DEVELOPMENT_API) throw new Error("The packaged app requires its managed local runtime.");
  const route = DEVELOPMENT_ROUTES.delete_data;
  const response = await fetch(`${DEV_API_BASE}${route.path}`, {
    method: "POST",
    headers: { "content-type": "application/json", "x-opsmineflow-delete-challenge": challenge.challenge },
    body: "{}"
  });
  if (!response.ok) throw new Error(`Local API returned ${response.status}`);
  return response.json() as Promise<{ deleted: boolean }>;
}

export async function exportArtifact(format: ExportFormat) {
  if (isTauri()) throw new Error("Packaged exports must use the native save dialog.");
  if (format === "markdown") return getJson<{ markdown: string }>("report_markdown");
  if (format === "json") return postJson<{ json: string }>("export_json");
  if (format === "csv") return postJson<{ csv: string }>("export_csv");
  if (format === "mermaid") return postJson<{ mermaid: string }>("export_mermaid");
  if (format === "llm-handoff") return postJson<{ filename: string; zip_base64: string }>("export_llm_handoff");
  return postJson<{ drawio: string }>("export_drawio");
}

export async function previewExport(format: ExportFormat) {
  return postJson<ExportPreview>("export_preview", { format });
}

export async function saveExport(format: ExportFormat, path: string) {
  if (isTauri()) return invoke<ExportSaveResult>("save_export_with_dialog", { format });
  return postJson<ExportSaveResult>("export_save", { format, path });
}
