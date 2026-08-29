# Spec: Attendance Export (CSV and PDF)

## Overview

Attendance is recorded, corrected, and displayed, but it cannot leave the application. A faculty member who needs to hand a register to a head of department, reconcile it against a paper record, or work out which students are short across a whole term has no route out of the screen: `/faculty/attendance/history` shows one page of lecture summaries, and opening a lecture shows one lecture's roster. Nothing in AutoAttend produces a per-student view of a class over time, and nothing produces a file.

This feature adds two exports of the same class over the same date range, for a class the faculty member holds:

- **CSV — the data.** One row per student per lecture, absences included, with the provenance of every decision. It is what a spreadsheet pivots into per-student totals, per-date totals, or both, and what another system reads.
- **PDF — the report.** A titled document naming the class's place in the hierarchy and the range, then one row per student with present / absent / total / attendance percentage, then an index of the lectures those figures came from. It is what gets printed, signed, and handed over.

They are two shapes on purpose, and the split is the whole design decision here (rule 5). A CSV of per-student totals would restate what a spreadsheet can compute in three clicks; a PDF of four thousand register rows is not a document anybody reads. Each format carries what its medium is good for, over exactly the same authorization boundary.

Both are reads. They compute no new attendance, store nothing, add no collection, and add no index, and neither one reaches any data the caller cannot already fetch through `GET /api/attendance/sessions` and `GET /api/attendance/sessions/<id>` — one request instead of a hundred, over the same owner check, carrying the same `student_summary` allow-list. The PDF's percentages come from `attendance/threshold.py`, unchanged and unwrapped, so the figure in a printed report cannot disagree with the figure on the student's own dashboard.

One new dependency, `reportlab`, and the reasoning is in "New dependencies". CSV needs none.

This is a backend vertical slice with a user-facing half, so it takes the full pipeline (`/test-feature`, then `/code-review-feature`) **and** the browser verification the frontend-only rule requires for its UI.

### A note on scope

CLAUDE.md's own preference is a small, single-purpose spec, and this one carries two formats. That is a deliberate exception, taken because they share everything that is hard — the endpoint shape, the owner check, the date-range validation, the session cap, the filename rules, the frontend download path — and differ only in the module that turns rows into bytes. Splitting them would mean writing that shared half twice, or writing a second spec whose only content is "the same, but `pdf_export.py`". They ship together.

## Depends on

- `01-project-foundation` — `create_app()`, blueprint registration, the `{ "error": "..." }` response contract, and `requirements.txt` as the single dependency declaration.
- `03-authentication` — `role_required(*roles)` (`backend/auth/decorators.py`), HttpOnly-cookie JWT + CSRF, `apiFetch` / `requestJson` (`frontend/src/api/client.js`), `AuthContext`, `ProtectedRoute`.
- `04-academic-hierarchy-management` — the `classes` collection and `academic/context.py::class_hierarchy_context`, which names the class in the PDF's title block and in both filenames.
- `05-admin-user-management` — `classes.faculty_id`, the `class_enrollments` roster, and the `common/` shared layer (`errors.py`, `validators.py`, `serializers.py`).
- `06-face-enrollment` / `07-attendance-capture` — not for their features, but for the **pattern this feature copies**: a heavyweight formatting library confined to one module, imported lazily, with an import-isolation test and a `503` when it is unavailable (`recognition/encoder.py`, `recognition/frames.py`, and the two isolation test classes in `tests/test_no_secrets_and_scope.py`).
- `07-attendance-capture` — the `attendance_sessions` / `attendance_records` collections, `attendance/service.py::require_owned_class`, `attendance/errors.py::ForbiddenError`, the `attendance_bp` blueprint and its error handlers.
- `08-faculty-attendance-history` — everything this feature extends: `list_sessions`'s query shape and its `uniq_class_id_date` index prefix, `_session_counts`'s grouped-aggregation precedent, `attendance/validators.py::parse_date_range` (the inclusive `from`/`to` rule, shared rather than re-derived), `attendance/serializers.py::DATE_FORMAT`, `frontend/src/api/attendance.js`, and the `/faculty/attendance/history` page and `styles/faculty-history.css` the controls are added to.
- `09-student-attendance-dashboard` / `11-student-attendance-threshold` — `attendance/threshold.py`: `attendance_percentage`, `current_threshold`, `meets_threshold`. The PDF imports these and computes no percentage of its own.
- `13-component-vocabulary` — `.btn`, `.btn--secondary`, `.callout`, `.form-field`.
- `18-spacing-scale` — any new spacing declaration composes `--space-*` or is deliberately off the scale.

