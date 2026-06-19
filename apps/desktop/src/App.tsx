import { useEffect, useMemo, useState } from "react";
import {
  deleteLocalData,
  exportArtifact,
  importActivityWatchLocal,
  importEvents,
  loadDashboardData,
  previewImport,
  saveSettings
} from "./api";
import type {
  AppSwitching,
  AppSettings,
  AutomationCandidate,
  Diagnostics,
  EventRecord,
  Health,
  ImportHistoryEntry,
  ImportPreview,
  ProcessMap,
  Summary
} from "./types";

type Tab = "home" | "dashboard" | "events" | "process" | "switching" | "candidates" | "reports" | "settings";

type DashboardData = {
  health: Health;
  diagnostics: Diagnostics;
  settings: AppSettings;
  importHistory: ImportHistoryEntry[];
  events: EventRecord[];
  summary: Summary;
  processMap: ProcessMap;
  candidates: AutomationCandidate[];
  appSwitching: AppSwitching;
  markdown: string;
};

type AppActions = {
  refresh: () => Promise<void>;
  previewImport: (format: "csv" | "json", path: string) => Promise<ImportPreview>;
  importEvents: (format: "csv" | "json", path: string) => Promise<void>;
  importActivityWatch: () => Promise<void>;
  exportArtifact: (format: "markdown" | "json" | "csv" | "mermaid" | "drawio") => Promise<void>;
  saveSettings: (settings: Partial<AppSettings>) => Promise<void>;
  deleteData: () => Promise<void>;
};

const tabs: Array<{ id: Tab; label: string }> = [
  { id: "home", label: "Home" },
  { id: "dashboard", label: "Dashboard" },
  { id: "events", label: "Event Explorer" },
  { id: "process", label: "Process Map" },
  { id: "switching", label: "App Switching" },
  { id: "candidates", label: "Automation" },
  { id: "reports", label: "Reports" },
  { id: "settings", label: "Settings" }
];

export function App() {
  const [activeTab, setActiveTab] = useState<Tab>("home");
  const [data, setData] = useState<DashboardData | null>(null);
  const [error, setError] = useState<string>("");
  const [actionMessage, setActionMessage] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [working, setWorking] = useState(false);

  async function refresh() {
    setLoading(true);
    setError("");
    try {
      setData(await loadDashboardData());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Local API unavailable");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void refresh();
  }, []);

  async function runAction(task: () => Promise<string>) {
    setWorking(true);
    setError("");
    setActionMessage("");
    try {
      const message = await task();
      setActionMessage(message);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Action failed");
    } finally {
      setWorking(false);
    }
  }

  const actions: AppActions = {
    refresh,
    previewImport: async (format, path) => {
      setWorking(true);
      setError("");
      setActionMessage("");
      try {
        return await previewImport(format, path);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Preview failed");
        throw err;
      } finally {
        setWorking(false);
      }
    },
    importEvents: (format, path) =>
      runAction(async () => {
        const result = await importEvents(format, path);
        return `${result.imported_events} events imported from ${result.source || format}.`;
      }),
    importActivityWatch: () =>
      runAction(async () => {
        const result = await importActivityWatchLocal(true);
        return result.message || `${result.imported_events} ActivityWatch events imported.`;
      }),
    exportArtifact: (format) =>
      runAction(async () => {
        if (!window.confirm("Review masked fields and confidential flags before sharing this export. Continue?")) {
          return "Export cancelled.";
        }
        const filename = downloadExport(format, await exportArtifact(format));
        return `${filename} downloaded.`;
      }),
    saveSettings: (settings) =>
      runAction(async () => {
        await saveSettings(settings);
        return "Settings saved.";
      }),
    deleteData: () =>
      runAction(async () => {
        await deleteLocalData();
        return "Local analysis data deleted.";
      })
  };

  return (
    <main className="app-shell">
      <header className="topbar">
        <div>
          <h1>OpsMineFlow</h1>
          <p>Local-first task mining for consent-based As-Is discovery</p>
        </div>
        <div className="status-strip">
          <StatusPill label="Network" value={data?.health.local_only ? "Local Only" : "Checking"} tone="good" />
          <StatusPill label="LLM" value={data?.health.llm_supported ? "Enabled" : "Not Supported"} tone="neutral" />
          <button className="refresh-button" onClick={() => void refresh()} disabled={loading}>
            Refresh
          </button>
        </div>
      </header>

      <nav className="tabs" aria-label="Views">
        {tabs.map((tab) => (
          <button
            key={tab.id}
            className={tab.id === activeTab ? "tab is-active" : "tab"}
            onClick={() => setActiveTab(tab.id)}
          >
            {tab.label}
          </button>
        ))}
      </nav>

      {error ? <div className="api-warning">Local API is not available: {error}</div> : null}
      {actionMessage ? <div className="action-message">{actionMessage}</div> : null}
      {loading && !data ? <div className="loading">Loading local analysis...</div> : null}

      {data ? <View tab={activeTab} data={data} actions={actions} working={working || loading} /> : null}
    </main>
  );
}

