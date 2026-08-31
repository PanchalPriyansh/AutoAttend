import { useEffect, useRef, useState } from 'react'
import { Link, Navigate } from 'react-router-dom'
import { requestPasswordReset, resetPassword } from '../api/passwordReset'
import { useAuth } from '../context/AuthContext'

/* Three stages, one component, one route.
 *
 * REQUEST asks for the address, RESET takes the code and the new password
 * together, and DONE confirms. They are stages rather than routes because
 * the flow is one transaction: a /forgot-password/verify URL would be
 * reachable, bookmarkable and shareable while meaning nothing on its own,
 * and refreshing it would strand somebody holding a valid code on a page
 * that had forgotten which account it was for.
 *
 * The code and the new password are submitted in ONE request. There is no
 * "verify code" endpoint to call first, deliberately: it would burn an
 * attempt to answer a question, and it would create a half-authenticated
 * state between verifying and setting.
 *
 * This page renders no AppShell — it is reached by people who are signed
 * out — and, like Login, sends an already-signed-in user to their portal.
 */

const REQUEST = 'request'
const RESET = 'reset'
const DONE = 'done'

function ForgotPassword() {
  const { user, loading } = useAuth()

  const [stage, setStage] = useState(REQUEST)
  const [email, setEmail] = useState('')
  const [code, setCode] = useState('')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [submitting, setSubmitting] = useState(false)

  const errorRef = useRef(null)
  const codeRef = useRef(null)

  // Focused rather than merely announced, the same call Account and
  // UserForm make: the backend answers with one message for the whole
  // form, so the message itself is the target and a keyboard user is
  // taken to it instead of being left on a button that did nothing.
  useEffect(() => {
    if (error) errorRef.current?.focus()
  }, [error])

  // Entering the second stage moves focus to the field the user has come
  // back from their inbox to fill in. Without this, submitting stage one
  // leaves focus on a button that no longer exists.
  useEffect(() => {
    if (stage === RESET) codeRef.current?.focus()
  }, [stage])

  if (!loading && user) {
    return <Navigate to={`/${user.role}`} replace />
  }

  async function handleRequest(event) {
    event.preventDefault()
    setError('')
    setSubmitting(true)
    try {
      const result = await requestPasswordReset({ email })
      // The backend's own wording, which is deliberately non-committal
      // about whether the address has an account. Repeating it verbatim
      // is what keeps the page from promising more than the API does.
      setNotice(result?.message || 'If that email is registered, a reset code has been sent.')
      setStage(RESET)
    } catch (err) {
      setError(err.message)
    } finally {
      setSubmitting(false)
    }
  }

  async function handleReset(event) {
    event.preventDefault()
    setError('')

    // The only rule this form enforces on its own, and it is enforced
    // before any request is made. A mismatch is a typo, not something to
    // ask the server about — and asking would cost one of the five
    // attempts the code is allowed.
    if (password !== confirm) {
      setError('The new passwords do not match.')
      return
    }

    setSubmitting(true)
    try {
      await resetPassword({ email, code, newPassword: password })
      setStage(DONE)
    } catch (err) {
      // Deliberately keeps the code and the passwords in place. A wrong
      // digit should cost one character, not the whole form.
      setError(err.message)
    } finally {
      setSubmitting(false)
    }
  }

  function startOver() {
    setStage(REQUEST)
    setCode('')
    setPassword('')
    setConfirm('')
    setError('')
    setNotice('')
  }

  const alert = error && (
    <p
      ref={errorRef}
      tabIndex={-1}
      role="alert"
      className="callout callout--error fp-alert"
    >
      <span className="callout-mark" aria-hidden="true">
        !
      </span>
      {error}
    </p>
  )

  return (
    <div className="auth-screen">
      <main className="auth-card card">
        <p className="auth-brand">AutoAttend</p>

        {stage === DONE ? (
          <>
            <h1 className="auth-title">Password reset</h1>
            <p className="callout callout--success fp-alert">
              <span className="callout-mark" aria-hidden="true">
                ✓
              </span>
              Your password has been reset. You can sign in with it now.
            </p>
            {/* Not a redirect. The reset deliberately does not sign
                anybody in, so sending them to /login is an act they take
                rather than something that happens to them. */}
            <Link className="btn btn--primary btn--lg auth-submit fp-done-link" to="/login">
              Go to login
            </Link>
            <p className="auth-note">
              Signing in again is expected: resetting a password signs out
              every device that was already signed in to this account.
            </p>
          </>
        ) : (
          <>
            <h1 className="auth-title">Reset password</h1>

            {/* Rendered whether or not it holds anything: a live region
                added to the page at the same moment as its text is not
                reliably announced. */}
            <div aria-live="polite">
              {notice && stage === RESET && (
                <p className="callout fp-notice">{notice}</p>
              )}
            </div>

            {stage === REQUEST ? (
              <form className="auth-form" onSubmit={handleRequest}>
                <div className="form-field form-field--lg">
                  <label htmlFor="reset-email">Email</label>
                  <input
                    id="reset-email"
                    name="email"
                    type="email"
                    value={email}
                    onChange={(event) => setEmail(event.target.value)}
                    autoComplete="username"
                    spellCheck={false}
                    aria-describedby="reset-email-hint"
                    required
                  />
                  <p id="reset-email-hint" className="fp-hint">
                    Use the address your college administrator set your
                    account up with.
                  </p>
                </div>

                {alert}

                <button
                  type="submit"
                  className="btn btn--primary btn--lg auth-submit"
                  disabled={submitting}
                >
                  {submitting ? 'Sending…' : 'Send code'}
                </button>
              </form>
            ) : (
              <form className="auth-form" onSubmit={handleReset}>
                <p className="fp-sent-to">
                  Code sent to <span className="fp-email">{email}</span>
                </p>

                <div className="form-field form-field--lg">
                  <label htmlFor="reset-code">Reset code</label>
                  <input
                    ref={codeRef}
                    id="reset-code"
                    name="code"
                    className="fp-code"
                    type="text"
                    value={code}
                    onChange={(event) => setCode(event.target.value)}
                    /* A numeric keypad on a phone, without making this a
                       number input: type="number" would bring a spinner,
                       silently drop leading zeros, and let "1e6" through.
                       autoComplete="one-time-code" is what lets iOS and
                       Android offer the code straight from the SMS/mail
                       notification. */
                    inputMode="numeric"
                    autoComplete="one-time-code"
                    spellCheck={false}
                    maxLength={6}
                    aria-describedby="reset-code-hint"
                    required
                  />
                  <p id="reset-code-hint" className="fp-hint">
                    Six digits, from the email. It expires in 15 minutes.
                  </p>
                </div>

                <div className="form-field form-field--lg">
                  <label htmlFor="reset-new-password">New password</label>
                  <input
                    id="reset-new-password"
                    name="new_password"
                    type="password"
                    value={password}
                    onChange={(event) => setPassword(event.target.value)}
                    autoComplete="new-password"
                    aria-describedby="reset-password-hint"
                    required
                  />
                  {/* Mirrors MIN_PASSWORD_LENGTH in users/validators.py.
                      The server is the authority and names the number
                      itself when it refuses; this is only so the rule is
                      known before the attempt — which matters more here,
                      where a refusal costs a round trip to an inbox. */}
                  <p id="reset-password-hint" className="fp-hint">
                    Use at least 8 characters.
                  </p>
                </div>

                <div className="form-field form-field--lg">
                  <label htmlFor="reset-confirm-password">
                    Confirm new password
                  </label>
                  <input
                    id="reset-confirm-password"
                    name="confirm_password"
                    type="password"
                    value={confirm}
                    onChange={(event) => setConfirm(event.target.value)}
                    autoComplete="new-password"
                    required
                  />
                </div>

                {alert}

                <button
                  type="submit"
                  className="btn btn--primary btn--lg auth-submit"
                  disabled={submitting}
                >
                  {submitting ? 'Resetting…' : 'Reset password'}
                </button>

                <button
                  type="button"
                  className="btn btn--secondary fp-restart"
                  onClick={startOver}
                >
                  Use a different email
                </button>
              </form>
            )}

            <p className="auth-note">
              The code resets your password — it does not sign you in, and
              nothing changes until you use it.{' '}
              <Link to="/login">Back to login</Link>
            </p>
          </>
        )}
      </main>
    </div>
  )
}

export default ForgotPassword
