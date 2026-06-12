# Data Model

OpsMineFlow uses a standard event record as the common contract between importers, analysis, API, UI, and exports.

## Core Entities

- Event: one normalized observed work activity.
- Case: a group of events representing one business instance.
- Session: a contiguous work session.
- Business label: a rule-based or manually assigned work category.
- Process map: activities and transitions derived from case-ordered events.
- Automation candidate: a scored task or pattern that may deserve improvement review.

## Storage

The MVP uses in-memory processing and local files. Production storage remains an open question and should stay local-only.