## APIs

Two endpoints, added to the existing `attendance_bp` blueprint (`url_prefix="/api"`).

- `GET /api/attendance/export/csv?class_id=…&from=YYYY-MM-DD&to=YYYY-MM-DD` — the register, as CSV — **faculty (owner)**
- `GET /api/attendance/export/pdf?class_id=…&from=YYYY-MM-DD&to=YYYY-MM-DD` — the report, as PDF — **faculty (owner)**

**Why two paths rather than `?format=`.** The two answers differ in shape, not just in encoding — different rows, different columns, different service call, different failure mode (only the PDF can `503`). A `format` parameter would put a branch in the handler and invite a third value later; two static paths keep each handler to one job and cannot be passed a value nobody implemented.

**Why not `/attendance/sessions/export`.** It collides with the existing `/attendance/sessions/<session_id>` rule. Werkzeug does rank a static segment above a converter, so it would route correctly today — but a URL whose correctness depends on that ranking is one refactor away from being a 404, and the collision is avoidable for free. `/attendance/export/*` also sits beside `/attendance/recognize`, which is already a verb.

`from` and `to` are optional and inclusive on both, parsed by the **same** `parse_date_range` the history list and the student dashboard already use — a second copy of "to cannot be earlier than from" is how two screens start disagreeing about what an empty range means. There is no `limit` and no `skip` on either: an export is the whole range or it is not an export (rule 7 says what bounds it instead).

### CSV response

`200`, with:

- `Content-Type: text/csv; charset=utf-8`
- `Content-Disposition: attachment; filename="attendance-cs-3a-2026-07-01-to-2026-08-29.csv"`
- `Cache-Control: no-store` — the file names students and their attendance; it must not sit in a shared or disk cache after the faculty member signs out.

Columns, in this order, with a header row:

```csv
date,student_name,student_email,status,marked_by,source
2026-07-01,Aarti Desai,aarti.desai@example.edu,present,recognition,photo
2026-07-01,Rohit Nair,rohit.nair@example.edu,absent,faculty,photo
```

- `date` — the lecture date, `YYYY-MM-DD`, from the same `DATE_FORMAT` the JSON responses use.
- `student_name` / `student_email` — from `student_summary`'s allow-list, so the file cannot carry a field a JSON response would not.
- `status` — `present` or `absent`. Absences are explicit rows, exactly as they are stored; a student missing from a lecture's rows would be indistinguishable from one who was never considered.
- `marked_by` — `recognition` or `faculty`. Provenance travels with the row, for the same reason `08` refused to let a correction silently re-attribute a record.
- `source` — the session's `photo` / `video` / `manual`, repeated on each of that lecture's rows. It is a property of the lecture, and long format is where a session-level fact is repeated rather than lost.

`edited` is deliberately **not** a column. It is a fact about the record's history, not about who attended, and repeating it on every row of a lecture invites a reader to filter on it as though it were a per-student fact. The screen shows it; the file does not.

**Row order** — `date` ascending, then student name case-insensitively, matching `_load_session_records`'s existing sort. The list on screen is newest-first because the lecture you just took is the one you want; a file is read from the top and a register runs forwards, so the two orders differ on purpose.

### PDF response

`200`, with:

- `Content-Type: application/pdf`
- `Content-Disposition: attachment; filename="attendance-cs-3a-2026-07-01-to-2026-08-29.pdf"`
- `Cache-Control: no-store`

The document, in order:

```text
ATTENDANCE REPORT
Sri Institute · Computer Engineering · Semester 3
Data Structures — CS-3A
1 Jul 2026 – 29 Aug 2026 · 42 lectures recorded
Prepared by Dr A. Mehta · generated 29 Aug 2026

Student                Present   Absent   Total   Attendance
-------------------------------------------------------------
Aarti Desai                 39        3      42        92.9%
Rohit Nair                  28       14      42        66.7%   below 75%
Sneha Patel                 41        1      42        97.6%
...

LECTURE INDEX
2026-07-01   photo    38 / 42
2026-07-03   photo    40 / 42
...

                                                   Page 1 of 3
```

