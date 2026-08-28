# Spec: Muted-on-Accent Contrast Fix

## Overview

`--text-muted` on `--accent-weak` measures **4.47:1 in light and 4.49:1 in dark** — both under the 4.5:1 floor, by about a hundredth. The project already knows this: `admin-users.css` and `admin-face-enrollment.css` each carry the measurement in a comment and each correct the one element that would have hit it. `faculty-history.css` does not, and it is the one file where the pairing actually ships — `.fh-session-facts` is `--text-muted` at `0.78rem` (~14px, so the 4.5:1 floor applies rather than the 3:1 large-text one) inside `.fh-session--open`, which is tinted `--accent-weak`. It is the "23 present · 2 absent · from recognition" line on whichever lecture the faculty member has open, which is to say on the row they are working in.

This is **item 2 of the four-item polish pass** scoped 2026-08-28, after `14-confirm-dialog-focus-trap`. It is a two-line fix with a four-page audit behind it, and the audit is most of the value: the reason this shipped is that nobody had checked the whole set, so the spec's real job is to establish that the set is now closed and to leave the next tinted row a note it will actually find.

The change is **colour only, on one element, in one state**. No layout, no spacing, no new class, no markup, no behaviour.

## Depends on

- `13-component-vocabulary` — `.pill`, `.btn` and the tokens the four tinted rows compose. None of them changes.
- The group 4a/4b teardown — which established both the tinted-row pattern (`.ah-item--selected`, `.au-row--open`, `.fe-row--open`, `.fh-session--open`: rail plus tint, always redundant with a word) and the correction this spec applies (`.au-row--open .au-email`, `.fe-row--open .fe-email`).
- `08-faculty-attendance-history` — the page and the markup being restyled. Not modified.

## The audit

Every `--accent-weak` fill in the project, and whether muted text can land on it. This table is the reason the spec exists, and it is what the Definition of done re-verifies.

| Fill | What it tints | Muted text inside? | Status |
|---|---|---|---|
| `admin-academics.css:298` `.ah-item--selected` | selected hierarchy row | No — `.ah-pick` is `--text-h`, the two actions are `.btn` | Clean |
| `admin-users.css:368` `.au-row--open` | account whose password is being set | Yes — `.au-email` | **Already corrected** (`:436`, → `--text`) |
| `admin-face-enrollment.css:358` `.fe-row--open` | student whose faces are shown | Yes — `.fe-email`; `.fe-samples` is `--text`, the flag is a `.pill` with its own fill | **Already corrected** (`:417`, → `--text`) |
| `faculty-history.css:300` `.fh-session--open` | lecture open below | Yes — `.fh-session-facts`; `.fh-session-date` is `--text-h`, `.fh-flag` is `.pill--warning`, `.fh-open` is `.btn--secondary` | **THE DEFECT** |
| `components.css:130` `.btn--secondary:hover` | a button | No — `.btn--secondary` is `--text-h` | Clean |
| `student-attendance.css:220,228` `.sa-toggle:active`, `.sa-class.is-open .sa-toggle` | a button | No — `--text-h`, then `--accent` when open (both well clear) | Clean |
| `faculty-attendance.css:263`, `admin-face-enrollment.css:617` `::file-selector-button:hover` | a button | No — `--text-h` | Clean |
| `student-dashboard.css:142`, `faculty-dashboard.css:195`, `admin-portal.css:230` portal card `:has(:active)` | a card, while pressed | No — all three descriptions are deliberately `--text`, and `admin-portal.css:198` says so in a comment | Clean |

`.fh-provenance` and `.fh-context` are muted but sit on `.card`/`.page`, not on a tint. `faculty-roster-row.css`'s muted metadata is never tinted — no `--accent-weak` fill exists in that file or on the rows that render it.

**So: one defect, one element, one state.** Nothing else in the project pairs these two tokens.

## APIs

**No API changes.** Nothing in this spec reaches the network.

## Database changes

**No database changes.**

## Frontend

