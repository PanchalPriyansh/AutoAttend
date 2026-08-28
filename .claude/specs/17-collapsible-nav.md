# Spec: Collapsible Mobile Nav

## Overview

On a phone the app shell's header is three rows deep — brand, nav, user — and `shell.css:45-63` has carried the measurement and the reason since group 3: at 360px it is 138px tall, each wrapped row costs about 39px, and the two rows that could be merged cannot be merged honestly without moving the nav visually below the user block, which would send the tab sequence top-left, down to the bottom row, then back up to Log out. That comment ends by naming its own fix — "merging them honestly needs the collapsible nav this project has deferred" — and this spec is that deferral being paid off. The `<h1>` currently starts a third of a phone screen down on all nine signed-in pages.

It is **item 4 of the four-item polish pass** scoped 2026-08-28, after `14-confirm-dialog-focus-trap`, `15-muted-on-accent-contrast` and `16-portal-card-link`. The pass entry names three shell deferrals together — the collapsible nav, a spacing scale, and a manual theme toggle. **This spec is the collapsible nav only.** The other two are additive rather than corrective, share no code with this one, and become specs `18` and `19`; merging them here would trade a small, testable change for a three-headed one.

`shell.css` is the one stylesheet the page-by-page redesign never replaced. This spec does not replace it either — it fixes the defect its own comments describe, and corrects the header that still calls the file temporary scaffolding awaiting a group that has now finished.

## Depends on

- `12-app-shell` — `AppShell.jsx`, `NavBar.jsx`, `navigation.js`, and `styles/shell.css`. The nav being collapsed is the one built there, and `navigation.js` stays the only place a role's link list is written.
- `13-component-vocabulary` — `.btn` / `.btn--secondary`, which the toggle composes rather than reinvents.
- `14-confirm-dialog-focus-trap` — the project's worked example of keyboard-correct disclosure. This spec deliberately does **less** than it does; see "Not a dialog" below.
- Group 3's narrow-width header work — the `max-width: 480px` block in `shell.css` that tightened the header's gaps. It stays.
- The `(pointer: coarse)` pattern adopted in group 4b (`admin-academics.css:692`), for the toggle's touch target.

## APIs

**No API changes.** Nothing in this spec reaches the network.

## Database changes

**No database changes.**

## The shape

`NavBar` gains a disclosure button that owns the visibility of the list it already renders:

```jsx
<nav className="app-nav" aria-label="Main">
  <button
    type="button"
    className="btn btn--secondary app-nav-toggle"
    aria-expanded={open}
    aria-controls="app-nav-list"
    onClick={…}
  >
    Menu
  </button>
  <ul id="app-nav-list">…</ul>
</nav>
```

Four decisions are load-bearing:

**The toggle lives inside `<nav>`, not beside it.** `.app-header` is a three-item flex row — brand, nav, user — with `justify-content: space-between`, and a fourth child would change how the header distributes space at *every* width on *every* signed-in page, including the desktop widths this spec is not meant to touch. Keeping the toggle inside the nav keeps the header's flex children at three. It also puts the button immediately before the list it controls in the DOM, which is what makes the focus behaviour below free.

**Visibility is decided by CSS, never by JavaScript.** `.app-nav ul` stays `display: flex` at all widths; the narrow media block hides it and an open-state class shows it again. React state therefore only matters below the breakpoint — above it the toggle is `display: none` and the list is visible regardless of what `open` happens to be. The alternative, `matchMedia` in the component, would write the breakpoint in two places and make the wide layout depend on JavaScript having run. A consequence worth stating rather than hiding: `aria-expanded` can read `false` while the list is visible on a desktop viewport. That is not exposed to anyone, because the element carrying it is `display: none` and so is out of the accessibility tree.

**One breakpoint for all three roles, measured from the widest.** The three roles carry different nav widths, so the width at which the nav stops fitting differs by role. The breakpoint is still one number, because `navigation.js` decides *what is listed* and has never decided what the frame looks like; a role-conditional breakpoint would show two different headers at the same viewport to two people standing next to each other.

**Measured, that number is 413px and it comes from faculty, not admin.** This paragraph originally said the widest role was admin, on the assumption that three links beat two. They do not: "Take Attendance / Attendance History" is 274px against the admin list's 263px. The measurements, taken with each role's real links and a 16-character user name, are in `shell.css` beside the rule.

**And a one-destination nav does not collapse at all.** This is the one thing the measurement overturned rather than corrected. A `.btn` is 44px tall — 16px of padding around the 26.1px line-height every element inherits from `font: 18px/145%` — where the row of text links it replaces is 26px. For a role with a single link there is no row to win back, so collapsing makes the header *taller*: 108.2px against 91.4px at 360, with the link hidden as well. Student is that nav today, so the toggle would have made the only role that never had the defect strictly worse off. The rule is therefore the **count of destinations, not the role** — `.app-nav--single`, one class, set from `items.length` — so a second student destination collapses like everyone else's. This is not a role-conditional frame: it is the same rule applied to different content, which is what `navigation.js` has always fed the header.

