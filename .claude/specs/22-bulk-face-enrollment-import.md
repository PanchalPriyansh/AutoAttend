# Spec: Bulk Face Enrollment Import

## Overview

Face enrollment works, one student and one photo at a time. `/admin/face-enrollment` reaches a class, lists its roster with each student's sample count, and opens a panel where the admin uploads a file or captures from the webcam. For one student that is the right interaction. For a first-year intake it is the reason the system does not get used: a sixty-student class is sixty select-open-upload-close cycles, and every student the admin skips is a student who is silently invisible to every attendance pass afterwards — `06` made that gap visible on the roster, and this feature is what closes it at the scale it actually appears.

This feature adds one endpoint and one panel: the admin selects a folder of photos named after the students, and the server matches each file to a student **on that class's roster**, encodes it, and returns a per-file report of what happened.

The whole design turns on two decisions:

- **The server resolves identity, not the client.** The browser already holds the roster, so it could match filenames itself and call the existing per-student endpoint in a loop. It must not. Deciding which student a face belongs to is the single most consequential rule in this feature — a mis-parse attributes one person's face, and therefore their attendance for as long as the encoding survives, to another — and that rule belongs on the same side of the wire as the authorization that guards it.
- **Partial success is the normal outcome, not a failure.** A folder of sixty photos will contain a blurry one, a group shot, a student who left, and a typo in a filename. An all-or-nothing import would refuse all sixty for any one of them. Every file is judged on its own and reported on its own, successes commit as they go, and the report is the deliverable.

Every per-file rule already exists and none is rewritten here: this feature calls `recognition/service.py::register_encoding` once per matched file, so sample capacity, the one-face requirement, and the cross-student duplicate check apply exactly as they do to a single upload. What is new is resolving a filename to a student, orchestrating a batch, and reporting it.

No new dependency, no database change, and no change to the stored document — a bulk-imported sample is byte-for-byte what the single-upload path already writes.

This is a backend vertical slice with a user-facing half, so it takes the full pipeline (`/test-feature`, then `/code-review-feature`) **and** the browser verification its UI requires.

## Depends on

- `01-project-foundation` — `create_app()`, blueprint registration, the `{ "error": "..." }` response contract.
- `03-authentication` — `role_required(*roles)` (`backend/auth/decorators.py`), HttpOnly-cookie JWT + CSRF, `apiFetch` / `requestJson` (`frontend/src/api/client.js`).
- `04-academic-hierarchy-management` — the `classes` collection and the five-level picker `/admin/face-enrollment` already renders.
- `05-admin-user-management` — `users`, the `class_enrollments` roster, `users/assignments.py::list_enrollments`, and `common/serializers.py::student_summary`.
- `06-face-enrollment` — everything this feature extends: the `face_encodings` collection, `recognition/service.py::register_encoding` and `class_enrollment_status`, `recognition/encoder.py`'s lazy import and `is_available()`, `recognition/validators.py` (`require_image`, `MAX_IMAGE_BYTES`, `MAX_REQUEST_BYTES`, `require_single_face`), `recognition/serializers.py`, `recognition/errors.py`, the `faces_bp` blueprint and its five error handlers, `frontend/src/api/faces.js`, the `/admin/face-enrollment` page, and `styles/admin-face-enrollment.css`.
- `13-component-vocabulary` — `.btn`, `.btn--secondary`, `.callout`, `.pill`, `.form-field`.
- `18-spacing-scale` — any new spacing declaration composes `--space-*` or is deliberately off the scale.
- `21-attendance-export` — for the pattern, not the feature: a report of what happened to each of many rows, assembled by a pure module and serialized through an allow-list.

## APIs

One endpoint, added to the existing `faces_bp` blueprint (`url_prefix="/api"`).

- `POST /api/classes/<class_id>/face-enrollment/import` — register face samples for many students of one class in one request — **admin**

It sits under the existing `GET /api/classes/<class_id>/face-enrollment` because it acts on exactly what that endpoint reports. `POST` on the status path itself was rejected: posting to a resource that reads as "the enrollment status of this class" says nothing about what is being created.

### Request

`multipart/form-data`, with **one or more `images` parts** read by `request.files.getlist("images")`. No other field: `source` is **not** accepted from the client and is always recorded as `upload` (rule 6), and `created_by` comes from the verified token as it already does.

