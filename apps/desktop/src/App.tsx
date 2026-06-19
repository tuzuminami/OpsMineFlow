import { useEffect, useMemo, useState } from "react";
import {
  deleteLocalData,
  exportArtifact,
  importActivityWatchLocal,
  importEvents,
  loadDashboardData,
  previewImport,
  previewExport,
  runDiagnosticChecks,
  saveAutomationReview,
  saveExport,
  saveSettings
} from "./api";
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
  previewExport: (format: ExportFormat) => Promise<ExportPreview>;
  exportArtifact: (format: ExportFormat) => Promise<void>;
  saveExport: (format: ExportFormat, path: string) => Promise<void>;
  saveSettings: (settings: Partial<AppSettings>) => Promise<void>;
  saveAutomationReview: (activity: string, status: AutomationReviewStatus) => Promise<void>;
  runDiagnosticChecks: () => Promise<DiagnosticChecks>;
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
    previewExport: async (format) => {
      setWorking(true);
      setError("");
      setActionMessage("");
      try {
        return await previewExport(format);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Export preview failed");
        throw err;
      } finally {
        setWorking(false);
      }
    },
    exportArtifact: (format) =>
      runAction(async () => {
        if (!window.confirm("Review masked fields and confidential flags before sharing this export. Continue?")) {
          return "Export cancelled.";
        }
        const filename = downloadExport(format, await exportArtifact(format));
        return `${filename} downloaded.`;
      }),
    saveExport: (format, path) =>
      runAction(async () => {
        if (!window.confirm("Review masked fields and confidential flags before sharing this export. Continue?")) {
          return "Export cancelled.";
        }
        const result = await saveExport(format, path);
        return `Saved ${result.format} export to ${result.path}.`;
      }),
    saveSettings: (settings) =>
      runAction(async () => {
        await saveSettings(settings);
        return "Settings saved.";
      }),
    saveAutomationReview: (activity, status) =>
      runAction(async () => {
        const result = await saveAutomationReview(activity, status);
        return `Review saved for ${result.activity}.`;
      }),
    runDiagnosticChecks: async () => {
      setWorking(true);
      setError("");
      setActionMessage("");
      try {
        const result = await runDiagnosticChecks();
        setActionMessage("Diagnostics checks finished.");
        return result;
      } catch (err) {
        setError(err instanceof Error ? err.message : "Diagnostics checks failed");
        throw err;
      } finally {
        setWorking(false);
      }
    },
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
  if (tab === "process") return <ProcessView processMap={data.processMap} events={data.events} />;
  if (tab === "switching") return <SwitchingView switching={data.appSwitching} />;
  if (tab === "candidates") return <CandidatesView candidates={data.candidates} actions={actions} working={working} />;
  if (tab === "reports") return <ReportsView markdown={data.markdown} />;
  if (tab === "settings") return <SettingsView data={data} actions={actions} working={working} />;
  return <DashboardView data={data} />;
}