**The panel is in flow.** Open, it occupies a row under the toggle and pushes the page down; it is not an overlay, has no backdrop, and covers nothing. This is what lets it skip the machinery in the next section.

## Not a dialog

`ConfirmDialog` traps Tab, sets `aria-modal="true"`, focuses its safe button on open and restores focus on close. **None of that belongs here**, and the difference is not a shortcut:

- The dialog guards a write that cannot be undone and covers the page behind a backdrop; it owes the keyboard the modality it claims. A nav panel guards nothing and covers nothing.
- **No focus trap.** Tabbing past the last link should reach the user block and Log out, exactly as it does when the nav is expanded on a desktop.
- **No focus move on open.** The button precedes the list in the DOM, so the next Tab lands on the first link on its own. Moving focus into the panel would be a second, competing rule for where focus is.
- **No `role="dialog"`, no `aria-modal`, no backdrop, no scroll lock.**

What it does owe the keyboard:

- **Escape closes it, and returns focus to the toggle only when focus was inside the panel** — the one focus move in the feature, and it exists only because closing destroys the element focus is sitting on. Pressed while focus has already tabbed out to Log out, it closes the panel and leaves Log out alone: taking focus back would be a jump nobody asked for, and the panel is not modal. Same guard `ConfirmDialog` applies before restoring, for the same reason.
- **Navigating closes it.** Following a link changes the route while leaving the panel open over the new page. It closes on route change, so the panel never outlives the page it was opened from.
- **`aria-expanded` is always the truth** about whether the list is displayed at that viewport.

## Frontend

- **Modify:**

  - `frontend/src/components/layout/NavBar.jsx` — add the toggle, the `open` state, the Escape handler, and the close-on-route-change effect. The existing contract is untouched: it still reads `navigationFor(role)`, still returns `null` for a role with no destinations, still renders `NavLink` with `end` on every link, and still lets `NavLink` set `aria-current="page"` itself. The keydown listener is attached only while open and removed on close, on the model of `ConfirmDialog`'s. Route changes come from `useLocation()` — `react-router-dom` is already a dependency.

  - `frontend/src/styles/shell.css` — `.app-nav-toggle` (hidden above the breakpoint, shown below), the narrow block that hides `.app-nav ul` and the open-state rule that shows it, and the touch-target floor under `(pointer: coarse)`. Two comments are rewritten rather than left standing: the `max-width: 480px` header block's note that "merging them honestly needs the collapsible nav this project has deferred" is now false, and the file header still describes itself as temporary scaffolding awaiting a redesign group that has finished.

  - `CLAUDE.md` — the collapsible nav leaves the shell-items bullet in "Next planned feature", leaving the spacing scale and the theme toggle behind it as `18` and `19`; the App shell row of the feature table stops listing "no collapsible nav" among its deferrals and records what the header now does at narrow widths.

