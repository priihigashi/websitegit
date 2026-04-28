# Ideas & Inbox Spreadsheet Redesign Research

Date: 2026-04-28
Owner: Priscila / Oak Park AI Hub
Scope: Improve `💡 Ideas & Inbox` usability, consistency, and automation reliability without deleting current data.

## 1) What we are fixing (from Priscila feedback + live sheet review)

Primary complaints:
- View is messy and hard to use day-to-day.
- Design feels traditional and visually noisy.
- Claude/scripts keep breaking columns when spreadsheet evolves.
- New spreadsheet updates often fail to follow existing schema.

Live findings from `💡 Ideas & Inbox` (ID `1IrFrCNGVIF7cvAr9cIuAXvCtUR_-eQN1mdCpHXpfbcU`):
- `📥 Inbox` has 51 columns, but only ~21 active header labels in row 1.
- Extra right-side columns still carry heavy header formatting, causing false “active area” perception.
- `📊 Analytics` row 1 has one schema, but rows 2+ repeat a different mini-header set (`Logged At`, `Project`, etc.).
- This layout drift creates high risk when scripts append by index instead of header key.

## 2) Goals document (what success looks like)

### Goal A — Human readability
- One clean working view per team function (Inbox triage, Queue planning, Analytics summary).
- Narrow, intentional visible columns for daily work.
- Color/formatting reserved for status and priority signals only.

### Goal B — Automation safety
- Single canonical schema per tab.
- No script writes by raw column position.
- Header-based mapping only, with required field validation.

### Goal C — Change resilience
- If a column is inserted/reordered visually, pipelines still work.
- Clear schema contract tab + protected headers.
- Script fail-fast with actionable schema mismatch logs.

## 3) Research-backed patterns to apply

1. Filter Views for role-based workflows (triage, publish, review) rather than one overloaded table view.
2. Pivot + chart dashboard tabs for KPIs, with raw data left untouched.
3. Data validation dropdowns for controlled fields (Status, Type, Priority, Niche).
4. Protected ranges for header/formula zones.
5. API-side filters and explicit schema mapping for stable automation behavior.

## 4) Exact improvements to implement (additive, no destructive rewrite)

### Phase 1 — Stabilize schema (highest priority)
1. Create tab: `_schema_contract` with columns:
   - `sheet_name`, `column_index`, `column_key`, `display_name`, `required`, `type`, `allowed_values`, `notes`
2. Define canonical keys for `📥 Inbox`, `📋 Content Queue`, `📊 Analytics`.
3. Protect row 1 headers in those tabs.
4. Freeze row 1 and freeze key columns (A:C) in working tabs.

### Phase 2 — Improve view UX
1. Create tab: `📊 Inbox Dashboard` (pivot + charts only).
2. Create saved filter views on `📥 Inbox`:
   - `Triage: New + High`
   - `In Progress by Niche`
   - `Ready This Week`
   - `Blocked / Missing Data`
3. Trim visual noise:
   - Remove decorative formatting from unused right-side columns.
   - Keep status color coding only on status columns.

### Phase 3 — Make scripts robust
1. In all writers, resolve columns by header key, not hardcoded index.
2. Validate required headers before write.
3. On mismatch, fail fast with explicit error:
   - missing headers
   - duplicate headers
   - unknown headers
4. Add a `schema_version` constant in scripts and log it per run.

## 5) “Rules/Phrases” to enforce in prompts and scripts

Use these exact rules in pipeline prompts/instructions:

- “Never write by absolute column number when sheet schema may evolve. Resolve by header key first.”
- “If required headers are missing, stop and report exact missing header names.”
- “Do not create ad-hoc columns in production tabs. Propose schema change in `_schema_contract` first.”
- “Store automation output in canonical raw tab; dashboards and views consume that data.”
- “When source info is incomplete, preserve original excerpt in notes and mark verification-needed explicitly.”

## 6) Research flow topics to test next (content discovery)

Search topics for pipeline research collection:
1. Google Sheets schema governance for AI automation
2. Header-based mapping patterns for Sheets API append/update
3. Filter-view design patterns for operations dashboards
4. Pivot-table KPI dashboards for content pipelines
5. Data validation + protected ranges for collaborative Sheets

Expected output for each research item:
- pattern summary
- implementation snippet
- risk/tradeoff
- exact place in pipeline to apply

## 7) Source links used

- Google Sheets sort/filter + filter views:
  https://support.google.com/docs/answer/3540681
- Google Sheets pivot tables:
  https://support.google.com/docs/answer/1272900
- Google Sheets dropdown data validation:
  https://support.google.com/docs/answer/186103
- Google Sheets protect sheets/ranges:
  https://support.google.com/docs/answer/1218656
- Google Sheets chart types:
  https://support.google.com/docs/answer/190718
- Google Sheets API filters guide:
  https://developers.google.com/workspace/sheets/api/guides/filters

## 8) Immediate next action

Implement `_schema_contract` + header-based write validation in active writers before any further visual redesign. This yields the largest reliability gain and prevents future column breakage.
