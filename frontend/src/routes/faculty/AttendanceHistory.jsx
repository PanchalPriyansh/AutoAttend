import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  deleteAttendanceSession,
  getAttendanceSessionById,
  listAssignedClasses,
  listAttendanceSessions,
  updateAttendanceSession,
} from '../../api/attendance'
import AppShell from '../../components/layout/AppShell'
import StudentStatusRow from '../../components/faculty/StudentStatusRow'
import ConfirmDialog from '../../components/admin/ConfirmDialog'
import { describeClass, today } from '../../utils/lecture'

const PAGE_SIZE = 25

/**
 * The saved records as a working map, keyed by student. Kept beside the
 * loaded session rather than replacing it, so a flipped row can be compared
 * against what is actually stored.
 */
function toWorking(session) {
  const working = {}
  session.records.forEach((row) => {
    working[row.student.id] = { status: row.status, marked_by: row.marked_by }
  })
  return working
}

function AttendanceHistory() {
  const [classes, setClasses] = useState([])
  const [classId, setClassId] = useState('')
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')

  const [sessions, setSessions] = useState([])
  const [total, setTotal] = useState(0)
  const [skip, setSkip] = useState(0)

  const [openSession, setOpenSession] = useState(null)
  // studentId -> { status, marked_by }, the list that would be saved.
  const [working, setWorking] = useState({})

  const [loading, setLoading] = useState(false)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [deletePrompt, setDeletePrompt] = useState(false)

  useEffect(() => {
    let cancelled = false
    listAssignedClasses()
      .then((rows) => {
        if (!cancelled) setClasses(rows)
      })
      .catch((err) => {
        if (!cancelled) setError(err.message)
      })

    return () => {
      cancelled = true
    }
  }, [])

  // A different class or filter is a different list, so paging restarts and
  // any session opened from the old one is closed rather than left on screen
  // above results it no longer belongs to.
  useEffect(() => {
    setSkip(0)
    setOpenSession(null)
    setWorking({})
    setNotice('')
    setError('')
  }, [classId, from, to])

  const loadSessions = useCallback(() => {
    if (!classId) {
      setSessions([])
      setTotal(0)
      return undefined
    }

    let cancelled = false
    setLoading(true)
    setError('')
    listAttendanceSessions(classId, { from, to, limit: PAGE_SIZE, skip })
      .then((body) => {
        if (cancelled) return
        setSessions(body.sessions)
        setTotal(body.total)
      })
      .catch((err) => {
        if (!cancelled) setError(err.message)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [classId, from, to, skip])

  useEffect(loadSessions, [loadSessions])

  async function run(action) {
    setError('')
    setPending(true)
    try {
      return await action()
    } catch (err) {
      setError(err.message)
      return undefined
    } finally {
      setPending(false)
    }
  }

  async function open(sessionId) {
    setNotice('')
    const session = await run(() => getAttendanceSessionById(sessionId))
    if (!session) return

    setOpenSession(session)
    setWorking(toWorking(session))
  }

  // Only status lives here while a row is being worked on. marked_by is not
  // tracked per-toggle: flipping a row twice nets no change, and stamping
  // "faculty" on the way there would still overwrite that student's stored
  // provenance at save time even though nothing about their outcome moved.
  // save() derives the real marked_by by comparing the final status against
  // what openSession actually has stored.
  const toggle = useCallback((studentId) => {
    setWorking((current) => {
      const entry = current[studentId]
      if (!entry) return current

      return {
        ...current,
        [studentId]: { ...entry, status: entry.status === 'present' ? 'absent' : 'present' },
      }
    })
  }, [])

  /**
   * Which rows differ from what is stored. Drives both the save button and
   * what gets sent: an untouched row keeps its stored marked_by, so opening a
   * session and saving it cannot silently re-attribute every recognised
   * record to the person who looked at it.
   */
  const changed = useMemo(() => {
    if (!openSession) return []

    return openSession.records.filter(
      (row) => working[row.student.id]?.status !== row.status,
    )
  }, [openSession, working])

  const counts = useMemo(() => {
    const values = Object.values(working)
    return {
      present: values.filter((entry) => entry.status === 'present').length,
      absent: values.filter((entry) => entry.status === 'absent').length,
    }
  }, [working])

  async function save() {
    const records = openSession.records.map((row) => {
      const entry = working[row.student.id]
      return {
        student_id: row.student.id,
        status: entry.status,
        // "faculty" only when the final status genuinely differs from what
        // is stored -- flipping a row an even number of times must not
        // re-attribute it, whichever record it started as.
        marked_by: entry.status === row.status ? row.marked_by : 'faculty',
      }
    })

    setNotice('')
    const saved = await run(() => updateAttendanceSession(openSession.id, records))
    if (!saved) return

    setOpenSession(saved)
    setWorking(toWorking(saved))
    setNotice(
      `Correction saved: ${saved.present_count} present, ${saved.absent_count} absent.`,
    )
    loadSessions()
  }

  async function remove() {
    setDeletePrompt(false)
    setNotice('')
    const result = await run(() => deleteAttendanceSession(openSession.id))
    if (!result) return

    setOpenSession(null)
    setWorking({})
    setNotice('Attendance for that lecture was deleted.')
    // Back to the first page: the session that anchored this one is gone.
    if (skip === 0) loadSessions()
    else setSkip(0)
  }

  const selectedClass = classes.find((item) => item.id === classId)
  const shown = sessions.length

  return (
    <AppShell title="Attendance History">
      <div className="fh-page">
        <p className="fh-intro">
          What has been recorded for your classes. Open a lecture to correct who was marked
          present — a correction replaces what was recorded and is attributed to you.
        </p>

        <section className="fh-panel card" aria-labelledby="filter-heading">
          <header className="fh-head">
            <h2 id="filter-heading" className="fh-eyebrow">
              Choose a class
            </h2>
          </header>

          <div className="fh-form">
            <span className="form-field fh-field">
              <label htmlFor="history-class">Class</label>
              <select
                id="history-class"
                value={classId}
                onChange={(event) => setClassId(event.target.value)}
              >
                <option value="">— Select —</option>
                {classes.map((item) => (
                  <option key={item.id} value={item.id}>
                    {describeClass(item)}
                  </option>
                ))}
              </select>
            </span>

            <span className="form-field fh-field fh-field--date">
              <label htmlFor="history-from">From</label>
              <input
                id="history-from"
                type="date"
                value={from}
                max={to || today()}
                onChange={(event) => setFrom(event.target.value)}
              />
            </span>

            <span className="form-field fh-field fh-field--date">
              <label htmlFor="history-to">To</label>
              <input
                id="history-to"
                type="date"
                value={to}
                min={from || undefined}
                max={today()}
                onChange={(event) => setTo(event.target.value)}
              />
            </span>
          </div>

          {classes.length === 0 && (
            <p className="fh-empty">
              No classes are assigned to you yet. An admin assigns classes from the academic
              hierarchy screen.
            </p>
          )}
          {selectedClass && (
            <p className="fh-context">
              {selectedClass.institute} · {selectedClass.department} ·{' '}
              {selectedClass.semester} · {selectedClass.student_count} enrolled
            </p>
          )}
        </section>

        {error && (
          <p role="alert" className="callout callout--error fh-alert">
            <span className="callout-mark" aria-hidden="true">
              !
            </span>
            {error}
          </p>
        )}
        {notice && (
          <p className="callout callout--success fh-alert">
            <span className="callout-mark" aria-hidden="true">
              ✓
            </span>
            {notice}
          </p>
        )}

        {classId && (
          <section className="fh-sessions" aria-labelledby="sessions-heading">
            <header className="fh-head">
              <h2 id="sessions-heading" className="fh-title">
                Recorded lectures {total > 0 && <span className="fh-count">({total})</span>}
              </h2>
            </header>

            {loading && <p className="fh-loading">Loading…</p>}
            {!loading && shown === 0 && (
              <p className="fh-empty">
                No attendance has been recorded for this class
                {from || to ? ' in that date range' : ' yet'}.
              </p>
            )}

            <ul className="fh-list">
              {sessions.map((session) => (
                <li
                  key={session.id}
                  className={`fh-session${
                    openSession?.id === session.id ? ' fh-session--open' : ''
                  }`}
                >
                  <span className="fh-session-identity">
                    <span className="fh-session-date">{session.date}</span>
                    <span className="fh-session-meta">
                      <span className="fh-session-facts">
                        {session.present_count} present · {session.absent_count} absent ·
                        from {session.source}
                      </span>
                      {session.edited && (
                        <span className="pill pill--warning fh-flag">
                          edited since it was recorded
                        </span>
                      )}
                    </span>
                  </span>
                  <button
                    type="button"
                    className="btn btn--secondary fh-open"
                    onClick={() => open(session.id)}
                    disabled={pending}
                  >
                    {openSession?.id === session.id ? 'Open below' : 'Open'}
                  </button>
                </li>
              ))}
            </ul>

            {total > PAGE_SIZE && (
              <div className="fh-pager">
                <button
                  type="button"
                  className="btn btn--secondary"
                  onClick={() => setSkip(Math.max(0, skip - PAGE_SIZE))}
                  disabled={skip === 0 || loading}
                >
                  ← Newer
                </button>
                <button
                  type="button"
                  className="btn btn--secondary"
                  onClick={() => setSkip(skip + PAGE_SIZE)}
                  disabled={skip + shown >= total || loading}
                >
                  Older →
                </button>
                <span className="fh-range">
                  Showing {shown === 0 ? 0 : skip + 1}–{skip + shown} of {total}
                </span>
              </div>
            )}
          </section>
        )}

        {openSession && (
          <section className="fh-detail" aria-labelledby="session-heading">
            <header className="fh-head">
              <h2 id="session-heading" className="fh-title">
                {openSession.date}{' '}
                <span className="fh-count">
                  ({counts.present} present, {counts.absent} absent)
                </span>
              </h2>
            </header>

            <p className="fh-provenance">
              Recorded from {openSession.source}
              {openSession.edited && (
                <span className="pill pill--warning fh-flag">
                  corrected since it was recorded
                </span>
              )}
            </p>
            <p className="fh-instruction">
              Flip anyone who was marked wrongly, then save.
            </p>

            <ul className="fh-roster">
              {openSession.records.map((row) => (
                <StudentStatusRow
                  key={row.student.id}
                  student={row.student}
                  detail={
                    row.marked_by === 'recognition'
                      ? 'Marked by recognition'
                      : 'Marked by faculty'
                  }
                  status={working[row.student.id]?.status}
                  disabled={pending}
                  onToggle={toggle}
                />
              ))}
            </ul>

            <div className="fh-actions">
              <button
                type="button"
                className="btn btn--primary fh-save"
                onClick={save}
                disabled={pending || changed.length === 0}
              >
                {changed.length === 0
                  ? 'Save correction'
                  : `Save correction (${changed.length} changed)`}
              </button>
              <button
                type="button"
                className="btn btn--danger fh-delete"
                onClick={() => setDeletePrompt(true)}
                disabled={pending}
              >
                Delete this record
              </button>
            </div>
          </section>
        )}
      </div>

      <ConfirmDialog
        open={deletePrompt}
        title="Delete recorded attendance?"
        message={`The attendance recorded for ${
          selectedClass ? describeClass(selectedClass) : 'this class'
        } on ${openSession?.date} will be permanently deleted, along with every student's record for that lecture. This cannot be undone.`}
        confirmLabel="Delete"
        pending={pending}
        onConfirm={remove}
        onCancel={() => setDeletePrompt(false)}
      />
    </AppShell>
  )
}

export default AttendanceHistory
