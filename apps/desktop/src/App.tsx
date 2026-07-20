import { useEffect, useMemo, useState } from "react";
import {
  approveEventQuality,
  deleteLocalData,
  excludeEvent,
  exportArtifact,
  getNativeRuntimeStatus,
  importActivityWatchLocal,
  importEvents,
  loadDashboardData,
  mergeEvents,
  pauseRecording,
  previewActivityWatchLocal,
  previewImport,
  previewExport,
  repairNativeRuntimeState,
  resumeRecording,
  runDiagnosticChecks,
  saveAutomationReview,
  saveExport,
  saveSettings,
  splitEvent,
  startRecording,
  stopRecording,
  updateEventActivity
} from "./api";
import { useI18n } from "./i18n";
import type { TranslationKey } from "./i18n";
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
  EventRecord,
  EventQualityReport,
  ExportFormat,
  ExportPreview,
  Health,
  ImportHistoryEntry,
  ImportPreview,
  ProcessMap,
  RecordingStatus,
  RuntimeStatus,
  Summary
} from "./types";

type Tab = "home" | "dashboard" | "events" | "quality" | "process" | "switching" | "candidates" | "reports" | "settings";

type DashboardData = {
  health: Health;
  diagnostics: Diagnostics;
  recording: RecordingStatus;
  settings: AppSettings;
  importHistory: ImportHistoryEntry[];
  events: EventRecord[];
  quality: EventQualityReport;
  summary: Summary;
  processMap: ProcessMap;
  candidates: AutomationCandidate[];
  appSwitching: AppSwitching;
  markdown: string;
};

type AppActions = {
  refresh: () => Promise<void>;
  previewImport: (format: "csv" | "json", path: string, mapping?: CsvMapping, dateFormat?: string, timezone?: string) => Promise<ImportPreview>;
  importEvents: (format: "csv" | "json", path: string, mapping?: CsvMapping, dateFormat?: string, timezone?: string) => Promise<void>;
  previewActivityWatch: (enabled: boolean) => Promise<ActivityWatchPreview>;
  importActivityWatch: (enabled: boolean, mode: ActivityWatchImportMode) => Promise<void>;
  previewExport: (format: ExportFormat) => Promise<ExportPreview>;
  exportArtifact: (format: ExportFormat) => Promise<void>;
  saveExport: (format: ExportFormat, path: string) => Promise<void>;
  saveSettings: (settings: Partial<AppSettings>) => Promise<void>;
  saveAutomationReview: (activity: string, status: AutomationReviewStatus, note?: string) => Promise<void>;
  updateEventActivity: (eventId: string, activity: string) => Promise<void>;
  excludeEvent: (eventId: string) => Promise<void>;
  approveEventQuality: (eventId: string) => Promise<void>;
  splitEvent: (eventId: string, splitAfterSeconds: number, firstActivity?: string, secondActivity?: string) => Promise<void>;
  mergeEvents: (firstEventId: string, secondEventId: string, activity?: string) => Promise<void>;
  runDiagnosticChecks: () => Promise<DiagnosticChecks>;
  deleteData: () => Promise<void>;
  startRecording: (caseId: string, activityLabel: string, clearSample: boolean) => Promise<void>;
  stopRecording: () => Promise<void>;
  pauseRecording: (reason: string) => Promise<void>;
  resumeRecording: () => Promise<void>;
};

const tabs: Array<{ id: Tab; label: TranslationKey }> = [
  { id: "home", label: "nav.home" },
  { id: "dashboard", label: "nav.dashboard" },
  { id: "events", label: "nav.events" },
  { id: "quality", label: "nav.quality" },
  { id: "process", label: "nav.process" },
  { id: "switching", label: "nav.switching" },
  { id: "candidates", label: "nav.candidates" },
  { id: "reports", label: "nav.reports" },
  { id: "settings", label: "nav.settings" }
];

const RECORDING_TEMPLATES_KEY = "opsmineflow.recordingTemplates";
const CSV_MAPPING_KEY = "opsmineflow.csvMappingPreset";

const csvMappingFields: Array<{ key: string; label: TranslationKey; required?: boolean }> = [
  { key: "case_id", label: "csvMapping.caseId" },
  { key: "activity", label: "csvMapping.activity", required: true },
  { key: "timestamp_start", label: "csvMapping.start", required: true },
  { key: "timestamp_end", label: "csvMapping.end" },
  { key: "duration_seconds", label: "csvMapping.duration" },
  { key: "user", label: "csvMapping.user" },
  { key: "app_name", label: "csvMapping.app" },
  { key: "app_bundle_id", label: "csvMapping.bundle" },
  { key: "window_title", label: "csvMapping.window" },
  { key: "url", label: "csvMapping.url" },
  { key: "memo", label: "csvMapping.memo" },
  { key: "source_event_id", label: "csvMapping.sourceEventId" },
  { key: "event_type", label: "csvMapping.eventType" }
];

type CsvMappingPreset = {
  mapping: CsvMapping;
  dateFormat: string;
  timezone: string;
};

function runtimeRecoveryMessage(status: RuntimeStatus, t: (key: TranslationKey) => string): string {
  const keys: Record<string, TranslationKey> = {
    reinstall: "message.runtimeReinstall",
    close_conflicting_app: "message.runtimePortCollision",
    restart: "message.runtimeRestart",
    repair_runtime_state: "message.runtimeRepairState",
    development_setup: "message.runtimeDevelopmentSetup"
  };
  return t(keys[status.recovery_action] || "message.runtimeUnavailable");
}

