export type Health = {
  status: string;
  bind: string;
  local_only: boolean;
  llm_supported: boolean;
  storage_mode: string;
  event_count: number;
};

export type EventRecord = {
  event_id: string;
  case_id: string;
  user_hash: string;
  app_name: string;
  window_title_masked: string;
  url_masked: string;
  domain: string;
  activity_raw: string;
  timestamp_start: string;
  timestamp_end: string;
  duration_seconds: number;
  confidential_flag: boolean;
  quality_review_status: string;
};

export type Summary = {
  total_events: number;
  total_active_seconds: number;
  period_start: string;
  period_end: string;
  app_usage_seconds: Record<string, number>;
  label_usage_seconds: Record<string, number>;
  user_usage_seconds: Record<string, number>;
  average_event_duration_seconds: number;
};

export type ProcessNode = {
  activity: string;
  frequency: number;
  average_duration_seconds: number;
  bottleneck: boolean;
  automation_candidate: boolean;
};

export type ProcessEdge = {
  source: string;
  target: string;
  frequency: number;
  average_transition_seconds: number;
};

export type ProcessMap = {
  nodes: ProcessNode[];
  edges: ProcessEdge[];
  start_activities: Record<string, number>;
  end_activities: Record<string, number>;
};

export type AutomationReviewStatus = "unreviewed" | "adopted" | "on_hold" | "rejected";

export type AutomationCandidate = {
  activity: string;
  automation_score: number;
  frequency: number;
  classification: string;
  reasons: string[];
  review_status: AutomationReviewStatus;
};

export type AppSwitching = {
  transition_ranking: Array<{ source_app: string; target_app: string; count: number }>;
  round_trips: Array<{ pattern: string; count: number }>;
};

export type EventQualityIssue = {
  code: string;
  severity: string;
  label: string;
  remediation: string;
};

export type EventQualityItem = {
  event_id: string;
  case_id: string;
  activity: string;
  app_name: string;
  timestamp_start: string;
  timestamp_end: string;
  duration_seconds: number;
  quality_review_status: string;
  issues: EventQualityIssue[];
  recommended_action: string;
};

export type EventQualityReport = {
  summary: {
    total_events: number;
    affected_event_count: number;
    issue_count: number;
    approved_count: number;
    missing_fields: number;
    invalid_time: number;
    zero_duration: number;
    short_duration: number;
    long_duration: number;
    unlabeled: number;
    low_confidence: number;
  };
  items: EventQualityItem[];
};

export type Diagnostics = {
  api: {
    status: string;
    bind: string;
    port: number;
    cors: string[];
  };
  webui: DiagnosticItem & {
    expected_url: string;
  };
  storage: {
    storage_mode: string;
    storage_path: string;
    event_count: number;
    manual_label_count: number;
    import_history_count: number;
    automation_review_count: number;
  };
  dependencies: Record<string, DiagnosticItem>;
  ports: Record<string, DiagnosticItem & { host: string; port: number }>;
  activitywatch: DiagnosticItem & {
    enabled: boolean;
  };
  recording: RecordingStatus;
  privacy_evidence: PrivacyEvidence;
  guardrails: Record<string, DiagnosticItem & { command: string }>;
  runtime_policy: {
    local_only: boolean;
    external_network: string;
    llm_supported: boolean;
    remote_reporting: boolean;
  };
  remediation: string[];
};

export type RecordingStatus = {
  supported: boolean;
  installed: boolean;
  available: boolean;
  remediation: string;
  agent_path: string;
  agent_version: string;
  log_path: string;
  token_ttl_seconds: number;
  rate_limit_per_minute: number;
  active: boolean;
  paused: boolean;
  session_id: string;
  case_id: string;
  activity_label: string;
  started_at: string;
  paused_at: string;
  pause_reason: string;
  pause_intervals: Array<{
    started_at: string;
    ended_at: string;
    reason: string;
  }>;
  current_app: string;
  recorded_events: number;
  last_heartbeat_at: string;
  last_error: string;
  capture_scope: "frontmost_app_only";
};

export type PrivacyEvidence = {
  status: string;
  capture_scope: string;
  summary: string;
  items: Array<{
    name: string;
    status: string;
    evidence: string;
  }>;
};

export type DiagnosticItem = {
  status: string;
  version?: string;
  remediation: string;
};

export type DiagnosticCheckResult = {
  status: string;
  command: string;
  exit_code?: number;
  output: string;
  remediation: string;
};

export type DiagnosticChecks = Record<string, DiagnosticCheckResult>;

export type AppSettings = {
  mask_url_paths: boolean;
  mask_window_titles: boolean;
  retention_days: number;
  activitywatch_enabled: boolean;
  excluded_apps: string[];
  excluded_domains: string[];
};

export type CsvMapping = Record<string, string>;

export type ImportPreview = {
  format: string;
  path: string;
  event_count: number;
  confidential_count: number;
  columns: string[];
  sample_rows: Array<Record<string, string>>;
  suggested_mapping: CsvMapping;
  mapping: CsvMapping;
  mapping_warnings: string[];
  date_format: string;
  timezone: string;
  sample_events: Array<{
    case_id: string;
    activity: string;
    app_name: string;
    domain: string;
    duration_seconds: number;
  }>;
};

export type ActivityWatchImportMode = "replace" | "append" | "skip_duplicates";

export type ActivityWatchPreview = {
  enabled: boolean;
  local_only: boolean;
  base_url: string;
  event_count: number;
  importable_event_count: number;
  duplicate_count: number;
  new_event_count: number;
  excluded_event_count: number;
  confidential_count: number;
  period_start: string;
  period_end: string;
  app_usage_seconds: Record<string, number>;
  sample_events: Array<{
    case_id: string;
    activity: string;
    app_name: string;
    domain: string;
    duration_seconds: number;
  }>;
  message: string;
};

export type ImportHistoryEntry = {
  id?: number;
  source: string;
  path: string;
  event_count: number;
  imported_at: string;
};

export type ExportFormat = "markdown" | "json" | "csv" | "mermaid" | "drawio";

export type ExportPreview = {
  format: ExportFormat;
  filename: string;
  byte_size: number;
  preview: string;
  confidential_count: number;
  warning: string;
};

export type ExportSaveResult = {
  saved: boolean;
  format: ExportFormat;
  path: string;
  byte_size: number;
  warning: string;
};