- **Modify:**

  - `frontend/src/styles/faculty-history.css` — **one rule and one comment**. After `.fh-session-facts` (currently `:334`), add the correction the two admin pages already carry, written the same way and scoped the same way:

    ```css
    .fh-session--open .fh-session-facts {
      color: var(--text);
    }
    ```

    with the comment recording the measurement and pointing at its two siblings, so the third instance of this correction reads like the first two rather than like a one-off. `--text` measures **6.30:1 light and 5.89:1 dark** on `--accent-weak` — the same figures `.au-email` and `.fe-email` already cite.

  - `frontend/src/styles/tokens.css` — **a comment only, no declaration.** Beside `--accent-weak` in the light block, record that muted text may not sit on this tint, with both measurements and a pointer to the three page rules that correct for it. This is the part that stops the fix being the third of an unbounded series: the trap is a property of the token pair, so the warning belongs at the token, where the next page to tint a row will meet it, rather than only in three page files it will never open.

    Explicitly **not** a rule: `tokens.css` is read by every page, and the standing project rule is not to add to it to solve one page's problem. A comment is not a rule.

  - `CLAUDE.md` — remove the contrast bullet from the polish-pass list in "Next planned feature" (`:326`), leaving `.card--link` and the shell items. Add nothing to the stylesheet section; a three-line correction on one page is not a convention.

- **Not modified:** `frontend/src/routes/faculty/AttendanceHistory.jsx`, and every other component. No markup changes — the selector already exists on the element, and the page already sets `.fh-session--open` on the `<li>`.

- **Not created:** no new file, no new class, no new token.

## Backend

**No backend changes.** Nothing under `backend/` is created, modified, or deleted; `pytest` is unaffected and must still pass.

## Files to change

- `frontend/src/styles/faculty-history.css`
- `frontend/src/styles/tokens.css` (comment only)
- `CLAUDE.md`

## Files to create

**None.**

## New dependencies

**No new dependencies.** `frontend/package.json` and `backend/requirements.txt` are byte-identical afterwards. No contrast-checking or colour library — the ratios are measured once, at spec time, and written into the comments as numbers.

## The decision, and what it rules out

The polish-pass entry left this open between raising the token and moving the element. It moves the element. The three alternatives, each with the arithmetic that decided it:

- **Raise `--text-muted` (rejected).** It works and it is small — light `#6f6a7c` → `#6c6778` gives 5.46 / 5.02 / 4.68 on `--bg` / `--surface` / `--accent-weak`; dark `#868d9c` → `#8a91a0` gives 5.65 / 5.14 / 4.73. But it repaints **every muted string in the application** — roughly thirty rules across nine stylesheets — to fix one line on one page, and it narrows the gap the tokens were deliberately built around. `tokens.css` says in its own header that `--text` was darkened to clear 6:1 *so that* `--text-muted` could carry the lighter role; muted is 5.21 against `--text`'s 7.35 on `--bg` today, and closing that costs the hierarchy on nine pages to buy a hundredth of a point on one. A palette change is also not a two-line spec, and the polish pass was scoped to four small ones.
- **Scope a token override to the row (rejected)** — redeclaring `--text-muted` on `.fh-session--open`. Genuinely tidier in the abstract: it would fix *any* muted descendant of the tint at once, including ones nobody has written yet. But it needs a colour that exists nowhere in `tokens.css`, which is a raw hex outside `tokens.css` by any reading of the rule, and it introduces a scoped-token-override pattern the project uses nowhere. The audit above is what makes the generality worthless anyway: there is no second muted descendant, in this row or any other.
- **Change `--accent-weak` (rejected outright).** It fills eleven things across nine files; lightening it to rescue one caption would move every selected row, every secondary hover and three portal cards.

What is left is the option **the project has already chosen twice**, on the two pages that met this exact pairing and stopped: the page owns its own correction, on its own selector, with the measurement in the comment. A third instance written identically to the first two is consistency; a fourth mechanism would not be.

## Rules for implementation

**Scope**

