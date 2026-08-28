# Spec: ConfirmDialog Focus Trap

## Overview

`ConfirmDialog` tells assistive technology it is modal — `role="alertdialog"`, `aria-modal="true"` — and then does not behave like it. It moves focus to Cancel on open and closes on Escape, but nothing holds focus inside it: one Tab past the confirm button lands on the page behind, where every control is still in the tab order, still reachable and still clickable. A screen-reader user is told the rest of the page is unavailable while their keyboard walks straight into it, and a keyboard user can tab to the "Delete" button *underneath* the dialog asking them to confirm a delete.

This is the last standing item from the redesign's own report. `styles/confirm-dialog.css` records it in the file, in words, as a deliberate deferral: *"the dialog says `aria-modal="true"` but does not trap focus… That is a behaviour change on six screens, not a style one, and it belongs with the polish pass's other accessibility work rather than inside the redesign. Reported, not silently changed."* This spec is that work, and it is the **first spec of the polish pass agreed 2026-08-26**.

Six files render this component and five of them are guarding an action that cannot be undone: deleting a hierarchy level, unenrolling a student, deactivating an account, deleting a face sample, deleting a recorded attendance session, and overwriting one. It is the last thing between an admin and an irreversible write, which is why it is worth fixing properly rather than approximately.

The change is **behaviour only**. Nothing about the dialog moves, resizes, or recolours; `confirm-dialog.css` gains no rule and loses none. Its one CSS edit is the comment quoted above, which stops being true.

## Depends on

- `12-app-shell` — `AppShell.jsx` renders `<main id="main" className="page" tabIndex={-1}>`. That `tabIndex={-1}`, added so the skip link could move focus, is already the right landing place for the one case focus restoration cannot solve, so this spec needs no new markup to fall back to.
- `13-component-vocabulary` — the dialog's two buttons are `.btn .btn--secondary` and `.btn .btn--danger`. The trap must treat them as ordinary focusable elements; it does not know or care that they are primitives.
- The group 4b teardown — `styles/confirm-dialog.css` and the note this spec closes.
- The six renderers, which are read but not changed: `components/admin/HierarchyLevel.jsx`, `components/admin/ClassAssignment.jsx`, `routes/admin/UserManagement.jsx`, `routes/admin/FaceEnrollment.jsx`, `routes/faculty/AttendanceCapture.jsx`, `routes/faculty/AttendanceHistory.jsx`.

## APIs

**No API changes.** No new or modified endpoint, request shape, response shape, query parameter, status code, role requirement, or error contract. Nothing in this spec reaches the network.

## Database changes

**No database changes.** No collection, field, index, or migration.

## Frontend

- **Modify:**

  - `frontend/src/components/admin/ConfirmDialog.jsx` — the whole feature. Five changes, all inside this one file:

    | Change | What it does | Why it is written this way |
    |---|---|---|
    | A ref and `tabIndex={-1}` on the `.dialog` element | Gives the dialog itself somewhere focus can rest, and gives the trap a root to query within. | `tabIndex={-1}` keeps it out of the tab order while making it programmatically focusable — the same device `AppShell` already uses on `<main>`. It is needed for the pending case below. |
    | Remember and restore the trigger | On open, capture `document.activeElement`. On close, return focus to it. | Without this the fix is half a fix: trapping focus and then dropping it on `<body>` when the dialog closes leaves a keyboard user at the top of the document, further from where they were than before. |
    | Restoration must survive the trigger being deleted | Before restoring, check the remembered element is still in the document (`isConnected` / `document.contains`). If it is gone, focus `<main id="main">` instead. | This is the normal case, not the edge case. In four of the six renderers a successful confirm removes the very button that opened the dialog: the `<li>` goes in `HierarchyLevel`, `ClassAssignment` and `FaceEnrollment`, and the open session panel goes in `AttendanceHistory`. Only `UserManagement` (the row stays, deactivated) and `AttendanceCapture` (the trigger is the Save button) leave the trigger in place. |
    | Trap Tab and Shift+Tab | In the existing `keydown` listener, on `Tab`: collect the focusable elements inside the dialog, and if focus is on the last one (or, with Shift, the first), move it to the other end and `preventDefault()`. | Two buttons is a small ring, but the handler must be written against a queried list rather than the two refs, so it stays correct if the dialog ever gains a third control. |
    | Handle "nothing focusable" | While `pending` is true both buttons are `disabled`, so the dialog contains no focusable element at all. Focus must be moved to — and held on — the dialog container. | This is a real state on every screen: it is what the dialog looks like for the whole duration of the delete request. Today, tabbing during that window walks out of the dialog exactly as it does at rest. The focusable list must therefore be recomputed **at each keydown**, not captured once on open, because `disabled` changes underneath it. |

  - `frontend/src/styles/confirm-dialog.css` — the comments, plus **one rule**. The closing "NOT fixed here, deliberately" paragraph is replaced by a short note recording that the trap now exists and lives in the component rather than the stylesheet, and the `.dialog-actions` ordering comment ("Cancel first in the DOM and focused on open, so the safe choice is where a keyboard lands") gains a line saying it is now enforced by both files.

    The rule is `.dialog:focus { outline: none }`, and it is required by the `tabIndex={-1}` above rather than being a style change in its own right. `base.css` declares a global `:focus-visible` ring, and Chrome matches `:focus-visible` on a programmatically-focused `tabindex="-1"` container — measured, 2px solid `--focus` — so without this rule the whole dialog gains an outline for the duration of every delete request, on all six screens. That is a visual regression, and this spec's own "no visual change of any kind" rule is what forbids it.

    It is written as CSS rather than as `focus({ focusVisible: false })` in the component because the option is not supported everywhere (it works in Chrome, measured; Safari ignores it and would still paint the ring), and because the project has already settled this exact case: `shell.css` carries `.page:focus { outline: none }` for `<main>`, the skip link's programmatically-focused target, and `base.css`'s focus comment names it as one of the two deliberate overrides of the global ring. This is the second, written the same way and for the same reason. It matches the container only — `.dialog .btn` is untouched, and the two buttons keep their rings.

  - `CLAUDE.md` — remove the focus-trap bullet from the polish-pass list in "Next planned feature", and update the App shell / stylesheet notes that name it as outstanding. The remaining polish-pass items are untouched.

