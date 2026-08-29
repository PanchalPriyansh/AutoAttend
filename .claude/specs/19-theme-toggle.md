# Spec: Theme Toggle

## Overview

The app has had a full dark palette since `tokens.css` was written, and no way to
choose it. `tokens.css:109` is `@media (prefers-color-scheme: dark)`, so every
user follows their operating system and nobody can override it — a student on a
phone locked to dark cannot read the app in light, and a faculty member
presenting a classroom capture on a projector cannot force light for the room.
This spec adds a **manual light / dark / system control** to the app shell's
header, persists the choice per browser, and applies it before first paint.

It is the **sixth and final item of the polish pass** scoped 2026-08-28, after
`14-confirm-dialog-focus-trap`, `15-muted-on-accent-contrast`,
`16-portal-card-link`, `17-collapsible-nav` and `18-spacing-scale`. The pass
entry originally named three shell deferrals in one bullet — the collapsible
nav, a spacing scale, and a manual theme toggle; they became three specs because
they share no code. This is the last of them, and the last scheduled item on the
roadmap. `shell.css`'s file header still ends "Still deferred on purpose, its own
spec: a manual theme toggle", and this spec is that deferral being paid off.

**No colour changes.** Not one token value moves. The dark palette that ships
today is the dark palette that ships afterwards — what changes is the selector it
hangs from and who gets to decide when it applies.

## Depends on

- `12-app-shell` — `AppShell.jsx` and `styles/shell.css`. The control lives in the
  header this built, and in the third of its three flex children.
- `13-component-vocabulary` — `.form-field`, which the control composes rather
  than reinventing, and `.visually-hidden` from `base.css` for its label.
- `15-muted-on-accent-contrast` — the three `--accent-weak` rows that moved their
  muted line to `--text`. Those corrections were measured in both palettes and
  must still hold in both; this spec changes no value they depend on.
- `17-collapsible-nav` — the header's **three flex children with
  `justify-content: space-between`**, which that spec made load-bearing by
  putting the nav toggle inside `<nav>` rather than beside it. This spec meets
  the identical constraint; see "Where the control goes".
- `18-spacing-scale` — `--space-*`, which any new spacing here composes, and the
  **computed-style probe** it built, which is how the "no colour changed" claim
  in the Definition of done is proved rather than eyeballed.

## APIs

**No API changes.** Nothing in this spec reaches the network. The theme is a
per-browser display preference, not account state.

## Database changes

**No database changes.** The choice is **not** stored on the user document and
**not** synced across devices. Reasons, stated so they are not re-litigated: it
would need a new field, a write endpoint, and an authenticated round-trip before
first paint — which is exactly the flash this spec exists to avoid — to deliver
something `localStorage` gives for free. A user signed in on two machines may
have two different themes, and that is correct: the preference belongs to the
screen, not to the account.

## Three states, not two

The default is **System**, and it must stay reachable. Every user today follows
their OS; a boolean light/dark control would silently delete that behaviour for
everyone, and there would be no way back to it. So the control has three values:

| Value | Stored | Behaviour |
|---|---|---|
| `system` | nothing (key removed) | Follows `prefers-color-scheme`, and keeps following it if the OS flips while the tab is open. |
| `light` | `"light"` | Light, on a dark OS too. |
| `dark` | `"dark"` | Dark, on a light OS too. |

## How the theme is applied

**A single resolved attribute on `<html>`.** A synchronous inline script in
`index.html` reads the stored choice, resolves `system` against `matchMedia`, and
writes `data-theme="light"` or `data-theme="dark"` on `document.documentElement`
before the first paint. `tokens.css` therefore needs one dark block and no media
query:

```css
:root                      { …light tokens…; color-scheme: light; }
:root[data-theme='dark']   { …dark tokens…;  color-scheme: dark;  }
```

**Why resolved rather than the obvious alternative.** Keeping
`@media (prefers-color-scheme: dark)` *and* supporting an override needs the dark
palette written twice — once as `:root:not([data-theme='light'])` inside the
media query, once as `:root[data-theme='dark']` outside it. That is twenty-odd
colour declarations duplicated in the one file whose entire job is to be the
single place a colour is written, and it is the same footgun the file already
warns about twice for `--bg`. Resolving in script keeps the palette written once.

**What that choice costs, stated up front.** The theme now depends on the inline
script having run, and `system` needs a `matchMedia` **change listener** to keep
following an OS that flips mid-session — free with a media query, explicit here.
Both are accepted: the app renders nothing without JavaScript at all, and the
listener is four lines.