- **This is the only rule added.** No other selector in `faculty-history.css` changes colour, and no declaration anywhere is removed or reordered.
- **No token declaration changes.** `tokens.css` gains comment lines and nothing else — the diff must contain no added or changed property.
- **No markup.** If the fix appears to need a class, an element, or a prop, it is wrong.
- **Placement matters.** The new rule must come *after* `.fh-session-facts` in the file. It is (0,2,0) against (0,1,0), so it would win anywhere, but the file's convention is that a state override sits immediately below the rule it overrides — the same shape `.fh-session--open .fh-open` uses further down.

**Correctness**

- **Only when open.** An untinted `.fh-session` keeps `--text-muted` on `--bg` (5.21 / 5.37), which is correct and is the whole point of a muted line. Flattening every row to `--text` would delete the date/metadata hierarchy the file's comment at `:311` describes.
- **`--text`, not a new value.** The two existing corrections use `--text` and cite 6.30 / 5.89. The third must produce the same two numbers.
- **The measurements in comments must be the real ones.** 4.47 / 4.49 for the defect, 6.30 / 5.89 for the fix, computed on the tokens as they stand. If a token ever moves, these comments are what makes the drift findable.

**Project-wide**

- No raw colour outside `tokens.css` — this spec adds no colour at all, only a token reference.
- Frontend-only: no route, no endpoint, no role check, no academic data, no biometric data, no secret.
- Preserve existing functionality: the history page's filtering, paging, opening, editing, saving and deleting are untouched.

**Deliberately out of scope**

- **The other three polish-pass items.** `.card--link`, the shell items, and the reported-but-declined guideline findings each get their own spec and their own session.
- **A contrast check in CI, or a lint rule for it.** Worth wanting; it is a tooling decision with its own spec, and the project has no frontend test runner at all yet (`14`'s note still stands).
- **Auditing token pairs other than these two.** The sweep here covers `--text-muted` on `--accent-weak` and the elements adjacent to it. A full palette-wide pairing matrix is a different, larger piece of work.

## Definition of done

**The fix**

- [ ] `/faculty/attendance/history`, with a lecture open: the "N present · N absent · from …" line on the open row renders `--text`, and measures **6.30:1 light / 5.89:1 dark** against the row's `--accent-weak` fill (verified in DevTools' contrast readout or by computing it from the resolved values).
- [ ] Every **closed** row's facts line still renders `--text-muted` on `--bg`, unchanged from `main`.
- [ ] The open row's date (`--text-h`), its `edited since it was recorded` pill, and its Open-below button are all unchanged.
- [ ] Nothing on the page moves, resizes, or reflows: the open row is pixel-identical to `main` except for the colour of that one line.

**The audit holds**

- [ ] No element anywhere in the project renders `--text-muted` on an `--accent-weak` fill. Re-verified against the table above on all four tinted rows — `/admin/academics` (selected level), `/admin/users` (open row), `/admin/face-enrollment` (open row), `/faculty/attendance/history` (open session) — in both themes.
- [ ] The three portal cards, pressed and held, still show `--text` descriptions on the tint.

**Nothing else changed**

- [ ] `frontend/src/styles/faculty-history.css` differs from `main` by exactly one added rule plus its comment. No existing declaration is changed, removed, or reordered.
- [ ] `frontend/src/styles/tokens.css` differs from `main` by comment lines only — every declaration is byte-identical, and `git diff` shows no added or changed property.
- [ ] No file under `frontend/src/components/`, `frontend/src/routes/`, or `backend/` is modified.
- [ ] All nine signed-in pages are visually identical to `main` at 360px, 768px and 1440px, in light and dark, apart from the one line above.

**Gates**

- [ ] `npm run lint` reports no new warning (the pre-existing `AuthContext.jsx:51` one may remain).
- [ ] `npm run build` passes.
- [ ] `frontend/package.json` is byte-identical.
- [ ] Nothing under `backend/` changed, and `pytest` still passes.

**Records updated**

- [ ] `tokens.css` carries the pairing warning beside `--accent-weak`, with both measurements and a pointer to the three page rules.
- [ ] `faculty-history.css`'s new comment names its two siblings, so the three corrections are findable from any one of them.
- [ ] `CLAUDE.md` no longer lists the contrast fix as an outstanding polish-pass item; `.card--link` and the shell items remain.