function View({
  tab,
  data,
  actions,
  working
}: {
  tab: Tab;
  data: DashboardData;
  actions: AppActions;
  working: boolean;
}) {
  if (tab === "home") return <HomeView data={data} actions={actions} working={working} />;
  if (tab === "events") return <EventsView events={data.events} />;
  if (tab === "process") return <ProcessView processMap={data.processMap} />;
  if (tab === "switching") return <SwitchingView switching={data.appSwitching} />;
  if (tab === "candidates") return <CandidatesView candidates={data.candidates} />;
  if (tab === "reports") return <ReportsView markdown={data.markdown} />;
  if (tab === "settings") return <SettingsView health={data.health} />;
  return <DashboardView data={data} />;
}

function downloadExport(
  format: "markdown" | "json" | "csv" | "mermaid" | "drawio",
  payload: unknown
): string {
  const typed = payload as {
    markdown?: string;
    snapshot?: unknown;
    events?: EventRecord[];
    mermaid?: string;
    drawio?: string;
  };
  const filename = `opsmineflow-export.${format === "markdown" ? "md" : format === "drawio" ? "drawio" : format}`;
  let content = "";
  let mime = "text/plain;charset=utf-8";

  if (format === "markdown") {
    content = typed.markdown || "";
    mime = "text/markdown;charset=utf-8";
  } else if (format === "json") {
    content = JSON.stringify(typed.snapshot, null, 2);
    mime = "application/json;charset=utf-8";
  } else if (format === "csv") {
    content = eventsToCsv(typed.events || []);
    mime = "text/csv;charset=utf-8";
  } else if (format === "mermaid") {
    content = typed.mermaid || "";
  } else {
    content = typed.drawio || "";
    mime = "application/xml;charset=utf-8";
  }

  const url = URL.createObjectURL(new Blob([content], { type: mime }));
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
  return filename;
}

function eventsToCsv(events: EventRecord[]): string {
  const columns: Array<keyof EventRecord> = [
    "event_id",
    "case_id",
    "user_hash",
    "app_name",
    "window_title_masked",
    "url_masked",
    "domain",
    "activity_raw",
    "timestamp_start",
    "timestamp_end",
    "duration_seconds",
    "confidential_flag"
  ];
  const rows = events.map((event) => columns.map((column) => csvCell(String(event[column]))).join(","));
  return [columns.join(","), ...rows].join("\n");
}