Each part is validated by the existing `require_image` — same content-type allow-list, same `MAX_IMAGE_BYTES` ceiling — and the number of parts is capped by a new `MAX_IMPORT_FILES` (10).

**The request ceiling is not raised.** `MAX_CONTENT_LENGTH` stays at `MAX_REQUEST_BYTES` (26 MB), and `MAX_IMPORT_FILES` × `MAX_IMAGE_BYTES` deliberately exceeds it: the bound on a batch is not the file count alone but the body size Werkzeug already refuses beyond, and the frontend packs batches against a byte budget under that ceiling (rule 12). Raising the global ceiling to fit ten maximal images would weaken the guard on every other route so that one route could buffer 50 MB of hostile body before rejecting it.

**Why the client sends batches rather than one large request.** Encoding is synchronous and CPU-bound — a request holds a worker for roughly as long as its files take to encode — so a sixty-photo import in one request risks a proxy timeout, gives no progress until it finishes, and loses everything if the connection drops. Ten at a time keeps each request short, makes progress reportable, and makes a failure cost one batch rather than the import.

### Matching a file to a student

The filename is the only thing that says who a photo is of, so the rule is deliberately dumb and stated in full. **The key is the student's ID** — the roll number a college already names its photo folders by, `24DCS001.jpg`.

There is no `student_id` field on `users`. `email` is the only unique identifier that collection has, and the ID is its local part: `24dcs001@charusat.edu.in` → `24DCS001`. The ID is therefore *derived* rather than stored, `recognition/filenames.py::student_id` is the whole of the project's notion of one, and adding a real roll-number field later means changing that function and the index built from it and nothing else.

For each file, take the **stem** (the name with its final extension removed), strip surrounding whitespace, and lowercase it. It matches a roster student when it equals, case-insensitively, either:

- that student's **ID** — `24DCS001.jpg`, which is the convention, or
- that student's **full email** — `24dcs001@charusat.edu.in.jpg`, which is not advertised and exists for one reason only (below).

The two key spaces cannot overlap, since an ID never contains an `@`, so precedence between them settles nothing in practice. An ID matching two roster students is `ambiguous` and is never guessed at — and that is what the email form is for: two students can only collide on an ID when their addresses differ by *domain*, which one institute's roster cannot do but a deployment spanning two can. The ambiguous message is the single place the email form is named. Matching is scoped to **this class's roster** and nothing else: a photo of a student in another class is `no_match`, not an enrollment.

**Nothing else is parsed.** No numeric suffix is stripped, no separator is interpreted, no name matching is attempted. `24DCS001-2.jpg` does not match `24DCS001`, because `-2` cannot be told apart from a real ID, and a wrong strip attributes a face to the wrong person. Names are not a key at all: two students may share one, and an ID does not. Enrolling several samples per student through this path means importing several folders (rule 5).

A roster student with no file in the import is **not** reported. The import reports on the files it was given; who is still unenrolled is what the roster panel below it already shows, computed from the database rather than from what happened to be selected.

### Response

`200`, JSON, with one entry per submitted file **in the order they were sent**:

```json
{
  "results": [
    { "filename": "24DCS001.jpg", "status": "registered",
      "student": { "id": "…", "name": "Aarav Shah", "email": "24dcs001@charusat.edu.in" },
      "message": null },
    { "filename": "group-photo.jpg", "status": "no_match",
      "student": null,
      "message": "No student on this class roster has this ID. Name each photo with the student's ID, as in 24DCS001.jpg." },
    { "filename": "24DCS003.jpg", "status": "ambiguous",
      "student": null,
      "message": "This ID matches 2 students on the roster. Rename the file with the student's full email address instead." },
    { "filename": "24DCS002.jpg", "status": "rejected",
      "student": { "id": "…", "name": "Bhavya Patel", "email": "24dcs002@charusat.edu.in" },
      "message": "No face was detected in the image. Use a clear, well-lit photo showing the student's face." }
  ],
  "summary": { "submitted": 4, "registered": 1, "no_match": 1, "ambiguous": 1, "rejected": 1 }
}
```

Four statuses, and no more:

- `registered` — a sample was stored. This is the only status that wrote anything.
- `no_match` — no roster student has that ID.
- `ambiguous` — more than one did.
- `rejected` — a student matched, and a domain rule refused the photo. `message` carries the domain exception's own text verbatim, so the admin reads the same sentence the single-upload path shows: no face, several faces, at the five-sample cap, or a face that closely matches another student's sample.

