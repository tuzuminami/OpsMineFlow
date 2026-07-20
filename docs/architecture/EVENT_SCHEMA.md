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

`case_id` is either supplied by the source, manually corrected, or marked as
unassigned/inferred with structured provenance in `metadata_json`:

- `opsmineflow_case_correlation.origin`: `observed`, `manual`, `inferred`, or
  `unassigned`
- `strategy`, `confidence`, and non-sensitive `evidence`

When a local reviewer corrects a case ID, OpsMineFlow records a bounded
single-line reason, the preceding case ID, a generic local-reviewer marker,
and the UTC change time under `opsmineflow_case_correlation_review`. Native
Mac recording case names are also `manual` evidence rather than source-observed
evidence. These fields preserve the distinction without claiming that local
operator input came from an imported source system.

An absent source case ID is stored as a reviewable singleton. OpsMineFlow never
uses a filename, domain, title, app transition, or activity label by itself to
merge events into a business case.

`timestamp_start` and `timestamp_end` are persisted as UTC instants. Import
requires an explicit source offset unless the user selected a timezone in the
mapped CSV import flow.
