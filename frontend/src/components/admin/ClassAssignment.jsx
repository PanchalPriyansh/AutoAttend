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
    <section className="hierarchy-level" aria-labelledby="class-assignment-heading">
      <header className="hierarchy-header">
        <h2 id="class-assignment-heading">Class: {classItem.name}</h2>
      </header>

      {error && (
        <p role="alert" className="hierarchy-error">
          {error}
        </p>
      )}
      {actionError && (
        <p role="alert" className="hierarchy-error">
          {actionError}
        </p>
      )}
      {loading && <p className="hierarchy-hint">Loading…</p>}

      <div className="hierarchy-form">
        <span className="field">
          <label htmlFor="assign-faculty">Assigned faculty</label>
          <select
            id="assign-faculty"
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
          <p className="hierarchy-hint">Assigned to a faculty member not in the list.</p>
        )}
      </div>

      <h3 className="hierarchy-subheading">Enrolled students ({enrollments.length})</h3>

      {!loading && enrollments.length === 0 && (
        <p className="hierarchy-hint">No students enrolled yet.</p>
      )}

      <ul className="hierarchy-items">
        {enrollments.map((enrollment) => (
          <li
            key={enrollment.id}
            className={enrollment.student.is_active ? undefined : 'is-inactive'}
          >
            <span className="user-identity">
              <span className="user-name">{enrollment.student.name}</span>
              <span className="hierarchy-hint">
                {enrollment.student.email}
                {!enrollment.student.is_active && ' · Deactivated'}
              </span>
            </span>
            <button
              type="button"
              className="danger"
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

      <form className="hierarchy-form" onSubmit={handleSearchStudents}>
        <span className="field">
          <label htmlFor="enroll-search">Add a student</label>
          <input
            id="enroll-search"
            type="search"
            value={studentQuery}
            onChange={(event) => setStudentQuery(event.target.value)}
            placeholder="Search by name or email"
          />
        </span>
        <button type="submit" disabled={pending}>
          Search
        </button>
      </form>

      {searched && addable.length === 0 && (
        <p className="hierarchy-hint">No matching students left to add.</p>
      )}

      <ul className="hierarchy-items">
        {addable.map((student) => (
          <li key={student.id} className={student.is_active ? undefined : 'is-inactive'}>
            <span className="user-identity">
              <span className="user-name">{student.name}</span>
              <span className="hierarchy-hint">
                {student.email}
                {!student.is_active && ' · Deactivated'}
              </span>
            </span>
            <button
              type="button"
              onClick={() => handleEnroll(student.id)}
              disabled={pending || !student.is_active}
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