- **Not modified:** the six renderers. This is a deliberate constraint, not an accident — see the rules below. No call site changes a prop, adds a ref, or learns anything about focus. `ConfirmDialog`'s props (`open`, `title`, `message`, `confirmLabel`, `pending`, `onConfirm`, `onCancel`) are unchanged in name, count, meaning, and default.

- **Not created:** `frontend/src/hooks/useFocusTrap.js`. See the rules.

## Backend

**No backend changes.** Nothing under `backend/` is created, modified, or deleted. `pytest` should be unaffected and must still pass.

## Files to change

- `frontend/src/components/admin/ConfirmDialog.jsx`
- `frontend/src/styles/confirm-dialog.css` (comment only)
- `CLAUDE.md`

## Files to create

**None.**

## New dependencies

**No new dependencies.** `frontend/package.json` and `backend/requirements.txt` are byte-identical afterwards.

Specifically ruled out, because an accessibility spec is exactly where they arrive: `focus-trap` / `focus-trap-react`, `react-focus-lock`, `react-modal`, `@radix-ui/react-dialog`, `@headlessui/react`, and any headless component library. The trap is one `keydown` branch over a two-item ring in a project with one modal; a dependency here would be larger than the thing it replaces and would bring a component API the six call sites would then be rewritten against.

**Open, and not decided by this spec:** the project has no frontend test runner at all — `frontend/package.json` has `dev`, `build`, `lint`, `preview` and nothing else, and there is not one `*.test.jsx` in the tree. Adding Vitest, jsdom and Testing Library to assert focus order is a reasonable thing to want and a project-wide decision with its own spec; it is not something this one smuggles in under an accessibility fix. The Definition of done below is therefore a keyboard walkthrough, and it is written to be executed rather than skimmed.

## Rules for implementation

**Scope**

- **Behaviour only.** No visual change of any kind — which is what requires the one CSS rule described above, rather than being contradicted by it: `tabIndex={-1}` on the dialog would otherwise show the global focus ring around it throughout every pending request. Nothing in `components.css`, `tokens.css` or `base.css` is touched, and the global `:focus-visible` rule is overridden for this one container in this one file rather than weakened for everyone.
- **The six renderers do not change.** If the fix appears to need a call site to pass a ref, to render the dialog elsewhere, or to know when it closed, the fix is wrong: everything this spec needs is inside the component or already in `AppShell`. This is also what keeps the change reviewable — one component file against six screens.
- **The trap lives in `ConfirmDialog.jsx`, not in a hook.** `hooks/useCamera.js` exists because two screens needed the same webcam lifecycle; `ConfirmDialog` is the only element in the project with `role="dialog"` or `aria-modal` — `UserForm` is an inline form, not a modal. The project's standing rule is that shared code is extracted from two real implementations and never invented for one, and that rule is why the vocabulary still has no `.modal`. A second modal, if one ever appears, is when the hook gets written.

**Deliberately out of scope, recorded here so they are not lost and not silently added**