function csvCell(value: string): string {
  if (!/[",\n]/.test(value)) return value;
  return `"${value.replaceAll('"', '""')}"`;
}

function HomeView({ data, actions, working }: { data: DashboardData; actions: AppActions; working: boolean }) {
  const [format, setFormat] = useState<"csv" | "json">("csv");
  const [path, setPath] = useState("data/sample/sample_events.csv");
  const [activityWatchEnabled, setActivityWatchEnabled] = useState(false);
  const [settingsDraft, setSettingsDraft] = useState<AppSettings>(data.settings);
  const [preview, setPreview] = useState<ImportPreview | null>(null);

  useEffect(() => {
    setSettingsDraft(data.settings);
  }, [data.settings]);

  return (
    <section className="home-grid">
      <section className="operation-panel primary-panel">
        <div className="panel-heading">
          <h2>Import</h2>
          <span>{data.events.length} events loaded</span>
        </div>
        <div className="inline-fields">
          <select value={format} onChange={(event) => setFormat(event.target.value as "csv" | "json")} disabled={working}>
            <option value="csv">CSV</option>
            <option value="json">JSON</option>
          </select>
          <input value={path} onChange={(event) => setPath(event.target.value)} disabled={working} />
          <button
            onClick={() => {
              void actions.previewImport(format, path).then(setPreview);
            }}
            disabled={working || path.trim() === ""}
          >
            Preview
          </button>
        </div>
        {preview ? (
          <div className="preview-panel">
            <div className="preview-summary">
              <b>{preview.event_count} events</b>
              <span>{preview.confidential_count} confidential flags</span>
            </div>
            <div className="preview-list">
              {preview.sample_events.map((event, index) => (
                <div className="preview-row" key={`${event.case_id}-${event.activity}-${index}`}>
                  <span>{event.case_id}</span>
                  <b>{event.activity}</b>
                  <span>{event.app_name || "Unknown"}</span>
                  <span>{Math.round(event.duration_seconds)}s</span>
                </div>
              ))}
            </div>
          </div>
        ) : null}
        <button onClick={() => void actions.importEvents(format, path)} disabled={working || path.trim() === ""}>
          Import Previewed File
        </button>
        <label className="check-row">
          <input
            type="checkbox"
            checked={activityWatchEnabled}
            onChange={(event) => setActivityWatchEnabled(event.target.checked)}
            disabled={working}
          />
          <span>ActivityWatch localhost import</span>
        </label>
        <button onClick={() => void actions.importActivityWatch()} disabled={working || !activityWatchEnabled}>
          Import ActivityWatch
        </button>
        {data.importHistory.length > 0 ? (
          <div className="history-list">
            {data.importHistory.slice(0, 4).map((item) => (
              <div className="history-row" key={`${item.imported_at}-${item.path}`}>
                <span>{item.source}</span>
                <b>{item.event_count}</b>
                <span>{new Date(item.imported_at).toLocaleString()}</span>
              </div>
            ))}
          </div>
        ) : null}
      </section>

      <section className="operation-panel">
        <div className="panel-heading">
          <h2>Exports</h2>
          <span>Local files only</span>
        </div>
        <div className="button-grid">
          {(["markdown", "json", "csv", "mermaid", "drawio"] as const).map((formatName) => (
            <button key={formatName} onClick={() => void actions.exportArtifact(formatName)} disabled={working}>
              {formatName}
            </button>
          ))}
        </div>
      </section>

      <section className="operation-panel">
        <div className="panel-heading">
          <h2>Settings</h2>
          <span>{settingsDraft.retention_days} days</span>
        </div>
        <label className="check-row">
          <input
            type="checkbox"
            checked={settingsDraft.mask_url_paths}
            onChange={(event) => setSettingsDraft({ ...settingsDraft, mask_url_paths: event.target.checked })}
          />
          <span>Mask URL paths</span>
        </label>
        <label className="check-row">
          <input
            type="checkbox"
            checked={settingsDraft.mask_window_titles}
            onChange={(event) => setSettingsDraft({ ...settingsDraft, mask_window_titles: event.target.checked })}
          />
          <span>Mask window titles</span>
        </label>
        <label className="number-row">
          <span>Retention days</span>
          <input
            type="number"
            min="1"
            max="365"
            value={settingsDraft.retention_days}
            onChange={(event) => setSettingsDraft({ ...settingsDraft, retention_days: Number(event.target.value) })}
          />
        </label>
        <button onClick={() => void actions.saveSettings(settingsDraft)} disabled={working}>
          Save Settings
        </button>
      </section>

      <section className="operation-panel">
        <div className="panel-heading">
          <h2>Diagnostics</h2>
          <span>{data.diagnostics.storage.storage_mode}</span>
        </div>
        <div className="diagnostic-list">
          <Setting label="API bind" value={data.diagnostics.api.bind} />
          <Setting label="Storage" value={data.diagnostics.storage.storage_mode} />
          <Setting label="Events" value={data.diagnostics.storage.event_count.toString()} />
          <Setting label="External network" value={data.diagnostics.runtime_policy.external_network} />
        </div>
        <div className="danger-row">
          <button onClick={() => void actions.refresh()} disabled={working}>
            Refresh
          </button>
          <button
            className="danger-button"
            onClick={() => {
              if (window.confirm("Delete all local analysis data?")) void actions.deleteData();
            }}
            disabled={working}
          >
            Delete Data
          </button>
        </div>
      </section>
    </section>
  );
}

function DashboardView({ data }: { data: DashboardData }) {
  const totalMinutes = Math.round(data.summary.total_active_seconds / 60);
  return (
    <section className="view-grid">
      <Metric label="Events" value={data.summary.total_events.toString()} />
      <Metric label="Active minutes" value={totalMinutes.toString()} />
      <Metric label="Avg event seconds" value={data.summary.average_event_duration_seconds.toFixed(0)} />
      <Metric label="Automation candidates" value={data.candidates.length.toString()} />
      <BarPanel title="App time" values={data.summary.app_usage_seconds} />
      <BarPanel title="Business label time" values={data.summary.label_usage_seconds} />
      <TopList
        title="Top automation candidates"
        rows={data.candidates.slice(0, 10).map((item) => ({
          key: item.activity,
          value: `${Math.round(item.automation_score * 100)} / ${item.classification}`
        }))}
      />
      <TopList
        title="Bottleneck signals"
        rows={data.processMap.nodes
          .filter((node) => node.bottleneck)
          .slice(0, 10)
          .map((node) => ({
            key: node.activity,
            value: `${node.average_duration_seconds.toFixed(0)}s avg`
          }))}
      />
    </section>
  );
}

function EventsView({ events }: { events: EventRecord[] }) {
  return (
    <section className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Case</th>
            <th>Activity</th>
            <th>App</th>
            <th>Window</th>
            <th>Domain</th>
            <th>Seconds</th>
            <th>Masking</th>
          </tr>
        </thead>
        <tbody>
          {events.map((event) => (
            <tr key={event.event_id}>
              <td>{event.case_id}</td>
              <td>{event.activity_raw}</td>
              <td>{event.app_name || "Unknown"}</td>
              <td>{event.window_title_masked || "-"}</td>
              <td>{event.domain || "-"}</td>
              <td>{event.duration_seconds.toFixed(0)}</td>
              <td>{event.confidential_flag ? "Confidential" : "Masked"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

function ProcessView({ processMap }: { processMap: ProcessMap }) {
  return (
    <section className="process-canvas">
      {processMap.nodes.map((node) => (
        <div
          key={node.activity}
          className={[
            "process-node",
            node.bottleneck ? "is-bottleneck" : "",
            node.automation_candidate ? "is-automation" : ""
          ].join(" ")}
        >
          <strong>{node.activity}</strong>
          <span>freq {node.frequency}</span>
          <span>avg {node.average_duration_seconds.toFixed(0)}s</span>
        </div>
      ))}
      <div className="edge-list">
        {processMap.edges.map((edge) => (
          <div key={`${edge.source}-${edge.target}`} className="edge-row">
            <span>{edge.source}</span>
            <span>to</span>
            <span>{edge.target}</span>
            <b>{edge.frequency}</b>
          </div>
        ))}
      </div>
    </section>
  );
}

function SwitchingView({ switching }: { switching: AppSwitching }) {
  return (
    <section className="split-view">
      <TopList
        title="App transition ranking"
        rows={switching.transition_ranking.map((item) => ({
          key: `${item.source_app} to ${item.target_app}`,
          value: `${item.count}`
        }))}
      />
      <TopList
        title="Round trips"
        rows={switching.round_trips.map((item) => ({
          key: item.pattern,
          value: `${item.count}`
        }))}
      />
    </section>
  );
}

function CandidatesView({ candidates }: { candidates: AutomationCandidate[] }) {
  return (
    <section className="candidate-list">
      {candidates.map((candidate) => (
        <article className="candidate-card" key={candidate.activity}>
          <div>
            <h2>{candidate.activity}</h2>
            <p>{candidate.reasons.join(", ")}</p>
          </div>
          <b>{Math.round(candidate.automation_score * 100)}</b>
          <span>{candidate.classification}</span>
        </article>
      ))}
    </section>
  );
}

function ReportsView({ markdown }: { markdown: string }) {
  return <pre className="report-preview">{markdown}</pre>;
}

function SettingsView({ health }: { health: Health }) {
  return (
    <section className="settings-grid">
      <Setting label="API bind" value={health.bind} />
      <Setting label="External network" value={health.local_only ? "Blocked by policy" : "Unknown"} />
      <Setting label="LLM integration" value={health.llm_supported ? "Enabled" : "Not supported"} />
      <Setting label="Data storage" value={health.storage_mode} />
      <Setting label="Events loaded" value={health.event_count.toString()} />
      <Setting label="ActivityWatch" value="Optional localhost import only" />
      <Setting label="Sensitive capture" value="No keystrokes, screenshots, audio, or camera" />
    </section>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <article className="metric">
      <span>{label}</span>
      <strong>{value}</strong>
    </article>
  );
}

function StatusPill({ label, value, tone }: { label: string; value: string; tone: "good" | "neutral" }) {
  return (
    <div className={`status-pill ${tone}`}>
      <span>{label}</span>
      <b>{value}</b>
    </div>
  );
}

function BarPanel({ title, values }: { title: string; values: Record<string, number> }) {
  const max = useMemo(() => Math.max(...Object.values(values), 1), [values]);
  return (
    <section className="panel">
      <h2>{title}</h2>
      <div className="bars">
        {Object.entries(values).map(([key, value]) => (
          <div className="bar-row" key={key}>
            <span>{key}</span>
            <div className="bar-track">
              <div className="bar-fill" style={{ width: `${Math.max((value / max) * 100, 4)}%` }} />
            </div>
            <b>{Math.round(value / 60)}m</b>
          </div>
        ))}
      </div>
    </section>
  );
}

function TopList({ title, rows }: { title: string; rows: Array<{ key: string; value: string }> }) {
  return (
    <section className="panel">
      <h2>{title}</h2>
      <div className="rank-list">
        {rows.length === 0 ? <p className="empty">No items yet</p> : null}
        {rows.map((row) => (
          <div className="rank-row" key={row.key}>
            <span>{row.key}</span>
            <b>{row.value}</b>
          </div>
        ))}
      </div>
    </section>
  );
}

function Setting({ label, value }: { label: string; value: string }) {
  return (
    <div className="setting-row">
      <span>{label}</span>
      <b>{value}</b>
    </div>
  );
}
