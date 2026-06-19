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

export type AutomationCandidate = {
  activity: string;
  automation_score: number;
  frequency: number;
  classification: string;
  reasons: string[];
};

export type AppSwitching = {
  transition_ranking: Array<{ source_app: string; target_app: string; count: number }>;
  round_trips: Array<{ pattern: string; count: number }>;
};

export type Diagnostics = {
  api: {
    status: string;
    bind: string;
    cors: string[];
  };
  storage: {
    storage_mode: string;
    storage_path: string;
    event_count: number;
    manual_label_count: number;
    import_history_count: number;
  };
  runtime_policy: {
    local_only: boolean;
    external_network: string;
    llm_supported: boolean;
    remote_reporting: boolean;
  };
};

export type AppSettings = {
  mask_url_paths: boolean;
  mask_window_titles: boolean;
  retention_days: number;
  activitywatch_enabled: boolean;
  excluded_apps: string[];
  excluded_domains: string[];
};

export type ImportPreview = {
  format: string;
  path: string;
  event_count: number;
  confidential_count: number;
  sample_events: Array<{
    case_id: string;
    activity: string;
    app_name: string;
    domain: string;
    duration_seconds: number;
  }>;
};

export type ImportHistoryEntry = {
  id?: number;
  source: string;
  path: string;
  event_count: number;
  imported_at: string;
};