- **Title block** — the four hierarchy names from `class_hierarchy_context`, the class, the effective range (or "all recorded attendance" when neither bound was given), the number of lectures the figures are computed from, who produced it, and when. A report with no provenance is not a report.
- **Student table** — sorted by name, case-insensitively, the same order as the CSV and the roster. `Total` is **that student's own record count in range**, not the number of lectures in range (rule 9); `Attendance` is `attendance_percentage(present, total)`, rendered `—` when it is `None`.
- **`below N%` marker** — present only when `current_threshold()` returns a usable bar, and set only where `meets_threshold(...)` is `False`. It is a recorded figure compared against a configured number, stated in words, and it is never a prediction, a score, a rank, or a consequence (rule 10).
- **Lecture index** — the sessions the figures came from: date, source, present / total. It is what makes the student table auditable, and it is where `edited` *would* belong if it belonged anywhere; it is still omitted, for the reason above.
- **Footer** — `Page N of M` on every page, and the student table's header row repeats on every page it spans.

### Status codes

Both endpoints, same `{ "error": "..." }` JSON contract as `01`–`20`. The error body stays JSON even though neither success body is, so the existing blueprint error handlers are reused unchanged and the frontend keeps one error path:

- `200` — the file, including the empty-range case (rule 8)
- `400` — malformed `class_id`, malformed `from`/`to`, a `to` earlier than `from`, or a range holding more sessions than `MAX_EXPORT_SESSIONS`
- `401` / `403` — unauthenticated / not faculty, **or** faculty who is not the assigned holder of this class
- `404` — the class does not exist
- `503` — **PDF only**, when `reportlab` is not importable. Same reasoning as `recognition`'s `503`: the request was valid and nothing failed, the server is missing a library, and the identical request succeeds once it is installed. The CSV endpoint can never return this.
- `500` — database error, via the blueprint-level `PyMongoError` / `RuntimeError` handlers that already exist

No `409`: nothing here collides. No CV library is imported on either path.

## Database changes

No database changes. No new collection, no new field, and no new index — both queries are the same `class_id` equality plus `date` range that `list_sessions` already serves from the `uniq_class_id_date` prefix, and the PDF's per-student totals come from a `$group` over `attendance_records`, the same shape `_session_counts` already uses. Nothing is written: an export does not record that it happened.

## Frontend

- **Create:** `frontend/src/utils/download.js` — `saveBlob(blob, filename)`, which creates an object URL, clicks a temporary `<a download>`, and revokes the URL. Kept out of `api/` because it is DOM, not HTTP.
- **Modify:** `frontend/src/api/attendance.js` — add `downloadAttendanceExport(classId, { from, to, format })`, where `format` is `'csv'` or `'pdf'` and selects the path. One function for both, because the fetch, the blob, the filename, and the error handling are identical — the shape difference is entirely server-side. It is built on `apiFetch` rather than `requestJson`, which parses JSON unconditionally; on a non-OK response it reads the JSON error body and throws an `Error` carrying `.status`, so it keeps `requestJson`'s contract and every existing call site's error handling still applies. On success it resolves to `{ blob, filename }`, the filename read from `Content-Disposition` with a locally-built fallback.
- **Modify:** `frontend/src/routes/faculty/AttendanceHistory.jsx` — a **Download CSV** and a **Download PDF** button, grouped in the filter panel below the three fields. They share one `exporting` state holding the format in flight (`'csv' | 'pdf' | null`), so both are disabled while either runs and only the one running says `Preparing…`. `exporting` is separate from `pending`, so downloading does not disable the open session's save and delete buttons. Both are disabled when no class is selected, while the list is loading, and when `total === 0`. Success and failure are announced through the page's existing `notice` / `error` callouts.
- **Modify:** `frontend/src/routes/faculty/AttendanceHistory.jsx` — add `aria-live="polite"` to the `notice` paragraph. It is currently silent to a screen reader (only `error` carries `role="alert"`), and a download that produces no visible page change is exactly the case that needs announcing. Same correction the group 4b batch applied to the three admin pages.
- **Modify:** `frontend/src/styles/faculty-history.css` — `.fh-export` (the group) and `.fh-export-btn` (each control, composing `.btn btn--secondary`), plus whatever the re-measured filter-row breakpoints need (rule 20).

## Backend

