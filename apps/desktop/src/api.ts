import type {
  AppSwitching,
  AutomationCandidate,
  EventRecord,
  Health,
  ProcessMap,
  Summary
} from "./types";

const API_BASE = "http://127.0.0.1:8765";

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`);
  if (!response.ok) {
    throw new Error(`Local API returned ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export async function loadDashboardData() {
  const [health, events, summary, processMap, candidates, appSwitching, report] = await Promise.all([
    getJson<Health>("/health"),
    getJson<EventRecord[]>("/events"),
    getJson<Summary>("/analytics/summary"),
    getJson<ProcessMap>("/analytics/process-map"),
    getJson<AutomationCandidate[]>("/analytics/automation-candidates"),
    getJson<AppSwitching>("/analytics/app-switching"),
    getJson<{ markdown: string }>("/reports/markdown")
  ]);

  return {
    health,
    events,
    summary,
    processMap,
    candidates,
    appSwitching,
    markdown: report.markdown
  };
}

