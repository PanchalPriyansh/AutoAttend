import { Link } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'

function FacultyDashboard() {
  const { user, logout } = useAuth()

  return (
    <div>
      <h1>Faculty Dashboard</h1>
      <p>Welcome, {user?.name}</p>

      <ul className="hierarchy-items">
        <li>
          <span className="user-identity">
            <span className="user-name">
              <Link to="/faculty/attendance">Take Attendance</Link>
            </span>
            <span className="hierarchy-hint">
              Capture a class photo or video, review who was recognised, and save the
              register.
            </span>
          </span>
        </li>
      </ul>

      <button onClick={logout}>Logout</button>
    </div>
  )
}

export default FacultyDashboard
