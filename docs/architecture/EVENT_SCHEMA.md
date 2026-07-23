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

The in-memory and persisted v4 event profile is intentionally smaller than the
compatibility-shaped schema above. `case_id` and `source_event_id` are opaque
references. `user_alias`, `app_bundle_id`, `window_title`,
`window_title_masked`, `url`, and `url_masked` are always empty. `domain` may
contain only a normalized host used for filtering. `metadata_json` has a
strict allowlist; it cannot retain raw memo, title, URL, alias, or unknown
source metadata.

`case_id` is represented with opaque provenance as supplied by the source,
manually corrected, or marked as unassigned/inferred in `metadata_json`:

- `opsmineflow_case_correlation.origin`: `observed`, `manual`, `inferred`, or
  `unassigned`
- `strategy`, `confidence`, and non-sensitive `evidence`

When a local reviewer corrects a case ID, OpsMineFlow preserves only structured
manual provenance. The supplied case ID and freeform correction reason are
tokenized or dropped before persistence and before the API response. A durable
human-review audit trail needs a separately designed constrained schema; raw
review notes are not an event metadata channel. Native Mac recording case names
are also `manual` evidence rather than source-observed evidence.

An absent source case ID is stored as a reviewable singleton. OpsMineFlow never
uses a filename, domain, title, app transition, or activity label by itself to
merge events into a business case.

`timestamp_start` and `timestamp_end` are persisted as UTC instants. Import
requires an explicit source offset unless the user selected a timezone in the
mapped CSV import flow.