function loadRecordingTemplates(): string[] {
  try {
    const raw = window.localStorage.getItem(RECORDING_TEMPLATES_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed.filter((item) => typeof item === "string" && item.trim()).slice(0, 8) : [];
  } catch {
    return [];
  }
}

function saveRecordingTemplates(templates: string[]) {
  try {
    window.localStorage.setItem(RECORDING_TEMPLATES_KEY, JSON.stringify(templates.slice(0, 8)));
  } catch {
    // The template list is a browser convenience; recording still works without localStorage.
  }
}

function loadCsvMappingPreset(): CsvMappingPreset | null {
  try {
    const raw = window.localStorage.getItem(CSV_MAPPING_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<CsvMappingPreset>;
    if (!parsed.mapping || typeof parsed.mapping !== "object") return null;
    return {
      mapping: Object.fromEntries(Object.entries(parsed.mapping).filter(([, value]) => typeof value === "string")),
      dateFormat: typeof parsed.dateFormat === "string" ? parsed.dateFormat : "",
      timezone: typeof parsed.timezone === "string" ? parsed.timezone : "UTC"
    };
  } catch {
    return null;
  }
}

function saveCsvMappingPreset(preset: CsvMappingPreset) {
  try {
    window.localStorage.setItem(CSV_MAPPING_KEY, JSON.stringify(preset));
  } catch {
    // Mapping presets are a browser convenience; imports still work without localStorage.
  }
}

export function App() {
  const { language, setLanguage, t } = useI18n();
  const [activeTab, setActiveTab] = useState<Tab>("home");
  const [data, setData] = useState<DashboardData | null>(null);
  const [error, setError] = useState<string>("");
  const [actionMessage, setActionMessage] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [working, setWorking] = useState(false);
  const [runtimeStatus, setRuntimeStatus] = useState<RuntimeStatus | null>(null);

  async function refresh(silent = false) {
    if (!silent) setLoading(true);
    setError("");
    try {
      const runtime = await getNativeRuntimeStatus();
      setRuntimeStatus(runtime);
      if (runtime && runtime.state !== "ready") {
        throw new Error(runtimeRecoveryMessage(runtime, t));
      }
      setData(await loadDashboardData());
    } catch (err) {
      setError(err instanceof Error ? err.message : t("message.apiUnavailable", { error: "" }));
    } finally {
      if (!silent) setLoading(false);
    }
  }

  async function repairRuntimeState() {
    if (!window.confirm(t("confirm.repairRuntimeState"))) return;
    setWorking(true);
    setError("");
    try {
      const runtime = await repairNativeRuntimeState();
      setRuntimeStatus(runtime);
      if (runtime && runtime.state !== "ready") {
        setError(runtimeRecoveryMessage(runtime, t));
        return;
      }
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : t("message.runtimeUnavailable"));
    } finally {
      setWorking(false);
    }
  }

  useEffect(() => {
    void refresh();
  }, []);

  useEffect(() => {
    if (data?.recording.active) return;
    const timer = window.setInterval(() => {
      void getNativeRuntimeStatus()
        .then((runtime) => {
          setRuntimeStatus(runtime);
          if (runtime && runtime.state !== "ready") setError(runtimeRecoveryMessage(runtime, t));
        })
        .catch(() => setError(t("message.runtimeUnavailable")));
    }, 2000);
    return () => window.clearInterval(timer);
  }, [data?.recording.active, t]);

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
    previewImport: async (format, path, mapping, dateFormat, timezone) => {
      setWorking(true);
      setError("");
      setActionMessage("");
      try {
        return await previewImport(format, path, mapping, dateFormat, timezone);
      } catch (err) {
        setError(err instanceof Error ? err.message : t("message.previewFailed"));
        throw err;
      } finally {
        setWorking(false);
      }
    },
    importEvents: (format, path, mapping, dateFormat, timezone) =>
      runAction(async () => {
        const result = await importEvents(format, path, mapping, dateFormat, timezone);
        return t("message.imported", { count: result.imported_events, source: result.source || format });
      }),
    previewActivityWatch: async (enabled) => {
      setWorking(true);
      setError("");
      setActionMessage("");
      try {
        return await previewActivityWatchLocal(enabled);
      } catch (err) {
        setError(err instanceof Error ? err.message : t("message.previewFailed"));
        throw err;
      } finally {
        setWorking(false);
      }
    },
    importActivityWatch: (enabled, mode) =>
      runAction(async () => {
        const result = await importActivityWatchLocal(enabled, mode);
        return t("message.activityImported", { count: result.imported_events, skipped: result.skipped_duplicates || 0 });
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
    saveAutomationReview: (activity, status, note = "") =>
      runAction(async () => {
        const result = await saveAutomationReview(activity, status, note);
        return t("message.reviewSaved", { activity: result.activity });
      }),
    updateEventActivity: (eventId, activity) =>
      runAction(async () => {
        await updateEventActivity(eventId, activity);
        return t("message.timelineActivityUpdated");
      }),
    excludeEvent: (eventId) =>
      runAction(async () => {
        await excludeEvent(eventId);
        return t("message.timelineEventExcluded");
      }),
    approveEventQuality: (eventId) =>
      runAction(async () => {
        await approveEventQuality(eventId);
        return t("message.qualityApproved");
      }),
    splitEvent: (eventId, splitAfterSeconds, firstActivity = "", secondActivity = "") =>
      runAction(async () => {
        await splitEvent(eventId, splitAfterSeconds, firstActivity, secondActivity);
        return t("message.timelineEventSplit");
      }),
    mergeEvents: (firstEventId, secondEventId, activity = "") =>
      runAction(async () => {
        await mergeEvents(firstEventId, secondEventId, activity);
        return t("message.timelineEventsMerged");
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
      }),
    pauseRecording: (reason) =>
      runAction(async () => {
        await pauseRecording(reason);
        return t("message.recordingPaused");
      }),
    resumeRecording: () =>
      runAction(async () => {
        await resumeRecording();
        return t("message.recordingResumed");
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
      {error ? (
        <div className="api-warning">
          <span>{t("message.apiUnavailable", { error })}</span>
          {runtimeStatus?.recovery_action === "repair_runtime_state" ? (
            <button onClick={() => void repairRuntimeState()} disabled={working || loading}>
              {t("action.repairRuntimeState")}
            </button>
          ) : null}
        </div>
      ) : null}
      {actionMessage ? <div className="action-message">{actionMessage}</div> : null}
      {loading && !data ? <div className="loading">{t("message.loading")}</div> : null}

      {data ? <View tab={activeTab} data={data} actions={actions} working={working || loading} onStart={openCollection} onNavigate={setActiveTab} /> : null}
    </main>
  );
}

function View({
  tab,
  data,
  actions,
  working,
  onStart,
  onNavigate
}: {
  tab: Tab;
  data: DashboardData;
  actions: AppActions;
  working: boolean;
  onStart: () => void;
  onNavigate: (tab: Tab) => void;
}) {
  if (tab === "home") return <HomeView data={data} actions={actions} working={working} onNavigate={onNavigate} />;
  if (data.events.length === 0 && tab !== "settings") return <EmptyDataView onStart={onStart} />;
  if (tab === "events") return <EventsView events={data.events} />;
  if (tab === "quality") return <QualityView quality={data.quality} actions={actions} working={working} />;
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

function HomeView({
  data,
  actions,
  working,
  onNavigate
}: {
  data: DashboardData;
  actions: AppActions;
  working: boolean;
  onNavigate: (tab: Tab) => void;
}) {
  const { formatDateTime, t } = useI18n();
  const [format, setFormat] = useState<"csv" | "json">("csv");
  const [path, setPath] = useState("");
  const [activityWatchEnabled, setActivityWatchEnabled] = useState(false);
  const [activityWatchMode, setActivityWatchMode] = useState<ActivityWatchImportMode>("skip_duplicates");
  const [activityWatchPreview, setActivityWatchPreview] = useState<ActivityWatchPreview | null>(null);
  const [collectionOpen, setCollectionOpen] = useState(data.events.length === 0);
  const [settingsDraft, setSettingsDraft] = useState<AppSettings>(data.settings);
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const [csvMapping, setCsvMapping] = useState<CsvMapping>({});
  const [csvDateFormat, setCsvDateFormat] = useState("");
  const [csvTimezone, setCsvTimezone] = useState(() => Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC");
  const [csvMappingPreset, setCsvMappingPreset] = useState<CsvMappingPreset | null>(loadCsvMappingPreset);
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

  useEffect(() => {
    if (!preview || preview.format !== "csv") return;
    setCsvMapping((current) => {
      if (Object.values(current).some((value) => value.trim())) return current;
      return csvMappingPreset?.mapping || preview.mapping || preview.suggested_mapping || {};
    });
    if (!csvDateFormat && (csvMappingPreset?.dateFormat || preview.date_format)) {
      setCsvDateFormat(csvMappingPreset?.dateFormat || preview.date_format);
    }
    if (csvMappingPreset?.timezone && csvTimezone === "UTC") {
      setCsvTimezone(csvMappingPreset.timezone);
    }
  }, [preview]);

  const previewCurrentImport = () => {
    const mapping = format === "csv" ? csvMapping : undefined;
    const dateFormat = format === "csv" ? csvDateFormat : "";
    const timezone = format === "csv" ? csvTimezone : "UTC";
    void actions.previewImport(format, path, mapping, dateFormat, timezone).then(setPreview);
  };

  const importCurrentFile = () => {
    const mapping = format === "csv" ? csvMapping : undefined;
    const dateFormat = format === "csv" ? csvDateFormat : "";
    const timezone = format === "csv" ? csvTimezone : "UTC";
    void actions.importEvents(format, path, mapping, dateFormat, timezone);
  };

  const previewActivityWatch = () => {
    void actions.previewActivityWatch(activityWatchEnabled).then(setActivityWatchPreview);
  };

  return (
    <section className="home-grid">
      <OnboardingPanel
        data={data}
        onRecord={() => document.getElementById("record-work")?.scrollIntoView({ behavior: "smooth", block: "start" })}
        onImport={() => {
          setCollectionOpen(true);
          window.setTimeout(() => document.getElementById("import-panel")?.scrollIntoView({ behavior: "smooth", block: "start" }), 0);
        }}
        onAnalyze={() => onNavigate("dashboard")}
        onExport={() => document.getElementById("export-panel")?.scrollIntoView({ behavior: "smooth", block: "start" })}
      />
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
          <select
            value={format}
            onChange={(event) => {
              setFormat(event.target.value as "csv" | "json");
              setPreview(null);
            }}
            disabled={working}
          >
            <option value="csv">CSV</option>
            <option value="json">JSON</option>
          </select>
          <input
            value={path}
            onChange={(event) => {
              setPath(event.target.value);
              setPreview(null);
              setCsvMapping({});
            }}
            disabled={working}
            placeholder={t("import.path")}
            aria-label={t("import.path")}
          />
          <button
            onClick={previewCurrentImport}
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
        {preview && preview.format === "csv" ? (
          <CsvMappingWizard
            preview={preview}
            mapping={csvMapping}
            dateFormat={csvDateFormat}
            timezone={csvTimezone}
            preset={csvMappingPreset}
            onMappingChange={setCsvMapping}
            onDateFormatChange={setCsvDateFormat}
            onTimezoneChange={setCsvTimezone}
            onPreview={previewCurrentImport}
            onSavePreset={() => {
              const preset = { mapping: csvMapping, dateFormat: csvDateFormat, timezone: csvTimezone };
              setCsvMappingPreset(preset);
              saveCsvMappingPreset(preset);
            }}
            onApplyPreset={() => {
              if (!csvMappingPreset) return;
              setCsvMapping(csvMappingPreset.mapping);
              setCsvDateFormat(csvMappingPreset.dateFormat);
              setCsvTimezone(csvMappingPreset.timezone);
            }}
            working={working}
          />
        ) : null}
        <button onClick={importCurrentFile} disabled={working || path.trim() === ""}>
          {t("import.previewed")}
        </button>
        <label className="check-row">
          <input
            type="checkbox"
            checked={activityWatchEnabled}
            onChange={(event) => {
              setActivityWatchEnabled(event.target.checked);
              setActivityWatchPreview(null);
            }}
            disabled={working}
          />
          <span>{t("import.activityConsent")}</span>
        </label>
        <div className="activitywatch-flow">
          <div className="inline-fields">
            <button onClick={previewActivityWatch} disabled={working || !activityWatchEnabled}>
              {t("import.activityPreviewButton")}
            </button>
            <select value={activityWatchMode} onChange={(event) => setActivityWatchMode(event.target.value as ActivityWatchImportMode)} disabled={working}>
              <option value="skip_duplicates">{t("import.activityModeSkip")}</option>
              <option value="append">{t("import.activityModeAppend")}</option>
              <option value="replace">{t("import.activityModeReplace")}</option>
            </select>
            <button
              onClick={() => void actions.importActivityWatch(activityWatchEnabled, activityWatchMode)}
              disabled={working || !activityWatchEnabled || !activityWatchPreview}
            >
              {t("import.activityButton")}
            </button>
          </div>
          <p>{t("import.activitySafety")}</p>
          {activityWatchPreview ? (
            <div className="preview-panel activitywatch-preview">
              <div className="preview-summary">
                <b>{t("import.activityPreviewTitle")}</b>
                <span>{activityWatchPreview.local_only ? t("status.localOnly") : activityWatchPreview.base_url}</span>
              </div>
              <div className="activitywatch-metrics">
                <DetailStat label={t("import.events", { count: activityWatchPreview.event_count })} value={t("import.activityImportable", { count: activityWatchPreview.importable_event_count })} />
                <DetailStat label={t("import.activityNew")} value={activityWatchPreview.new_event_count.toString()} />
                <DetailStat label={t("import.activityDuplicates")} value={activityWatchPreview.duplicate_count.toString()} />
                <DetailStat label={t("import.activityExcluded")} value={activityWatchPreview.excluded_event_count.toString()} />
                <DetailStat label={t("import.confidential", { count: activityWatchPreview.confidential_count })} value={activityWatchPreview.confidential_count.toString()} />
                <DetailStat
                  label={t("import.activityPeriod")}
                  value={
                    activityWatchPreview.period_start && activityWatchPreview.period_end
                      ? `${formatDateTime(activityWatchPreview.period_start)} - ${formatDateTime(activityWatchPreview.period_end)}`
                      : "-"
                  }
                />
              </div>
              {Object.keys(activityWatchPreview.app_usage_seconds).length > 0 ? (
                <div className="activitywatch-apps">
                  <strong>{t("import.activityTopApps")}</strong>
                  {Object.entries(activityWatchPreview.app_usage_seconds).map(([appName, seconds]) => (
                    <div className="history-row" key={appName}>
                      <span>{appName}</span>
                      <b>{t("unit.minutesShort", { count: Math.round(seconds / 60) })}</b>
                    </div>
                  ))}
                </div>
              ) : null}
              <div className="preview-list">
                {activityWatchPreview.sample_events.map((event, index) => (
                  <div className="preview-row" key={`${event.case_id}-${event.activity}-${index}`}>
                    <span>{event.case_id}</span>
                    <b>{event.activity}</b>
                    <span>{event.app_name || t("import.unknown")}</span>
                    <span>{t("unit.secondsShort", { count: Math.round(event.duration_seconds) })}</span>
                  </div>
                ))}
              </div>
              {activityWatchPreview.message ? <p>{activityWatchPreview.message}</p> : null}
            </div>
          ) : null}
        </div>
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

      <section className="operation-panel" id="export-panel">
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
          <Setting
            label={t("diagnostics.schema")}
            value={`${data.diagnostics.storage.schema_version}/${data.diagnostics.storage.schema_target_version} / ${localizeStatus(data.diagnostics.storage.migration_status, t)}`}
          />
          <Setting label={t("diagnostics.integrity")} value={localizeStatus(data.diagnostics.storage.integrity_status, t)} />
          <Setting label={t("diagnostics.wal")} value={localizeStatus(data.diagnostics.storage.wal_status, t)} />
          <Setting
            label={t("diagnostics.migrationBackup")}
            value={data.diagnostics.storage.migration_backup_created ? t("status.created") : t("status.notCreated")}
          />
          <Setting label={t("diagnostics.backupCleanup")} value={localizeStatus(data.diagnostics.storage.backup_cleanup_status, t)} />
          <Setting label={t("diagnostics.events")} value={data.diagnostics.storage.event_count.toString()} />
          <Setting label={t("diagnostics.reviews")} value={data.diagnostics.storage.automation_review_count.toString()} />
          <Setting
            label={t("diagnostics.recording")}
            value={data.diagnostics.recording.available ? t("status.available") : t("status.unavailable")}
          />
          <RecordingDiagnosticDetails status={data.diagnostics.recording} />
          <Setting label={t("diagnostics.activitywatch")} value={`${data.diagnostics.activitywatch.enabled ? t("status.enabled") : localizeStatus("disabled", t)} / ${localizeStatus(data.diagnostics.activitywatch.status, t)}`} />
          <Setting label={t("diagnostics.external")} value={localizeStatus(data.diagnostics.runtime_policy.external_network, t)} />
          {Object.entries(data.diagnostics.dependencies).map(([name, item]) => (
            <Setting key={name} label={name} value={`${localizeStatus(item.status, t)}${item.version ? ` / ${item.version}` : ""}`} />
          ))}
          {Object.entries(data.diagnostics.ports).map(([name, item]) => (
            <Setting key={name} label={t("diagnostics.port", { name })} value={`${item.host}:${item.port} / ${localizeStatus(item.status, t)}`} />
          ))}
        </div>
        <PrivacyEvidencePanel data={data} />
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

function OnboardingPanel({
  data,
  onRecord,
  onImport,
  onAnalyze,
  onExport
}: {
  data: DashboardData;
  onRecord: () => void;
  onImport: () => void;
  onAnalyze: () => void;
  onExport: () => void;
}) {
  const { t } = useI18n();
  const sampleDataLoaded = data.events.length > 0 && data.importHistory.length === 0;
  const hasEvents = data.events.length > 0;
  const nextTitle = data.recording.active
    ? t("onboarding.next.stop")
    : sampleDataLoaded
      ? t("onboarding.next.sample")
      : hasEvents
        ? t("onboarding.next.analyze")
        : t("onboarding.next.collect");

  return (
    <section className="onboarding-panel" aria-label={t("onboarding.title")}>
      <div className="onboarding-heading">
        <span>{t("onboarding.kicker")}</span>
        <h2>{t("onboarding.title")}</h2>
        <p>{t("onboarding.body")}</p>
      </div>
      <div className="next-action-strip">
        <strong>{t("onboarding.nextLabel")}</strong>
        <span>{nextTitle}</span>
      </div>
      <div className="next-action-grid">
        <button onClick={onRecord}>
          <b>{t("onboarding.recordTitle")}</b>
          <span>{t("onboarding.recordBody")}</span>
        </button>
        <button onClick={onImport}>
          <b>{t("onboarding.importTitle")}</b>
          <span>{t("onboarding.importBody")}</span>
        </button>
        <button onClick={onAnalyze} disabled={!hasEvents}>
          <b>{t("onboarding.analyzeTitle")}</b>
          <span>{hasEvents ? t("onboarding.analyzeBody") : t("onboarding.analyzeDisabled")}</span>
        </button>
        <button onClick={onExport} disabled={!hasEvents}>
          <b>{t("onboarding.exportTitle")}</b>
          <span>{hasEvents ? t("onboarding.exportBody") : t("onboarding.exportDisabled")}</span>
        </button>
      </div>
    </section>
  );
}

function CsvMappingWizard({
  preview,
  mapping,
  dateFormat,
  timezone,
  preset,
  onMappingChange,
  onDateFormatChange,
  onTimezoneChange,
  onPreview,
  onSavePreset,
  onApplyPreset,
  working
}: {
  preview: ImportPreview;
  mapping: CsvMapping;
  dateFormat: string;
  timezone: string;
  preset: CsvMappingPreset | null;
  onMappingChange: (mapping: CsvMapping) => void;
  onDateFormatChange: (value: string) => void;
  onTimezoneChange: (value: string) => void;
  onPreview: () => void;
  onSavePreset: () => void;
  onApplyPreset: () => void;
  working: boolean;
}) {
  const { t } = useI18n();
  const visibleColumns = preview.columns.slice(0, 6);
  const hasPreset = Boolean(preset && Object.values(preset.mapping).some((value) => value.trim()));

  return (
    <section className="csv-mapping-panel" aria-label={t("csvMapping.title")}>
      <div className="csv-mapping-heading">
        <div>
          <h3>{t("csvMapping.title")}</h3>
          <p>{t("csvMapping.body")}</p>
        </div>
        <span>{t("csvMapping.columns", { count: preview.columns.length })}</span>
      </div>
      {preview.mapping_warnings.length > 0 ? (
        <div className="api-warning">
          {preview.mapping_warnings.map((warning) => (
            <div key={warning}>{warning}</div>
          ))}
        </div>
      ) : null}
      {preview.sample_rows.length > 0 ? (
        <div className="csv-raw-preview">
          <strong>{t("csvMapping.rawPreview")}</strong>
          <div className="csv-raw-table">
            <div className="csv-raw-row csv-raw-header">
              {visibleColumns.map((column) => (
                <span key={column}>{column}</span>
              ))}
            </div>
            {preview.sample_rows.slice(0, 3).map((row, index) => (
              <div className="csv-raw-row" key={`sample-${index}`}>
                {visibleColumns.map((column) => (
                  <span key={column}>{row[column] || "-"}</span>
                ))}
              </div>
            ))}
          </div>
        </div>
      ) : null}
      <div className="csv-mapping-grid">
        {csvMappingFields.map((field) => {
          const selected = mapping[field.key] ?? preview.mapping[field.key] ?? preview.suggested_mapping[field.key] ?? "";
          return (
            <label key={field.key}>
              <span>
                {t(field.label)}
                {field.required ? <b>{t("csvMapping.required")}</b> : null}
              </span>
              <select
                value={selected}
                onChange={(event) => onMappingChange({ ...mapping, [field.key]: event.target.value })}
                disabled={working}
              >
                <option value="">{t("csvMapping.unmapped")}</option>
                {preview.columns.map((column) => (
                  <option key={column} value={column}>
                    {column}
                  </option>
                ))}
              </select>
            </label>
          );
        })}
      </div>
      <div className="csv-format-row">
        <label>
          <span>{t("csvMapping.dateFormat")}</span>
          <input value={dateFormat} onChange={(event) => onDateFormatChange(event.target.value)} placeholder={t("csvMapping.dateFormatPlaceholder")} disabled={working} />
        </label>
        <label>
          <span>{t("csvMapping.timezone")}</span>
          <input value={timezone} onChange={(event) => onTimezoneChange(event.target.value)} placeholder="Asia/Tokyo" disabled={working} />
        </label>
      </div>
      <div className="csv-mapping-actions">
        <button type="button" onClick={onPreview} disabled={working}>
          {t("csvMapping.previewWithMapping")}
        </button>
        <button type="button" onClick={onSavePreset} disabled={working}>
          {t("csvMapping.savePreset")}
        </button>
        <button type="button" onClick={onApplyPreset} disabled={working || !hasPreset}>
          {t("csvMapping.applyPreset")}
        </button>
      </div>
    </section>
  );
}

function RecordingPanel({ data, actions, working }: { data: DashboardData; actions: AppActions; working: boolean }) {
  const { t } = useI18n();
  const [caseId, setCaseId] = useState(() => `WORK-${new Date().toISOString().slice(0, 10)}`);
  const [activityLabel, setActivityLabel] = useState("");
  const [consent, setConsent] = useState(false);
  const [pauseReason, setPauseReason] = useState("");
  const [clock, setClock] = useState(Date.now());
  const [templates, setTemplates] = useState<string[]>(loadRecordingTemplates);
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
  const recordingTitle = status.paused ? t("recording.pausedTitle") : status.active ? t("recording.activeTitle") : t("recording.title");
  const recordingBody = status.paused ? t("recording.pausedBody") : status.active ? t("recording.activeBody") : t("recording.body");
  const recordingState = status.paused ? t("recording.paused") : status.active ? t("recording.active") : t("recording.stopped");

  return (
    <section id="record-work" className={status.paused ? "recording-panel is-paused" : status.active ? "recording-panel is-recording" : "recording-panel"} aria-live="polite">
      <div className="recording-heading">
        <div>
          <span className="recording-kicker">{t("recording.kicker")}</span>
          <h2>{recordingTitle}</h2>
          <p>{recordingBody}</p>
        </div>
        <strong className="recording-state">{recordingState}</strong>
      </div>

      {status.active ? (
        <div className="recording-live-grid">
          <DetailStat label={t("recording.currentApp")} value={status.current_app || t("recording.waitingForApp")} />
          <DetailStat label={t("recording.elapsed")} value={formatElapsed(elapsedSeconds)} />
          <DetailStat label={t("recording.eventsRecorded")} value={status.recorded_events.toString()} />
          <DetailStat label={t("recording.caseName")} value={status.case_id} />
          <DetailStat label={t("recording.workLabel")} value={status.activity_label} />
          <DetailStat label={t("recording.pauseIntervals")} value={t("recording.pauseIntervalsCount", { count: status.pause_intervals.length })} />
          {status.paused && status.paused_at ? <DetailStat label={t("recording.pausedSince")} value={formatElapsed(Math.max(Math.floor((clock - new Date(status.paused_at).getTime()) / 1000), 0))} /> : null}
          <div className="pause-control">
            <label>
              <span>{t("recording.pauseReason")}</span>
              <input
                value={pauseReason}
                onChange={(event) => setPauseReason(event.target.value)}
                placeholder={t("recording.pauseReasonPlaceholder")}
                disabled={working || status.paused}
              />
            </label>
            {status.paused ? (
              <button
                className="resume-recording-button"
                onClick={() => void actions.resumeRecording()}
                disabled={working}
              >
                {t("recording.resume")}
              </button>
            ) : (
              <button
                className="pause-recording-button"
                onClick={() => void actions.pauseRecording(pauseReason)}
                disabled={working}
              >
                {t("recording.pause")}
              </button>
            )}
          </div>
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
            <div className="input-with-button">
              <input value={activityLabel} onChange={(event) => setActivityLabel(event.target.value)} placeholder={t("recording.workPlaceholder")} disabled={working} />
              <button
                type="button"
                className="secondary-recording-button"
                onClick={() => {
                  const normalized = activityLabel.trim();
                  if (!normalized) return;
                  const next = [normalized, ...templates.filter((item) => item !== normalized)].slice(0, 8);
                  setTemplates(next);
                  saveRecordingTemplates(next);
                }}
                disabled={working || !activityLabel.trim()}
              >
                {t("recording.saveTemplate")}
              </button>
            </div>
          </label>
          {templates.length > 0 ? (
            <div className="recording-templates">
              <span>{t("recording.templates")}</span>
              <div>
                {templates.map((template) => (
                  <button key={template} type="button" className="template-chip" onClick={() => setActivityLabel(template)} disabled={working}>
                    {template}
                  </button>
                ))}
              </div>
            </div>
          ) : null}
          <div className="recording-preflight">
            <strong>{t("recording.preflightTitle")}</strong>
            <span>{status.available ? t("recording.preflightAgentReady") : t("recording.preflightAgentMissing")}</span>
            <span>{t("recording.preflightStorage", { mode: data.diagnostics.storage.storage_mode })}</span>
            <span>{data.settings.excluded_apps.length > 0 ? t("recording.preflightExcludedApps", { count: data.settings.excluded_apps.length }) : t("recording.preflightNoExcludedApps")}</span>
          </div>
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
      <RecordingTimeline events={data.events} actions={actions} working={working} />
      {!status.available ? <div className="api-warning">{status.remediation || t("recording.unavailable")}</div> : null}
      {status.last_error ? <div className="api-warning">{status.last_error}</div> : null}
    </section>
  );
}

function RecordingTimeline({ events, actions, working }: { events: EventRecord[]; actions: AppActions; working: boolean }) {
  const { formatDateTime, t } = useI18n();
  const orderedEvents = useMemo(
    () => [...events].sort((a, b) => `${a.case_id}|${a.timestamp_start}|${a.event_id}`.localeCompare(`${b.case_id}|${b.timestamp_start}|${b.event_id}`)),
    [events]
  );
  const breakCandidates = useMemo(() => findBreakCandidates(orderedEvents), [orderedEvents]);
  const [activityDrafts, setActivityDrafts] = useState<Record<string, string>>({});
  const [splitDrafts, setSplitDrafts] = useState<Record<string, string>>({});

  useEffect(() => {
    setActivityDrafts((current) => {
      const next: Record<string, string> = {};
      for (const event of orderedEvents) next[event.event_id] = current[event.event_id] ?? event.activity_raw;
      return next;
    });
    setSplitDrafts((current) => {
      const next: Record<string, string> = {};
      for (const event of orderedEvents) {
        next[event.event_id] = current[event.event_id] ?? Math.max(1, Math.floor(event.duration_seconds / 2)).toString();
      }
      return next;
    });
  }, [orderedEvents]);

  if (orderedEvents.length === 0) {
    return (
      <section className="recording-timeline">
        <div className="timeline-heading">
          <div>
            <strong>{t("timeline.title")}</strong>
            <p>{t("timeline.empty")}</p>
          </div>
        </div>
      </section>
    );
  }

  return (
    <section className="recording-timeline" aria-label={t("timeline.title")}>
      <div className="timeline-heading">
        <div>
          <strong>{t("timeline.title")}</strong>
          <p>{t("timeline.body")}</p>
        </div>
        <span>{t("timeline.count", { count: orderedEvents.length })}</span>
      </div>
      <div className="break-candidates">
        <div>
          <strong>{t("timeline.breakTitle")}</strong>
          <span>{breakCandidates.length > 0 ? t("timeline.breakBody", { count: breakCandidates.length }) : t("timeline.noBreakCandidates")}</span>
        </div>
        {breakCandidates.slice(0, 5).map((candidate) => {
          const eventId = candidate.eventId;
          return (
            <div className="break-candidate-row" key={candidate.id}>
              <span>{candidate.kind === "long_event" ? t("timeline.longEvent") : t("timeline.gapCandidate")}</span>
              <b>{candidate.label}</b>
              <span>{formatElapsed(candidate.seconds)}</span>
              {eventId ? (
                <button
                  onClick={() => {
                    if (window.confirm(t("timeline.confirmExclude"))) void actions.excludeEvent(eventId);
                  }}
                  disabled={working}
                >
                  {t("timeline.markBreak")}
                </button>
              ) : null}
            </div>
          );
        })}
      </div>
      <div className="timeline-list">
        {orderedEvents.map((event, index) => {
          const previousEvent = index > 0 && orderedEvents[index - 1].case_id === event.case_id ? orderedEvents[index - 1] : null;
          const activityDraft = activityDrafts[event.event_id] ?? event.activity_raw;
          const splitDraft = splitDrafts[event.event_id] ?? Math.max(1, Math.floor(event.duration_seconds / 2)).toString();
          return (
            <article className="timeline-row" key={event.event_id}>
              <div className="timeline-dot" aria-hidden="true" />
              <div className="timeline-main">
                <div className="timeline-meta">
                  <b>{event.app_name || t("import.unknown")}</b>
                  <span>{formatDateTime(event.timestamp_start)} - {formatDateTime(event.timestamp_end)}</span>
                  <span>{t("unit.secondsShort", { count: Math.round(event.duration_seconds) })}</span>
                </div>
                <label className="timeline-label-edit">
                  <span>{t("timeline.activityLabel")}</span>
                  <input
                    value={activityDraft}
                    onChange={(changeEvent) => setActivityDrafts({ ...activityDrafts, [event.event_id]: changeEvent.target.value })}
                    disabled={working}
                  />
                </label>
              </div>
              <div className="timeline-actions">
                <button
                  onClick={() => void actions.updateEventActivity(event.event_id, activityDraft)}
                  disabled={working || !activityDraft.trim() || activityDraft.trim() === event.activity_raw}
                >
                  {t("timeline.saveLabel")}
                </button>
                <label className="split-control">
                  <span>{t("timeline.splitAfter")}</span>
                  <input
                    type="number"
                    min="1"
                    max={Math.max(1, Math.floor(event.duration_seconds - 1))}
                    value={splitDraft}
                    onChange={(changeEvent) => setSplitDrafts({ ...splitDrafts, [event.event_id]: changeEvent.target.value })}
                    disabled={working || event.duration_seconds <= 1}
                  />
                </label>
                <button
                  onClick={() => {
                    const splitSeconds = Number(splitDraft);
                    if (!Number.isFinite(splitSeconds)) {
                      window.alert(t("timeline.invalidSplit"));
                      return;
                    }
                    void actions.splitEvent(event.event_id, splitSeconds, activityDraft, activityDraft);
                  }}
                  disabled={working || event.duration_seconds <= 1}
                >
                  {t("timeline.split")}
                </button>
                <button onClick={() => previousEvent ? void actions.mergeEvents(previousEvent.event_id, event.event_id) : undefined} disabled={working || !previousEvent}>
                  {t("timeline.mergePrevious")}
                </button>
                <button
                  className="timeline-danger"
                  onClick={() => {
                    if (window.confirm(t("timeline.confirmExclude"))) void actions.excludeEvent(event.event_id);
                  }}
                  disabled={working}
                >
                  {t("timeline.exclude")}
                </button>
              </div>
            </article>
          );
        })}
      </div>
    </section>
  );
}

type BreakCandidate = {
  id: string;
  kind: "long_event" | "gap";
  label: string;
  seconds: number;
  eventId?: string;
};

function findBreakCandidates(events: EventRecord[]): BreakCandidate[] {
  const candidates: BreakCandidate[] = [];
  for (const event of events) {
    if (event.duration_seconds >= 30 * 60) {
      candidates.push({
        id: `long-${event.event_id}`,
        kind: "long_event",
        label: `${event.app_name || event.activity_raw} / ${event.activity_raw}`,
        seconds: event.duration_seconds,
        eventId: event.event_id
      });
    }
  }
  for (let index = 1; index < events.length; index += 1) {
    const previous = events[index - 1];
    const current = events[index];
    if (previous.case_id !== current.case_id) continue;
    const previousEnd = new Date(previous.timestamp_end).getTime();
    const currentStart = new Date(current.timestamp_start).getTime();
    const gapSeconds = Math.floor((currentStart - previousEnd) / 1000);
    if (Number.isFinite(gapSeconds) && gapSeconds >= 15 * 60) {
      candidates.push({
        id: `gap-${previous.event_id}-${current.event_id}`,
        kind: "gap",
        label: `${previous.activity_raw} -> ${current.activity_raw}`,
        seconds: gapSeconds
      });
    }
  }
  return candidates.sort((a, b) => b.seconds - a.seconds);
}

function RecordingDiagnosticDetails({ status }: { status: RecordingStatus }) {
  const { formatDateTime, t } = useI18n();
  return (
    <div className="recording-diagnostic-card">
      <Setting label={t("diagnostics.agentVersion")} value={status.agent_version || t("status.unknown")} />
      <Setting label={t("diagnostics.agentPath")} value={status.agent_path || "-"} />
      <Setting label={t("diagnostics.agentLog")} value={status.log_path || "-"} />
      <Setting label={t("diagnostics.heartbeat")} value={status.last_heartbeat_at ? formatDateTime(status.last_heartbeat_at) : t("status.notChecked")} />
      <Setting label={t("diagnostics.captureScope")} value={status.capture_scope} />
      <Setting label={t("diagnostics.sessionSafety")} value={t("diagnostics.sessionSafetyValue", { minutes: Math.round((status.token_ttl_seconds || 0) / 60), count: status.rate_limit_per_minute || 0 })} />
      {status.remediation ? <p>{status.remediation}</p> : null}
    </div>
  );
}

function PrivacyEvidencePanel({ data }: { data: DashboardData }) {
  const { t } = useI18n();
  return (
    <section className="privacy-evidence">
      <div>
        <h3>{t("privacyEvidence.title")}</h3>
        <p>{data.diagnostics.privacy_evidence.summary}</p>
      </div>
      <div className="privacy-evidence-grid">
        {data.diagnostics.privacy_evidence.items.map((item) => (
          <article key={item.name}>
            <b>{t(`privacyEvidence.${item.name}` as TranslationKey)}</b>
            <span>{localizeStatus(item.status, t)}</span>
            <p>{item.evidence}</p>
          </article>
        ))}
      </div>
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

function QualityView({
  quality,
  actions,
  working
}: {
  quality: EventQualityReport;
  actions: AppActions;
  working: boolean;
}) {
  const { formatDateTime, t } = useI18n();
  const unresolved = quality.items.filter((item) => item.quality_review_status !== "approved");
  const [activityDrafts, setActivityDrafts] = useState<Record<string, string>>({});

  useEffect(() => {
    setActivityDrafts((current) => {
      const next: Record<string, string> = {};
      for (const item of quality.items) next[item.event_id] = current[item.event_id] ?? item.activity;
      return next;
    });
  }, [quality.items]);

  return (
    <section className="quality-view">
      <div className="quality-summary">
        <Metric label={t("quality.totalEvents")} value={quality.summary.total_events.toString()} />
        <Metric label={t("quality.affected")} value={quality.summary.affected_event_count.toString()} />
        <Metric label={t("quality.issues")} value={quality.summary.issue_count.toString()} />
        <Metric label={t("quality.approved")} value={quality.summary.approved_count.toString()} />
      </div>
      <div className="quality-issue-grid">
        {(["missing_fields", "invalid_time", "zero_duration", "short_duration", "long_duration", "unlabeled", "low_confidence"] as const).map((key) => (
          <div key={key}>
            <span>{t(`quality.${key}` as TranslationKey)}</span>
            <b>{quality.summary[key]}</b>
          </div>
        ))}
      </div>
      <section className="quality-list">
        <div className="panel-heading">
          <h2>{t("quality.title")}</h2>
          <span>{unresolved.length > 0 ? t("quality.remaining", { count: unresolved.length }) : t("quality.clean")}</span>
        </div>
        {quality.items.length === 0 ? (
          <div className="empty-panel">{t("quality.empty")}</div>
        ) : (
          quality.items.map((item) => {
            const draft = activityDrafts[item.event_id] ?? item.activity;
            const approved = item.quality_review_status === "approved";
            return (
              <article className={approved ? "quality-card is-approved" : "quality-card"} key={item.event_id}>
                <div className="quality-card-main">
                  <div className="quality-card-heading">
                    <b>{item.activity || t("import.unknown")}</b>
                    <span>{approved ? t("quality.statusApproved") : t("quality.statusNeedsReview")}</span>
                  </div>
                  <div className="quality-meta">
                    <span>{item.case_id}</span>
                    <span>{item.app_name || t("import.unknown")}</span>
                    <span>{formatDateTime(item.timestamp_start)} - {formatDateTime(item.timestamp_end)}</span>
                    <span>{formatElapsed(item.duration_seconds)}</span>
                  </div>
                  <div className="quality-issues">
                    {item.issues.map((issue) => (
                      <div className={`quality-issue severity-${issue.severity}`} key={`${item.event_id}-${issue.code}`}>
                        <strong>{localizeQualityIssue(issue.code, t) || issue.label}</strong>
                        <span>{issue.remediation}</span>
                      </div>
                    ))}
                  </div>
                </div>
                <div className="quality-actions">
                  <label>
                    <span>{t("timeline.activityLabel")}</span>
                    <input value={draft} onChange={(event) => setActivityDrafts({ ...activityDrafts, [item.event_id]: event.target.value })} disabled={working} />
                  </label>
                  <button
                    onClick={() => void actions.updateEventActivity(item.event_id, draft)}
                    disabled={working || !draft.trim() || draft.trim() === item.activity}
                  >
                    {t("timeline.saveLabel")}
                  </button>
                  <button onClick={() => void actions.approveEventQuality(item.event_id)} disabled={working || approved}>
                    {t("quality.approve")}
                  </button>
                  <button
                    className="timeline-danger"
                    onClick={() => {
                      if (window.confirm(t("timeline.confirmExclude"))) void actions.excludeEvent(item.event_id);
                    }}
                    disabled={working}
                  >
                    {t("timeline.exclude")}
                  </button>
                </div>
              </article>
            );
          })
        )}
      </section>
    </section>
  );
}

function localizeQualityIssue(code: string, t: (key: TranslationKey, params?: Record<string, string | number>) => string): string {
  const key = `quality.issue.${code}` as TranslationKey;
  return t(key);
}

function ProcessView({ processMap, events }: { processMap: ProcessMap; events: EventRecord[] }) {
  const { t } = useI18n();
  const [query, setQuery] = useState("");
  const [appFilter, setAppFilter] = useState("all");
  const [zoom, setZoom] = useState(1);
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
  const graphNodes = useMemo(() => {
    const columns = Math.max(2, Math.ceil(Math.sqrt(Math.max(visibleNodes.length, 1))));
    const rows = Math.max(1, Math.ceil(visibleNodes.length / columns));
    return visibleNodes.map((node, index) => {
      const column = index % columns;
      const row = Math.floor(index / columns);
      const x = columns === 1 ? 50 : 12 + column * (76 / Math.max(columns - 1, 1));
      const y = rows === 1 ? 50 : 16 + row * (68 / Math.max(rows - 1, 1));
      return { node, x, y };
    });
  }, [visibleNodes]);
  const nodePositions = useMemo(() => new Map(graphNodes.map((item) => [item.node.activity, item])), [graphNodes]);
  const maxEdgeFrequency = Math.max(...visibleEdges.map((edge) => edge.frequency), 1);
  const maxTransitionSeconds = Math.max(...visibleEdges.map((edge) => edge.average_transition_seconds), 1);
  const relatedEdges = selectedNode
    ? visibleEdges.filter((edge) => edge.source === selectedNode.activity || edge.target === selectedNode.activity)
    : [];
  const suggestedImprovement = selectedNode
    ? [
        selectedNode.automation_candidate ? t("process.improvementAutomation") : "",
        selectedNode.bottleneck ? t("process.improvementBottleneck") : ""
      ].filter(Boolean).join(", ") || t("process.improvementReview")
    : t("process.none");

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
        <label className="zoom-control">
          <span>{t("process.zoom")}</span>
          <input
            type="range"
            min="0.8"
            max="1.8"
            step="0.1"
            value={zoom}
            onChange={(event) => setZoom(Number(event.target.value))}
          />
        </label>
      </div>
      <section className="process-canvas">
        {visibleNodes.length === 0 ? (
          <p className="empty">{t("process.noNodes")}</p>
        ) : (
          <div
            className="process-graph"
            style={{ minHeight: `${Math.round(460 * zoom)}px`, minWidth: `${Math.round(900 * zoom)}px` }}
          >
            <svg className="process-edges" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
              <defs>
                <marker id="process-arrow" markerHeight="5" markerWidth="5" orient="auto" refX="5" refY="2.5">
                  <path d="M0,0 L5,2.5 L0,5 Z" fill="currentColor" />
                </marker>
              </defs>
              {visibleEdges.map((edge) => {
                const source = nodePositions.get(edge.source);
                const target = nodePositions.get(edge.target);
                if (!source || !target) return null;
                const linked = selectedNode && (edge.source === selectedNode.activity || edge.target === selectedNode.activity);
                const strokeWidth = 0.3 + (edge.frequency / maxEdgeFrequency) * 1.4;
                const slowRatio = edge.average_transition_seconds / maxTransitionSeconds;
                return (
                  <g key={`${edge.source}-${edge.target}`}>
                    <line
                      className={linked ? "process-edge is-linked" : "process-edge"}
                      x1={source.x}
                      y1={source.y}
                      x2={target.x}
                      y2={target.y}
                      stroke={slowRatio > 0.66 ? "#b45309" : "#0f766e"}
                      strokeWidth={strokeWidth}
                      markerEnd="url(#process-arrow)"
                    />
                    <text className="process-edge-label" x={(source.x + target.x) / 2} y={(source.y + target.y) / 2}>
                      {edge.frequency}x
                    </text>
                  </g>
                );
              })}
            </svg>
            {graphNodes.map(({ node, x, y }) => (
              <button
                key={node.activity}
                className={[
                  "process-node",
                  node.activity === selectedNode?.activity ? "is-selected" : "",
                  node.bottleneck ? "is-bottleneck" : "",
                  node.automation_candidate ? "is-automation" : ""
                ].join(" ")}
                style={{ left: `${x}%`, top: `${y}%` }}
                onClick={() => setSelectedActivity(node.activity)}
              >
                <strong>{node.activity}</strong>
                <span>{t("process.frequency")} {node.frequency}</span>
                <span>{t("process.avgSeconds")} {t("unit.secondsShort", { count: node.average_duration_seconds.toFixed(0) })}</span>
                <span>{t("process.startEnd", { start: processMap.start_activities[node.activity] || 0, end: processMap.end_activities[node.activity] || 0 })}</span>
              </button>
            ))}
          </div>
        )}
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
            <DetailStat label={t("process.relatedTransitions")} value={relatedEdges.length.toString()} />
            <DetailStat label={t("process.suggestedImprovement")} value={suggestedImprovement} />
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

type CandidateSortKey = "score" | "impact" | "savings" | "frequency" | "difficulty" | "risk" | "classification" | "reason" | "status";

const reviewOptions: Array<{ label: TranslationKey; value: AutomationReviewStatus }> = [
  { label: "candidate.unreviewed", value: "unreviewed" },
  { label: "candidate.adopt", value: "adopted" },
  { label: "candidate.hold", value: "on_hold" },
  { label: "candidate.reject", value: "rejected" }
];

function CandidatesView({ candidates, actions, working }: { candidates: AutomationCandidate[]; actions: AppActions; working: boolean }) {
  const { t } = useI18n();
  const [sortKey, setSortKey] = useState<CandidateSortKey>("score");
  const [noteDrafts, setNoteDrafts] = useState<Record<string, string>>({});

  useEffect(() => {
    setNoteDrafts((current) => {
      const next = { ...current };
      for (const candidate of candidates) {
        if (next[candidate.activity] === undefined) next[candidate.activity] = candidate.review_note || "";
      }
      return next;
    });
  }, [candidates]);

  const sortedCandidates = useMemo(() => {
    const valueFor = (candidate: AutomationCandidate) => {
      if (sortKey === "score") return candidate.automation_score;
      if (sortKey === "impact") return candidate.impact_score;
      if (sortKey === "savings") return candidate.estimated_time_savings_minutes;
      if (sortKey === "frequency") return candidate.frequency;
      if (sortKey === "difficulty") return candidate.implementation_difficulty_score;
      if (sortKey === "risk") return candidate.risk_score;
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
  const portfolioGroups = useMemo(() => {
    const groups: Record<AutomationCandidate["portfolio_quadrant"], AutomationCandidate[]> = {
      quick_win: [],
      strategic: [],
      low_effort: [],
      evaluate_later: []
    };
    for (const candidate of candidates) groups[candidate.portfolio_quadrant].push(candidate);
    return groups;
  }, [candidates]);
  const quadrantOrder: Array<AutomationCandidate["portfolio_quadrant"]> = ["quick_win", "strategic", "low_effort", "evaluate_later"];

  return (
    <section className="candidate-workspace">
      <div className="candidate-toolbar">
        <select value={sortKey} onChange={(event) => setSortKey(event.target.value as CandidateSortKey)}>
          <option value="score">{t("candidate.sortScore")}</option>
          <option value="impact">{t("candidate.sortImpact")}</option>
          <option value="savings">{t("candidate.sortSavings")}</option>
          <option value="frequency">{t("candidate.sortFrequency")}</option>
          <option value="difficulty">{t("candidate.sortDifficulty")}</option>
          <option value="risk">{t("candidate.sortRisk")}</option>
          <option value="classification">{t("candidate.sortClassification")}</option>
          <option value="reason">{t("candidate.sortReason")}</option>
          <option value="status">{t("candidate.sortStatus")}</option>
        </select>
      </div>
      <section className="portfolio-grid">
        {quadrantOrder.map((quadrant) => {
          const group = portfolioGroups[quadrant];
          return (
            <article className={`portfolio-cell ${quadrant}`} key={quadrant}>
              <strong>{localizePortfolioQuadrant(quadrant, t)}</strong>
              <span>{t("candidate.portfolioCount", { count: group.length })}</span>
              <p>{group.slice(0, 3).map((candidate) => candidate.activity).join(", ") || t("common.noItems")}</p>
            </article>
          );
        })}
      </section>
      <section className="candidate-list">
        {sortedCandidates.map((candidate) => {
          const status = candidate.review_status || "unreviewed";
          const noteDraft = noteDrafts[candidate.activity] ?? candidate.review_note ?? "";
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
              <div className="candidate-portfolio">
                <DetailStat label={t("candidate.impact")} value={candidate.impact_score.toString()} />
                <DetailStat label={t("candidate.estimatedSavings")} value={t("unit.minutesShort", { count: candidate.estimated_time_savings_minutes })} />
                <DetailStat label={t("candidate.difficulty")} value={localizeDifficulty(candidate.implementation_difficulty, t)} />
                <DetailStat label={t("candidate.risk")} value={localizeRisk(candidate.risk_level, t)} />
              </div>
              <div className="candidate-guidance">
                <p><b>{t("candidate.recommendedAction")}</b> {localizeRecommendedAction(candidate.recommended_action, t)}</p>
                <p><b>{t("candidate.requiredData")}</b> {candidate.required_data.map((item) => localizeRequiredData(item, t)).join(", ")}</p>
              </div>
              <div className="review-controls">
                {reviewOptions.map((option) => (
                  <button
                    key={option.value}
                    className={option.value === status ? "is-active" : ""}
                    onClick={() => void actions.saveAutomationReview(candidate.activity, option.value, noteDraft)}
                    disabled={working || (option.value === status && noteDraft === candidate.review_note)}
                  >
                    {t(option.label)}
                  </button>
                ))}
                <textarea
                  value={noteDraft}
                  onChange={(event) => setNoteDrafts({ ...noteDrafts, [candidate.activity]: event.target.value })}
                  placeholder={t("candidate.reviewNote")}
                  disabled={working}
                />
                <button
                  onClick={() => void actions.saveAutomationReview(candidate.activity, status, noteDraft)}
                  disabled={working || noteDraft === candidate.review_note}
                >
                  {t("candidate.saveNote")}
                </button>
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

function localizeDifficulty(value: string, t: Translate): string {
  const key = `candidate.difficulty.${value}` as TranslationKey;
  return key in difficultyKeys ? t(key) : value;
}

const difficultyKeys: Partial<Record<TranslationKey, true>> = {
  "candidate.difficulty.low": true,
  "candidate.difficulty.medium": true,
  "candidate.difficulty.high": true
};

function localizeRisk(value: string, t: Translate): string {
  const key = `candidate.risk.${value}` as TranslationKey;
  return key in riskKeys ? t(key) : value;
}

const riskKeys: Partial<Record<TranslationKey, true>> = {
  "candidate.risk.low": true,
  "candidate.risk.medium": true,
  "candidate.risk.high": true
};

function localizePortfolioQuadrant(value: string, t: Translate): string {
  const key = `candidate.quadrant.${value}` as TranslationKey;
  return key in quadrantKeys ? t(key) : value;
}

const quadrantKeys: Partial<Record<TranslationKey, true>> = {
  "candidate.quadrant.quick_win": true,
  "candidate.quadrant.strategic": true,
  "candidate.quadrant.low_effort": true,
  "candidate.quadrant.evaluate_later": true
};

function localizeRecommendedAction(value: string, t: Translate): string {
  const key = `candidate.action.${value}` as TranslationKey;
  return key in actionKeys ? t(key) : value;
}

const actionKeys: Partial<Record<TranslationKey, true>> = {
  "candidate.action.rpa_assessment": true,
  "candidate.action.system_integration_review": true,
  "candidate.action.standardize_rule": true,
  "candidate.action.process_review": true
};

function localizeRequiredData(value: string, t: Translate): string {
  const key = `candidate.data.${value}` as TranslationKey;
  return key in requiredDataKeys ? t(key) : value;
}

const requiredDataKeys: Partial<Record<TranslationKey, true>> = {
  "candidate.data.event_samples": true,
  "candidate.data.volume_frequency": true,
  "candidate.data.source_destination_fields": true,
  "candidate.data.system_owner": true,
  "candidate.data.interface_constraints": true,
  "candidate.data.current_rule": true,
  "candidate.data.exception_cases": true
};

function localizeStatus(status: string, t: Translate): string {
  const normalized = status.trim().toLowerCase().replaceAll(" ", "_");
  const statusKeys: Record<string, TranslationKey> = {
    ok: "status.ready",
    ready: "status.ready",
    passed: "status.passed",
    current: "status.current",
    migrated: "status.migrated",
    warning: "status.warning",
    not_applicable: "status.notApplicable",
    available: "status.available",
    free: "status.available",
    enabled: "status.enabled",
    disabled: "status.disabled",
    unavailable: "status.unavailable",
    blocked: "status.blocked",
    blocked_by_policy: "status.blocked",
    not_collected: "status.notCollected",
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
