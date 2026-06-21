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
  saveSettings,
  startRecording,
  stopRecording
} from "./api";
import { useI18n } from "./i18n";
import type { TranslationKey } from "./i18n";
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
  RecordingStatus,
  Summary
} from "./types";

type Tab = "home" | "dashboard" | "events" | "process" | "switching" | "candidates" | "reports" | "settings";

type DashboardData = {
  health: Health;
  diagnostics: Diagnostics;
  recording: RecordingStatus;
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
  startRecording: (caseId: string, activityLabel: string, clearSample: boolean) => Promise<void>;
  stopRecording: () => Promise<void>;
};

const tabs: Array<{ id: Tab; label: TranslationKey }> = [
  { id: "home", label: "nav.home" },
  { id: "dashboard", label: "nav.dashboard" },
  { id: "events", label: "nav.events" },
  { id: "process", label: "nav.process" },
  { id: "switching", label: "nav.switching" },
  { id: "candidates", label: "nav.candidates" },
  { id: "reports", label: "nav.reports" },
  { id: "settings", label: "nav.settings" }
];

export function App() {
  const { language, setLanguage, t } = useI18n();
  const [activeTab, setActiveTab] = useState<Tab>("home");
  const [data, setData] = useState<DashboardData | null>(null);
  const [error, setError] = useState<string>("");
  const [actionMessage, setActionMessage] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [working, setWorking] = useState(false);

  async function refresh(silent = false) {
    if (!silent) setLoading(true);
    setError("");
    try {
      setData(await loadDashboardData());
    } catch (err) {
      setError(err instanceof Error ? err.message : t("message.apiUnavailable", { error: "" }));
    } finally {
      if (!silent) setLoading(false);
    }
  }

  useEffect(() => {
    void refresh();
  }, []);

  useEffect(() => {
    if (!data?.recording.active) return;
    const timer = window.setInterval(() => void refresh(true), 2000);
    return () => window.clearInterval(timer);
  }, [data?.recording.active]);

  async function runAction(task: () => Promise<string>) {
    setWorking(true);
    setError("");
    setActionMessage("");
    try {
      const message = await task();
      setActionMessage(message);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : t("message.actionFailed"));
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
        setError(err instanceof Error ? err.message : t("message.previewFailed"));
        throw err;
      } finally {
        setWorking(false);
      }
    },
    importEvents: (format, path) =>
      runAction(async () => {
        const result = await importEvents(format, path);
        return t("message.imported", { count: result.imported_events, source: result.source || format });
      }),
    importActivityWatch: () =>
      runAction(async () => {
        const result = await importActivityWatchLocal(true);
        return t("message.activityImported", { count: result.imported_events });
      }),
    previewExport: async (format) => {
      setWorking(true);
      setError("");
      setActionMessage("");
      try {
        return await previewExport(format);
      } catch (err) {
        setError(err instanceof Error ? err.message : t("message.exportPreviewFailed"));
        throw err;
      } finally {
        setWorking(false);
      }
    },
    exportArtifact: (format) =>
      runAction(async () => {
        if (!window.confirm(t("message.exportReview"))) {
          return t("message.exportCancelled");
        }
        const filename = downloadExport(format, await exportArtifact(format));
        return t("message.downloaded", { filename });
      }),
    saveExport: (format, path) =>
      runAction(async () => {
        if (!window.confirm(t("message.exportReview"))) {
          return t("message.exportCancelled");
        }
        const result = await saveExport(format, path);
        return t("message.savedExport", { format: result.format, path: result.path });
      }),
    saveSettings: (settings) =>
      runAction(async () => {
        await saveSettings(settings);
        return t("message.settingsSaved");
      }),
    saveAutomationReview: (activity, status) =>
      runAction(async () => {
        const result = await saveAutomationReview(activity, status);
        return t("message.reviewSaved", { activity: result.activity });
      }),
    runDiagnosticChecks: async () => {
      setWorking(true);
      setError("");
      setActionMessage("");
      try {
        const result = await runDiagnosticChecks();
        setActionMessage(t("message.diagnosticsFinished"));
        return result;
      } catch (err) {
        setError(err instanceof Error ? err.message : t("message.diagnosticsFailed"));
        throw err;
      } finally {
        setWorking(false);
      }
    },
    deleteData: () =>
      runAction(async () => {
        await deleteLocalData();
        return t("message.dataDeleted");
      }),
    startRecording: (caseId, activityLabel, clearSample) =>
      runAction(async () => {
        if (clearSample) await deleteLocalData();
        await startRecording(caseId, activityLabel);
        return t("message.recordingStarted");
      }),
    stopRecording: () =>
      runAction(async () => {
        const result = await stopRecording();
        return t("message.recordingStopped", { count: result.recorded_events });
      })
  };

  const demoMode = Boolean(data && data.events.length > 0 && data.importHistory.length === 0);
  const openCollection = () => {
    setActiveTab("home");
    window.setTimeout(() => document.getElementById("collection-start")?.scrollIntoView({ behavior: "smooth", block: "start" }), 0);
  };

  return (
    <main className="app-shell">
      <header className="topbar">
        <div>
          <h1>OpsMineFlow</h1>
          <p>{t("app.tagline")}</p>
        </div>
        <div className="status-strip">
          <div className="language-switch" role="group" aria-label={t("language.label")}>
            <button className={language === "ja" ? "is-active" : ""} onClick={() => setLanguage("ja")} aria-pressed={language === "ja"}>
              日本語
            </button>
            <button className={language === "en" ? "is-active" : ""} onClick={() => setLanguage("en")} aria-pressed={language === "en"}>
              English
            </button>
          </div>
          <StatusPill label={t("status.network")} value={data?.health.local_only ? t("status.localOnly") : t("status.checking")} tone="good" />
          <StatusPill label={t("status.llm")} value={data?.health.llm_supported ? t("status.enabled") : t("status.notSupported")} tone="neutral" />
          <button className="refresh-button" title={t("action.refreshHelp")} onClick={() => void refresh()} disabled={loading}>
            {t("action.refresh")}
          </button>
        </div>
      </header>

      <nav className="tabs" aria-label={t("nav.views")}>
        {tabs.map((tab) => (
          <button
            key={tab.id}
            className={tab.id === activeTab ? "tab is-active" : "tab"}
            onClick={() => setActiveTab(tab.id)}
          >
            {t(tab.label)}
          </button>
        ))}
      </nav>

      {demoMode ? (
        <section className="data-state-banner sample-state" aria-live="polite">
          <div>
            <strong>{t("sample.title")}</strong>
            <p>{t("sample.body")}</p>
          </div>
          <button
            className="danger-button"
            onClick={() => {
              if (window.confirm(t("confirm.deleteData"))) void actions.deleteData();
            }}
            disabled={working}
          >
            {t("action.deleteSample")}
          </button>
        </section>
      ) : null}
      {error ? <div className="api-warning">{t("message.apiUnavailable", { error })}</div> : null}
      {actionMessage ? <div className="action-message">{actionMessage}</div> : null}
      {loading && !data ? <div className="loading">{t("message.loading")}</div> : null}

      {data ? <View tab={activeTab} data={data} actions={actions} working={working || loading} onStart={openCollection} /> : null}
    </main>
  );
}

