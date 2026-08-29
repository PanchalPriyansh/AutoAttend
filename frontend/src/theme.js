/* What a theme is, written once.
 *
 * Three choices -- 'system', 'light', 'dark' -- resolving to two
 * palettes. 'system' is the default and is stored as the ABSENCE of the
 * key, so a browser that has never chosen and a browser that chose
 * System are the same state; there is no third stored value to keep in
 * step with the other two.
 *
 * The theme is applied as a single resolved attribute on <html>, which
 * is why tokens.css needs one dark block instead of two. The
 * alternative -- keeping @media (prefers-color-scheme: dark) AND
 * honouring an override -- writes the dark palette twice, once inside
 * the media query as :root:not([data-theme='light']) and once outside
 * it, in the one file whose whole job is to be the single place a
 * colour is written. What that costs us instead is the listener below:
 * resolving in script means 'system' has to watch the OS itself, where
 * a media query would have followed it for free.
 *
 * index.html carries a second, smaller copy of the read-and-apply logic
 * as a classic inline script. That copy is not removable: it has to run
 * before the first paint, and anything imported from here runs after
 * React has already painted. The storage key, the attribute name and
 * the two --bg values are therefore written in both places -- change
 * them together.
 *
 * Every localStorage access is guarded. It does not merely return null
 * in some privacy modes, it throws, and a display preference must not
 * be able to break the app that renders the control for it.
 */

export const THEME_STORAGE_KEY = 'autoattend-theme'
export const THEME_ATTRIBUTE = 'data-theme'

/* The two values of --bg in tokens.css. A <meta> cannot read a custom
   property, so the browser-chrome tint has to be given the literal --
   the same trade index.html has always made, and the reason tokens.css
   warns beside both --bg declarations. Change all three together. */
const CHROME_TINT = {
  light: '#ffffff',
  dark: '#16171d',
}

/* 'system' is deliberately absent: it is what an unrecognised or
   missing value falls back to, so it never needs to be recognised. */
const STORED_CHOICES = ['light', 'dark']

const DARK_QUERY = '(prefers-color-scheme: dark)'

/* The stored choice, never the resolved one -- the control shows what
   the user picked, so somebody who picked System sees System and not
   the palette it happens to resolve to tonight. */
export function readTheme() {
  try {
    const stored = localStorage.getItem(THEME_STORAGE_KEY)
    return STORED_CHOICES.includes(stored) ? stored : 'system'
  } catch {
    return 'system'
  }
}

export function storeTheme(choice) {
  try {
    if (choice === 'system') localStorage.removeItem(THEME_STORAGE_KEY)
    else localStorage.setItem(THEME_STORAGE_KEY, choice)
  } catch {
    // A browser that will not store it still gets the theme for this
    // session; only the memory of it is lost.
  }
}

export function resolveTheme(choice) {
  if (choice === 'light' || choice === 'dark') return choice
  return window.matchMedia?.(DARK_QUERY).matches ? 'dark' : 'light'
}

export function applyTheme(choice) {
  const resolved = resolveTheme(choice)
  document.documentElement.setAttribute(THEME_ATTRIBUTE, resolved)
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content', CHROME_TINT[resolved])
}

/* Only meaningful while the choice is 'system'; the caller is what
   decides that, and unsubscribes when the choice moves off it. Returns
   the unsubscribe so it can be an effect's cleanup directly. */
export function subscribeToSystemTheme(onChange) {
  const query = window.matchMedia(DARK_QUERY)
  query.addEventListener('change', onChange)
  return () => query.removeEventListener('change', onChange)
}
