import type {
  AppSwitching,
  AppSettings,
  ActivityWatchImportMode,
  ActivityWatchPreview,
  AutomationCandidate,
  AutomationCandidatesResponse,
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
  ProjectMutationResponse,
  ProjectsResponse,
  ProjectScope,
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
  projects: { method: "GET", path: "/projects" },
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
  event_case_correlation: { method: "POST", path: "/events/case-correlation" },
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
  export_save: { method: "POST", path: "/export/save" },
  project_create: { method: "POST", path: "/projects" },
  project_select: { method: "POST", path: "/projects/select" },
  project_rename: { method: "POST", path: "/projects/rename" },
  project_delete: { method: "POST", path: "/projects/delete" }
};

export async function getNativeRuntimeStatus(): Promise<RuntimeStatus | null> {
  if (!isTauri()) return null;
  return invoke<RuntimeStatus>("runtime_status");
}

export async function repairNativeRuntimeState(): Promise<RuntimeStatus | null> {
  if (!isTauri()) return null;
  return invoke<RuntimeStatus>("repair_runtime_state");
}

function withProjectScope(payload: unknown, projectScope?: ProjectScope): Record<string, unknown> {
  const base = payload && typeof payload === "object" && !Array.isArray(payload) ? payload as Record<string, unknown> : {};
  if (!projectScope) return base;
  return {
    ...base,
    project_id: projectScope.projectId,
    ...(projectScope.expectedRevision === undefined ? {} : { expected_revision: projectScope.expectedRevision })
  };
}

