import { Link } from 'react-router-dom'
import AppShell from '../components/layout/AppShell'
import { navigationFor } from '../navigation'

function StudentDashboard() {
  return (
    <AppShell title="Student Dashboard">
      <ul className="student-home">
        {navigationFor('student').map((item) => (
          <li className="student-home-card" key={item.to}>
            <Link className="student-home-link" to={item.to}>
              {item.label}
            </Link>
            <p className="student-home-desc">{item.description}</p>
          </li>
        ))}
      </ul>
    </AppShell>
  )
}

export default StudentDashboard
