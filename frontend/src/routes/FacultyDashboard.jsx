import { Link } from 'react-router-dom'
import AppShell from '../components/layout/AppShell'
import { navigationFor } from '../navigation'

function FacultyDashboard() {
  return (
    <AppShell title="Faculty Dashboard">
      <ul className="faculty-home">
        {navigationFor('faculty').map((item) => (
          <li className="faculty-home-card card" key={item.to}>
            <Link className="faculty-home-link" to={item.to}>
              {item.label}
            </Link>
            <p className="faculty-home-desc">{item.description}</p>
          </li>
        ))}
      </ul>
    </AppShell>
  )
}

export default FacultyDashboard
