import { useEffect, useMemo, useState } from "react";
import { loadDashboardData } from "./api";
import type {
  AppSwitching,
  AutomationCandidate,
  EventRecord,
  Health,
  ProcessMap,
  Summary
} from "./types";

type Tab = "dashboard" | "events" | "process" | "switching" | "candidates" | "reports" | "settings";

type DashboardData = {
  health: Health;
  events: EventRecord[];
  summary: Summary;
  processMap: ProcessMap;
  candidates: AutomationCandidate[];
  appSwitching: AppSwitching;
  markdown: string;
};

const tabs: Array<{ id: Tab; label: string }> = [
  { id: "dashboard", label: "Dashboard" },
  { id: "events", label: "Event Explorer" },
  { id: "process", label: "Process Map" },
  { id: "switching", label: "App Switching" },
  { id: "candidates", label: "Automation" },
  { id: "reports", label: "Reports" },
  { id: "settings", label: "Settings" }
];

export function App() {
  const [activeTab, setActiveTab] = useState<Tab>("dashboard");
  const [data, setData] = useState<DashboardData | null>(null);
  const [error, setError] = useState<string>("");
  const [loading, setLoading] = useState(true);

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
      {loading && !data ? <div className="loading">Loading local analysis...</div> : null}

      {data ? <View tab={activeTab} data={data} /> : null}
    </main>
  );
}

function View({ tab, data }: { tab: Tab; data: DashboardData }) {
  if (tab === "events") return <EventsView events={data.events} />;
  if (tab === "process") return <ProcessView processMap={data.processMap} />;
  if (tab === "switching") return <SwitchingView switching={data.appSwitching} />;
  if (tab === "candidates") return <CandidatesView candidates={data.candidates} />;
  if (tab === "reports") return <ReportsView markdown={data.markdown} />;
  if (tab === "settings") return <SettingsView health={data.health} />;
  return <DashboardView data={data} />;
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
      <Setting label="Data storage" value="Local files and local memory for MVP" />
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