- **Create:** `backend/attendance/export_naming.py` — pure. The filename slug and the `.csv` / `.pdf` names both formats use. Its whitelist is the `Content-Disposition` injection defence (rule 12), so it is one module with one rule rather than the same regex written twice.
- **Create:** `backend/attendance/csv_export.py` — pure. No Flask, no pymongo, no database: takes the resolved rows, returns the CSV bytes. Owns the header, the column order, and the neutralisation of formula-shaped cells. Mirrors the way `notifications/reporting.py` and `database/reporting.py` keep formatting out of the module that fetches.
- **Create:** `backend/attendance/pdf_export.py` — the **only** module in the backend allowed to import `reportlab`, and it imports it lazily inside the function, exactly as `recognition/encoder.py` does with `face_recognition`. Exposes `is_available()` and `render_report(...)`, takes already-resolved data, and touches no database and no Flask object. Raises `ExportUnavailableError` when the library is missing.
- **Modify:** `backend/attendance/errors.py` — add `ExportUnavailableError`. It gets its own blueprint handler mapping to `503`; it does not subclass `RecognitionUnavailableError`, because it has nothing to do with recognition and a shared parent would make one `except` catch both.
- **Modify:** `backend/attendance/service.py` — add three things:
  - `_export_sessions(db, class_id, faculty_id, *, date_from, date_to)` — private. `require_owned_class`, then the session query, then the cap check, then the sessions in ascending date order. Both public functions start here so the authorization and the bound cannot diverge.
  - `export_records(...)` → `(class_document, context, triples)`, where `triples` are `(session, record, student)` in export order. The records come from one `$in` on the session ids and the students from one `$in` on the student ids — the batched form of `_load_session_records`, not a call to it per session.
  - `export_summary(...)` → `(class_document, context, faculty, sessions_with_counts, student_standings)`. `student_standings` is `(student, present, total)` per student. The **counts** come from one `$group` over `attendance_records` keyed by `student_id` — following `_session_counts`'s precedent of summing where the rows live rather than pulling a term of rosters into Python. The **students** are the union of the class roster and everyone holding a record in range (rule 9), left-joined onto those counts, so an enrolled student with nothing recorded appears at `0 / 0 / —` rather than vanishing. `faculty` is the acting user's document, for the title block's "Prepared by".
- **Modify:** `backend/attendance/validators.py` — add `MAX_EXPORT_SESSIONS` and `parse_export_filters(args)`, which is `parse_date_range` and nothing else. It exists so the routes read like their neighbours, and so the paging parameters cannot be added to it by reflex.
- **Modify:** `backend/routes/attendance.py` — the two handlers plus the `ExportUnavailableError` → `503` handler. Each handler validates, calls the service, calls its formatter, and builds a `flask.Response` with the three headers. No query, no formatting, and no ownership check inline.
- **Modify:** `backend/requirements.txt` — `reportlab`, with the comment explaining why it is here and why it is confined (matching the existing comments on `face_recognition` and `opencv-python`).

## Files to change

- `backend/attendance/service.py`
- `backend/attendance/validators.py`
- `backend/attendance/errors.py`
- `backend/routes/attendance.py`
- `backend/requirements.txt`
- `backend/tests/attendance_test_helpers.py` — a helper seeding several sessions with records across dates, if the existing helpers do not already cover it
- `backend/tests/test_attendance_routes.py` — both endpoints' auth, ownership, filtering, and error cases
- `backend/tests/test_attendance_validators.py` — `parse_export_filters`
- `backend/tests/test_no_secrets_and_scope.py` — a `reportlab` import-isolation test class, mirroring the existing `cv2` and `face_recognition` ones
- `frontend/src/api/attendance.js`
- `frontend/src/routes/faculty/AttendanceHistory.jsx`
- `frontend/src/styles/faculty-history.css`
- `CLAUDE.md` — the feature table and "Next planned feature"

## Files to create

- `backend/attendance/export_naming.py`
- `backend/attendance/csv_export.py`
- `backend/attendance/pdf_export.py`
- `backend/tests/test_attendance_export_naming.py`
- `backend/tests/test_attendance_csv_export.py`
- `backend/tests/test_attendance_pdf_export.py`
- `frontend/src/utils/download.js`

## New dependencies

**`reportlab>=4.0,<5.0`** — one new backend package, for the PDF only. CSV adds none: stdlib `csv` and `io` write it, and the browser's own `Blob` and `URL.createObjectURL` save both.

CLAUDE.md permits a package only when genuinely required, so the alternatives are recorded here rather than left implicit:

- **WeasyPrint** — rejected. It renders HTML/CSS to PDF, which is attractive, but it needs cairo, Pango, and GDK-PixBuf as system libraries. That is the dlib problem again: an install that fails on a Windows machine until a toolchain is set up, in a project that has already paid that cost once.
- **fpdf2** — a reasonable second choice, and smaller. Rejected on capability: laying out a table that breaks across pages with a repeating header row is manual work there and one flowable in reportlab, and that is the entire PDF.
- **Building the PDF by hand** — rejected. The format is not hard to emit badly and very hard to emit correctly, and a hand-rolled writer is a maintenance liability with no upside.
- **reportlab** — pure Python, no native build, BSD-licensed, on PyPI as a wheel for every platform this project runs on. Its `platypus` `Table` does page breaks with `repeatRows`, and `SimpleDocTemplate` does the page furniture.

