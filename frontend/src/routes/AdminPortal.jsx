import AppShell from '../components/layout/AppShell'
import PortalCard from '../components/layout/PortalCard'
import { navigationFor } from '../navigation'

function AdminPortal() {
  return (
    <AppShell title="Admin Portal">
      <ul className="admin-portal">
        {navigationFor('admin').map((item) => (
          <PortalCard
            key={item.to}
            to={item.to}
            label={item.label}
            description={item.description}
          />
        ))}
      </ul>
    </AppShell>
  )
}

export default AdminPortal
