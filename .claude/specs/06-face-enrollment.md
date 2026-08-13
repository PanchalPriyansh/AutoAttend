# Spec: Face Enrollment

## Overview

Everything AutoAttend promises about attendance depends on the system knowing what each student looks like. `05-admin-user-management` created student accounts and enrolled them into classes, but a student record today is a name, an email, and a role — there is nothing to match a classroom photo against. This feature closes that gap: an admin selects a class, sees its roster, and registers each student's face by uploading a photo or capturing one from the webcam. The backend derives a face *encoding* (a numeric descriptor) from the image and stores only that encoding — never the photograph. It is the first feature in the project to touch computer vision, so it also owns the introduction of `face_recognition`/`numpy` as dependencies and the isolation boundary that keeps CV code out of the rest of the application. `07-attendance-capture` consumes what this feature produces: without stored encodings there is nothing for the recognition pipeline to compare a classroom photo to.

This spec covers **enrollment only** — registering, listing, and deleting a student's face data. Matching many faces in one classroom image, marking attendance, and anything to do with confidence thresholds at attendance time belong to `07`.

## Depends on

- `01-project-foundation` — `create_app()` app factory, blueprint registration, env-driven `Config`, React routing shell.
- `02-database-setup` — `database/schema.py` as the single declaration point for collections/validators/indexes, and the `flask init-db` command that applies them.
- `03-authentication` — `role_required(*roles)` (`backend/auth/decorators.py`), the HttpOnly-cookie JWT + CSRF setup, `apiFetch`/`requestJson` (`frontend/src/api/client.js`), `AuthContext`, `ProtectedRoute`.
- `04-academic-hierarchy-management` — the `classes` collection and `GET /api/classes`, the cascading selector pattern in `AcademicHierarchy.jsx`, `ConfirmDialog.jsx`, and `frontend/src/api/academic.js`.
- `05-admin-user-management` — student accounts in `users`, the `class_enrollments` roster and `GET /api/classes/<id>/students`, the `common/errors.py` + `common/validators.py` + `common/http.py` shared layer, and the thin-route / service / serializer / validator package layout established by `backend/users/`.

## APIs

All endpoints live on a new `faces_bp` blueprint (`url_prefix="/api"`). **Every endpoint is `admin` only.** `CLAUDE.md` assigns face enrollment to the admin, and faculty do not need it to take attendance — `07` will read encodings server-side inside its own pipeline, not through a client-facing endpoint. Students get no access.

- `GET /api/students/<student_id>/face-encodings` — list the student's registered samples (metadata only) — **admin**
- `POST /api/students/<student_id>/face-encodings` — register one sample; `multipart/form-data` with an `image` file part and an optional `source` field (`upload` | `camera`) — **admin**
- `DELETE /api/students/<student_id>/face-encodings/<encoding_id>` — delete one sample — **admin**
- `DELETE /api/students/<student_id>/face-encodings` — delete every sample for that student (erasure of their biometric data) — **admin**
- `GET /api/classes/<class_id>/face-enrollment` — the class roster with each student's sample count and most recent enrollment time, so the admin can see at a glance who is still unregistered — **admin**

**Status codes** — same `{ "error": "..." }` contract as `01`–`05`:

- `200` — successful read or delete
- `201` — sample registered, returning its metadata
- `400` — malformed ObjectId; missing `image` part; an unsupported or unreadable image; **no face found in the image**; **more than one face found**; `student_id` references a user who is not an active student
- `401` / `403` — unauthenticated / non-admin (handled by `role_required`)
- `404` — the student, class, or encoding does not exist
- `409` — the sample cap for that student is already reached, or the submitted face matches a **different** student's registered face
- `413` — the uploaded file exceeds the size limit
- `500` — database error, via blueprint-level `PyMongoError` / `RuntimeError` handlers mirroring `routes/users.py`
- `503` — the face-recognition library is not installed/importable on the server (see Rules)

**Serialized encoding shape — the encoding vector itself is never included in any response, under any condition:**

