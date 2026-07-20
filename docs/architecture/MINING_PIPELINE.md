# Mining Pipeline

OpsMineFlow runs this pipeline locally and does not use PM4Py, an LLM, or a
network service. The analysis contract is `MiningConfig + prepare_analysis()`.
All process outputs must consume the resulting `PreparedAnalysis`; a caller
must not independently regroup or reorder raw events.

1. Load CSV or JSON and normalize to the standard event record.
2. Require an explicit timestamp offset, or use the timezone explicitly chosen
   in mapped CSV import. Persist the instant in UTC.
3. Record case-correlation provenance as `observed`, `manual`, `inferred`, or
   `unassigned`, with strategy, confidence, and evidence.
4. Create an `AnalysisReceipt`: deduplicate exact source events, fail closed on
   conflicting source IDs, and reason-code invalid timestamps/durations, idle
   events, and overlap/parallel ambiguity. The receipt includes privacy-safe
   SHA-256 scope and filter fingerprints, so a recipient can tell whether two
   results came from the same local analysis input and settings without seeing
   raw event fields.
5. Treat inferred or unassigned records as singleton cases. A domain, app, or
   activity label alone is never evidence that events belong to one case.
6. Sessionize observed/manual cases in UTC. A new session starts only when the
   gap after the latest preceding event end is **greater than** `gap_minutes`
   (30 minutes by default). The non-engineer-facing **New-session gap** setting
   accepts 0–1,440 minutes; the threshold and algorithm version are recorded.
7. Exclude any session containing overlapping/parallel intervals from
   sequential DFG, variant, app-switch, and Mermaid calculations rather than
   inventing an order.
8. Calculate duration metrics, DFG, variants, bottlenecks, and automation
   candidates from the same prepared events and sessions.
9. Export the identical receipt with every analysis artifact: API/JSON
   snapshots, Markdown reports, Mermaid comment metadata, draw.io `mxfile`
   metadata, the `analysis-receipt.json` sidecar in the CSV ZIP bundle, and
   the manual LLM/Mermaid handoff. CSV is a ZIP specifically because plain CSV
   has no portable metadata channel.

## Time and duration definitions

- Internal timestamp: an explicit-offset source instant normalized to UTC.
- Display timezone: a UI concern; it must not change process order.
- `raw_active_seconds`: sum of canonical event intervals used by analysis.
- `active_union_seconds`: union of used event intervals within each analysis
  session.
- `case_elapsed_seconds`: first start to last end for each analysis session.
- `waiting_seconds`: positive gaps between adjacent sequential events.

All output ordering is deterministic: UTC start, UTC end, source,
source-event ID, then event ID. Empty and single-event input produces typed
zero results; non-finite values or source-duration/interval mismatches beyond
one second are reason-coded exclusions and never emitted.

## Output calculation contract

Every item below uses the same `PreparedAnalysis` population. Its denominator
is therefore the receipt's `used_event_count` or `analysis_case_count`, never
the raw import count. An event excluded for a receipt reason is absent from all
of these calculations; overlap/parallel ambiguity excludes the whole affected
session from sequential outputs.

| Output | Population and calculation | Unit / denominator | Exclusion rule |
|---|---|---|---|
| DFG node frequency | Count each used event by activity. | events; denominator is `used_event_count` when rendered as a ratio. | All receipt exclusions. |
| DFG edge frequency | Count each adjacent pair inside one ordered analysis session. | transitions; denominator is total eligible adjacent pairs when rendered as a ratio. | All receipt exclusions; no edge for singleton or overlap/parallel sessions. |
| Average transition time | Mean of `next.start - current.end` for one DFG edge, clamped at zero by validated sequential ordering. | seconds; denominator is the edge frequency. | Same as DFG edges. |
| Start / end activities | Count the first / last event in each analysis session. | sessions; denominator is `analysis_case_count`. | Same as DFG edges. |
| Variant count | Count identical ordered activity sequences per analysis session. | sessions; denominator is `analysis_case_count`. | Same as DFG edges. |
| Average case duration | Mean of `last.end - first.start` for cases sharing a variant. | seconds; denominator is cases with that variant. | Same as variants. |
| Bottleneck candidate | Compare an activity's mean event duration with the mean across used events; candidates also require a five-minute mean. | seconds; denominator is used events for that activity. | All receipt exclusions. |
| Automation candidate | Score repeatability, volume, rule wording, handover, and transfer risk from used activity and transition counts. | score 0–1; frequency denominator is `used_event_count`. | All receipt exclusions; it is a prioritization signal, not a business rule. |
