export type OpsMineFlowEvent = {
  event_id: string;
  source: string;
  source_event_id: string;
  case_id: string;
  session_id: string;
  user_alias: string;
  user_hash: string;
  device_id: string;
  app_name: string;
  app_bundle_id: string;
  window_title: string;
  window_title_masked: string;
  url: string;
  url_masked: string;
  domain: string;
  activity_raw: string;
  activity_normalized: string;
  event_type: string;
  timestamp_start: string;
  timestamp_end: string;
  duration_seconds: number;
  idle_flag: boolean;
  confidential_flag: boolean;
  metadata_json: string;
  created_at: string;
};

export type EventImportResult = {
  events: OpsMineFlowEvent[];
  warnings: string[];
};

