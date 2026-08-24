import { useCallback, useEffect, useState } from 'react'
import { classes, courses, departments, institutes, semesters } from '../../api/academic'
import {
  deleteFaceEncoding,
  listClassFaceEnrollment,
  listStudentFaceEncodings,
  registerFaceEncoding,
} from '../../api/faces'
import AppShell from '../../components/layout/AppShell'
import ConfirmDialog from '../../components/admin/ConfirmDialog'
import FaceCapture from '../../components/admin/FaceCapture'

const EMPTY_SELECTION = { institute: '', department: '', semester: '', course: '', class: '' }

// The hierarchy order, which is also the order descendants are cleared in.
const LEVELS = [
  { key: 'institute', label: 'Institute', resource: institutes, parent: null },
  { key: 'department', label: 'Department', resource: departments, parent: 'institute' },
  { key: 'semester', label: 'Semester', resource: semesters, parent: 'department' },
  { key: 'course', label: 'Course', resource: courses, parent: 'semester' },
  { key: 'class', label: 'Class', resource: classes, parent: 'course' },
]

/**
 * Read-only sibling of AcademicHierarchy's useHierarchyLevel: this screen
 * only needs to *reach* a class, not manage the hierarchy, so it reuses the
 * same api/academic.js resources through a plain dropdown per level instead
 * of the full CRUD panels.
 */
function useLevelOptions(resource, parentId) {
  const [items, setItems] = useState([])

  const scoped = resource.parentParam !== null
  const ready = !scoped || Boolean(parentId)

  useEffect(() => {
    if (!ready) {
      setItems([])
      return undefined
    }

    let cancelled = false
    resource
      .list(parentId)
      .then((rows) => {
        if (!cancelled) setItems(rows)
      })
      .catch(() => {
        // Silent: the page-level error channel below reports anything that
        // actually blocks the admin, and a failed dropdown simply stays empty
        // rather than burying the roster under a second error banner.
        if (!cancelled) setItems([])
      })

    return () => {
      cancelled = true
    }
  }, [resource, parentId, ready])

  return items
}