```json
{
  "id": "...",
  "student_id": "...",
  "model": "hog",
  "source": "camera",
  "created_at": "2026-08-13T00:00:00+00:00",
  "created_by": "..."
}
```

**Serialized class enrollment-status shape:**

```json
{
  "student": { "id": "...", "name": "...", "email": "...", "is_active": true },
  "sample_count": 3,
  "last_enrolled_at": "2026-08-13T00:00:00+00:00"
}
```

List endpoints return their array under a named key (`{ "encodings": [...] }`, `{ "students": [...] }`), matching the `04`/`05` convention. The bulk delete returns `{ "deleted": <count> }`.

## Database changes

**One new collection: `face_encodings`.** Declared in `backend/database/schema.py` alongside the existing seven so `flask init-db` creates it with its validator and indexes; nothing else in the project may create a collection.

Document shape — **one document per sample**, not an array of samples on the student:

| Field | Type | Notes |
|---|---|---|
| `student_id` | `objectId` | required; the `users._id` of a `student` |
| `encoding` | `array` of 128 `double` | required; exactly 128 items — the `face_recognition` descriptor length |
| `model` | `string` | required; which detector produced it (e.g. `"hog"`), so a later model change is distinguishable rather than silently mixed in |
| `source` | `string` | required; `"upload"` or `"camera"` |
| `created_at` | `date` | required; real BSON date |
| `created_by` | `objectId` | required; the admin who registered it, taken from the JWT identity |

Indexes: `idx_student_id` on `student_id` (non-unique — multiple samples per student are the point).

Additional constraints:

- **The source image is never stored** — not in MongoDB, not on disk, not in a temp file that outlives the request, not in a log. Only the derived encoding persists. This is the whole point of storing an encoding rather than a photo, and it is what `CLAUDE.md`'s "never expose sensitive biometric data unnecessarily" means in practice here.
- **No unbounded arrays and no new fields on `users`.** A student's enrollment state is derived by counting `face_encodings` documents, not by a flag or a count cached on the user document that could drift.
- `numpy` float64 values must be converted with `.tolist()` before insertion — pymongo cannot encode `numpy.float64`, and the `bsonType: "double"` validator requires real floats.
- Deactivating a user does **not** delete their encodings, and unenrolling a student from a class does not either. Deletion is an explicit admin action through the endpoints above.

## Frontend

- **Create:**
  - `frontend/src/api/faces.js` — wrappers over the endpoints above, following `frontend/src/api/users.js` exactly: unwrap the JSON, let `requestJson` throw the backend's own `error` message. The upload sends a `FormData`; it must not set `Content-Type` itself (the browser sets the multipart boundary).
  - `frontend/src/routes/admin/FaceEnrollment.jsx` — the `/admin/face-enrollment` screen: pick a class, list its roster with each student's sample count, and register/delete samples for the selected student. Reuse `frontend/src/api/academic.js` for the class cascade rather than inventing a second selector.
  - `frontend/src/components/admin/FaceCapture.jsx` — the capture control: a file input **and** a webcam capture (`navigator.mediaDevices.getUserMedia` → `<video>` → `<canvas>` → `toBlob`). It must stop every media track when the component unmounts or capture ends, so the camera light does not stay on. If camera access is denied or unavailable, it degrades to file upload with a plain message instead of breaking the page.

- **Modify:**
  - `frontend/src/api/client.js` — `rawFetch` currently sets `Content-Type: application/json` whenever a body is present, which corrupts a multipart upload. Skip that header when the body is a `FormData`. **Nothing else in this file changes** — cookies, the CSRF header, and the refresh-and-retry stay exactly as `03` built them.
  - `frontend/src/App.jsx` — add `/admin/face-enrollment` wrapped in `<ProtectedRoute role="admin">`.
  - `frontend/src/routes/AdminPortal.jsx` — add a link alongside the existing academic-hierarchy and user links.
  - Reuse `frontend/src/components/admin/ConfirmDialog.jsx` for delete confirmations. Do not write a second dialog.

## Backend