It is nonetheless treated as an optional dependency at runtime — lazily imported, isolated to one module, guarded by an import-isolation test, and answering `503` when absent. Not because it is fragile like dlib, but because that is this project's established shape for "a library only one feature needs", and following it costs a function.

## Rules for implementation

1. **Faculty only, and owner only, on both endpoints.** `@role_required("faculty")` settles what the caller is; `require_owned_class` settles whether this class is theirs, and it runs before any record is read. A class with no assigned faculty is refused, exactly as everywhere else in `07`/`08`. Never rely on a button being hidden.
2. **No student id is accepted, anywhere.** Both exports are addressed by class, and the students in them are the ones with attendance recorded in that class. `09`'s property — that no endpoint in this project takes a student id — must still hold after this feature.
3. **Read-only.** Nothing is inserted, updated, or deleted on either path, and no count, percentage, or "exported at" timestamp is stored. There is no export audit trail, and that is a deferral, not an oversight.
4. **Both formats share one authorization and one bound.** `_export_sessions` is the single entry point for `require_owned_class`, the date query, and the cap. A second copy of the owner check is how one format ends up enforcing something the other does not.
5. **The two shapes are fixed, and neither grows a `format`-style switch.** CSV is one row per student per lecture and nothing else; PDF is the summary report described above and nothing else. A CSV of per-student totals restates what a pivot computes; a PDF of every register row is not a document anybody reads. If a third shape is ever wanted it is a separate spec, not a parameter on one of these.
6. **Never truncate silently, in either format.** A short file is indistinguishable from a term with fewer lectures, and an attendance record that quietly omits ten lectures is worse than no file at all. There is therefore no `limit` and no `skip`, and no "first N students".
7. **Bound the export by refusing, not by trimming.** `_export_sessions` counts the matching sessions first, and a count above `MAX_EXPORT_SESSIONS` raises `ValidationError` naming the count and asking for a narrower range. Set it to `400` — well above a real semester for one class, and low enough that no single request assembles an unreasonable document. The count runs before any record or student is fetched, for both formats.
8. **An empty range is a `200`, not a `404`.** CSV returns a header-only file; the PDF returns a complete document whose title block says the range holds no recorded lectures and whose tables are replaced by that sentence. "Nothing was recorded" is an answer, and a faculty member who prints it has evidence of it.
9. **A student's denominator is their own records, never the lecture count.** `09` established this and the PDF must not quietly undo it: a student enrolled halfway through a term has fewer records, and dividing by the class's session count would mark them down for lectures held before they existed on the roster. This is why `Total` is a column rather than a constant in the title block, and the two numbers may legitimately differ between rows.

   **Who the report lists is the union of two sets**: the current roster, and everyone holding a record in range. Roster alone would drop a student unenrolled mid-term whose lectures are still in the range and still in the CSV — and two files describing the same range must not disagree about who was in it. Records alone would drop an enrolled student who has none yet, which is exactly the row a head of department is looking for. A student in the union with no records shows `0`, `0`, `0`, `—` and carries no marker, because `attendance_percentage(0, 0)` is `None` and `meets_threshold(None, …)` is `None` — not `False`.
