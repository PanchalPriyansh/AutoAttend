import { Link } from 'react-router-dom'
import AppShell from '../components/layout/AppShell'
import { navigationFor } from '../navigation'

function AdminPortal() {
  return (
    <AppShell title="Admin Portal">
      <ul className="admin-portal">
        {navigationFor('admin').map((item) => (
          <li className="admin-portal-card card" key={item.to}>
            <Link className="admin-portal-link" to={item.to}>
              {item.label}
            </Link>
            <p className="admin-portal-desc">{item.description}</p>
          </li>
        ))}
      </ul>
    </AppShell>
  )
}

export default AdminPortal