- **Create:**
  - `backend/recognition/__init__.py`
  - `backend/recognition/encoder.py` — the **only** module that imports `face_recognition`/`numpy`. Pure CV: image bytes in, list of 128-float encodings out, plus `is_available()` and the comparison helper (`distance` / `matches` against a tolerance). No database access, no Flask objects, no business rules. The tolerance, the 128-length constant, the detector model name, the per-student sample cap, and the max image size live here (or in a small sibling constants block) as named module constants — not as literals scattered through the routes.
  - `backend/recognition/errors.py` — only if a genuinely feature-specific exception is needed (e.g. `RecognitionUnavailableError` for the 503). Bad input, missing target, and conflict already exist in `common/errors.py` and must be reused, not re-declared.
  - `backend/recognition/validators.py` — image-input rules: required file part, allowed content types, size ceiling, and the "exactly one face" rule expressed as domain validation. Takes bytes and metadata, never a Flask `FileStorage`.
  - `backend/recognition/serializers.py` — `serialize_encoding`, `serialize_encodings`, `serialize_enrollment_status`. Built from an explicit field allow-list, like `users/serializers.py`, so the `encoding` array is structurally unable to reach a response.
  - `backend/recognition/service.py` — enrollment business logic with no Flask objects in any signature: `register_encoding`, `list_encodings`, `delete_encoding`, `delete_student_encodings`, `class_enrollment_status`. It resolves and role-checks the student, calls `encoder.py`, applies the sample cap and the cross-student duplicate check, and writes `face_encodings`.
  - `backend/routes/faces.py` — the `faces_bp` blueprint. Handlers stay thin exactly as `routes/users.py` is: `role_required("admin")`, pull the file part off `request.files`, validate, call the service, serialize, return. Exception→status mapping goes in blueprint-level `errorhandler`s, not in per-handler try/except.

- **Modify:**
  - `backend/database/schema.py` — add `FACE_ENCODINGS`, its validator, and its index to `COLLECTIONS`. Existing declarations are untouched.
  - `backend/app.py` — register `faces_bp`; set `MAX_CONTENT_LENGTH` from the recognition constant and register a JSON handler for `RequestEntityTooLarge` so an oversized upload returns `{"error": ...}` with `413` rather than Flask's HTML page. No other change.
  - `backend/requirements.txt` — add `face_recognition` and `numpy` with a comment noting the native `dlib`/CMake build requirement.
  - `CLAUDE.md` — mark Face enrollment implemented in the "Implemented vs stub features" table, note what is deferred, and add any install step the backend command list now needs.

## Files to change

- `backend/database/schema.py`
- `backend/app.py`
- `backend/requirements.txt`
- `CLAUDE.md`
- `frontend/src/api/client.js`
- `frontend/src/App.jsx`
- `frontend/src/routes/AdminPortal.jsx`

## Files to create

- `backend/recognition/__init__.py`
- `backend/recognition/encoder.py`
- `backend/recognition/errors.py` (only if a feature-specific exception is genuinely needed)
- `backend/recognition/validators.py`
- `backend/recognition/serializers.py`
- `backend/recognition/service.py`
- `backend/routes/faces.py`
- `frontend/src/api/faces.js`
- `frontend/src/routes/admin/FaceEnrollment.jsx`
- `frontend/src/components/admin/FaceCapture.jsx`

## New dependencies

- `face_recognition` — face detection and 128-dimension encoding.
- `numpy` — the array type `face_recognition` returns and the distance computation.

**Not now:** `opencv-python` belongs to `07-attendance-capture`, which needs video frame extraction; enrollment works on a single still image and does not need it. `scikit-learn` belongs to `10`, SMTP/email to `11`. Adding any of them here is the mid-feature dependency creep `CLAUDE.md` prohibits. No image-processing helper beyond what `face_recognition` already pulls in, no upload/storage service, no UI kit, no webcam React library — `getUserMedia` is a browser API.

**`face_recognition` needs `dlib`, which compiles native code and needs CMake on Windows.** Document the install step; do not work around it by swapping in a different recognition stack, and do not vendor a prebuilt binary into the repo.