10. **The percentage and the bar come from `attendance/threshold.py`, unwrapped.** Import `attendance_percentage`, `current_threshold`, and `meets_threshold` and call them. Do not round differently, do not re-derive, do not add a local "below" comparison. The printed figure has to be the figure the student sees on their own dashboard and the figure the low-attendance sweep mails about, and shared functions are the only thing that guarantees it. `attendance_percentage` returning `None` prints `—`, never `0%`.
11. **A misconfigured threshold degrades, it does not fail.** When `current_threshold()` returns `None` the PDF simply omits the marker column and states no bar, and the report is otherwise identical — the same choice `09` made for the dashboard. An unreadable environment variable must not cost a faculty member the report they came for. And the marker is a recorded figure against a configured number, stated in words: never a risk score, a rank, a prediction, or a consequence.
12. **The filename is built from a whitelist, never from user text.** Lowercase the class name, replace every character outside `[a-z0-9]` with `-`, collapse and trim the dashes, cap the length, and fall back to `attendance` if nothing survives. That whitelist *is* the header-injection defence — a CR, an LF, or a quote cannot reach the `Content-Disposition` header because no such character can survive it. Both bounds given produces `attendance-<slug>-<from>-to-<to>.<ext>`; otherwise `attendance-<slug>-<today>.<ext>`. One module, both formats.
13. **`Cache-Control: no-store` on both responses.** Both files name students and their attendance.
14. **CSV: neutralise formula-shaped cells.** A cell whose first character is `=`, `+`, `-`, `@`, a tab, or a carriage return is prefixed with a single apostrophe before being written. Spreadsheet applications execute such cells on open, and a student name is admin-provisioned but still user-controlled text. Apply the rule uniformly to every cell rather than a hand-picked subset; in practice only a name or an email can trigger it, and a subset is what a later added column falls outside of.

    **The PDF half of this rule, added after the security review.** Rule 14 used to end *"the PDF needs no equivalent — it renders text and executes nothing"*, and that was false. The sentence is quoted here rather than quietly deleted, because it was a confident wrong assumption about a dependency and is worth a reader's attention.

    `reportlab.platypus.Paragraph` parses a small HTML-like dialect. Measured against reportlab 4.5.1 on 2026-08-29 with the title block unescaped: `<img src="http://…"/>` in the class name, the course name, any of the three hierarchy names, or the faculty name made **the Flask process itself fetch that URL** while rendering; a `file:///` source made it read that path off the server's disk; and a stray `<` — `Section <A>` is a plausible class name — raised `ValueError` out of the parser as an unhandled `500`, recurring on every export of that class until somebody edited the name. All of those fields are admin-typed.

    So: **escape at the `Paragraph` boundary**, through one helper every `Paragraph` is built by, and **not** inside the font-substitution helper. Table cells go through that one too, and a Table cell holding a plain string is drawn literally rather than parsed — escaping there would print a student called `Smith & Sons` as `Smith &amp; Sons` while fixing nothing, because the table was never the vector. The student table holds by far the most user-controlled strings and is safe precisely because it is not a markup context. The route additionally turns a `ValueError`/`OSError` from the writer into a `503`; that is a net under the escaping, not the fix.
15. **CSV: RFC 4180 and Excel both.** `csv.writer` with `lineterminator="\r\n"`, and the text encoded `utf-8-sig`. The BOM is not decoration: Excel on Windows reads a plain UTF-8 file as the system code page, which mangles every non-ASCII name in an Indian college roster.
16. **PDF: `reportlab` is imported in `pdf_export.py` and nowhere else, lazily, inside the function.** Enforced by a test class in `tests/test_no_secrets_and_scope.py` written to match the two that already guard `cv2` and `face_recognition`. Importing it at module scope would put it on the import path of every request the blueprint serves.
17. **PDF: know what the font can render, and write it down.** reportlab's standard Helvetica covers Latin-1 and no more; a name outside it will not render as itself. Verify what actually happens with such a name, state the result in the module docstring and in CLAUDE.md's deferrals, and do **not** silently drop characters — the CSV is UTF-8 and is the escape hatch for a roster the PDF cannot set. Bundling a Unicode TTF is out of scope for this spec.

   **Measured, against reportlab 4.5.1 on 2026-08-29, and the answer changed what this rule requires.** The failure is not a blank and not an exception: reportlab silently switches the offending run to **ZapfDingbats**, where the substituted byte renders as a filled black square — so a Devanagari or CJK name comes out as ■ per character with the ASCII parts of the same name intact, `stringWidth` returns a plausible number, and nothing anywhere raises. Naming a student ■■■■■■ is a wrong report, so the writer does the substitution itself instead: any character outside cp1252 (what WinAnsiEncoding covers) becomes `?`, per character rather than per cell, and the document carries a note at its foot saying it happened and pointing at the CSV. Leaving reportlab's own behaviour in place would have satisfied the letter of "do not silently drop" and broken its intent.
18. **Errors stay JSON, on both endpoints.** The handlers raise the same `ValidationError` / `ForbiddenError` / `NotFoundError` / `ExportUnavailableError` the rest of the blueprint raises and let the existing handlers answer. No `try`/`except` in a handler, and no file body on an error — a client that got a 400 must not be handed something to save.

    **One sanctioned exception, added after the security review:** a `try`/`except (ValueError, OSError)` around the PDF writer's call, translating a render failure into `ExportUnavailableError` (`503`). It exists *because* of this rule rather than despite it — an escape out of reportlab's parser is exactly the case that would otherwise leave this blueprint answering `500` with a traceback. It lives in a named helper beside the handler rather than inside it, stays narrow (those two types only, so a service-layer bug or a driver failure still reaches the handler that knows what it is), and does not move into the writer, which stays a pure function that either returns bytes or raises.