function downloadExport(
  format: ExportFormat,
  payload: unknown
): string {
  const typed = payload as {
    markdown?: string;
    json?: string;
    csv?: string;
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
    content = typed.json || "";
    mime = "application/json;charset=utf-8";
  } else if (format === "csv") {
    content = typed.csv || "";
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

function HomeView({ data, actions, working }: { data: DashboardData; actions: AppActions; working: boolean }) {
  const [format, setFormat] = useState<"csv" | "json">("csv");
  const [path, setPath] = useState("data/sample/sample_events.csv");
  const [activityWatchEnabled, setActivityWatchEnabled] = useState(false);
  const [settingsDraft, setSettingsDraft] = useState<AppSettings>(data.settings);
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const [exportFormat, setExportFormat] = useState<ExportFormat>("markdown");
  const [exportPath, setExportPath] = useState(defaultExportPath("markdown"));
  const [exportPreview, setExportPreview] = useState<ExportPreview | null>(null);
  const [diagnosticChecks, setDiagnosticChecks] = useState<DiagnosticChecks | null>(null);

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
          <span>{exportPreview ? `${exportPreview.byte_size} bytes` : "Local files only"}</span>
        </div>
        <div className="inline-fields">
          <select
            value={exportFormat}
            onChange={(event) => {
              const nextFormat = event.target.value as ExportFormat;
              setExportFormat(nextFormat);
              setExportPath(defaultExportPath(nextFormat));
              setExportPreview(null);
            }}
            disabled={working}
          >
            {(["markdown", "json", "csv", "mermaid", "drawio"] as const).map((formatName) => (
              <option key={formatName} value={formatName}>
                {formatName}
              </option>
            ))}
          </select>
          <input value={exportPath} onChange={(event) => setExportPath(event.target.value)} disabled={working} />
          <button
            onClick={() => {
              void actions.previewExport(exportFormat).then(setExportPreview);
            }}
            disabled={working}
          >
            Preview
          </button>
        </div>
        {exportPreview ? (
          <div className="export-preview-box">
            <div className="preview-summary">
              <b>{exportPreview.filename}</b>
              <span>{exportPreview.confidential_count} confidential flags</span>
            </div>
            <pre>{exportPreview.preview}</pre>
          </div>
        ) : null}
        <div className="button-grid">
          <button onClick={() => void actions.saveExport(exportFormat, exportPath)} disabled={working || !exportPath.trim()}>
            Save to Path
          </button>
          <button onClick={() => void actions.exportArtifact(exportFormat)} disabled={working}>
            Download
          </button>
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
        <label className="text-row">
          <span>Excluded apps</span>
          <textarea
            value={listToText(settingsDraft.excluded_apps)}
            onChange={(event) => setSettingsDraft({ ...settingsDraft, excluded_apps: textToList(event.target.value) })}
          />
        </label>
        <label className="text-row">
          <span>Excluded domains</span>
          <textarea
            value={listToText(settingsDraft.excluded_domains)}
            onChange={(event) => setSettingsDraft({ ...settingsDraft, excluded_domains: textToList(event.target.value) })}
          />
        </label>
        <button onClick={() => void actions.saveSettings(settingsDraft)} disabled={working}>
          Save Settings
        </button>
      </section>

      <section className="operation-panel">
        <div className="panel-heading">
          <h2>Diagnostics</h2>
          <span>{data.diagnostics.webui.status}</span>
        </div>
        <div className="diagnostic-list">
          <Setting label="API" value={`${data.diagnostics.api.status} ${data.diagnostics.api.bind}:${data.diagnostics.api.port}`} />
          <Setting label="WebUI" value={`${data.diagnostics.webui.status} ${data.diagnostics.webui.expected_url}`} />
          <Setting label="Storage" value={`${data.diagnostics.storage.storage_mode} ${data.diagnostics.storage.storage_path || ""}`} />
          <Setting label="Events" value={data.diagnostics.storage.event_count.toString()} />
          <Setting label="Reviews" value={data.diagnostics.storage.automation_review_count.toString()} />
          <Setting label="ActivityWatch" value={`${data.diagnostics.activitywatch.enabled ? "enabled" : "disabled"} / ${data.diagnostics.activitywatch.status}`} />
          <Setting label="External network" value={data.diagnostics.runtime_policy.external_network} />
          {Object.entries(data.diagnostics.dependencies).map(([name, item]) => (
            <Setting key={name} label={name} value={`${item.status}${item.version ? ` / ${item.version}` : ""}`} />
          ))}
          {Object.entries(data.diagnostics.ports).map(([name, item]) => (
            <Setting key={name} label={`${name} port`} value={`${item.host}:${item.port} / ${item.status}`} />
          ))}
        </div>
        {diagnosticChecks ? (
          <div className="check-results">
            {Object.entries(diagnosticChecks).map(([name, result]) => (
              <div className="check-result" key={name}>
                <span>{result.command}</span>
                <b>{result.status}</b>
                <pre>{result.output}</pre>
              </div>
            ))}
          </div>
        ) : null}
        <div className="danger-row">
          <button
            onClick={() => {
              void actions.runDiagnosticChecks().then(setDiagnosticChecks);
            }}
            disabled={working}
          >
            Run Checks
          </button>
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

function ProcessView({ processMap, events }: { processMap: ProcessMap; events: EventRecord[] }) {
  const [query, setQuery] = useState("");
  const [appFilter, setAppFilter] = useState("all");
  const [selectedActivity, setSelectedActivity] = useState("");
  const activityApps = useMemo(() => {
    const mapping = new Map<string, Set<string>>();
    for (const event of events) {
      if (!mapping.has(event.activity_raw)) mapping.set(event.activity_raw, new Set<string>());
      mapping.get(event.activity_raw)?.add(event.app_name || "Unknown");
    }
    return mapping;
  }, [events]);
  const appOptions = useMemo(() => {
    return Array.from(new Set(events.map((event) => event.app_name || "Unknown"))).sort();
  }, [events]);
  const visibleNodes = processMap.nodes.filter((node) => {
    const matchesQuery = query.trim() === "" || node.activity.toLowerCase().includes(query.trim().toLowerCase());
    const apps = activityApps.get(node.activity) || new Set<string>();
    const matchesApp = appFilter === "all" || apps.has(appFilter);
    return matchesQuery && matchesApp;
  });
  const visibleActivities = new Set(visibleNodes.map((node) => node.activity));
  const visibleEdges = processMap.edges.filter((edge) => visibleActivities.has(edge.source) && visibleActivities.has(edge.target));
  const selectedNode = visibleNodes.find((node) => node.activity === selectedActivity) || visibleNodes[0] || null;
  const selectedEvents = selectedNode ? events.filter((event) => event.activity_raw === selectedNode.activity) : [];
  const selectedApps = selectedNode ? Array.from(activityApps.get(selectedNode.activity) || []).sort() : [];

  return (
    <section className="process-workspace">
      <div className="process-toolbar">
        <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Filter activity" />
        <select value={appFilter} onChange={(event) => setAppFilter(event.target.value)}>
          <option value="all">All apps</option>
          {appOptions.map((appName) => (
            <option key={appName} value={appName}>
              {appName}
            </option>
          ))}
        </select>
      </div>
      <section className="process-canvas">
        {visibleNodes.map((node) => (
          <button
            key={node.activity}
            className={[
              "process-node",
              node.activity === selectedNode?.activity ? "is-selected" : "",
              node.bottleneck ? "is-bottleneck" : "",
              node.automation_candidate ? "is-automation" : ""
            ].join(" ")}
            onClick={() => setSelectedActivity(node.activity)}
          >
            <strong>{node.activity}</strong>
            <span>freq {node.frequency}</span>
            <span>avg {node.average_duration_seconds.toFixed(0)}s</span>
            <span>
              start {processMap.start_activities[node.activity] || 0} / end {processMap.end_activities[node.activity] || 0}
            </span>
          </button>
        ))}
        {visibleNodes.length === 0 ? <p className="empty">No process nodes match the filters</p> : null}
      </section>
      <section className="process-detail">
        <div>
          <h2>{selectedNode?.activity || "No activity selected"}</h2>
          <p>{selectedApps.join(", ") || "No app data"}</p>
        </div>
        {selectedNode ? (
          <div className="process-detail-grid">
            <DetailStat label="Frequency" value={selectedNode.frequency.toString()} />
            <DetailStat label="Avg seconds" value={selectedNode.average_duration_seconds.toFixed(0)} />
            <DetailStat label="Start count" value={(processMap.start_activities[selectedNode.activity] || 0).toString()} />
            <DetailStat label="End count" value={(processMap.end_activities[selectedNode.activity] || 0).toString()} />
            <DetailStat label="Events" value={selectedEvents.length.toString()} />
            <DetailStat label="Signals" value={[selectedNode.bottleneck ? "Bottleneck" : "", selectedNode.automation_candidate ? "Automation" : ""].filter(Boolean).join(", ") || "None"} />
          </div>
        ) : null}
      </section>
      <section className="edge-list">
        {visibleEdges.map((edge) => (
          <div
            key={`${edge.source}-${edge.target}`}
            className={["edge-row", selectedNode && (edge.source === selectedNode.activity || edge.target === selectedNode.activity) ? "is-linked" : ""].join(" ")}
          >
            <span>{edge.source}</span>
            <span>to</span>
            <span>{edge.target}</span>
            <b>{edge.frequency}x</b>
            <span>{edge.average_transition_seconds.toFixed(0)}s avg</span>
          </div>
        ))}
        {visibleEdges.length === 0 ? <p className="empty">No transitions match the filters</p> : null}
      </section>
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

type CandidateSortKey = "score" | "frequency" | "classification" | "reason" | "status";

const reviewOptions: Array<{ label: string; value: AutomationReviewStatus }> = [
  { label: "Unreviewed", value: "unreviewed" },
  { label: "Adopt", value: "adopted" },
  { label: "Hold", value: "on_hold" },
  { label: "Reject", value: "rejected" }
];

function CandidatesView({ candidates, actions, working }: { candidates: AutomationCandidate[]; actions: AppActions; working: boolean }) {
  const [sortKey, setSortKey] = useState<CandidateSortKey>("score");
  const sortedCandidates = useMemo(() => {
    const valueFor = (candidate: AutomationCandidate) => {
      if (sortKey === "score") return candidate.automation_score;
      if (sortKey === "frequency") return candidate.frequency;
      if (sortKey === "classification") return candidate.classification;
      if (sortKey === "reason") return candidate.reasons.join(", ");
      return candidate.review_status || "unreviewed";
    };
    return [...candidates].sort((a, b) => {
      const first = valueFor(a);
      const second = valueFor(b);
      if (typeof first === "number" && typeof second === "number") return second - first;
      return String(first).localeCompare(String(second));
    });
  }, [candidates, sortKey]);

  return (
    <section className="candidate-workspace">
      <div className="candidate-toolbar">
        <select value={sortKey} onChange={(event) => setSortKey(event.target.value as CandidateSortKey)}>
          <option value="score">Score</option>
          <option value="frequency">Frequency</option>
          <option value="classification">Classification</option>
          <option value="reason">Reason</option>
          <option value="status">Review status</option>
        </select>
      </div>
      <section className="candidate-list">
        {sortedCandidates.map((candidate) => {
          const status = candidate.review_status || "unreviewed";
          return (
            <article className="candidate-card" key={candidate.activity}>
              <div>
                <h2>{candidate.activity}</h2>
                <p>{candidate.reasons.join(", ")}</p>
              </div>
              <div className="candidate-metrics">
                <b>{Math.round(candidate.automation_score * 100)}</b>
                <span>{candidate.frequency}x</span>
              </div>
              <span>{candidate.classification}</span>
              <div className="review-controls">
                {reviewOptions.map((option) => (
                  <button
                    key={option.value}
                    className={option.value === status ? "is-active" : ""}
                    onClick={() => void actions.saveAutomationReview(candidate.activity, option.value)}
                    disabled={working || option.value === status}
                  >
                    {option.label}
                  </button>
                ))}
              </div>
            </article>
          );
        })}
      </section>
    </section>
  );
}

function ReportsView({ markdown }: { markdown: string }) {
  return <pre className="report-preview">{markdown}</pre>;
}

function SettingsView({ data, actions, working }: { data: DashboardData; actions: AppActions; working: boolean }) {
  const [settingsDraft, setSettingsDraft] = useState<AppSettings>(data.settings);

  useEffect(() => {
    setSettingsDraft(data.settings);
  }, [data.settings]);

  return (
    <section className="settings-workspace">
      <section className="operation-panel">
        <div className="panel-heading">
          <h2>Privacy Controls</h2>
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
        <label className="text-row">
          <span>Excluded apps</span>
          <textarea
            value={listToText(settingsDraft.excluded_apps)}
            onChange={(event) => setSettingsDraft({ ...settingsDraft, excluded_apps: textToList(event.target.value) })}
          />
        </label>
        <label className="text-row">
          <span>Excluded domains</span>
          <textarea
            value={listToText(settingsDraft.excluded_domains)}
            onChange={(event) => setSettingsDraft({ ...settingsDraft, excluded_domains: textToList(event.target.value) })}
          />
        </label>
        <div className="danger-row">
          <button onClick={() => void actions.saveSettings(settingsDraft)} disabled={working}>
            Save Settings
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
      <section className="settings-grid">
        <Setting label="API bind" value={data.health.bind} />
        <Setting label="External network" value={data.health.local_only ? "Blocked by policy" : "Unknown"} />
        <Setting label="LLM integration" value={data.health.llm_supported ? "Enabled" : "Not supported"} />
        <Setting label="Data storage" value={data.health.storage_mode} />
        <Setting label="Events loaded" value={data.health.event_count.toString()} />
        <Setting label="ActivityWatch" value="Optional localhost import only" />
        <Setting label="Sensitive capture" value="No keystrokes, screenshots, audio, or camera" />
      </section>
    </section>
  );
}

function listToText(items: string[]): string {
  return items.join("\n");
}

function textToList(value: string): string[] {
  return value
    .split(/[\n,]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function defaultExportPath(format: ExportFormat): string {
  const extensionByFormat: Record<ExportFormat, string> = {
    markdown: "md",
    json: "json",
    csv: "csv",
    mermaid: "mmd",
    drawio: "drawio"
  };
  return `exports/opsmineflow-export.${extensionByFormat[format]}`;
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <article className="metric">
      <span>{label}</span>
      <strong>{value}</strong>
    </article>
  );
}

function DetailStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="detail-stat">
      <span>{label}</span>
      <b>{value}</b>
    </div>
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
