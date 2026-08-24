import { Link } from 'react-router-dom'
import AppShell from '../components/layout/AppShell'
import { navigationFor } from '../navigation'

function StudentDashboard() {
  return (
    <AppShell title="Student Dashboard">
      <ul className="portal-links">
        {navigationFor('student').map((item) => (
          <li className="portal-link" key={item.to}>
            <Link to={item.to}>{item.label}</Link>
            <p>{item.description}</p>
          </li>
        ))}
      </ul>
    </AppShell>
  )
}

export default StudentDashboard