19. **The formatter modules stay pure.** No Flask, no pymongo, no `datetime.now()` — the generation date is passed in. They are the modules the unit tests exercise without a database or an app context, and `pdf_export.py` is pure in that sense too despite its lazy library import.
20. **Re-measure the filter row.** Two more controls near `.fh-form` change where that flex row wraps, and the file already carries measured breakpoints at 677, 450, and 389. Verify the page at named widths, adjust the breakpoints if the measurements moved, and **write the new numbers into `faculty-history.css`** as the existing comments do. Numbers are the evidence; the next spec reads them.

   **Settled during implementation: the controls went into their own row (`.fh-export`), not into `.fh-form`.** A button in a row of `align-items: flex-end` labelled fields aligns to the bottom of a label it does not have, and keeping the fields' flex row at three children is what leaves the three measured breakpoints alone. That the breakpoints are in fact untouched was measured, not asserted — `.fh-form` is identical in height and line count with the export row present and removed, at all fifteen widths checked. The new row needs no breakpoint of its own: it takes three shapes (buttons + note on one line from 677 up, note wrapped below from 360, buttons stacked at 320) from `flex-wrap` alone. **And no `(pointer: coarse)` rule**, because `.btn` already measures 44.09px tall at every width — `:root`'s `font: 18px/145%` inherits a 26.09px line-height into it. The measurements are written into the stylesheet.
21. **The frontend gets its designed, responsive UI in this cycle** — per CLAUDE.md, no build-now-restyle-later. `.fh-export` / `.fh-export-btn` are page hooks composing `.btn btn--secondary`; they must not restyle `.btn` or any other `components.css` primitive, and any new spacing declaration composes `--space-*` or is deliberately and visibly off the scale. Two buttons that look alike are not a new primitive.
22. **The downloads go through `apiFetch`.** A plain `<a href>` or `window.open` sends no `X-CSRF-TOKEN`, cannot transparently refresh an expired access token, and turns a 403 into a browser error page instead of a message on screen. Revoke the object URL after the click.
23. **`test_app_factory.py`'s route guard still passes.** `prediction`, `risk`, and `notification` must remain absent from every registered route; this feature adds none of them. `test_no_secrets_and_scope.py`'s out-of-scope dependency check must also still pass — `reportlab` is a document writer, and nothing here trains or predicts anything.
24. **Preserve existing behaviour.** `GET /api/attendance/sessions`, the singular `GET /api/attendance/session`, the detail/edit/delete trio, and both `/attendance/me` routes are untouched. The history page's existing filtering, paging, correction, and delete flows all still work.

## Definition of done

**Backend — shared**

1. Both endpoints require `faculty`; a student, an admin, and an unauthenticated caller each get `401`/`403` and no part of a file.
2. A faculty member who does not hold the class gets `403`, an unknown class id gets `404`, and a class with no assigned faculty gets `403` — on both endpoints.
3. Inclusive `from` / `to` select the same sessions `GET /api/attendance/sessions` selects for the same bounds, on both endpoints; a session on either bound is included.
4. A `to` earlier than `from`, a malformed date, and a malformed `class_id` each return `400` with a JSON `{ "error": … }` body and no file, on both endpoints.
5. A range holding more than `MAX_EXPORT_SESSIONS` sessions returns `400` naming the count, on both endpoints, and no record or student is fetched.
6. Both responses carry `Cache-Control: no-store` and a `Content-Disposition: attachment` header naming the right extension.
7. The filename slug drops every character outside `[a-z0-9-]`, verified against a class name containing quotes, a newline, and non-ASCII text; nothing but the whitelist reaches the header, for both extensions.
8. Both endpoints respond correctly with `face_recognition` and `cv2` unimportable.
9. `pytest` passes in full, including `test_app_factory.py`'s route guard and every check in `test_no_secrets_and_scope.py`.

**Backend — CSV**