- **Backdrop click to dismiss.** The dialog has never had it. Adding it to a destructive-action prompt is a product decision about how easy the prompt should be to escape, not part of making the keyboard match the ARIA.
- **`inert` or `aria-hidden` on the rest of the page.** It would make the modality real for pointer and virtual-cursor users too, but the dialog is a DOM descendant of `<main>` at every call site — there is no portal — so it cannot be done without either moving the dialog to a portal or inerting its own ancestor. Both are larger than this spec.
- **Body scroll lock.** The backdrop already carries `overscroll-behavior: contain`, which was the deliberate scope in group 4b.
- **`aria-describedby` pointing at the message, and `useId()` for `confirm-title`.** Both are worth doing and neither is a focus trap. The static `id` is not currently a duplicate-id bug — `/admin/academics` mounts five `HierarchyLevel`s, but a closed dialog returns `null` and renders no element at all — so there is nothing here that is broken today.

**Correctness**

- **Escape semantics are unchanged.** Escape still cancels, and is still ignored while `pending` — a request is in flight and closing the dialog would leave the user with no sign of it.
- **Cancel still receives focus on open.** The safe choice is where a keyboard lands; the stylesheet's `.dialog-actions` comment says so and the DOM order exists to make it true.
- **Recompute focusables on every keydown, never cache them on open.** `pending` toggles `disabled` on both buttons mid-dialog.
- **Every effect cleans up.** The app runs in `<StrictMode>`, so effects mount, unmount and remount in development; a `keydown` listener added without a matching `removeEventListener` will be installed twice and will `preventDefault()` twice.
- **No listener survives close.** `ConfirmDialog` returns `null` when `open` is false, and it must also be holding no document-level listener then — a closed dialog that still handles Tab would break every page that renders one.
- **Restoration never throws and never focuses a removed node.** The remembered element may be gone, may never have existed (`document.activeElement` can be `<body>`), and must be checked before use.

**Project-wide**

- No raw colour anywhere outside `tokens.css` — this spec adds no colour at all.
- Frontend-only: no `ProtectedRoute`, no `@role_required`, no endpoint, no academic data, no biometric data, no secret. Authorization is untouched; a focus trap is not a permission.
- Preserve existing functionality: every confirm, cancel, pending state and error path on all six screens behaves exactly as before.

## Definition of done

**The trap works**

- [ ] With the dialog open, pressing Tab repeatedly cycles only between Cancel and the confirm button and never reaches any control on the page behind it.
- [ ] Shift+Tab from Cancel moves to the confirm button, not out of the dialog.
- [ ] Tab from the confirm button moves to Cancel, not out of the dialog.
- [ ] While `pending` (both buttons disabled), Tab and Shift+Tab do not move focus out of the dialog; focus rests on the dialog container.
- [ ] Cancel is focused when the dialog opens, on every one of the six screens.
- [ ] Escape cancels the dialog, and is still ignored while `pending`.
- [ ] The focused element always shows the project focus ring — the trap moves focus, it never suppresses the outline.

**Focus comes back to the right place**

- [ ] `/admin/users` — cancelling and confirming a deactivation both return focus to the row's Deactivate button, which is still in the DOM.
- [ ] `/faculty/attendance` — cancelling and confirming the replace prompt both return focus to the Save button.
- [ ] `/admin/academics` (both `HierarchyLevel` and `ClassAssignment`), `/admin/face-enrollment`, `/faculty/attendance/history` — **cancelling** returns focus to the trigger; **confirming** deletes the row and its trigger with it, and focus lands on `<main id="main">` rather than on `<body>`.
- [ ] After any close, `document.activeElement` is never `<body>` and never a detached node.

**Nothing else changed**

- [ ] All six screens confirm, cancel, show their pending label, and surface their error messages exactly as on `main`.
- [ ] The six renderers are byte-identical to `main`.
- [ ] `ConfirmDialog`'s prop list and defaults are unchanged.
- [ ] The dialog is visually identical to `main` at 360px, 768px and 1440px, in light and dark, including the 360px column-reverse button stack.
- [ ] `frontend/src/styles/confirm-dialog.css` differs from `main` in comment text plus exactly one added rule, `.dialog:focus { outline: none }`. No existing declaration is changed, removed, or reordered.
- [ ] While `pending`, no focus ring is painted around the dialog container, and both buttons still show theirs when focused.
- [ ] No file outside `frontend/src/components/admin/ConfirmDialog.jsx`, `frontend/src/styles/confirm-dialog.css` and `CLAUDE.md` is modified.

**Gates**

- [ ] `npm run lint` reports no new warning (the pre-existing `AuthContext.jsx:51` one may remain).
- [ ] `npm run build` passes.
- [ ] No development-mode double-invocation artefacts under `<StrictMode>`: opening and closing the dialog repeatedly leaves no accumulating listener and no console error.
- [ ] `frontend/package.json` is byte-identical.
- [ ] Nothing under `backend/` changed, and `pytest` still passes.

**Records updated**

- [ ] `confirm-dialog.css`'s deferral note is replaced by a note recording the trap, so the file no longer describes a defect it no longer has.
- [ ] `CLAUDE.md` no longer lists the focus trap as an outstanding polish-pass item.
