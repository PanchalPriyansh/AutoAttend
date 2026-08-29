import { useEffect, useState } from 'react'
import { applyTheme, readTheme, storeTheme, subscribeToSystemTheme } from '../../theme'

/* The id the hidden label points at. A module constant for the same
 * reason NavBar's is: there is one header on the page, and it must not
 * collide with #main or #app-nav-list. */
const SELECT_ID = 'app-theme-select'

/* The theme control, in the header's user block.
 *
 * Three states and not two. Everyone followed their OS before this
 * existed, so a light/dark switch would have deleted that behaviour for
 * every user with no way back to it -- "System" is the default and is
 * stored as the absence of the key (see theme.js).
 *
 * The select shows the STORED choice, never the resolved one: somebody
 * who picked System sees System, not whichever palette it resolves to
 * tonight.
 *
 * It does not apply the theme on first paint -- index.html's inline
 * script has already done that, before React existed. The effect below
 * runs on mount anyway and is idempotent: it writes the attribute the
 * script already wrote. What the effect is actually for is the change,
 * and the listener.
 *
 * A native <select> rather than a segmented group or a cycling button:
 * all three states are named and visible, it is the narrowest control
 * that manages that, and keyboard and touch behaviour come free. It
 * composes .form-field, so the header invents no chrome; .app-theme
 * carries only what is shell-specific.
 */
function ThemeToggle() {
  const [choice, setChoice] = useState(readTheme)

  // Only 'system' subscribes. The other two ignore the OS by definition,
  // and leaving a listener attached for them would mean a flip of the OS
  // theme re-applied a palette the user had explicitly overridden.
  useEffect(() => {
    applyTheme(choice)
    if (choice !== 'system') return undefined
    return subscribeToSystemTheme(() => applyTheme('system'))
  }, [choice])

  function handleChange(event) {
    const next = event.target.value
    storeTheme(next)
    setChoice(next)
  }

  return (
    <div className="form-field app-theme">
      {/* A real label, hidden, rather than an aria-label -- what every
          other form in this project does. */}
      <label className="visually-hidden" htmlFor={SELECT_ID}>
        Theme
      </label>
      <select id={SELECT_ID} name="theme" value={choice} onChange={handleChange}>
        <option value="system">System</option>
        <option value="light">Light</option>
        <option value="dark">Dark</option>
      </select>
    </div>
  )
}

export default ThemeToggle