function View({
  tab,
  data,
  actions,
  working,
  onStart
}: {
  tab: Tab;
  data: DashboardData;
  actions: AppActions;
  working: boolean;
  onStart: () => void;
}) {
  if (tab === "home") return <HomeView data={data} actions={actions} working={working} />;
  if (data.events.length === 0 && tab !== "settings") return <EmptyDataView onStart={onStart} />;
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
  const { formatDateTime, t } = useI18n();
  const [format, setFormat] = useState<"csv" | "json">("csv");
  const [path, setPath] = useState("");
  const [activityWatchEnabled, setActivityWatchEnabled] = useState(false);
  const [collectionOpen, setCollectionOpen] = useState(data.events.length === 0);
  const [settingsDraft, setSettingsDraft] = useState<AppSettings>(data.settings);
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const [exportFormat, setExportFormat] = useState<ExportFormat>("markdown");
  const [exportPath, setExportPath] = useState(defaultExportPath("markdown"));
  const [exportPreview, setExportPreview] = useState<ExportPreview | null>(null);
  const [diagnosticChecks, setDiagnosticChecks] = useState<DiagnosticChecks | null>(null);

  useEffect(() => {
    setSettingsDraft(data.settings);
  }, [data.settings]);

  useEffect(() => {
    if (data.events.length === 0) setCollectionOpen(true);
  }, [data.events.length]);

  return (
    <section className="home-grid">
      <RecordingPanel data={data} actions={actions} working={working} />

      <section className="collection-intro" id="collection-start">
        <div>
          <h2>{t("collection.title")}</h2>
          <p>{t("collection.body")}</p>
        </div>
        <button onClick={() => setCollectionOpen((current) => !current)} aria-expanded={collectionOpen}>
          {collectionOpen ? t("action.cancel") : t("action.startCollecting")}
        </button>
        {collectionOpen ? (
          <div className="collection-options">
            <div>
              <strong>{t("collection.fileTitle")}</strong>
              <p>{t("collection.fileBody")}</p>
              <button
                onClick={() => {
                  document.getElementById("import-panel")?.scrollIntoView({ behavior: "smooth", block: "start" });
                }}
              >
                {t("action.goToImport")}
              </button>
            </div>
            <div>
              <strong>{t("collection.activityTitle")}</strong>
              <p>{t("collection.activityBody")}</p>
            </div>
            <div>
              <strong>{t("collection.autoTitle")}</strong>
              <p>{t("collection.autoBody")}</p>
            </div>
          </div>
        ) : null}
      </section>

      <section className="operation-panel primary-panel" id="import-panel">
        <div className="panel-heading">
          <h2>{t("import.title")}</h2>
          <span>{t("import.loaded", { count: data.events.length })}</span>
        </div>
        <div className="inline-fields">
          <select value={format} onChange={(event) => setFormat(event.target.value as "csv" | "json")} disabled={working}>
            <option value="csv">CSV</option>
            <option value="json">JSON</option>
          </select>
          <input
            value={path}
            onChange={(event) => setPath(event.target.value)}
            disabled={working}
            placeholder={t("import.path")}
            aria-label={t("import.path")}
          />
          <button
            onClick={() => {
              void actions.previewImport(format, path).then(setPreview);
            }}
            disabled={working || path.trim() === ""}
          >
            {t("action.preview")}
          </button>
        </div>
        {preview ? (
          <div className="preview-panel">
            <div className="preview-summary">
              <b>{t("import.events", { count: preview.event_count })}</b>
              <span>{t("import.confidential", { count: preview.confidential_count })}</span>
            </div>
            <div className="preview-list">
              {preview.sample_events.map((event, index) => (
                <div className="preview-row" key={`${event.case_id}-${event.activity}-${index}`}>
                  <span>{event.case_id}</span>
                  <b>{event.activity}</b>
                  <span>{event.app_name || t("import.unknown")}</span>
                  <span>{t("unit.secondsShort", { count: Math.round(event.duration_seconds) })}</span>
                </div>
              ))}
            </div>
          </div>
        ) : null}
        <button onClick={() => void actions.importEvents(format, path)} disabled={working || path.trim() === ""}>
          {t("import.previewed")}
        </button>
        <label className="check-row">
          <input
            type="checkbox"
            checked={activityWatchEnabled}
            onChange={(event) => setActivityWatchEnabled(event.target.checked)}
            disabled={working}
          />
          <span>{t("import.activityConsent")}</span>
        </label>
        <button onClick={() => void actions.importActivityWatch()} disabled={working || !activityWatchEnabled}>
          {t("import.activityButton")}
        </button>
        {data.importHistory.length > 0 ? (
          <div className="history-list">
            <strong>{t("import.history")}</strong>
            {data.importHistory.slice(0, 4).map((item) => (
              <div className="history-row" key={`${item.imported_at}-${item.path}`}>
                <span>{item.source}</span>
                <b>{item.event_count}</b>
                <span>{formatDateTime(item.imported_at)}</span>
              </div>
            ))}
          </div>
        ) : null}
      </section>

      <section className="operation-panel">
        <div className="panel-heading">
          <h2>{t("export.title")}</h2>
          <span>{exportPreview ? t("export.bytes", { count: exportPreview.byte_size }) : t("export.localOnly")}</span>
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
            {t("action.preview")}
          </button>
        </div>
        {exportPreview ? (
          <div className="export-preview-box">
            <div className="preview-summary">
              <b>{exportPreview.filename}</b>
              <span>{t("import.confidential", { count: exportPreview.confidential_count })}</span>
            </div>
            <pre>{exportPreview.preview}</pre>
          </div>
        ) : null}
        <div className="button-grid">
          <button onClick={() => void actions.saveExport(exportFormat, exportPath)} disabled={working || !exportPath.trim()}>
            {t("action.savePath")}
          </button>
          <button onClick={() => void actions.exportArtifact(exportFormat)} disabled={working}>
            {t("action.download")}
          </button>
        </div>
      </section>

      <section className="operation-panel">
        <div className="panel-heading">
          <h2>{t("settings.title")}</h2>
          <span>{t("settings.days", { count: settingsDraft.retention_days })}</span>
        </div>
        <label className="check-row">
          <input
            type="checkbox"
            checked={settingsDraft.mask_url_paths}
            onChange={(event) => setSettingsDraft({ ...settingsDraft, mask_url_paths: event.target.checked })}
          />
          <span>{t("settings.maskUrls")}</span>
        </label>
        <label className="check-row">
          <input
            type="checkbox"
            checked={settingsDraft.mask_window_titles}
            onChange={(event) => setSettingsDraft({ ...settingsDraft, mask_window_titles: event.target.checked })}
          />
          <span>{t("settings.maskWindows")}</span>
        </label>
        <label className="number-row">
          <span>{t("settings.retention")}</span>
          <input
            type="number"
            min="1"
            max="365"
            value={settingsDraft.retention_days}
            onChange={(event) => setSettingsDraft({ ...settingsDraft, retention_days: Number(event.target.value) })}
          />
        </label>
        <label className="text-row">
          <span>{t("settings.excludedApps")}</span>
          <textarea
            value={listToText(settingsDraft.excluded_apps)}
            onChange={(event) => setSettingsDraft({ ...settingsDraft, excluded_apps: textToList(event.target.value) })}
          />
        </label>
        <label className="text-row">
          <span>{t("settings.excludedDomains")}</span>
          <textarea
            value={listToText(settingsDraft.excluded_domains)}
            onChange={(event) => setSettingsDraft({ ...settingsDraft, excluded_domains: textToList(event.target.value) })}
          />
        </label>
        <button onClick={() => void actions.saveSettings(settingsDraft)} disabled={working}>
          {t("action.save")}
        </button>
      </section>

      <section className="operation-panel">
        <div className="panel-heading">
          <h2>{t("diagnostics.title")}</h2>
          <span>{localizeStatus(data.diagnostics.webui.status, t)}</span>
        </div>
        <div className="diagnostic-list">
          <Setting label={t("diagnostics.api")} value={`${localizeStatus(data.diagnostics.api.status, t)} ${data.diagnostics.api.bind}:${data.diagnostics.api.port}`} />
          <Setting label={t("diagnostics.webui")} value={`${localizeStatus(data.diagnostics.webui.status, t)} ${data.diagnostics.webui.expected_url}`} />
          <Setting label={t("diagnostics.storage")} value={`${data.diagnostics.storage.storage_mode} ${data.diagnostics.storage.storage_path || ""}`} />
          <Setting label={t("diagnostics.events")} value={data.diagnostics.storage.event_count.toString()} />
          <Setting label={t("diagnostics.reviews")} value={data.diagnostics.storage.automation_review_count.toString()} />
          <Setting
            label={t("diagnostics.recording")}
            value={data.diagnostics.recording.available ? t("status.available") : t("status.unavailable")}
          />
          <Setting label={t("diagnostics.activitywatch")} value={`${data.diagnostics.activitywatch.enabled ? t("status.enabled") : localizeStatus("disabled", t)} / ${localizeStatus(data.diagnostics.activitywatch.status, t)}`} />
          <Setting label={t("diagnostics.external")} value={localizeStatus(data.diagnostics.runtime_policy.external_network, t)} />
          {Object.entries(data.diagnostics.dependencies).map(([name, item]) => (
            <Setting key={name} label={name} value={`${localizeStatus(item.status, t)}${item.version ? ` / ${item.version}` : ""}`} />
          ))}
          {Object.entries(data.diagnostics.ports).map(([name, item]) => (
            <Setting key={name} label={t("diagnostics.port", { name })} value={`${item.host}:${item.port} / ${localizeStatus(item.status, t)}`} />
          ))}
        </div>
        {diagnosticChecks ? (
          <div className="check-results">
            {Object.entries(diagnosticChecks).map(([name, result]) => (
              <div className="check-result" key={name}>
                <span>{result.command}</span>
                <b>{localizeStatus(result.status, t)}</b>
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
            {t("action.runChecks")}
          </button>
          <button onClick={() => void actions.refresh()} disabled={working}>
            {t("action.refresh")}
          </button>
          <button
            className="danger-button"
            onClick={() => {
              if (window.confirm(t("confirm.deleteData"))) void actions.deleteData();
            }}
            disabled={working}
          >
            {t("action.delete")}
          </button>
        </div>
      </section>
    </section>
  );
}

function RecordingPanel({ data, actions, working }: { data: DashboardData; actions: AppActions; working: boolean }) {
  const { t } = useI18n();
  const [caseId, setCaseId] = useState(() => `WORK-${new Date().toISOString().slice(0, 10)}`);
  const [activityLabel, setActivityLabel] = useState("");
  const [consent, setConsent] = useState(false);
  const [clock, setClock] = useState(Date.now());
  const status = data.recording;
  const sampleDataLoaded = data.events.length > 0 && data.importHistory.length === 0;

  useEffect(() => {
    if (!status.active) return;
    const timer = window.setInterval(() => setClock(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [status.active]);

  const elapsedSeconds = status.active && status.started_at
    ? Math.max(Math.floor((clock - new Date(status.started_at).getTime()) / 1000), 0)
    : 0;

  return (
    <section className={status.active ? "recording-panel is-recording" : "recording-panel"} aria-live="polite">
      <div className="recording-heading">
        <div>
          <span className="recording-kicker">{t("recording.kicker")}</span>
          <h2>{status.active ? t("recording.activeTitle") : t("recording.title")}</h2>
          <p>{status.active ? t("recording.activeBody") : t("recording.body")}</p>
        </div>
        <strong className="recording-state">{status.active ? t("recording.active") : t("recording.stopped")}</strong>
      </div>

      {status.active ? (
        <div className="recording-live-grid">
          <DetailStat label={t("recording.currentApp")} value={status.current_app || t("recording.waitingForApp")} />
          <DetailStat label={t("recording.elapsed")} value={formatElapsed(elapsedSeconds)} />
          <DetailStat label={t("recording.eventsRecorded")} value={status.recorded_events.toString()} />
          <DetailStat label={t("recording.caseName")} value={status.case_id} />
          <DetailStat label={t("recording.workLabel")} value={status.activity_label} />
          <button className="stop-recording-button" onClick={() => void actions.stopRecording()} disabled={working}>
            {t("recording.stop")}
          </button>
        </div>
      ) : (
        <div className="recording-setup">
          <label>
            <span>{t("recording.caseName")}</span>
            <input value={caseId} onChange={(event) => setCaseId(event.target.value)} placeholder={t("recording.casePlaceholder")} disabled={working} />
          </label>
          <label>
            <span>{t("recording.workLabel")}</span>
            <input value={activityLabel} onChange={(event) => setActivityLabel(event.target.value)} placeholder={t("recording.workPlaceholder")} disabled={working} />
          </label>
          <label className="recording-consent">
            <input type="checkbox" checked={consent} onChange={(event) => setConsent(event.target.checked)} disabled={working || !status.available} />
            <span>{t("recording.consent")}</span>
          </label>
          <button
            className="start-recording-button"
            onClick={() => {
              if (sampleDataLoaded && !window.confirm(t("recording.confirmSampleRemoval"))) return;
              void actions.startRecording(caseId, activityLabel, sampleDataLoaded);
            }}
            disabled={working || !status.available || !consent || !caseId.trim() || !activityLabel.trim()}
          >
            {t("recording.start")}
          </button>
        </div>
      )}

      <div className="recording-scope">
        <strong>{t("recording.scopeTitle")}</strong>
        <span>{t("recording.scopeBody")}</span>
      </div>
      {!status.available ? <div className="api-warning">{status.remediation || t("recording.unavailable")}</div> : null}
      {status.last_error ? <div className="api-warning">{status.last_error}</div> : null}
    </section>
  );
}

function DashboardView({ data }: { data: DashboardData }) {
  const { t } = useI18n();
  const totalMinutes = Math.round(data.summary.total_active_seconds / 60);
  return (
    <section className="view-grid">
      <Metric label={t("dashboard.events")} value={data.summary.total_events.toString()} />
      <Metric label={t("dashboard.activeMinutes")} value={totalMinutes.toString()} />
      <Metric label={t("dashboard.avgSeconds")} value={data.summary.average_event_duration_seconds.toFixed(0)} />
      <Metric label={t("dashboard.candidates")} value={data.candidates.length.toString()} />
      <BarPanel title={t("dashboard.appTime")} values={data.summary.app_usage_seconds} />
      <BarPanel title={t("dashboard.labelTime")} values={data.summary.label_usage_seconds} />
      <TopList
        title={t("dashboard.topCandidates")}
        rows={data.candidates.slice(0, 10).map((item) => ({
          key: item.activity,
          value: `${Math.round(item.automation_score * 100)} / ${localizeClassification(item.classification, t)}`
        }))}
      />
      <TopList
        title={t("dashboard.bottlenecks")}
        rows={data.processMap.nodes
          .filter((node) => node.bottleneck)
          .slice(0, 10)
          .map((node) => ({
            key: node.activity,
            value: t("unit.secondsAverage", { count: node.average_duration_seconds.toFixed(0) })
          }))}
      />
    </section>
  );
}

function EventsView({ events }: { events: EventRecord[] }) {
  const { t } = useI18n();
  return (
    <section className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>{t("table.case")}</th>
            <th>{t("table.activity")}</th>
            <th>{t("table.app")}</th>
            <th>{t("table.window")}</th>
            <th>{t("table.domain")}</th>
            <th>{t("table.seconds")}</th>
            <th>{t("table.masking")}</th>
          </tr>
        </thead>
        <tbody>
          {events.map((event) => (
            <tr key={event.event_id}>
              <td>{event.case_id}</td>
              <td>{event.activity_raw}</td>
              <td>{event.app_name || t("import.unknown")}</td>
              <td>{event.window_title_masked || "-"}</td>
              <td>{event.domain || "-"}</td>
              <td>{event.duration_seconds.toFixed(0)}</td>
              <td>{event.confidential_flag ? t("table.confidential") : t("table.masked")}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

function ProcessView({ processMap, events }: { processMap: ProcessMap; events: EventRecord[] }) {
  const { t } = useI18n();
  const [query, setQuery] = useState("");
  const [appFilter, setAppFilter] = useState("all");
  const [selectedActivity, setSelectedActivity] = useState("");
  const activityApps = useMemo(() => {
    const mapping = new Map<string, Set<string>>();
    for (const event of events) {
      if (!mapping.has(event.activity_raw)) mapping.set(event.activity_raw, new Set<string>());
      mapping.get(event.activity_raw)?.add(event.app_name || t("import.unknown"));
    }
    return mapping;
  }, [events, t]);
  const appOptions = useMemo(() => {
    return Array.from(new Set(events.map((event) => event.app_name || t("import.unknown")))).sort();
  }, [events, t]);
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
        <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder={t("process.filter")} />
        <select value={appFilter} onChange={(event) => setAppFilter(event.target.value)}>
          <option value="all">{t("process.allApps")}</option>
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
            <span>{t("process.frequency")} {node.frequency}</span>
            <span>{t("process.avgSeconds")} {t("unit.secondsShort", { count: node.average_duration_seconds.toFixed(0) })}</span>
            <span>{t("process.startEnd", { start: processMap.start_activities[node.activity] || 0, end: processMap.end_activities[node.activity] || 0 })}</span>
          </button>
        ))}
        {visibleNodes.length === 0 ? <p className="empty">{t("process.noNodes")}</p> : null}
      </section>
      <section className="process-detail">
        <div>
          <h2>{selectedNode?.activity || t("process.noActivity")}</h2>
          <p>{selectedApps.join(", ") || t("process.noApp")}</p>
        </div>
        {selectedNode ? (
          <div className="process-detail-grid">
            <DetailStat label={t("process.frequency")} value={selectedNode.frequency.toString()} />
            <DetailStat label={t("process.avgSeconds")} value={selectedNode.average_duration_seconds.toFixed(0)} />
            <DetailStat label={t("process.startCount")} value={(processMap.start_activities[selectedNode.activity] || 0).toString()} />
            <DetailStat label={t("process.endCount")} value={(processMap.end_activities[selectedNode.activity] || 0).toString()} />
            <DetailStat label={t("process.events")} value={selectedEvents.length.toString()} />
            <DetailStat label={t("process.signals")} value={[selectedNode.bottleneck ? t("process.bottleneck") : "", selectedNode.automation_candidate ? t("process.automation") : ""].filter(Boolean).join(", ") || t("process.none")} />
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
            <span>{t("process.to")}</span>
            <span>{edge.target}</span>
            <b>{t("unit.times", { count: edge.frequency })}</b>
            <span>{t("unit.secondsAverage", { count: edge.average_transition_seconds.toFixed(0) })}</span>
          </div>
        ))}
        {visibleEdges.length === 0 ? <p className="empty">{t("process.noTransitions")}</p> : null}
      </section>
    </section>
  );
}

function SwitchingView({ switching }: { switching: AppSwitching }) {
  const { t } = useI18n();
  return (
    <section className="split-view">
      <TopList
        title={t("switching.ranking")}
        rows={switching.transition_ranking.map((item) => ({
          key: `${item.source_app} ${t("process.to")} ${item.target_app}`,
          value: `${item.count}`
        }))}
      />
      <TopList
        title={t("switching.roundTrips")}
        rows={switching.round_trips.map((item) => ({
          key: item.pattern,
          value: `${item.count}`
        }))}
      />
    </section>
  );
}

type CandidateSortKey = "score" | "frequency" | "classification" | "reason" | "status";

const reviewOptions: Array<{ label: TranslationKey; value: AutomationReviewStatus }> = [
  { label: "candidate.unreviewed", value: "unreviewed" },
  { label: "candidate.adopt", value: "adopted" },
  { label: "candidate.hold", value: "on_hold" },
  { label: "candidate.reject", value: "rejected" }
];

function CandidatesView({ candidates, actions, working }: { candidates: AutomationCandidate[]; actions: AppActions; working: boolean }) {
  const { t } = useI18n();
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
          <option value="score">{t("candidate.sortScore")}</option>
          <option value="frequency">{t("candidate.sortFrequency")}</option>
          <option value="classification">{t("candidate.sortClassification")}</option>
          <option value="reason">{t("candidate.sortReason")}</option>
          <option value="status">{t("candidate.sortStatus")}</option>
        </select>
      </div>
      <section className="candidate-list">
        {sortedCandidates.map((candidate) => {
          const status = candidate.review_status || "unreviewed";
          return (
            <article className="candidate-card" key={candidate.activity}>
              <div>
                <h2>{candidate.activity}</h2>
                <p>{candidate.reasons.map((reason) => localizeReason(reason, t)).join(", ")}</p>
              </div>
              <div className="candidate-metrics">
                <b>{Math.round(candidate.automation_score * 100)}</b>
                <span>{candidate.frequency}x</span>
              </div>
              <span>{localizeClassification(candidate.classification, t)}</span>
              <div className="review-controls">
                {reviewOptions.map((option) => (
                  <button
                    key={option.value}
                    className={option.value === status ? "is-active" : ""}
                    onClick={() => void actions.saveAutomationReview(candidate.activity, option.value)}
                    disabled={working || option.value === status}
                  >
                    {t(option.label)}
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
  const { t } = useI18n();
  const [settingsDraft, setSettingsDraft] = useState<AppSettings>(data.settings);

  useEffect(() => {
    setSettingsDraft(data.settings);
  }, [data.settings]);

  return (
    <section className="settings-workspace">
      <section className="operation-panel">
        <div className="panel-heading">
          <h2>{t("settings.privacy")}</h2>
          <span>{t("settings.days", { count: settingsDraft.retention_days })}</span>
        </div>
        <label className="check-row">
          <input
            type="checkbox"
            checked={settingsDraft.mask_url_paths}
            onChange={(event) => setSettingsDraft({ ...settingsDraft, mask_url_paths: event.target.checked })}
          />
          <span>{t("settings.maskUrls")}</span>
        </label>
        <label className="check-row">
          <input
            type="checkbox"
            checked={settingsDraft.mask_window_titles}
            onChange={(event) => setSettingsDraft({ ...settingsDraft, mask_window_titles: event.target.checked })}
          />
          <span>{t("settings.maskWindows")}</span>
        </label>
        <label className="number-row">
          <span>{t("settings.retention")}</span>
          <input
            type="number"
            min="1"
            max="365"
            value={settingsDraft.retention_days}
            onChange={(event) => setSettingsDraft({ ...settingsDraft, retention_days: Number(event.target.value) })}
          />
        </label>
        <label className="text-row">
          <span>{t("settings.excludedApps")}</span>
          <textarea
            value={listToText(settingsDraft.excluded_apps)}
            onChange={(event) => setSettingsDraft({ ...settingsDraft, excluded_apps: textToList(event.target.value) })}
          />
        </label>
        <label className="text-row">
          <span>{t("settings.excludedDomains")}</span>
          <textarea
            value={listToText(settingsDraft.excluded_domains)}
            onChange={(event) => setSettingsDraft({ ...settingsDraft, excluded_domains: textToList(event.target.value) })}
          />
        </label>
        <div className="danger-row">
          <button onClick={() => void actions.saveSettings(settingsDraft)} disabled={working}>
            {t("action.save")}
          </button>
          <button
            className="danger-button"
            onClick={() => {
              if (window.confirm(t("confirm.deleteData"))) void actions.deleteData();
            }}
            disabled={working}
          >
            {t("action.delete")}
          </button>
        </div>
      </section>
      <section className="settings-grid">
        <Setting label={t("settings.apiBind")} value={data.health.bind} />
        <Setting label={t("diagnostics.external")} value={data.health.local_only ? t("status.blocked") : t("status.unknown")} />
        <Setting label={t("settings.llmIntegration")} value={data.health.llm_supported ? t("status.enabled") : t("status.notSupported")} />
        <Setting label={t("settings.dataStorage")} value={data.health.storage_mode} />
        <Setting label={t("settings.eventsLoaded")} value={data.health.event_count.toString()} />
        <Setting label={t("diagnostics.activitywatch")} value={t("status.optionalLocalImport")} />
        <Setting label={t("settings.sensitiveCapture")} value={t("status.noSensitiveCapture")} />
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

function formatElapsed(totalSeconds: number): string {
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  return [hours, minutes, seconds].map((value) => value.toString().padStart(2, "0")).join(":");
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
  const { t } = useI18n();
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
            <b>{t("unit.minutesShort", { count: Math.round(value / 60) })}</b>
          </div>
        ))}
      </div>
    </section>
  );
}

function TopList({ title, rows }: { title: string; rows: Array<{ key: string; value: string }> }) {
  const { t } = useI18n();
  return (
    <section className="panel">
      <h2>{title}</h2>
      <div className="rank-list">
        {rows.length === 0 ? <p className="empty">{t("common.noItems")}</p> : null}
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

function EmptyDataView({ onStart }: { onStart: () => void }) {
  const { t } = useI18n();
  return (
    <section className="empty-workspace">
      <h2>{t("empty.title")}</h2>
      <p>{t("empty.body")}</p>
      <button onClick={onStart}>{t("action.startCollecting")}</button>
    </section>
  );
}

type Translate = (key: TranslationKey, params?: Record<string, string | number>) => string;

function localizeClassification(classification: string, t: Translate): string {
  const key = `candidate.class.${classification}` as TranslationKey;
  return key in classificationKeys ? t(key) : classification;
}

const classificationKeys: Partial<Record<TranslationKey, true>> = {
  "candidate.class.rpa": true,
  "candidate.class.operations_rule_change": true,
  "candidate.class.system_change": true,
  "candidate.class.improvement_review": true
};

function localizeReason(reason: string, t: Translate): string {
  const key = `candidate.reason.${reason}` as TranslationKey;
  return key in reasonKeys ? t(key) : reason;
}

const reasonKeys: Partial<Record<TranslationKey, true>> = {
  "candidate.reason.repeated activity": true,
  "candidate.reason.rule-based wording": true,
  "candidate.reason.manual transfer risk": true,
  "candidate.reason.system handover": true,
  "candidate.reason.low-volume hypothesis": true
};

function localizeStatus(status: string, t: Translate): string {
  const normalized = status.trim().toLowerCase().replaceAll(" ", "_");
  const statusKeys: Record<string, TranslationKey> = {
    ok: "status.ready",
    ready: "status.ready",
    passed: "status.passed",
    available: "status.available",
    free: "status.available",
    enabled: "status.enabled",
    disabled: "status.disabled",
    unavailable: "status.unavailable",
    blocked: "status.blocked",
    blocked_by_policy: "status.blocked",
    not_checked: "status.notChecked",
    reachable: "status.reachable",
    installed: "status.installed",
    detected: "status.detected",
    bound_by_current_api: "status.bound",
    open: "status.open",
    not_detected: "status.notDetected",
    not_open: "status.notOpen",
    not_reachable: "status.notReachable"
  };
  return statusKeys[normalized] ? t(statusKeys[normalized]) : status;
}
