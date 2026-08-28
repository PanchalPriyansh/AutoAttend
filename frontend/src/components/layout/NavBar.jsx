import { useEffect, useRef, useState } from 'react'
import { NavLink, useLocation } from 'react-router-dom'
import { navigationFor } from '../../navigation'

/* The id the toggle points at with aria-controls. A module constant
 * rather than a generated one: there is exactly one header on the page,
 * and a stable id is what lets the relationship be read in the
 * accessibility tree. It must not collide with #main. */
const NAV_LIST_ID = 'app-nav-list'

/* The role's destinations, as a list in the shell's header.
 *
 * NavLink sets aria-current="page" on the active link itself, so this
 * component does not set it by hand.
 *
 * `end` is on every link because the faculty routes nest: without it
 * /faculty/attendance would also match while the user is on
 * /faculty/attendance/history, and two items would claim to be current.
 *
 * Below the width in shell.css the list collapses behind a Menu button,
 * because the header was three rows deep on a phone and the nav was the
 * row that would not fit. Two things about that are deliberate:
 *
 * - This is a disclosure, NOT a dialog. The panel sits in the flow, it
 *   covers nothing, and it guards nothing, so it gets none of
 *   ConfirmDialog's machinery: no focus trap, no aria-modal, no
 *   backdrop, and no focus move when it opens. Tab past the last link
 *   reaches Log out, exactly as it does on a desktop. The button sits
 *   immediately before the list in the DOM, so the first Tab after
 *   opening lands on the first link without anyone arranging it.
 * - A nav with ONE destination does not collapse at all. That is the
 *   count, not the role: hiding a single link behind a button costs more
 *   than it saves, because the button is taller than the link it hides
 *   (measured in shell.css). Give the student role a second destination
 *   and it collapses like the other two.
 * - Whether the list is SHOWN is decided entirely by CSS. `open` only
 *   matters below that width; above it the button is display:none and
 *   the list is visible whatever this state happens to say. Deciding it
 *   here instead -- matchMedia, a resize listener -- would write the
 *   breakpoint in two places and make the desktop nav depend on this
 *   component having run.
 */
function NavBar({ role }) {
  const items = navigationFor(role)
  const [open, setOpen] = useState(false)
  const toggleRef = useRef(null)
  const navRef = useRef(null)
  const { pathname } = useLocation()

  // A route change closes the panel, so it never outlives the page it was
  // opened from. This covers navigation nobody clicked -- a redirect, a
  // session ending -- while the links close it themselves on the way out
  // (see onClick below), which is what also handles clicking the link for
  // the page already open, where the pathname never changes.
  useEffect(() => {
    setOpen(false)
  }, [pathname])

  // Escape closes it, and hands focus back to the button only if closing
  // is about to destroy where focus is. This is the one focus move in the
  // component and it stays that narrow on purpose: the panel is not modal
  // and Tab leads out of it, so Escape pressed while focus is on Log out
  // should close the panel and leave Log out alone. Taking focus back
  // there would be a jump nobody asked for. Same guard ConfirmDialog
  // applies before restoring, for the same reason.
  //
  // Listener only while open, on the model of ConfirmDialog's.
  useEffect(() => {
    if (!open) return undefined

    function handleKeyDown(event) {
      if (event.key !== 'Escape') return
      const inPanel = navRef.current?.contains(document.activeElement)
      setOpen(false)
      if (inPanel) toggleRef.current?.focus()
    }

    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [open])

  // After the hooks, never before them: an early return above a hook is
  // a different hook order on the render where a role has no links.
  if (items.length === 0) return null

  const collapsible = items.length > 1
  const className = ['app-nav', collapsible ? null : 'app-nav--single', open ? 'app-nav--open' : null]
    .filter(Boolean)
    .join(' ')

  return (
    <nav className={className} aria-label="Main" ref={navRef}>
      {/* The accessible name stays "Menu" in both states -- aria-expanded
          is what says which one it is. */}
      {collapsible ? (
        <button
          type="button"
          className="btn btn--secondary app-nav-toggle"
          aria-expanded={open}
          aria-controls={NAV_LIST_ID}
          ref={toggleRef}
          onClick={() => setOpen((wasOpen) => !wasOpen)}
        >
          Menu
        </button>
      ) : null}

      <ul id={NAV_LIST_ID}>
        {items.map((item) => (
          <li key={item.to}>
            <NavLink
              to={item.to}
              end
              className={({ isActive }) => (isActive ? 'is-current' : undefined)}
              onClick={() => setOpen(false)}
            >
              {item.label}
            </NavLink>
          </li>
        ))}
      </ul>
    </nav>
  )
}

export default NavBar