**The flash is the real risk.** The script must be a classic inline
`<script>` in `<head>`. A `type="module"` script is deferred, and a `useEffect`
runs after React has already painted the wrong palette — both produce a visible
white flash on a dark-themed load, on every page, every time. This is the one
implementation detail that cannot be got wrong quietly.

**`color-scheme` becomes explicit.** `tokens.css:96` is `color-scheme: light
dark` today, which hands native chrome — scrollbars, focus rings on native
controls, and the date inputs on `/faculty/attendance`,
`/faculty/attendance/history` and `/student/attendance` — to the OS. Left alone,
a user who forces light would get a light palette with dark scrollbars and a dark
date picker. It becomes `light` in `:root` and `dark` in the dark block.

**`<meta name="theme-color">` is restructured.** `index.html:11-12` carry two
tags scoped `media="(prefers-color-scheme: …)"`, and a manual choice cannot be
expressed in a media-scoped meta. They collapse to **one** unscoped tag whose
`content` the inline script sets from a two-entry map. `tokens.css` warns beside
both `--bg` declarations that the meta and `--bg` must change together; those
warnings stay and are re-pointed at the script. Deriving the value from
`getComputedStyle(...).getPropertyValue('--bg')` was considered and rejected: in
dev Vite injects the CSS from a JS module, so the read returns empty before
mount, and the meta would be right in production and wrong in development.

## The control

A native `<select>` with three options, composing `.form-field`:

```jsx
<div className="form-field app-theme">
  <label className="visually-hidden" htmlFor="app-theme-select">Theme</label>
  <select id="app-theme-select" name="theme" value={theme} onChange={…}>
    <option value="system">System</option>
    <option value="light">Light</option>
    <option value="dark">Dark</option>
  </select>
</div>
```

Why a select rather than a segmented group or a cycling button: all three states
are named and visible, it is the narrowest three-state control that shows its
options, native keyboard and touch behaviour come free, and `.form-field select`
already carries the chrome — so the header invents no widget and no icon.

Two details are deliberate. The label is a real `<label>` hidden with
`.visually-hidden` rather than an `aria-label`, which is what the rest of the
project's forms do. And `.form-field`'s `gap: 6px` costs nothing here, because an
absolutely-positioned label is out of flow and so is not a flex item.

## Where the control goes

**Inside `.app-user`, the header's third flex child** — beside the user name and
before Log out, in that DOM order. `.app-header` is a three-item flex row with
`justify-content: space-between`, and `17` put the nav toggle inside `<nav>`
specifically so the header would keep three children at every width on every
page. A fourth child would change how the header distributes space at *every*
width including the desktop widths this spec must not touch. This spec makes the
same choice for the same reason, rather than overturning it.

`.app-user` already carries `min-width: 0` and ellipses the name, so the name is
what gives way when the row is tight — which is correct: the name is a label, the
select and the button are controls.

## Frontend

- **Create:**

  - `frontend/src/theme.js` — the storage key, the attribute name, the two
    `--bg` values for the meta, `readTheme()`, `applyTheme(theme)`, and the
    `matchMedia` subscription. One module so that "what a theme is" is written
    once. Every `localStorage` access is wrapped in `try`/`catch`: it throws
    outright in some privacy modes, and a theme control must not be able to break
    the app that renders it.

  - `frontend/src/components/layout/ThemeToggle.jsx` — the select, its state, and
    the effect that applies and persists a change. `AppShell` stays a frame that
    renders parts; it does not grow theme logic.

- **Modify:**

  - `frontend/index.html` — the two media-scoped `theme-color` metas become one,
    and the pre-paint inline script is added. Its comment says why it is inline
    and classic rather than a module, and why the storage key is written here as
    well as in `theme.js`.

  - `frontend/src/styles/tokens.css` — `@media (prefers-color-scheme: dark)`
    becomes `:root[data-theme='dark']`, `color-scheme` becomes explicit in both
    blocks, and the two `--bg` warnings are re-pointed at the script. **The token
    values themselves are untouched.**

  - `frontend/src/components/layout/AppShell.jsx` — renders `<ThemeToggle />`
    inside `.app-user`. Nothing else about the header changes: same three
    children, same DOM order, same brand and nav.

  - `frontend/src/styles/shell.css` — the `.app-theme` hook (width and whatever
    the narrow-width measurements show it needs, and nothing that belongs to a
    form field in general), plus the header measurements. Its file-header line
    "Still deferred on purpose, its own spec: a manual theme toggle" is now false
    and is rewritten.

  - `CLAUDE.md` — the App shell row stops listing "no manual theme toggle" among
    its deferrals and records what the header now carries; "Next planned feature"
    loses the last item of the polish pass, which closes the pass.