`message` is `null` only on `registered`. `student` is populated whenever one was resolved — including on `rejected`, which is the case where knowing who it was is the entire point.

The encoding vector never appears, on any status. `student` is `common/serializers.py::student_summary`, the same allow-list every other roster response uses, rather than a shape invented here.

**Why one status code for a mixed outcome.** The request was well-formed, was authorized, and was fully processed; that is a `200`, and the report is the body. A `207` would be technically defensible and practically worse — every existing client path in `api/client.js` treats non-2xx as a thrown error with a single message string, and a partly-successful import is not an error to be caught, it is a result to be read.

### Status codes

Same `{ "error": "..." }` contract as `01`–`21`, answered by `faces_bp`'s existing handlers:

- `200` — the report, including a report in which nothing was registered
- `400` — malformed `class_id`, no `images` part at all, more than `MAX_IMPORT_FILES` parts, or any part failing `require_image` (wrong content type, over `MAX_IMAGE_BYTES`)
- `401` / `403` — unauthenticated / not an admin
- `404` — the class does not exist
- `413` — the body exceeds `MAX_CONTENT_LENGTH`, from Werkzeug, unchanged
- `503` — `face_recognition` is not importable, checked **once before any file is read**
- `500` — database error, via the blueprint's existing `PyMongoError` / `RuntimeError` handlers

Note what is deliberately not here: no `409`. A per-file conflict is a `rejected` row inside a `200`, because the request as a whole did not conflict with anything.

## Database changes

No database changes. No new collection, no new field, no new index, and no validator change. A bulk-imported document is exactly what `register_encoding` already writes — `student_id`, `encoding`, `model`, `source`, `created_at`, `created_by` — and `source` stays within the existing `("upload", "camera")` enum.

**A third `source` value of `import` was considered and rejected.** Provenance matters in this project, but a bulk-imported file *is* an uploaded photo taken by the admin, and the distinction is about how the admin operated the UI, not about where the biometric data came from. Adding it means a validator change to a live collection, a new label everywhere `source` is rendered, and a value every consumer must learn — for a fact nothing acts on.

Nothing about the import itself is recorded: no import run, no manifest, no audit row. That is a deferral, stated as one.

## Frontend

- **Create:** `frontend/src/components/admin/BulkFaceImport.jsx` — the panel. A `<input type="file" multiple accept="image/jpeg,image/png,image/webp">`, the naming rule stated in plain words with an example, the selected-file count and total size, an **Import** button, a progress line while batches run, and the grouped result report. Local state only; it reports upward through an `onImported` callback. Rendered by one page, so it does not get its own stylesheet (rule 15).
- **Create:** `frontend/src/utils/fileBatches.js` — pure. `packBatches(files, { maxFiles, maxBytes })` splits a selection into batches that respect both bounds, and puts any single file above `maxBytes` in a batch of its own so the server is the one that rejects it with its own message rather than the client silently dropping it. Sits in `utils/` beside `download.js` and `lecture.js` because it is neither HTTP nor DOM.
- **Modify:** `frontend/src/api/faces.js` — add `importClassFaceEncodings(classId, files)`, which posts **one** batch as `FormData` with repeated `images` parts and resolves to `{ results, summary }`. It does not loop: batching is the component's business, and an API function that hid several requests behind one call would make progress impossible to report.
- **Modify:** `frontend/src/routes/admin/FaceEnrollment.jsx` — render `BulkFaceImport` inside the roster section, above the student list, only when a class is selected. On completion it calls the page's existing `refresh()` so the sample counts and the "N of M students have no face samples yet" summary are recomputed from the server rather than adjusted locally.
- **Modify:** `frontend/src/styles/admin-face-enrollment.css` — `.fe-import*` hooks for the panel, the file summary, the progress line, and the result rows, composing `components.css` primitives. Same file because it is the same page; the page's existing `(pointer: coarse)` pairing and its measured 480 breakpoint apply to the new controls too and are re-measured, not assumed (rule 16).

## Backend

