# Notes Content Contract

## Product entities

### Knowledge

Knowledge is AI-generated content derived from immutable source revisions. It may be a Source Note created from PDF, Threads or Reels evidence. Knowledge remains read-only during Personal Note editing.

### Research

Research is a research run and its output note, findings and verified citation snapshots. A Research result can be copied into a Personal Note but remains owned by the Research workspace.

### Personal Note

A Personal Note is a user-authored `manual_note` with rich structured content, plain text, Topics, versions, imports and citation snapshots. User edits never mutate Knowledge, Research, assets, Evidence or source revisions.

### Topics

All note types use the same user-owned Topics system. Personal Notes may have multiple Topics. Manual selection or manual clearing is authoritative and must not be overwritten by a later AI result.

### Citation

A citation contains an Evidence snapshot identifier when available, immutable asset and revision identifiers, a locator, supporting quote, source label, optional safe source URL and validity state. The allowed states are `valid`, `source_deleted`, `unavailable` and `user_modified`.

## Import rules

1. Import always copies content into a new import snapshot.
2. Importing into an existing note appends at the chosen insertion point and never silently replaces content.
3. Re-import creates another import event; it does not mutate the earlier snapshot.
4. Source deletion preserves imported text and citation snapshots while changing citation availability.
5. User-modified text is not represented as verified source text.
6. A missing citation stays missing. The system never invents a locator, quote, page, timestamp or URL.

## Source adapter envelopes

- Knowledge: note id, title, body or structured content, Topics and claim citations.
- Threads: canonical URL, post id, text segments and post locator.
- Reels: canonical URL, caption, transcript segments, keyframe descriptions and timestamps.
- Research: run id, output note, finding structure and verified citations.
- DOCX: filename, supported document blocks and import warnings.
- Spreadsheet: cell matrix, typed values, formulas and merge metadata when supported.

## Security

- Every object carries the authenticated owner id in storage.
- Client-supplied ownership is never trusted without RLS and ownership joins.
- Service-role and LLM credentials are server-only.
- Source URLs are rendered only after scheme and source-type validation.
- Rich content rendering uses a known node schema and does not execute arbitrary HTML.
