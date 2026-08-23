import { Link } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'

function StudentDashboard() {
  const { user, logout } = useAuth()

  return (
    <div>
      <h1>Student Dashboard</h1>
      <p>Welcome, {user?.name}</p>

      <ul className="hierarchy-items">
        <li>
          <span className="user-identity">
            <span className="user-name">
              <Link to="/student/attendance">My Attendance</Link>
            </span>
            <span className="hierarchy-hint">
              See how you stand in each of your classes, how that has moved month
              by month, and what was recorded for every lecture.
            </span>
          </span>
        </li>
      </ul>

      <button onClick={logout}>Logout</button>
    </div>
  )
}

export default StudentDashboard
