import { NavLink } from 'react-router-dom'
import { navigationFor } from '../../navigation'

/* The role's destinations, as a list in the shell's header.
 *
 * NavLink sets aria-current="page" on the active link itself, so this
 * component does not set it by hand.
 *
 * `end` is on every link because the faculty routes nest: without it
 * /faculty/attendance would also match while the user is on
 * /faculty/attendance/history, and two items would claim to be current.
 */
function NavBar({ role }) {
  const items = navigationFor(role)

  if (items.length === 0) return null

  return (
    <nav className="app-nav" aria-label="Main">
      <ul>
        {items.map((item) => (
          <li key={item.to}>
            <NavLink
              to={item.to}
              end
              className={({ isActive }) => (isActive ? 'is-current' : undefined)}
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
