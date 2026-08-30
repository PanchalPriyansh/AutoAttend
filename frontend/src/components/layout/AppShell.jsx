import { Link } from 'react-router-dom'
import { useAuth } from '../../context/AuthContext'
import { homeFor } from '../../navigation'
import NavBar from './NavBar'
import ThemeToggle from './ThemeToggle'

/* The frame every signed-in page renders as its root.
 *
 * It owns four things that have to stay in this relationship for the
 * page to be navigable, which is why they are one component rather than
 * parts each page assembles: the skip link, the header, the <main>
 * landmark, and the page's single <h1>.
 *
 * Login and NotFound are reachable while signed out and deliberately do
 * not render this — there is no one to greet, nothing to navigate to,
 * and nothing to log out of.
 *
 * `logout()` only clears the user; ProtectedRoute is what redirects to
 * /login. That is unchanged here.
 */
function AppShell({ title, children }) {
  const { user, logout } = useAuth()

  return (
    <>
      <a className="skip-link visually-hidden" href="#main">
        Skip to main content
      </a>

      <header className="app-header">
        <Link className="app-brand" to={homeFor(user?.role)}>
          AutoAttend
        </Link>

        <NavBar role={user?.role} />

        {/* The theme control goes INSIDE this block rather than beside
            it: .app-header is a three-item flex row with
            space-between, and a fourth child would redistribute the
            header at every width on every page. Same call NavBar's
            toggle made, for the same reason. */}
        <div className="app-user">
          {/* The name is the link to /account, rather than a control
              added beside it. 19 showed this block has no slack -- a
              105px addition cost two gap reductions and moved three
              one-row thresholds -- so the account gets no new element,
              only a destination on text that was already here. */}
          {user?.name ? (
            <Link className="app-account-link" to="/account">
              {user.name}
            </Link>
          ) : null}
          <ThemeToggle />
          <button type="button" className="btn btn--secondary" onClick={logout}>
            Log out
          </button>
        </div>
      </header>

      {/* tabIndex -1 so the skip link can actually move focus here.
          It is not in the tab order; only the skip link targets it. */}
      <main id="main" className="page" tabIndex={-1}>
        <h1 className="page-title">{title}</h1>
        {children}
      </main>
    </>
  )
}

export default AppShell