- **Not modified:** `AppShell.jsx` (the header's three children and the DOM order brand → nav → user are exactly as they are), `navigation.js`, `components.css`, `tokens.css`, `base.css`, and every page stylesheet. No page renders `NavBar`; only `AppShell` does.

- **No new stylesheet.** `styles/<component>.css` exists for a component **several pages render** — `StudentStatusRow`, `ConfirmDialog`, `PortalCard`. `NavBar` has exactly one renderer, and that renderer is the shell, so its rules stay in `shell.css` where they already are. Creating `styles/nav.css` here would apply the shared-component rule to something that is not shared.

## Backend

**No backend changes.** Nothing under `backend/` is created, modified, or deleted; `pytest` is unaffected and must still pass.

## Files to change

- `frontend/src/components/layout/NavBar.jsx`
- `frontend/src/styles/shell.css`
- `CLAUDE.md`

## Files to create

**No new files.**

## New dependencies

**No new dependencies.** `frontend/package.json` is byte-identical afterwards — no menu library, no icon package, no `focus-trap`, and no headless-UI anything. The chevron in `portal-card.css` is two rotated borders for the same reason.

## Rules for implementation

**Scope**

- The collapsible nav only. **No spacing scale and no theme toggle** — they are specs `18` and `19` and share no code with this.
- No change to what any role is shown. Role filtering stays in `navigation.js`, and it decides what is *listed*, never what is allowed: `ProtectedRoute` and `@role_required` are untouched. Hiding links behind a toggle is a layout act and nothing more.
- Desktop is not being redesigned. Above the breakpoint the header must be byte-for-byte the header on `main`.

**The toggle**

- It composes `.btn .btn--secondary` and carries `.app-nav-toggle` for what is shell-specific — the width-conditional `display`, and nothing else that belongs to a button in general. **Do not restyle `.btn`** from `shell.css`, and do not add a primitive or a variant for one control; the rule is two independent implementations first.
- Its accessible name is stable and does not change with state — `aria-expanded` is what carries open/closed. If a glyph is drawn it is borders or an inline SVG with `aria-hidden`, never an icon dependency and never a raw colour.
- `aria-controls` points at the `<ul>`'s id. The id is a constant in the module, not generated per render, and must not collide with `#main`.
- Touch target: at least 44×44 under `(pointer: coarse)`, following `admin-academics.css:692`. The paired width breakpoint is **measured for this header**, not copied from the admin pages — those numbers were measured for their own content and the files say so.

**The panel**

- Closed, the links are `display: none` — genuinely absent from the tab order, not merely invisible. `visibility: hidden` or `opacity: 0` are not acceptable substitutes.
- Open, `.app-nav ul` returns to the flex list it already is; the links keep their existing type, hover, and `.is-current` underline. The current-page mark is not redesigned here.
- The wide layout must not depend on component state: with JavaScript state stuck at `open === false`, a 1024px viewport still shows the full nav.

**Keyboard and focus**

- No focus trap, no `aria-modal`, no backdrop, no scroll lock — see "Not a dialog".
- Escape closes and returns focus to the toggle **when focus is inside the nav**, and otherwise leaves focus where it is. The listener exists only while open.
- Opening moves focus nowhere; the next Tab reaches the first link because the button precedes the list.
- The panel closes on route change, and the header never renders two competing current-page marks.

**Project-wide**

- Frontend-only: no route, no endpoint, no academic data, no biometric data, no secret.
- No raw colour anywhere — every value is a `var(--…)`, in both themes.
- If any transition is added, `prefers-reduced-motion: reduce` disables it. A panel that simply appears is an acceptable outcome; an animation is not required.
- Preserve existing functionality: every destination still navigates, from mouse, touch, and keyboard, on all nine signed-in pages.

**Deliberately out of scope**

- The spacing scale and the manual theme toggle (`18`, `19`).
- The `/admin/face-enrollment` picker arithmetic, and the reported-but-declined guideline findings (Title Case, URL-reflected filter state, list virtualization).
- Login and NotFound, which render no shell.
- Any change to `.page`, `.page-title`, the skip link, or the user block.

## Definition of done

**Rows and height**

- [ ] At 360px the header is **at most two rows** for all three roles, measured, against the three rows and 138px recorded in `shell.css`, and the number it actually reaches is written into the file beside the measurement it replaces.
- [ ] At 390 and 414px it is no taller than at 360.
- [x] The breakpoint is a measured number, chosen from the widest role — **faculty, at 413px**, not admin as this spec first assumed — and `shell.css` states the measurements that produced it, including the 402–413 band where admin's shorter nav pays 17px for the single number.
- [ ] From 700px up the header is the same single 61px row it is on `main`, and the toggle is absent from the accessibility tree there.

**Above the breakpoint, nothing moved**

- [ ] At 768, 1024 and 1440px, in light and dark, every signed-in page's header is pixel-identical to `main` for all three roles.
- [ ] The header still has three flex children and still distributes them with `space-between`.
- [ ] The group 3 `max-width: 480px` header block still applies its tightened gaps.

**The disclosure**

- [x] Below the breakpoint, **for a nav with more than one destination**, the toggle is visible and the links are hidden by default; `aria-expanded="false"`.
- [x] A one-destination nav renders no toggle and never hides its link, at any width: `/student`'s header is unchanged from `main` at all twenty widths measured.
- [ ] Activating it reveals the links and flips `aria-expanded` to `"true"`; activating it again hides them.
- [ ] While closed, `Tab` goes brand → toggle → Log out, and reaches no nav link.
- [ ] While open, `Tab` goes toggle → each link in order → Log out, and **is not trapped** — tabbing past the last link leaves the nav.
- [ ] `Escape` closes the panel and focus lands on the toggle.
- [ ] Following a link navigates and leaves the panel closed on the new page.
- [ ] The toggle's hit area is at least 44×44 under `(pointer: coarse)`.

**Unchanged behaviour**

- [ ] The current page is still marked, still by `NavLink`'s own `aria-current="page"`, and still with the `.is-current` underline; the two nested faculty routes still cannot both claim to be current.
- [ ] `NavBar` still returns `null` for a role with no destinations, and no toggle renders for it.
- [ ] Admin, faculty and student each still see exactly the destinations `navigation.js` lists for them — no more, no fewer.
- [ ] The skip link still reaches `<main>`, and `<main>` still takes focus without showing a ring.
- [ ] With JavaScript state forced closed, a 1024px viewport still shows the full nav.

**Gates**

- [ ] `npm run lint` reports no new warning (the pre-existing `AuthContext.jsx:51` one may remain).
- [ ] `npm run build` passes.
- [ ] `frontend/package.json` is byte-identical; nothing under `backend/` changed, and `pytest` still passes.
- [ ] There is no frontend test runner in this project, so the width, keyboard and focus items above are verified in a real browser against the dev server at the named widths — the method groups 3 and 4 and spec `16` used — and the widths measured are recorded in `shell.css`.

**Records updated**

- [ ] `shell.css`'s header no longer describes itself as temporary scaffolding awaiting a redesign group.
- [ ] The `max-width: 480px` comment no longer says the collapsible nav is deferred; it says what the header now does and what the 24 → 12px gap is still buying.
- [ ] `CLAUDE.md`'s App shell row no longer lists a collapsible nav among its deferrals, and "Next planned feature" carries only the spacing scale and the theme toggle as `18` and `19`.
