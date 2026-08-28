import AppShell from '../components/layout/AppShell'
import PortalCard from '../components/layout/PortalCard'
import { navigationFor } from '../navigation'

function FacultyDashboard() {
  return (
    <AppShell title="Faculty Dashboard">
      <ul className="faculty-home">
        {navigationFor('faculty').map((item) => (
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

export default FacultyDashboard
