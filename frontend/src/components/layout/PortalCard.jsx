import { Link } from 'react-router-dom'

/**
 * One destination on a role's landing page: a label, a sentence
 * describing what is behind it, and a chevron, on a card whose whole
 * area is the link.
 *
 * Rendered by all three portals -- /student, /faculty and /admin -- each
 * of which maps it over navigationFor(role). It is styled by
 * styles/portal-card.css, its own stylesheet rather than any one page's;
 * see the note at the top of that file for what it owns and what each
 * page keeps.
 *
 * It renders the <li> because the pages render the <ul>. The stretched
 * hit area is CSS on the link's ::after rather than a <p> moved inside
 * the <a>, so the accessible name stays the label and does not become
 * the label plus the whole description.
 *
 * Presentational only: it takes what it renders and reads nothing. Which
 * links a role is shown is navigation.js's business, and whether a role
 * may follow one is ProtectedRoute's and @role_required's.
 */
function PortalCard({ to, label, description }) {
  return (
    <li className="portal-card card">
      <Link className="portal-card-link" to={to}>
        {label}
      </Link>
      <p className="portal-card-desc">{description}</p>
    </li>
  )
}

export default PortalCard
