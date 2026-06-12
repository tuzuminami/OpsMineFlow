# Architecture

OpsMineFlow is a monorepo with local-only boundaries.

```mermaid
flowchart LR
  CSV[CSV import] --> Core[mining-core]
  JSON[JSON import] --> Core
  AW[Optional ActivityWatch localhost import] --> Core
  Core --> API[local-api on 127.0.0.1]
  Core --> Export[exports]
  API --> UI[desktop UI]
  Export --> Drawio[draw.io XML]
  Export --> Mermaid[Mermaid]
  Export --> Report[Markdown report]
```

## Components

- `services/mining-core`: local normalization, masking, labeling, mining, scoring, and report generation.
- `services/local-api`: FastAPI app bound to localhost only.
- `packages/event-schema`: TypeScript types and JSON Schema.
- `packages/drawio-exporter`: draw.io mxfile XML generation.
- `apps/desktop`: Tauri-ready React UI.
- `scripts`: setup, test, lint, license, and local-only checks.

## Data Boundary

All runtime data remains local. No component should require remote services after dependencies are installed.