function FaceEnrollment() {
  const [selection, setSelection] = useState(EMPTY_SELECTION)

  const [roster, setRoster] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [reloadToken, setReloadToken] = useState(0)

  const [selectedStudent, setSelectedStudent] = useState(null)
  const [encodings, setEncodings] = useState([])
  const [pendingDeletion, setPendingDeletion] = useState(null)
  const [pending, setPending] = useState(false)
  const [actionError, setActionError] = useState('')
  const [notice, setNotice] = useState('')

  const options = {
    institute: useLevelOptions(institutes, null),
    department: useLevelOptions(departments, selection.institute),
    semester: useLevelOptions(semesters, selection.department),
    course: useLevelOptions(courses, selection.semester),
    class: useLevelOptions(classes, selection.course),
  }

  const classId = selection.class
  const refresh = useCallback(() => setReloadToken((token) => token + 1), [])

  const select = useCallback((level, id) => {
    setSelection((current) => {
      const next = { ...current }
      let below = false
      for (const key of Object.keys(EMPTY_SELECTION)) {
        if (key === level) {
          next[key] = id
          below = true
        } else if (below) {
          next[key] = ''
        }
      }
      return next
    })
  }, [])

  // A different class means a different roster, so the open student panel and
  // any message about the previous one must go.
  useEffect(() => {
    setSelectedStudent(null)
    setEncodings([])
    setActionError('')
    setNotice('')
  }, [classId])

  useEffect(() => {
    if (!classId) {
      setRoster([])
      setError('')
      return undefined
    }

    let cancelled = false
    setLoading(true)
    setError('')

    listClassFaceEnrollment(classId)
      .then((rows) => {
        if (!cancelled) setRoster(rows)
      })
      .catch((err) => {
        if (cancelled) return
        setError(err.message)
        setRoster([])
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [classId, reloadToken])

  const loadEncodings = useCallback(async (studentId) => {
    const rows = await listStudentFaceEncodings(studentId)
    setEncodings(rows)
  }, [])

  async function runAction(action) {
    setActionError('')
    setNotice('')
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

  async function openStudent(student) {
    setSelectedStudent(student)
    setEncodings([])
    await runAction(() => loadEncodings(student.id))
  }

  async function handleCapture(image, source) {
    const student = selectedStudent
    const captured = await runAction(() => registerFaceEncoding(student.id, image, source))
    if (!captured) return

    setNotice(`Face sample registered for ${student.name}.`)
    await loadEncodings(student.id)
    // The roster's sample count is now stale.
    refresh()
  }

  async function handleDelete() {
    const encoding = pendingDeletion
    const student = selectedStudent
    const deleted = await runAction(() => deleteFaceEncoding(student.id, encoding.id))
    // Closed either way: the error message would otherwise sit behind the dialog.
    setPendingDeletion(null)
    if (!deleted) return

    setNotice('Face sample deleted.')
    await loadEncodings(student.id)
    refresh()
  }

  const unenrolled = roster.filter((row) => row.sample_count === 0).length

  return (
    <AppShell title="Face Enrollment">
      <p className="hierarchy-hint">
        Register each student&apos;s face so attendance can recognise them later. Photos are
        never stored — only the numeric encoding derived from them. A student with no
        samples cannot be recognised at all.
      </p>

      <section className="hierarchy-level" aria-labelledby="class-picker-heading">
        <header className="hierarchy-header">
          <h2 id="class-picker-heading">Choose a class</h2>
        </header>

        <div className="hierarchy-form">
          {LEVELS.map((level) => {
            const parentSelected = level.parent === null || Boolean(selection[level.parent])
            return (
              <span className="field" key={level.key}>
                <label htmlFor={`pick-${level.key}`}>{level.label}</label>
                <select
                  id={`pick-${level.key}`}
                  value={selection[level.key]}
                  onChange={(event) => select(level.key, event.target.value)}
                  disabled={!parentSelected}
                >
                  <option value="">— Select —</option>
                  {options[level.key].map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.name}
                    </option>
                  ))}
                </select>
              </span>
            )
          })}
        </div>
      </section>

      {classId && (
        <section className="hierarchy-level" aria-labelledby="roster-heading">
          <header className="hierarchy-header">
            <h2 id="roster-heading">Roster ({roster.length})</h2>
          </header>

          {error && (
            <p role="alert" className="hierarchy-error">
              {error}
            </p>
          )}
          {loading && <p className="hierarchy-hint">Loading…</p>}
          {!loading && !error && roster.length === 0 && (
            <p className="hierarchy-hint">
              No students are enrolled in this class yet. Enroll them from the academic
              hierarchy screen first.
            </p>
          )}
          {!loading && !error && roster.length > 0 && unenrolled > 0 && (
            <p className="hierarchy-hint">
              {unenrolled} of {roster.length} students have no face samples yet.
            </p>
          )}

          <ul className="hierarchy-items">
            {roster.map((row) => (
              <li
                key={row.student.id}
                className={row.student.is_active ? undefined : 'is-inactive'}
              >
                <span className="user-identity">
                  <span className="user-name">{row.student.name}</span>
                  <span className="hierarchy-hint">
                    {row.student.email}
                    {!row.student.is_active && ' · Deactivated'}
                    {' · '}
                    {row.sample_count === 0
                      ? 'No face samples'
                      : `${row.sample_count} face sample${row.sample_count === 1 ? '' : 's'}`}
                  </span>
                </span>
                <button
                  type="button"
                  onClick={() => openStudent(row.student)}
                  disabled={pending}
                >
                  {selectedStudent?.id === row.student.id ? 'Managing' : 'Manage faces'}
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}

      {selectedStudent && (
        <section className="hierarchy-level" aria-labelledby="student-faces-heading">
          <header className="hierarchy-header">
            <h2 id="student-faces-heading">Faces: {selectedStudent.name}</h2>
          </header>

          {actionError && (
            <p role="alert" className="hierarchy-error">
              {actionError}
            </p>
          )}
          {notice && <p className="hierarchy-hint">{notice}</p>}

          <FaceCapture onCapture={handleCapture} disabled={pending} />

          <h3 className="hierarchy-subheading">Registered samples ({encodings.length})</h3>

          {encodings.length === 0 && (
            <p className="hierarchy-hint">No samples registered yet.</p>
          )}

          <ul className="hierarchy-items">
            {encodings.map((encoding) => (
              <li key={encoding.id}>
                <span className="user-identity">
                  <span className="user-name">
                    {new Date(encoding.created_at).toLocaleString()}
                  </span>
                  <span className="hierarchy-hint">
                    {encoding.source === 'camera' ? 'Camera capture' : 'Uploaded photo'}
                    {' · '}
                    {encoding.model}
                  </span>
                </span>
                <button
                  type="button"
                  className="danger"
                  onClick={() => {
                    setActionError('')
                    setPendingDeletion(encoding)
                  }}
                  disabled={pending}
                >
                  Delete
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}

      <ConfirmDialog
        open={pendingDeletion !== null}
        title="Delete face sample?"
        message={
          pendingDeletion && selectedStudent
            ? `This sample for ${selectedStudent.name} will be removed. Their account and enrollment are not affected.`
            : ''
        }
        pending={pending}
        onConfirm={handleDelete}
        onCancel={() => setPendingDeletion(null)}
      />
    </AppShell>
  )
}

export default FaceEnrollment
