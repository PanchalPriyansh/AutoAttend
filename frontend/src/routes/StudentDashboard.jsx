import AppShell from '../components/layout/AppShell'
import PortalCard from '../components/layout/PortalCard'
import { navigationFor } from '../navigation'

function StudentDashboard() {
  return (
    <AppShell title="Student Dashboard">
      <ul className="student-home">
        {navigationFor('student').map((item) => (
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

export default StudentDashboard
