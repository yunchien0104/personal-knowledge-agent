# Personal Brain Notes Workspace Product Requirements

## Product definition

The Notes Workspace is a separate user-authored workspace. Knowledge continues to contain AI-generated Source Notes, Research remains a dedicated research workflow, and Library continues to manage original sources. Personal Notes can copy and reorganize those outputs without changing them.

## Entry points and creation

The product supports three first-class creation paths:

1. Create a blank Personal Note.
2. Create a Personal Note from Knowledge, Threads, Reels or a Research result, with real citation snapshots when available.
3. Import a user file, initially DOCX or Markdown, or create a structured note by pasting spreadsheet cells from Excel.

Each path creates a user-owned `manual_note`. Importing creates a snapshot and never establishes a mutable pointer to source content.

## Notes home

- Desktop card mode uses a three-column grid. Responsive layouts reduce to two and one column.
- List mode offers compact scanning and sorting.
- Each item shows title, Topics, plain-text preview, source/import indicators and last update time.
- Search covers all owned titles and plain text, including Traditional Chinese, at the database level.
- Filters include Topics, unclassified, creation path and updated date.
- Default sort is most recently updated. Additional sort options include title and creation date.
- Users can create, duplicate, archive and delete notes. Destructive actions require confirmation.
- Empty, loading, no-result, offline and failed states are explicit.

## Editor experience

The editor combines familiar Word controls with Notion-style block editing.

### Text and blocks

- Paragraph and H1 through H4 with visible hierarchy.
- Font size, bold, italic, underline, strike, text color and highlight.
- Links, inline code, block quote and horizontal divider.
- Bulleted, numbered and nested lists.
- Checkable task lists with persistent state.
- Drag or keyboard-based block reordering where accessibility permits.
- Undo, redo and keyboard shortcuts.
- Contextual toolbars for text selection, links, tables, spreadsheet cells, formulas and citations.
- First release does not support inserting or importing images. Unsupported DOCX images produce a visible warning and are not silently dropped.

### Tables and spreadsheet behavior

- Insert, delete, resize and reorder rows and columns.
- Merge and split cells where the editor engine safely supports it.
- Paste tabular ranges from Excel while preserving typed values, formulas and relative cell positions.
- Support formulas in spreadsheet cells, including references and common arithmetic or aggregation functions.
- Display formula input and calculated value separately.
- Detect invalid references and cycles without converting errors into plausible zero values.
- Preserve formulas through save, reload, version history and export where the target format supports them.

### Equations

- Insert and edit inline and display mathematical formulas.
- Store a structured equation source such as LaTeX rather than only a rendered image.
- DOCX export should use native editable equations when a verified conversion path exists; otherwise report the limitation.

## Other Notes query sidebar

- A sidebar searches only the authenticated user's other Personal Notes.
- Users can preview matches, open another note or insert a quoted snapshot with provenance.
- Query results never expose another account's notes.
- The sidebar distinguishes user-written text, imported source snapshots and citation-backed content.

## AI editing assistant

- The assistant can rewrite, shorten, expand, translate, summarize, reorganize or transform explicitly selected content.
- It may use explicitly selected Knowledge, Research or other owned Notes as context.
- It returns a proposed patch or replacement and shows a diff before applying it.
- Applying a suggestion creates an ordinary user-edit revision.
- AI-modified prose is not automatically marked verified or source-grounded.
- The initial implementation uses a deterministic Mock adapter. Real provider credentials remain server-only.

## Imports

### Knowledge

Import into a new or existing Personal Note. Multiple Knowledge items can be appended to one note. Topics may be suggested but do not override manual choices.

### Threads

Import canonical post text and post locators. Preserve the canonical URL when available. Inaccessible or deleted posts retain the imported snapshot and display the source state.

### Reels

Import caption, transcript segments, timestamps and keyframe descriptions. First release does not insert keyframe images into the editor.

### Research

Import a Research note or selected findings. Preserve verified citation snapshots and the Research run identity. Editing the Personal Note never changes the Research result.

### DOCX and Markdown

- DOCX import supports headings, paragraphs, emphasis, lists, tasks when identifiable, tables and equations where conversion is reliable.
- DOCX images are unsupported in the first release and produce a report.
- Markdown import supports headings, emphasis, links, lists, tasks, tables, fenced code and equations when syntax is recognized.

## Autosave and conflict handling

- Save title, rich content, plain text and Topics using a reasonable debounce.
- Show saving, saved, failed, retrying, offline and conflict states.
- Preserve unsaved local content after failure.
- Prevent older responses from overwriting newer edits.
- Use optimistic revision checks for multiple tabs or devices. Conflicts require reload, compare or explicit resolution.
- Navigating away flushes or safely records pending edits without silently losing content.

## Version history

- Create immutable versions at meaningful autosave checkpoints, manual snapshots, imports and restores.
- Show timestamp, reason and available author/device metadata without leaking sensitive identifiers.
- Compare versions and restore by creating a new current revision.
- Version history retains citation and import relationships for that snapshot.

## Topics

- Reuse the existing user-owned Topics system.
- Support multiple Topics, creation, rename, merge, filtering and explicit clearing.
- Manual selection and manual clearing remain authoritative over delayed AI classification.

## Citations

- Preserve evidence, asset and revision snapshots, locator, supporting quote, label and safe source URL when available.
- Citation states are valid, source deleted, unavailable and user modified.
- Imported text remains after source deletion.
- The editor never invents citations or presents user-edited text as verified source text.

## Preview and export

- Provide print-style PDF preview before export.
- Export the visible note to PDF with readable citations, tables and equations.
- Export Markdown with stable plain-text fallbacks for unsupported rich constructs.
- Export DOCX with headings, lists, tasks, tables, citations and supported equations.
- Imported DOCX can be exported again, but only supported constructs are guaranteed to round-trip.

## Security and privacy

- Users can access only their own Notes, versions, imports, Topics and citations.
- Rich content is schema-validated and rendered without arbitrary HTML execution.
- Uploads are bounded by type and size and parsed server-side.
- Service-role, model and acquisition credentials never reach browser code.
- Tests and development use only synthetic fixtures and isolated infrastructure.

## Non-functional requirements

- Traditional Chinese search and IME input must not lose cursor position or characters.
- The editor must remain usable with keyboard navigation and accessible control labels.
- Large notes and spreadsheet blocks require bounded rendering and save payloads.
- Incomplete imports or provider outages do not corrupt the current note.

## Delivery truthfulness

The handoff repository currently provides contracts, schema, fixtures and a UI Stub. Every feature in this document remains required, but a feature is complete only after its corresponding acceptance tests pass.
