import { useCallback, useEffect, useState } from 'react'
import {
  assignFaculty,
  enrollStudent,
  listClassStudents,
  listUsers,
  unenrollStudent,
} from '../../api/users'
import ConfirmDialog from './ConfirmDialog'

/**
 * Manages who teaches a class and who is enrolled in it — the "-> Student"
 * tier the academic hierarchy left empty.
 *
 * Takes the selected class object rather than its id because it needs the
 * current `faculty_id`, and calls `onClassChanged` after assigning so the
 * hierarchy re-reads the class list instead of this panel holding a private
 * copy that could drift from what the level above displays.
 */
function ClassAssignment({ classItem, onClassChanged }) {
  const classId = classItem.id

  const [faculty, setFaculty] = useState([])
  const [enrollments, setEnrollments] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [reloadToken, setReloadToken] = useState(0)

  const [studentQuery, setStudentQuery] = useState('')
  const [studentResults, setStudentResults] = useState([])
  const [searched, setSearched] = useState(false)
  const [pendingRemoval, setPendingRemoval] = useState(null)
  const [pending, setPending] = useState(false)
  const [actionError, setActionError] = useState('')

  const refresh = useCallback(() => setReloadToken((token) => token + 1), [])

  // Selecting a different class must not leave the previous class's search
  // results or errors on screen.
  useEffect(() => {
    setStudentQuery('')
    setStudentResults([])
    setSearched(false)
    setActionError('')
  }, [classId])

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError('')

    Promise.all([listClassStudents(classId), listUsers({ role: 'faculty' })])
      .then(([rows, facultyRows]) => {
        if (cancelled) return
        setEnrollments(rows)
        // Unfiltered on purpose: a deactivated faculty member stays assigned,
        // and the "currently assigned" line must still be able to name them.
        setFaculty(facultyRows)
      })
      .catch((err) => {
        if (cancelled) return
        setError(err.message)
        setEnrollments([])
        setFaculty([])
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [classId, reloadToken])

  async function runAction(action) {
    setActionError('')
    setPending(true)
    try {
      await action()
      return true
    } catch (err) {
      setActionError(err.message)
      return false
    } finally {
      setPending(false)
    }
  }

  async function handleAssign(event) {
    const facultyId = event.target.value || null
    if (await runAction(() => assignFaculty(classId, facultyId))) {
      onClassChanged()
    }
  }

  async function handleSearchStudents(event) {
    event.preventDefault()
    await runAction(async () => {
      // Searched rather than listed as a dropdown: a whole student body does
      // not belong in a <select>.
      const rows = await listUsers({ role: 'student', q: studentQuery })
      setStudentResults(rows)
      setSearched(true)
    })
  }

  async function handleEnroll(studentId) {
    if (await runAction(() => enrollStudent(classId, studentId))) refresh()
  }

  async function handleRemove() {
    const enrollment = pendingRemoval
    await runAction(() => unenrollStudent(classId, enrollment.student.id))
    setPendingRemoval(null)
    refresh()
  }

  const assigned = faculty.find((member) => member.id === classItem.faculty_id)
  const enrolledIds = new Set(enrollments.map((enrollment) => enrollment.student.id))
  const addable = studentResults.filter((student) => !enrolledIds.has(student.id))

  return (
    <section className="card ah-class" aria-labelledby="class-assignment-heading">
      <header className="ah-class-head">
        <h2 id="class-assignment-heading" className="ah-class-title">
          Class: {classItem.name}
        </h2>
      </header>

      {error && (
        <p role="alert" className="callout callout--error ah-alert">
          <span className="callout-mark" aria-hidden="true">
            !
          </span>
          {error}
        </p>
      )}
      {actionError && (
        <p role="alert" className="callout callout--error ah-alert">
          <span className="callout-mark" aria-hidden="true">
            !
          </span>
          {actionError}
        </p>
      )}
      {loading && <p className="ah-loading">Loading…</p>}

      <div className="ah-assign">
        <span className="form-field ah-field">
          <label htmlFor="assign-faculty">Assigned faculty</label>
          <select
            id="assign-faculty"
            name="faculty_id"
            value={classItem.faculty_id ?? ''}
            onChange={handleAssign}
            disabled={pending}
          >
            <option value="">— Unassigned —</option>
            {faculty
              .filter((member) => member.is_active || member.id === classItem.faculty_id)
              .map((member) => (
                <option key={member.id} value={member.id}>
                  {member.name}
                  {member.is_active ? '' : ' (deactivated)'}
                </option>
              ))}
          </select>
        </span>
        {classItem.faculty_id && !assigned && (
          <p className="ah-note">Assigned to a faculty member not in the list.</p>
        )}
      </div>

      <h3 className="ah-subtitle">Enrolled students ({enrollments.length})</h3>

      {!loading && enrollments.length === 0 && (
        <p className="ah-empty">No students enrolled yet.</p>
      )}

      <ul className="ah-roster">
        {enrollments.map((enrollment) => (
          <li
            key={enrollment.id}
            className={`ah-person${enrollment.student.is_active ? '' : ' ah-person--inactive'}`}
          >
            <span className="ah-person-identity">
              <span className="ah-person-name">{enrollment.student.name}</span>
              <span className="ah-person-meta">
                <span className="ah-person-email">{enrollment.student.email}</span>
                {!enrollment.student.is_active && <span className="ah-flag">Deactivated</span>}
              </span>
            </span>
            <button
              type="button"
              className="btn btn--danger ah-action"
              onClick={() => {
                setActionError('')
                setPendingRemoval(enrollment)
              }}
              disabled={pending}
            >
              Remove
            </button>
          </li>
        ))}
      </ul>

      <form className="ah-search" onSubmit={handleSearchStudents}>
        <span className="form-field ah-field">
          <label htmlFor="enroll-search">Add a student</label>
          <input
            id="enroll-search"
            name="q"
            type="search"
            value={studentQuery}
            onChange={(event) => setStudentQuery(event.target.value)}
            autoComplete="off"
            spellCheck={false}
            placeholder="Search by name or email…"
          />
        </span>
        <button type="submit" className="btn btn--secondary ah-submit" disabled={pending}>
          Search
        </button>
      </form>

      {searched && addable.length === 0 && (
        <p className="ah-empty">No matching students left to add.</p>
      )}

      <ul className="ah-roster ah-results">
        {addable.map((student) => (
          <li
            key={student.id}
            className={`ah-person${student.is_active ? '' : ' ah-person--inactive'}`}
          >
            <span className="ah-person-identity">
              <span className="ah-person-name">{student.name}</span>
              <span className="ah-person-meta">
                <span className="ah-person-email">{student.email}</span>
                {!student.is_active && <span className="ah-flag">Deactivated</span>}
              </span>
            </span>
            <button
              type="button"
              className="btn btn--primary ah-action"
              onClick={() => handleEnroll(student.id)}
              disabled={pending || !student.is_active}
              /* A disabled button is not focusable and title is unreliable
                 for assistive tech, so the reason has to be in the
                 accessible name to reach anyone not hovering a mouse.
                 title stays for the mouse hover it does serve. */
              aria-label={
                student.is_active
                  ? undefined
                  : `Enroll ${student.name} — unavailable, account deactivated`
              }
              title={student.is_active ? undefined : 'Deactivated accounts cannot be enrolled'}
            >
              Enroll
            </button>
          </li>
        ))}
      </ul>

      <ConfirmDialog
        open={pendingRemoval !== null}
        title="Remove student?"
        confirmLabel="Remove"
        message={
          pendingRemoval
            ? `${pendingRemoval.student.name} will be unenrolled from ${classItem.name}. Their account is not affected.`
            : ''
        }
        pending={pending}
        onConfirm={handleRemove}
        onCancel={() => setPendingRemoval(null)}
      />
    </section>
  )
}

export default ClassAssignment
