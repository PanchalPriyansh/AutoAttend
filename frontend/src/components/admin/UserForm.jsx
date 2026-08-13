import { useState } from 'react'

const ROLES = ['admin', 'faculty', 'student']

function initialValues(user) {
  return {
    name: user?.name ?? '',
    email: user?.email ?? '',
    password: '',
    role: user?.role ?? 'student',
    institute_id: user?.institute_id ?? '',
  }
}

/**
 * Create/edit form for a user account. One component for both modes because
 * they share every field they have in common; the differences are exactly
 * the two backend rules:
 *
 *   - role is immutable after creation, so edit mode shows it as text
 *   - passwords are set through their own endpoint, so edit mode has no
 *     password field
 *
 * Showing an editable role or a password box in edit mode would imply the
 * API accepts changes it silently ignores.
 */
function UserForm({ mode, user, institutes, onSubmit, onCancel }) {
  const [values, setValues] = useState(() => initialValues(user))
  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)

  const isCreate = mode === 'create'

  function setField(field, value) {
    setValues((current) => ({ ...current, [field]: value }))
  }

  async function handleSubmit(event) {
    event.preventDefault()
    setError('')
    setSubmitting(true)
    try {
      const payload = {
        name: values.name,
        email: values.email,
        institute_id: values.institute_id || null,
      }
      if (isCreate) {
        payload.password = values.password
        payload.role = values.role
      }
      await onSubmit(payload)
    } catch (err) {
      setError(err.message)
    } finally {
      setSubmitting(false)
    }
  }

  const idPrefix = isCreate ? 'new-user' : `edit-user-${user.id}`

  return (
    <form className="hierarchy-form" onSubmit={handleSubmit}>
      <span className="field">
        <label htmlFor={`${idPrefix}-name`}>Name</label>
        <input
          id={`${idPrefix}-name`}
          type="text"
          value={values.name}
          onChange={(event) => setField('name', event.target.value)}
          required
        />
      </span>

      <span className="field">
        <label htmlFor={`${idPrefix}-email`}>Email</label>
        <input
          id={`${idPrefix}-email`}
          type="email"
          value={values.email}
          onChange={(event) => setField('email', event.target.value)}
          autoComplete="off"
          required
        />
      </span>

      {isCreate && (
        <span className="field">
          <label htmlFor={`${idPrefix}-password`}>Password</label>
          <input
            id={`${idPrefix}-password`}
            type="password"
            value={values.password}
            onChange={(event) => setField('password', event.target.value)}
            autoComplete="new-password"
            required
          />
        </span>
      )}

      <span className="field">
        <label htmlFor={`${idPrefix}-role`}>Role</label>
        {isCreate ? (
          <select
            id={`${idPrefix}-role`}
            value={values.role}
            onChange={(event) => setField('role', event.target.value)}
          >
            {ROLES.map((role) => (
              <option key={role} value={role}>
                {role}
              </option>
            ))}
          </select>
        ) : (
          <input id={`${idPrefix}-role`} type="text" value={values.role} readOnly />
        )}
      </span>

      <span className="field">
        <label htmlFor={`${idPrefix}-institute`}>Institute</label>
        <select
          id={`${idPrefix}-institute`}
          value={values.institute_id}
          onChange={(event) => setField('institute_id', event.target.value)}
        >
          <option value="">— None —</option>
          {institutes.map((institute) => (
            <option key={institute.id} value={institute.id}>
              {institute.name}
            </option>
          ))}
        </select>
      </span>

      <button type="submit" disabled={submitting}>
        {submitting ? 'Saving…' : 'Save'}
      </button>
      <button type="button" onClick={onCancel} disabled={submitting}>
        Cancel
      </button>

      {error && (
        <p role="alert" className="hierarchy-error">
          {error}
        </p>
      )}
    </form>
  )
}

export default UserForm