- **Not modified:** `NavBar.jsx`, `navigation.js`, `Login.jsx`, `NotFound.jsx`,
  `components.css`, `base.css`, and every page stylesheet. Login and NotFound
  **honour** the stored theme — the script is global — they simply offer no
  control, which is the agreed scope.

- **No new stylesheet.** `styles/<component>.css` exists for a component several
  pages render (`StudentStatusRow`, `ConfirmDialog`, `PortalCard`). `ThemeToggle`
  has exactly one renderer and that renderer is the shell, so its rules stay in
  `shell.css` — the same call `17` made for `NavBar`.

## Backend

**No backend changes.** Nothing under `backend/` is created, modified, or
deleted; `pytest` is unaffected and must still pass.

## Files to change

- `frontend/index.html`
- `frontend/src/styles/tokens.css`
- `frontend/src/components/layout/AppShell.jsx`
- `frontend/src/styles/shell.css`
- `CLAUDE.md`

## Files to create

- `frontend/src/theme.js`
- `frontend/src/components/layout/ThemeToggle.jsx`

## New dependencies

**No new dependencies.** `frontend/package.json` is byte-identical afterwards —
no theme library, no icon package, no `use-local-storage` hook.

## Rules for implementation

**Scope**

- The theme toggle only. **No palette redesign, no new token, no new primitive,
  no spacing change.** If a colour looks wrong in one theme, that is a finding to
  report, not a value to change here.
- No token value changes. `tokens.css`'s dark block must be the values that are
  in the media query on `main`, moved and not edited.
- Desktop is not being redesigned. Above the narrow breakpoints the header must
  be the header on `main`, plus one control.

**Applying the theme**

- Pre-paint, via a classic inline `<script>` in `<head>`. **Not** a module, not a
  `useEffect`, not a React state initialiser.
- One resolved `data-theme` attribute on `<html>`; the CSS carries one light
  block and one dark block and no `prefers-color-scheme` media query for the
  palette.
- `system` follows the OS live, via a `matchMedia` change listener that is
  attached only while the stored choice is `system` and removed otherwise.
- Choosing `system` **removes** the storage key rather than storing the string
  `"system"`, so a browser that has never chosen and a browser that chose System
  are the same state.
- Every `localStorage` read and write is guarded; a throw leaves the app running
  on the system theme.
- An unrecognised stored value is treated as `system`, not as an error.

**The control**

- Composes `.form-field` and carries `.app-theme` for what is shell-specific.
  **Do not restyle `.form-field` or `select` from `shell.css`**, and do not add a
  primitive or a variant for one control — the rule is two independent
  implementations first.
- A real `<label>`, hidden with `.visually-hidden`. It has `name` and an `id`
  that does not collide with `#main` or `#app-nav-list`.
- The select's value is the **stored choice** (`system`), never the resolved one
  (`dark`) — a user who picked System must see System selected.
- Changing it applies the theme immediately, with no reload and no navigation.

**Project-wide**

- Frontend-only: no route, no endpoint, no academic data, no biometric data, no
  secret. Nothing under `backend/` and nothing in `navigation.js`.
- No raw colour outside `tokens.css`, in either theme — including in the inline
  script, whose two `--bg` literals are the one deliberate exception and carry
  the same "change both together" warning `index.html` carries today.
- No transition on the theme switch. If one is added anyway,
  `prefers-reduced-motion: reduce` disables it.
- Preserve existing functionality on all eleven pages, in all three states.

**Deliberately out of scope**

- A control on `/login` or `/404` (agreed: signed-in pages only — both still
  honour the stored theme).
- Per-account or cross-device sync, and any backend field for it.
- The `/admin/face-enrollment` picker arithmetic, and the reported-but-declined
  guideline findings (Title Case, URL-reflected filter state, list
  virtualization).
- A high-contrast or any third palette.

## Whether this takes `/code-review-feature`

**It does not, and that is a decision rather than an omission.**

The argument for a review is real: this rewrites `tokens.css`, which every
stylesheet reads, and `18` set the precedent that a change to that file gets one.