- **Create:** `backend/recognition/filenames.py` — pure. No Flask, no pymongo, no CV library. `stem(filename)` and `resolve_roster(filenames, students)`, returning one resolution per name: the matched student, `None` for no match, or an ambiguity carrying how many matched. The identity rule lives here, in one testable place, and is the module the security review should read first.
- **Create:** `backend/recognition/bulk_import.py` — the orchestration. `import_class_faces(db, class_id, files, *, created_by)` where `files` is a list of `(filename, image_bytes, content_type)`. It checks availability once, loads the roster once through `list_enrollments`, resolves every name through `filenames.py`, then calls `service.register_encoding` per matched file, catching `ValidationError` / `ConflictError` **per file** and turning each into a `rejected` result. It owns no rule that `service.py` already owns.
- **Modify:** `backend/recognition/validators.py` — add `MAX_IMPORT_FILES = 10` and `require_import_files(files)`, which rejects an empty selection and an over-cap one, then runs each part through the existing `require_image`. It returns plain `(filename, bytes, content_type)` triples, so `bulk_import.py` never sees a `FileStorage`, exactly as `service.py` never does.
- **Modify:** `backend/recognition/serializers.py` — add `serialize_import_result(s)` and `serialize_import_summary`, built from a literal allow-list like every function already in that module.
- **Modify:** `backend/routes/faces.py` — the one handler: read the parts, validate, call the service, serialize. No matching, no loop, no `try`/`except`, no new error handler — every exception it can raise already has one.

## Files to change

- `backend/recognition/validators.py`
- `backend/recognition/serializers.py`
- `backend/routes/faces.py`
- `backend/tests/faces_test_helpers.py` — a helper building a multi-file multipart body and a roster with known emails, if the existing helpers do not already cover it
- `backend/tests/test_face_routes.py` — the endpoint's auth, caps, matching, and per-file outcomes
- `frontend/src/api/faces.js`
- `frontend/src/routes/admin/FaceEnrollment.jsx`
- `frontend/src/styles/admin-face-enrollment.css`
- `CLAUDE.md` — the feature table and "Next planned feature"

## Files to create

- `backend/recognition/filenames.py`
- `backend/recognition/bulk_import.py`
- `backend/tests/test_face_filenames.py`
- `backend/tests/test_face_bulk_import.py`
- `frontend/src/components/admin/BulkFaceImport.jsx`
- `frontend/src/utils/fileBatches.js`

## New dependencies

No new dependencies, backend or frontend. Multipart with repeated parts is what the browser and Werkzeug already do, the roster join already exists, and the encoding path is unchanged.

**A ZIP upload was considered and rejected**, even though it would make the import one request. `zipfile` is stdlib, so it is not a dependency that decides it: it is that accepting an archive means owning decompression-bomb defence (declared sizes lie, so a total-bytes budget must be enforced while extracting), path-traversal defence on entry names, and nested-archive and encrypted-entry cases — a new attack surface, in the one feature that writes biometric data, to save the admin from selecting files they have already got in a folder. `<input multiple>` and directory selection cover the same workflow with no parser.

**A CSV manifest mapping file names to students was also rejected**: it adds a second file format, a second parsing surface, and a second way for the mapping to be wrong, to replace a convention the admin can satisfy by naming a file.

## Rules for implementation