10. `GET /api/attendance/export/csv?class_id=…` returns `200` with `Content-Type: text/csv; charset=utf-8`.
11. The body's first line is exactly `date,student_name,student_email,status,marked_by,source`, the body is encoded `utf-8-sig`, and lines end `\r\n`.
12. Every stored record in range appears as its own row, absences included, with the session's date and source and the record's own `marked_by`.
13. Rows are ordered by date ascending, then by student name case-insensitively.
14. A range with no recorded lectures returns `200` and a header-only file.
15. A student name beginning `=`, `+`, `-`, `@`, a tab, or a CR is written prefixed with `'`, verified by a unit test on `csv_export.py`.
16. `csv_export.py` and `export_naming.py` import neither Flask nor pymongo, verified by reading their imports, and their tests run with no app context and no database.

**Backend — PDF**

17. `GET /api/attendance/export/pdf?class_id=…` returns `200` with `Content-Type: application/pdf` and a body beginning `%PDF-`.
18. The document contains the four hierarchy names, the class name, the effective range, the lecture count, who prepared it, and the generation date.
19. Each student appears once with present, absent, total, and percentage; `total` is that student's own record count in range, verified by a case where two students in the same class have different totals.
20. Every percentage equals `attendance_percentage(present, total)` for that row, and a student with no records in range renders `—` rather than `0%`.
21. With a usable `LOW_ATTENDANCE_THRESHOLD`, exactly the students for whom `meets_threshold(...)` is `False` carry the `below N%` marker, and the bar is stated in the title block.
22. With `LOW_ATTENDANCE_THRESHOLD` unset or out of range, the report renders complete with no marker and no stated bar, and returns `200`.
23. The lecture index lists every session in range with its date, source, and present/total.
24. A range with no recorded lectures returns `200` and a complete document saying so.
25. A report spanning more than one page repeats the student table's header row and carries `Page N of M` on every page.
26. With `reportlab` unimportable the endpoint returns `503` with a JSON error, the CSV endpoint still returns `200`, and the app still starts and serves `/api/health`.
27. No `reportlab` import exists outside `backend/attendance/pdf_export.py`, and it is inside a function there — asserted by the new test class in `test_no_secrets_and_scope.py`.
28. What Helvetica does with a name outside Latin-1 is verified, recorded in `pdf_export.py`'s docstring, and listed as a deferral in CLAUDE.md.

**Frontend**

29. `/faculty/attendance/history` shows **Download CSV** and **Download PDF** in the filter panel; both are disabled with no class selected, while the list is loading, and when the filtered total is 0.
30. Clicking either downloads a file whose name matches the `Content-Disposition` header, without navigating away from the page and without a full reload.
31. While one export is in flight both buttons are disabled and only the one running shows `Preparing…`.
32. A backend error (a `403` on a class no longer held, or the PDF's `503`) surfaces in the page's existing error callout with the backend's own message, and no file is saved.
33. A successful download is announced in the `notice` callout, which now carries `aria-live="polite"`, and names which format was saved.
34. Downloading does not disable or interfere with the open session's save and delete buttons.
35. The filter row is measured at the widths the page's existing breakpoints name (389, 450, 677, plus 320/768/1024/1440), both buttons reach their 44px touch target at coarse-pointer widths, and the measurements are written into `faculty-history.css`. **Additionally: `.fh-form`'s height and line count are unchanged by this feature at every measured width** — the export row is a sibling, not a fourth field, and the existing 677/450/389 breakpoints must be shown to still hold rather than assumed to.
36. Both buttons are keyboard reachable and operable, have a visible focus ring, and read sensibly to a screen reader in both themes.
37. `npm run build` completes with no new warnings.

**Process**

38. `/test-feature 21-attendance-export` and `/code-review-feature 21-attendance-export` have both run, and approved findings are fixed.
39. `CLAUDE.md`'s feature table and "Next planned feature" are updated, including this feature's own deferrals: no admin or student export, no per-lecture register in the PDF, no summary shape in the CSV, no export audit trail, no Unicode font beyond Latin-1 in the PDF, and both responses assembled in memory rather than streamed.

**Added by `/code-review-feature` (2026-08-29)**

These came out of the security and quality reviews rather than the original spec, and are listed separately so it stays visible which requirements the review added.

40. Markup in any string reaching a `Paragraph` is rendered literally, never interpreted. Verified for the class name, the course name, the hierarchy names, and the faculty name against `<img src="http://…"/>`, `<img src="file:///…"/>`, `<b>`, `<script>`, `Section <A>`, and an unclosed tag: no exception escapes, and **no outbound request is attempted** — proved with a local listener that records hits, which received four before the fix and none after.
41. A student's name keeps its angle brackets and ampersands in the PDF's student table. The table is not a markup context, so escaping there would be a defect, not extra safety.
42. The generation date is read once per request: the date inside the report and the date in its filename are always the same.