The argument against is what decides it. `18` was a 170-declaration substitution
across sixteen files whose correctness could not be *seen* — which is exactly why
it needed a proof and a review. This change is the opposite: it moves one block's
selector and adds one control, and its correctness is **directly observable** by
flipping the control and looking at all eleven pages in both palettes, which is
precisely the test `CLAUDE.md`'s blast-radius rule names ("I measured every
surface it touches"). The token values do not change at all, and that claim is
provable by the same computed-style probe `18` built rather than by reading a
diff. `/test-feature` has nothing to run either — the backend is untouched and
there is no frontend test runner.

So: **spec, implement, verify in a browser against the Definition of done, record
the measurements in `shell.css`, commit** — the frontend-only route `12`–`17`
took.

## Definition of done

**The three states**

- [ ] With nothing stored, the app follows the OS, on every one of the eleven
      pages — the behaviour on `main`, unchanged.
- [ ] Choosing **Light** renders the light palette on a dark-set OS; choosing
      **Dark** renders the dark palette on a light-set OS.
- [ ] Choosing **System** removes the stored key and returns to following the OS.
- [ ] While on System, flipping the OS theme with the tab open changes the app
      without a reload.
- [ ] While on Light or Dark, flipping the OS theme changes nothing.
- [ ] The choice survives a reload, a route change, and **a logout and re-login**
      — `logout()` clears the user and must not clear the theme.

**No flash**

- [ ] With Dark stored, a hard reload of each of the eleven pages paints dark
      first: no white frame, verified by throttling and by stepping the first
      paint, not by "it looked fine".
- [ ] The same with Light stored on a dark-set OS.

**Nothing else moved**

- [ ] With `data-theme="dark"` forced, every page is **pixel-identical** to the
      same page on `main` under an OS set to dark; and with `data-theme="light"`,
      identical to `main` under a light OS. Proved with `18`'s computed-style
      probe over the old and new built stylesheets, **with a negative control**
      that shows the harness can fail.
- [ ] `tokens.css`'s colour values diff against `main` as a pure move: no value
      added, removed, or edited.
- [ ] The three `15-muted-on-accent-contrast` rows — `.au-row--open .au-email`,
      `.fe-row--open .fe-email`, `.fh-session--open .fh-session-facts` — still
      clear 4.5:1 in both themes.
- [ ] The header still has exactly **three flex children** and still distributes
      them with `space-between`.

**Native chrome follows the choice**

- [ ] Scrollbars match the chosen theme, not the OS, in both forced states.
- [ ] The date inputs on `/faculty/attendance`, `/faculty/attendance/history` and
      `/student/attendance` match the chosen theme.
- [ ] The browser chrome tint (`theme-color`) matches the chosen theme on a
      mobile browser, and the single meta's value equals `--bg` in that theme.

**The control**

- [ ] It is reachable by keyboard in header order — brand → nav → name → theme →
      Log out — and operable with the keyboard alone.
- [ ] It has an accessible name ("Theme") and reports its current value.
- [ ] Its own focus ring is the global `:focus-visible` ring from `base.css`.
- [ ] It renders on all nine signed-in pages and on none of the signed-out ones.
- [ ] With `localStorage` made to throw, the app still loads, still renders the
      control, and follows the system theme.

**Widths**

- [ ] At 360, 390, 413 and 414px the header is **at most two rows** for all three
      roles — no worse than `17` left it — and the name ellipses rather than the
      block wrapping to a third row. The numbers are recorded in `shell.css`.
- [ ] At 768, 1024 and 1440px the header is the single row it is on `main`, one
      control wider, for all three roles.
- [ ] The `17` collapse still works: below 413px the Menu button still shows for
      admin and faculty, `/student`'s single link still does not collapse, and
      Escape still closes the panel.
- [ ] The control's hit area is at least 44×44 under `(pointer: coarse)`, as the
      shell's other control already is.

**Gates**

- [ ] `npm run lint` reports no new warning (the pre-existing
      `AuthContext.jsx:51` one may remain).
- [ ] `npm run build` passes.
- [ ] `frontend/package.json` is byte-identical; nothing under `backend/`
      changed, and `pytest` still passes.
- [ ] Any verification harness is deleted before the commit, and `git status` is
      checked — the practice `18` established.

**Records updated**

- [ ] `shell.css`'s file header no longer says a manual theme toggle is deferred;
      it says what the header now carries.
- [ ] `tokens.css`'s two `--bg` warnings point at the inline script in
      `index.html`, and the file explains why the dark palette is an attribute
      block rather than a media query.
- [ ] `CLAUDE.md`'s App shell row drops the theme-toggle deferral, and "Next
      planned feature" records the polish pass as complete, leaving only the
      unscheduled per-feature deferrals.
