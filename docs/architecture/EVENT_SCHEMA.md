# Event Schema

Required fields:

- `event_id`
- `source`
- `source_event_id`
- `case_id`
- `session_id`
- `user_alias`
- `user_hash`
- `device_id`
- `app_name`
- `app_bundle_id`
- `window_title`
- `window_title_masked`
- `url`
- `url_masked`
- `domain`
- `activity_raw`
- `activity_normalized`
- `event_type`
- `timestamp_start`
- `timestamp_end`
- `duration_seconds`
- `idle_flag`
- `confidential_flag`
- `metadata_json`
- `created_at`

`case_id` can be imported directly or inferred from filename, domain, window title, temporal proximity, app transition patterns, manual labels, or CSV business IDs.