1. **Admin only.** `@role_required("admin")`, matching every other endpoint in `faces_bp`. Face enrollment is the admin's job in CLAUDE.md, and the roster is reachable to faculty for attendance but enrollment is not. Never rely on the panel being hidden.
2. **The server decides which student a face belongs to.** The client sends files and nothing else — no student id, no mapping, no resolved match. It may *display* what the server resolved; it may never propose it. This is the rule the whole feature exists to protect, and it is why `filenames.py` is a backend module and not a helper in the React component.
3. **Every per-file rule comes from `register_encoding`, unwrapped.** Sample capacity, `require_single_face`, and `_require_no_other_student_matches` are applied by calling the existing function once per file — not reimplemented, not inlined, not bypassed for speed. A bulk path that enforced one rule differently is how a second identity gets created for one face. In particular, the cross-student duplicate check must still run for every file, including against samples inserted earlier in the same batch.
4. **One bad file never stops the import.** `ValidationError` and `ConflictError` are caught per file and become a `rejected` row; the loop continues. Nothing else is caught — a `PyMongoError`, a `RuntimeError`, or an unavailable recognition library is a failure of the request, not of one photo, and must reach the blueprint's handlers rather than be flattened into a per-file message.
5. **Successes commit as they go, and there is no transaction.** A partial import is a correct outcome, and re-importing the remainder is the repair. Re-running the same folder is safe but wasteful — it adds a second near-identical sample per student until the five-sample cap refuses it. A same-student duplicate check would prevent that, and is deliberately **not** added here: it would change `06`'s single-upload behaviour from a second module, and the cap already bounds the waste. Deferred, and recorded as such.
6. **`source` is `"upload"`, fixed server-side.** It is not read from the request, so no client can write a value the validator would refuse or claim a capture came from a camera.
7. **Availability is checked once, before any file is read.** `encoder.is_available()` gates the whole request into a `503`; a server missing `dlib` must not encode nine photos and then report ten failures. Same reasoning as `register_encoding`'s cheapest-and-most-decisive-first ordering.
8. **The roster is loaded once per request.** `list_enrollments(db, class_id)` runs once and resolves every file; a per-file roster query would multiply an already index-backed join by the batch size for nothing. An unknown class raises `NotFoundError` from that call — a `404` before any image is touched.
9. **A file name is untrusted text.** It is never used as a path, never opened, never joined to a directory, and never written to disk. It is matched, and it is echoed back into JSON truncated to a sane length. **It is also not logged**: a file name here is a student's roll number — identifying them as surely as an email address would, and the same fact about a real person — and this project's existing log line deliberately records an ObjectId and nothing else.
10. **Nothing about the image is persisted.** The bytes exist for the duration of the request. No temp file, no thumbnail, no "imported photos" folder, no image in a log or an error message — the rule `06` set, restated because a bulk path is exactly where a "just cache them" shortcut would look reasonable.
11. **The report never carries an encoding.** `serialize_import_result` is built from a literal allow-list, like every other function in `recognition/serializers.py`, so the vector is structurally unable to reach a response rather than filtered out of one.
12. **Batching respects the existing request ceiling; it does not raise it.** `MAX_CONTENT_LENGTH` stays at `MAX_REQUEST_BYTES`. The client packs each batch under both `MAX_IMPORT_FILES` and a byte budget below that ceiling, and the server enforces the file cap and the per-file ceiling on its own — the client's packing is transport convenience, never the check.
13. **Batches run sequentially, and progress is visible.** One request in flight at a time: concurrent batches would multiply the CPU cost on a synchronous encoder and make the cross-student duplicate check race against samples inserted by a sibling request. The panel reports files completed out of files selected while it runs.
14. **A failed batch does not discard the batches that succeeded.** If one request errors, the panel stops, keeps and shows every result already collected, and says which files were not attempted. An import that loses its own report is worse than one that stops early.
15. **The frontend gets its designed, responsive UI in this cycle** — per CLAUDE.md, no build-now-restyle-later. `.fe-import*` are page hooks composing `.btn`, `.callout`, `.pill`, and `.form-field`; they must not restyle a `components.css` primitive, and `BulkFaceImport` is rendered by one page so its rules live in that page's file rather than a new component stylesheet. Any new spacing declaration composes `--space-*` or is deliberately and visibly off the scale.
16. **Re-measure this page, and write the numbers down.** `/admin/face-enrollment` already carries a measured `(pointer: coarse)` pairing and a 480 breakpoint chosen from a measurement recorded in the file. A new panel above the roster changes what that page is at narrow widths: verify at named widths, confirm the picker and roster measurements still hold rather than assuming they do, and write the new numbers into `admin-face-enrollment.css` as its existing comments do.
17. **A result list is a list, not a table.** It reuses the page's existing row shape (`.fe-row` and its identity/meta children) rather than introducing a new layout the rest of the page does not have — and it is announced through an `aria-live="polite"` region, like the roster and student panels already are.
18. **`test_app_factory.py`'s route guard still passes.** `prediction`, `risk`, and `notification` must remain absent from every registered route; this feature adds none of them. `test_no_secrets_and_scope.py`'s checks must also still pass, including the `face_recognition` and `cv2` import-isolation classes — `filenames.py` and `bulk_import.py` import neither, and `bulk_import.py` reaches the library only through `service.py` and `encoder.py`.
19. **Preserve existing behaviour.** All four existing `/api/students/<id>/face-encodings` endpoints and `GET /api/classes/<id>/face-enrollment` are untouched, and the page's existing per-student capture, sample list, delete, and confirm-dialog flows all still work exactly as before.

## Definition of done

**Backend — authorization and validation**

