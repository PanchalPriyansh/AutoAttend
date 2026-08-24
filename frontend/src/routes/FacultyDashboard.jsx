import { Link } from 'react-router-dom'
import AppShell from '../components/layout/AppShell'
import { navigationFor } from '../navigation'

function FacultyDashboard() {
  return (
    <AppShell title="Faculty Dashboard">
      <ul className="portal-links">
        {navigationFor('faculty').map((item) => (
          <li className="portal-link" key={item.to}>
            <Link to={item.to}>{item.label}</Link>
            <p>{item.description}</p>
          </li>
        ))}
      </ul>
    </AppShell>
  )
}

export default FacultyDashboard
