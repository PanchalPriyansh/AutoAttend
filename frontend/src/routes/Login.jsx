import { useState } from 'react'
import { Link, Navigate, useNavigate } from 'react-router-dom'
import PasswordInput from '../components/PasswordInput'
import { useAuth } from '../context/AuthContext'

function Login() {
  const { user, loading, login } = useAuth()
  const navigate = useNavigate()

  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)

  if (!loading && user) {
    return <Navigate to={`/${user.role}`} replace />
  }

  async function handleSubmit(event) {
    event.preventDefault()
    setError('')
    setSubmitting(true)
    try {
      const profile = await login(email, password)
      navigate(`/${profile.role}`, { replace: true })
    } catch (err) {
      setError(err.message)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="auth-screen">
      <main className="auth-card card">
        <p className="auth-brand">AutoAttend</p>
        <h1 className="auth-title">Login</h1>
        <form className="auth-form" onSubmit={handleSubmit}>
          <div className="form-field form-field--lg">
            <label htmlFor="email">Email</label>
            <input
              id="email"
              name="email"
              type="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              autoComplete="username"
              spellCheck={false}
              required
            />
          </div>
          <div className="form-field form-field--lg">
            <label htmlFor="password">Password</label>
            <PasswordInput
              id="password"
              name="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              autoComplete="current-password"
              required
            />
          </div>
          {error && (
            <p className="callout callout--error" role="alert">
              <span className="callout-mark" aria-hidden="true">
                !
              </span>
              {error}
            </p>
          )}
          <button
            type="submit"
            className="btn btn--primary btn--lg auth-submit"
            disabled={submitting}
          >
            {submitting ? 'Logging in…' : 'Log in'}
          </button>
        </form>
        {/* Below the form rather than beside the password label: the
            card is 288px wide at 320, and a right-aligned link on the
            label row is the first thing to collide with a long one. */}
        <p className="auth-forgot">
          <Link to="/forgot-password">Forgot your password?</Link>
        </p>
        {/* Reworded by 25-forgot-password: "if you cannot sign in,
            contact them" was the whole truth until a self-service reset
            existed, and would now send people to a person for something
            they can do themselves. What still needs an administrator is
            everything else — there is no public sign-up. */}
        <p className="auth-note">
          Accounts are created by your college administrator. AutoAttend has no
          public sign-up, so if you do not have an account, contact them.
        </p>
      </main>
    </div>
  )
}

export default Login