async function localApiOperation<T>(operation: string, payload?: unknown, projectScope?: ProjectScope): Promise<T> {
  const scopedPayload = withProjectScope(payload, projectScope);
  if (isTauri()) return invoke<T>("local_api_operation", { operation, payload: Object.keys(scopedPayload).length ? scopedPayload : null });
  if (!DIRECT_DEVELOPMENT_API) throw new Error("The packaged app requires its managed local runtime.");
  const route = DEVELOPMENT_ROUTES[operation];
  if (!route) throw new Error("Local API operation is not available in development.");
  const headers: Record<string, string> = {};
  if (route.method === "POST") headers["content-type"] = "application/json";
  if (projectScope?.projectId) headers["x-opsmineflow-project"] = projectScope.projectId;
  const response = await fetch(`${DEV_API_BASE}${route.path}`, {
    method: route.method,
    headers: Object.keys(headers).length ? headers : undefined,
    body: route.method === "POST" ? JSON.stringify(scopedPayload) : undefined
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

function getJson<T>(operation: string, projectScope?: ProjectScope): Promise<T> {
  return localApiOperation<T>(operation, undefined, projectScope);
}

function postJson<T>(operation: string, payload: unknown = {}, projectScope?: ProjectScope): Promise<T> {
  return localApiOperation<T>(operation, payload, projectScope);
}

export async function loadProjects(): Promise<ProjectsResponse> {
  return getJson<ProjectsResponse>("projects");
}

export async function createProject(displayName: string): Promise<ProjectMutationResponse> {
  return postJson<ProjectMutationResponse>("project_create", { display_name: displayName });
}

export async function selectProject(projectId: string): Promise<ProjectMutationResponse> {
  return postJson<ProjectMutationResponse>("project_select", { project_id: projectId });
}

export async function renameProject(projectId: string, displayName: string, expectedRevision: number): Promise<ProjectMutationResponse> {
  return postJson<ProjectMutationResponse>("project_rename", { project_id: projectId, display_name: displayName, expected_revision: expectedRevision });
}

export async function deleteProject(projectId: string, expectedRevision: number): Promise<ProjectMutationResponse> {
  return postJson<ProjectMutationResponse>("project_delete", { project_id: projectId, expected_revision: expectedRevision });
}

export async function loadDashboardData(projectId: string) {
  const projectScope: ProjectScope = { projectId };
  const [health, diagnostics, recording, settings, importHistory, eventPage, quality, summary, processMap, candidates, appSwitching, report] = await Promise.all([
    getJson<Health>("health"),
    getJson<Diagnostics>("diagnostics", projectScope),
    getJson<RecordingStatus>("recording_status", projectScope),
    getJson<AppSettings>("settings", projectScope),
    getJson<{ imports: ImportHistoryEntry[] }>("import_history", projectScope),
    postJson<EventPage>("events_page", { offset: 0, limit: 500 }, projectScope),
    getJson<EventQualityReport>("event_quality", projectScope),
    getJson<Summary>("summary", projectScope),
    getJson<ProcessMap>("process_map", projectScope),
    getJson<AutomationCandidatesResponse>("automation_candidates", projectScope),
    getJson<AppSwitching>("app_switching", projectScope),
    getJson<{ markdown: string }>("report_markdown", projectScope)
  ]);

  return {
    health,
    diagnostics,
    recording,
    settings,
    importHistory: importHistory.imports,
    events: eventPage.events,
    eventTotal: eventPage.total,
    quality,
    summary,
    processMap,
    candidates: candidates.candidates,
    appSwitching,
    markdown: report.markdown
  };
}

export async function loadEventPage(offset: number, limit: number, projectScope: ProjectScope) {
  return postJson<EventPage>("events_page", { offset, limit }, projectScope);
}

export async function getRecordingStatus(projectScope: ProjectScope) {
  return getJson<RecordingStatus>("recording_status", projectScope);
}

export async function startRecording(caseId: string, activityLabel: string, projectScope: ProjectScope) {
  return postJson<RecordingStatus>("recording_start", { case_id: caseId, activity_label: activityLabel, consent: true }, projectScope);
}

export async function stopRecording(projectScope: ProjectScope) {
  return postJson<RecordingStatus>("recording_stop", {}, projectScope);
}

export async function pauseRecording(reason: string, projectScope: ProjectScope) {
  return postJson<RecordingStatus>("recording_pause", { reason }, projectScope);
}

export async function resumeRecording(projectScope: ProjectScope) {
  return postJson<RecordingStatus>("recording_resume", {}, projectScope);
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

export async function importEvents(
  format: "csv" | "json",
  path: string,
  projectScope: ProjectScope,
  mapping?: CsvMapping,
  dateFormat = "",
  timezone = "UTC"
) {
  const payload = withProjectScope(importPayload(path, format === "csv" ? mapping : undefined, dateFormat, timezone), projectScope);
  if (isTauri()) {
    return invoke<ImportResult>("import_selected_file", {
      handle: path,
      payload
    });
  }
  return postJson<ImportResult>(`import_${format}`, payload, projectScope);
}

export async function previewImport(
  format: "csv" | "json",
  path: string,
  projectScope: ProjectScope,
  mapping?: CsvMapping,
  dateFormat = "",
  timezone = "UTC"
) {
  const payload = withProjectScope({ format, ...importPayload(path, format === "csv" ? mapping : undefined, dateFormat, timezone) }, projectScope);
  if (isTauri()) {
    return invoke<ImportPreview>("preview_selected_import", {
      handle: path,
      payload
    });
  }
  return postJson<ImportPreview>("import_preview", payload, projectScope);
}

export async function previewActivityWatchLocal(enabled: boolean, projectScope: ProjectScope) {
  return postJson<ActivityWatchPreview>("activitywatch_preview", { enabled, base_url: "http://127.0.0.1:5600" }, projectScope);
}

export async function importActivityWatchLocal(enabled: boolean, mode: ActivityWatchImportMode, projectScope: ProjectScope) {
  return postJson<ImportResult>("activitywatch_import", { enabled, mode, base_url: "http://127.0.0.1:5600" }, projectScope);
}

export async function saveSettings(settings: Partial<AppSettings>, projectScope: ProjectScope) {
  return postJson<AppSettings>("settings_update", settings, projectScope);
}

export async function runDiagnosticChecks(projectScope: ProjectScope) {
  return postJson<DiagnosticChecks>("diagnostics_checks", {}, projectScope);
}

export async function saveAutomationReview(activity: string, status: AutomationReviewStatus, note: string, projectScope: ProjectScope) {
  return postJson<{ activity: string; review_status: AutomationReviewStatus; review_note: string }>("automation_review", { activity, status, note }, projectScope);
}

export async function updateEventActivity(eventId: string, activity: string, projectScope: ProjectScope) {
  return postJson<{ event: EventRecord }>("event_activity", { event_id: eventId, activity }, projectScope);
}

export async function updateEventCaseCorrelation(eventId: string, caseId: string, reason: string, projectScope: ProjectScope) {
  return postJson<{ event: EventRecord }>("event_case_correlation", {
    event_id: eventId,
    case_id: caseId,
    reason
  }, projectScope);
}

export async function excludeEvent(eventId: string, projectScope: ProjectScope) {
  return postJson<{ excluded: boolean; event_id: string }>("event_exclude", { event_id: eventId }, projectScope);
}

export async function approveEventQuality(eventId: string, projectScope: ProjectScope) {
  return postJson<{ event_id: string; quality_review_status: string }>("event_quality_review", { event_id: eventId, status: "approved" }, projectScope);
}

export async function splitEvent(eventId: string, splitAfterSeconds: number, firstActivity: string, secondActivity: string, projectScope: ProjectScope) {
  return postJson<{ split: boolean; events: EventRecord[] }>("event_split", {
    event_id: eventId,
    split_after_seconds: splitAfterSeconds,
    first_activity: firstActivity,
    second_activity: secondActivity
  }, projectScope);
}

export async function mergeEvents(firstEventId: string, secondEventId: string, activity: string, projectScope: ProjectScope) {
  return postJson<{ merged: boolean; event: EventRecord }>("event_merge", {
    first_event_id: firstEventId,
    second_event_id: secondEventId,
    activity
  }, projectScope);
}

export async function deleteLocalData(projectScope: ProjectScope) {
  if (isTauri()) return invoke<{ deleted: boolean }>("delete_local_data", { payload: withProjectScope({}, projectScope) });
  const challenge = await postJson<{ challenge: string }>("delete_challenge", {}, projectScope);
  if (!DIRECT_DEVELOPMENT_API) throw new Error("The packaged app requires its managed local runtime.");
  const route = DEVELOPMENT_ROUTES.delete_data;
  const response = await fetch(`${DEV_API_BASE}${route.path}`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "x-opsmineflow-delete-challenge": challenge.challenge,
      "x-opsmineflow-project": projectScope.projectId
    },
    body: JSON.stringify(withProjectScope({}, projectScope))
  });
  if (!response.ok) throw new Error(`Local API returned ${response.status}`);
  return response.json() as Promise<{ deleted: boolean }>;
}

export async function exportArtifact(format: ExportFormat, projectScope: ProjectScope) {
  if (isTauri()) throw new Error("Packaged exports must use the native save dialog.");
  if (format === "markdown") return getJson<{ markdown: string }>("report_markdown", projectScope);
  if (format === "json") return postJson<{ json: string }>("export_json", {}, projectScope);
  if (format === "csv") return postJson<{ filename: string; zip_base64: string }>("export_csv", {}, projectScope);
  if (format === "mermaid") return postJson<{ mermaid: string }>("export_mermaid", {}, projectScope);
  if (format === "llm-handoff") return postJson<{ filename: string; zip_base64: string }>("export_llm_handoff", {}, projectScope);
  return postJson<{ drawio: string }>("export_drawio", {}, projectScope);
}

export async function previewExport(format: ExportFormat, projectScope: ProjectScope) {
  return postJson<ExportPreview>("export_preview", { format }, projectScope);
}

export async function saveExport(format: ExportFormat, path: string, projectScope: ProjectScope) {
  if (isTauri()) return invoke<ExportSaveResult>("save_export_with_dialog", { payload: withProjectScope({ format }, projectScope) });
  return postJson<ExportSaveResult>("export_save", { format, path }, projectScope);
}