## Rules for implementation

- **Every endpoint is guarded on the backend** with `role_required("admin")`. `ProtectedRoute` and hidden UI are conveniences only — an authenticated faculty or student calling these endpoints directly must receive `403`.
- **The encoding vector never leaves the backend.** Serialize from an explicit allow-list; no endpoint, debug route, or error message returns the array. A raw `face_encodings` document must never reach `jsonify`.
- **The source image is never persisted or logged.** Process it in memory, derive the encoding, discard the bytes. No `uploads/` directory, no base64 copy in Mongo, no image bytes in a log line or an exception message.
- **All CV code stays inside `backend/recognition/encoder.py`.** No `import face_recognition` or `import numpy` in a route, a service that also talks to Mongo, or anywhere in `users/`, `academic/`, or `auth/`. This is the separation `CLAUDE.md` requires, and it is what makes the rest of the suite runnable without the native library.
- **The library is imported lazily and its absence is handled.** The Flask app must still start, `/api/health` must still respond, and the `01`–`05` test suites must still pass on a machine where `dlib` is not built. An enrollment request on such a machine returns `503` with a clear message — never a stack trace, never a `500`.
- **Face recognition is not treated as perfectly accurate**, per `CLAUDE.md`:
  - Zero faces detected → `400` telling the admin to retry with a clearer photo. Never store "no face" as a valid enrollment.
  - More than one face detected → `400`. Never guess which face is the student; a wrong pick would silently poison attendance for two people.
  - A submitted face whose distance to an existing encoding of a **different** student is within tolerance → `409`, naming the collision as a possible wrong-student enrollment. The admin resolves it; the system does not overwrite anything.
  - The comparison tolerance is a named constant with a comment stating it is a heuristic, not a guarantee.
- **Multiple samples per student are supported and capped.** The cap is a named constant; exceeding it returns `409` rather than silently trimming or overwriting the oldest sample.
- **Referenced entities are verified before anything is stored.** `student_id` must resolve to a user with `role: "student"`; `class_id` must resolve in `classes`. A wrong-role id is a `400` and a non-existent id is a `404`, matching the rule `users/assignments.py` already established — reuse that reasoning rather than inventing new codes.
- **`created_by` comes from the JWT identity**, never from the request body.
- **Import collection names from `database/schema.py`** (`FACE_ENCODINGS`, `USERS`, `CLASSES`, `CLASS_ENROLLMENTS`). No hardcoded `"face_encodings"` literal anywhere else.
- **Keep business logic out of route handlers.** `routes/faces.py` translates HTTP ↔ domain; `recognition/service.py` owns the rules and holds no Flask objects.
- **Reuse before rewriting.** `common/errors.py`, `common/validators.py`, `common/http.py`, `common/serializers.py`, `role_required`, `ConfirmDialog.jsx`, `requestJson`, and `api/academic.js` all exist. A second ObjectId parser, a second error hierarchy, or a second class-selector cascade is a defect.
- **The upload path must not weaken CSRF.** A multipart `POST` still carries the `X-CSRF-TOKEN` header; the `client.js` change removes only the JSON `Content-Type`, nothing else.
- **Validate the upload before decoding it.** Enforce the size limit and an allowed content type first, so a hostile or malformed file is rejected on cheap checks rather than inside the decoder. A decoder failure is a `400`, never a `500` or a traceback.
- **Never leak internals.** Driver exceptions, `$jsonSchema` validator failures, and library errors are translated into the clean JSON contract and logged server-side.
- **Never hardcode academic or user data.** The class list, roster, and student identities all come from MongoDB.
- **Preserve existing functionality.** `/api/health`, `flask init-db`, `flask create-admin`, every auth endpoint, all twenty academic endpoints, all ten user-management endpoints, `/admin/academics`, and `/admin/users` must keep working. `flask init-db` must stay idempotent on a database that already has the first seven collections. The app must still start when MongoDB is unreachable.
- **Do not touch attendance, ML, or notification code**, do not create their collections, and do not start matching faces against a classroom photo — that is `07`.
- **Deferred deliberately, to be recorded in `CLAUDE.md` rather than solved here:** students and faculty cannot see enrollment status (admin-only for now); there is no bulk/multi-student import; and no re-encoding migration path exists if the detector model is later changed — the stored `model` field is what makes that migration possible later.
- **Tests must not use real production credentials, a production database, real personal data, or any real biometric data.** Use synthetic generated images and a monkeypatched/faked encoder for the service and route tests; no real faces, no photographs of real people, and no image fixtures committed to the repository.