1. The endpoint requires `admin`; a faculty member, a student, and an unauthenticated caller each get `403`/`401` and nothing is written.
2. An unknown `class_id` returns `404` and a malformed one returns `400`, both before any image is decoded.
3. A request with no `images` part returns `400`; a request with more than `MAX_IMPORT_FILES` parts returns `400` naming the cap; and neither writes anything.
4. A part that is not an allowed content type, and a part over `MAX_IMAGE_BYTES`, each return `400` through the existing `require_image`, and no other part in that request is registered.
5. A body over `MAX_CONTENT_LENGTH` is refused as `413` by Werkzeug, and `MAX_REQUEST_BYTES` is unchanged from its value before this feature.
6. With `face_recognition` unimportable the endpoint returns `503` with a JSON error, no file is decoded, and the app still starts and serves `/api/health`.

**Backend — matching**

7. A file named with a student's ID registers to that student — including in caps, `24DCS001.jpg`, against a lowercase stored address; a file named with the full email does the same.
8. Matching is case-insensitive on both forms, and surrounding whitespace in the stem is ignored.
9. A file whose stem matches a student not on this class's roster is `no_match`, and no encoding is written for them.
10. An ID matching two roster students returns `ambiguous`, names how many matched, and writes nothing; a full-email stem matching one of those two still registers.
11. `24DCS001-2.jpg` does not match `24DCS001` — no suffix is stripped — and a file named after a student's *name* rather than their ID is `no_match`.
12. `filenames.py` imports neither Flask, nor pymongo, nor any CV library, verified by reading its imports, and its tests run with no app context and no database.

**Backend — per-file outcomes**

13. A photo with no detectable face, and one with several faces, are each `rejected` for the matched student with the domain message verbatim, while the other files in the same request still register.
14. A student already holding `MAX_SAMPLES_PER_STUDENT` samples is `rejected` at the cap, and their existing samples are unchanged.
15. A photo whose face closely matches another student's existing sample is `rejected` with the cross-student conflict message, including when that other student's sample was inserted earlier in the same request.
16. Results appear in the order the files were submitted, one entry per submitted file, and `summary` counts equal the statuses in `results`.
17. A stored document written by the import is identical in shape to one written by `POST /api/students/<id>/face-encodings`, with `source` recorded as `upload` and `created_by` the acting admin — and a client-supplied `source` field is ignored.
18. No response on any status contains an `encoding` field or any part of the vector.
19. A request in which every file fails still returns `200` with a complete report.
20. A database error during the import surfaces as the blueprint's `500` JSON error, not as a per-file `rejected` row.
21. `pytest` passes in full, including `test_app_factory.py`'s route guard and every check in `test_no_secrets_and_scope.py`.

**Frontend**

22. `/admin/face-enrollment` shows the import panel only when a class is selected, above the roster, and states the naming rule with a student-ID example.
23. Selecting files shows how many were selected and their total size; the Import button is disabled with nothing selected and while an import is running.
24. An import of more files than fit one request is sent as several sequential requests, and the panel reports progress as files completed out of files selected.
25. The finished report groups the four statuses, names the student for every `registered` and `rejected` row, shows the server's own message for each failure, and is announced through an `aria-live` region.
26. When a batch fails, the results already collected stay on screen and the panel says which files were not attempted.
27. After an import the roster's sample counts and the "N of M students have no face samples yet" summary are refreshed from the server, without a full page reload.
28. A backend error (`403`, `413`, `503`) surfaces in the page's existing error callout with the backend's own message, and no partial state is left claiming success.
29. The panel is keyboard reachable and operable end to end, every control has a visible focus ring, and it reads sensibly to a screen reader in both light and dark themes.
30. The page is measured at 320/360/414/480/636/768/1024/1440, the existing picker and roster measurements are confirmed to still hold, every control reaches its 44px touch target at coarse-pointer widths, and the new numbers are written into `admin-face-enrollment.css`.
31. `npm run build` completes with no new warnings.

**Process**

32. `/test-feature 22-bulk-face-enrollment-import` and `/code-review-feature 22-bulk-face-enrollment-import` have both run, and approved findings are fixed.
33. `CLAUDE.md`'s feature table and "Next planned feature" are updated, including this feature's own deferrals: no ZIP or manifest input, no multi-sample naming convention, no same-student duplicate detection, no import audit trail, no background or asynchronous processing, and bulk import stays admin-only.
