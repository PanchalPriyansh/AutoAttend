import { Link } from 'react-router-dom'
import AppShell from '../components/layout/AppShell'
import { navigationFor } from '../navigation'

function AdminPortal() {
  return (
    <AppShell title="Admin Portal">
      <ul className="portal-links">
        {navigationFor('admin').map((item) => (
          <li className="portal-link" key={item.to}>
            <Link to={item.to}>{item.label}</Link>
            <p>{item.description}</p>
          </li>
        ))}
      </ul>
    </AppShell>
  )
}

export default AdminPortal