## Definition of done

- [ ] `flask init-db` creates `face_encodings` with its validator and `idx_student_id`, is idempotent on re-run, and leaves the existing seven collections unchanged.
- [ ] `python app.py` starts with `faces_bp` registered; `/api/health`, the auth endpoints, the twenty academic endpoints, and the ten user-management endpoints all still respond as before.
- [ ] `POST /api/students/<id>/face-encodings` with a single-face image returns `201` and the sample's metadata; the stored document has exactly 128 `double` values, a real BSON `date` in `created_at` (verified in MongoDB — `typeof` is `date`, not `string`), and a `created_by` matching the acting admin.
- [ ] **No response from any endpoint contains the `encoding` array** — verified for create, list, class status, and both delete endpoints.
- [ ] **No image bytes are persisted anywhere** — nothing is written to disk, no image field exists on the stored document, and no log line contains image data.
- [ ] An image with **no detectable face** returns `400`; an image with **two faces** returns `400`; neither writes a document.
- [ ] Submitting a face that matches a **different** student's registered encoding within tolerance returns `409` and stores nothing; submitting another sample of the **same** student succeeds.
- [ ] Registering more than the sample cap for one student returns `409`, and the existing samples are untouched.
- [ ] A non-image file, a corrupt image, and a missing `image` part each return `400` with a clear message — never a `500` or a traceback.
- [ ] An upload above the size limit returns `413` as JSON on the `{"error": ...}` contract, not an HTML error page.
- [ ] `student_id` referencing a **faculty** or **admin** user returns `400`; a non-existent id returns `404`; a malformed id returns `400`.
- [ ] `GET /api/students/<id>/face-encodings` lists only that student's samples, newest first, with no cross-student leakage.
- [ ] `DELETE /api/students/<id>/face-encodings/<encoding_id>` removes one sample with `200` and repeating it returns `404`; `DELETE /api/students/<id>/face-encodings` removes all of them and reports the count.
- [ ] `GET /api/classes/<id>/face-enrollment` returns that class's roster with accurate `sample_count` and `last_enrolled_at`, including students with zero samples, verified against two populated classes with no cross-class leakage.
- [ ] With the recognition library unavailable, the app still starts, `/api/health` returns `200`, the `01`–`05` suites pass, and an enrollment request returns `503` with a clear message.
- [ ] An authenticated **faculty** token receives `403` on all five endpoints, an authenticated **student** token receives `403`, and an unauthenticated request receives `401`.
- [ ] A multipart `POST` with valid cookies but no `X-CSRF-TOKEN` header is rejected, confirming CSRF still applies to the upload route.
- [ ] `/admin/face-enrollment` lists a class roster with per-student sample counts, registers a face by **file upload**, registers one by **webcam capture**, and deletes a sample with confirmation — each updating the list without a full page reload.
- [ ] The webcam stream stops when capture ends or the component unmounts (camera indicator goes off), and a denied camera permission leaves the file-upload path working.
- [ ] A backend `400`/`404`/`409`/`413`/`503` surfaces in the UI as the server's own message, not a generic failure.
- [ ] Existing JSON API calls still work after the `client.js` change — verified by `/admin/academics` and `/admin/users` continuing to create, update, and delete normally.
- [ ] No `face_recognition` or `numpy` import exists outside `backend/recognition/encoder.py`.
- [ ] `CLAUDE.md` reflects the new Face enrollment status, the deferred items, and the `dlib`/CMake install note.
