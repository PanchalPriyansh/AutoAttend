import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  getAttendanceSession,
  listAssignedClasses,
  recognizeAttendance,
  saveAttendance,
} from '../../api/attendance'
import AppShell from '../../components/layout/AppShell'
import ClassroomCapture from '../../components/faculty/ClassroomCapture'
import StudentStatusRow from '../../components/faculty/StudentStatusRow'
import ConfirmDialog from '../../components/admin/ConfirmDialog'
import { describeClass, today } from '../../utils/lecture'

function AttendanceCapture() {
  const [classes, setClasses] = useState([])
  const [classId, setClassId] = useState('')
  const [date, setDate] = useState(today)

  const [existing, setExisting] = useState(null)
  const [proposal, setProposal] = useState(null)
  // studentId -> { status, marked_by }. The single source of truth for what
  // gets saved; the proposal below it is only what recognition suggested.
  const [working, setWorking] = useState({})

  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [conflict, setConflict] = useState(false)
  const [replacePrompt, setReplacePrompt] = useState(false)

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

  // A different class or date means a different lecture, so any proposal for
  // the previous one has to go rather than be saved against this one.
  useEffect(() => {
    setProposal(null)
    setWorking({})
    setNotice('')
    setError('')
    setConflict(false)
  }, [classId, date])

  useEffect(() => {
    if (!classId || !date) {
      setExisting(null)
      return undefined
    }

    let cancelled = false
    getAttendanceSession(classId, date)
      .then((session) => {
        if (!cancelled) setExisting(session)
      })
      .catch(() => {
        // Silent: this is a "has it been taken already?" courtesy check. The
        // save path reports a genuine conflict itself, so a failure here must
        // not put an error banner over a screen that still works.
        if (!cancelled) setExisting(null)
      })

    return () => {
      cancelled = true
    }
  }, [classId, date])

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

  async function handleCapture(file, kind) {
    setNotice('')
    const result = await run(() => recognizeAttendance(classId, file, kind))
    if (!result) return

    // Everything the pipeline proposed starts as its proposal; flipping a row
    // is what re-attributes it to the faculty member.
    const next = {}
    result.recognized.forEach((row) => {
      next[row.student.id] = { status: 'present', marked_by: 'recognition' }
    })
    result.unrecognized.forEach((row) => {
      next[row.student.id] = { status: 'absent', marked_by: 'recognition' }
    })
    result.not_enrolled.forEach((row) => {
      next[row.student.id] = { status: 'absent', marked_by: 'recognition' }
    })

    setProposal(result)
    setWorking(next)
  }

  const toggle = useCallback((studentId) => {
    setWorking((current) => {
      const entry = current[studentId]
      if (!entry) return current

      return {
        ...current,
        [studentId]: {
          status: entry.status === 'present' ? 'absent' : 'present',
          // The record now reflects a person's judgement, not the pipeline's.
          // Keeping it as "recognition" would overstate how well matching
          // performed the next time anyone looks.
          marked_by: 'faculty',
        },
      }
    })
  }, [])

  /**
   * Saves the working list. Not routed through run() because a 409 needs
   * different handling from every other failure: it means this lecture was
   * already recorded, which the faculty member may legitimately want to
   * overwrite, so it becomes an explicit offer rather than a dead end.
   */
  async function persist(replace) {
    const records = Object.entries(working).map(([studentId, entry]) => ({
      student_id: studentId,
      status: entry.status,
      marked_by: entry.marked_by,
    }))

    setError('')
    setNotice('')
    setConflict(false)
    setPending(true)
    try {
      const saved = await saveAttendance({
        classId,
        date,
        source: proposal.source,
        records,
        replace,
      })

      setExisting(saved)
      setProposal(null)
      setWorking({})
      setNotice(
        `Attendance saved: ${saved.present_count} present, ${saved.absent_count} absent.`,
      )
    } catch (err) {
      setError(err.message)
      if (err.status === 409) setConflict(true)
    } finally {
      setPending(false)
    }
  }

  const counts = useMemo(() => {
    const values = Object.values(working)
    return {
      present: values.filter((entry) => entry.status === 'present').length,
      absent: values.filter((entry) => entry.status === 'absent').length,
    }
  }, [working])

  const selectedClass = classes.find((item) => item.id === classId)

  return (
    <AppShell title="Take Attendance">
      <p className="hierarchy-hint">
        Capture the room, then review what was recognised before saving. Face recognition
        is an aid, not a verdict — the list you save is your own record of who attended,
        so correct anything it got wrong.
      </p>

      <section className="hierarchy-level" aria-labelledby="lecture-heading">
        <header className="hierarchy-header">
          <h2 id="lecture-heading">Choose a lecture</h2>
        </header>

        <div className="hierarchy-form">
          <span className="field">
            <label htmlFor="pick-class">Class</label>
            <select
              id="pick-class"
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

          <span className="field">
            <label htmlFor="pick-date">Date</label>
            <input
              id="pick-date"
              type="date"
              value={date}
              max={today()}
              onChange={(event) => setDate(event.target.value)}
            />
          </span>
        </div>

        {classes.length === 0 && (
          <p className="hierarchy-hint">
            No classes are assigned to you yet. An admin assigns classes from the academic
            hierarchy screen.
          </p>
        )}
        {selectedClass && (
          <p className="hierarchy-hint">
            {selectedClass.institute} · {selectedClass.department} ·{' '}
            {selectedClass.semester} · {selectedClass.student_count} enrolled
          </p>
        )}
      </section>

      {error && (
        <p role="alert" className="hierarchy-error">
          {error}
        </p>
      )}
      {notice && <p className="hierarchy-hint">{notice}</p>}

      {classId && existing && !proposal && (
        <p className="hierarchy-hint">
          Attendance for this date was already recorded — {existing.present_count} present,{' '}
          {existing.absent_count} absent. Capturing again lets you replace it.
        </p>
      )}

      {classId && (
        <section className="hierarchy-level" aria-labelledby="capture-heading">
          <header className="hierarchy-header">
            <h2 id="capture-heading">Capture the room</h2>
          </header>
          <ClassroomCapture onCapture={handleCapture} disabled={pending} />
          {pending && !proposal && (
            <p className="hierarchy-hint">Analysing the capture — this can take a moment…</p>
          )}
        </section>
      )}

      {proposal && (
        <>
          <section className="hierarchy-level" aria-labelledby="review-heading">
            <header className="hierarchy-header">
              <h2 id="review-heading">
                Review ({counts.present} present, {counts.absent} absent)
              </h2>
            </header>

            <p className="hierarchy-hint">
              Analysed {proposal.frames_analyzed} frame
              {proposal.frames_analyzed === 1 ? '' : 's'} and found{' '}
              {proposal.detected_faces} face{proposal.detected_faces === 1 ? '' : 's'}.
            </p>

            {proposal.unknown_faces > 0 && (
              <p role="alert" className="hierarchy-error">
                {proposal.unknown_faces} detected face
                {proposal.unknown_faces === 1 ? '' : 's'} did not match anyone on this
                roster. That may be a visitor, or a student whose registered photos are
                too different to match — check the list below before saving.
              </p>
            )}
          </section>

          <section className="hierarchy-level" aria-labelledby="recognized-heading">
            <header className="hierarchy-header">
              <h2 id="recognized-heading">
                Recognised ({proposal.recognized.length})
              </h2>
            </header>
            {proposal.recognized.length === 0 && (
              <p className="hierarchy-hint">Nobody was recognised in this capture.</p>
            )}
            <ul className="hierarchy-items">
              {proposal.recognized.map((row) => (
                <StudentStatusRow
                  key={row.student.id}
                  student={row.student}
                  detail={
                    row.confidence === 'low'
                      ? `Weak match (${row.distance}) — check this one`
                      : `Matched (${row.distance})`
                  }
                  status={working[row.student.id]?.status}
                  disabled={pending}
                  onToggle={toggle}
                />
              ))}
            </ul>
          </section>

          <section className="hierarchy-level" aria-labelledby="unrecognized-heading">
            <header className="hierarchy-header">
              <h2 id="unrecognized-heading">
                Not recognised ({proposal.unrecognized.length})
              </h2>
            </header>
            <p className="hierarchy-hint">
              These students have registered faces but were not matched in this capture.
            </p>
            <ul className="hierarchy-items">
              {proposal.unrecognized.map((row) => (
                <StudentStatusRow
                  key={row.student.id}
                  student={row.student}
                  detail={`${row.sample_count} registered sample${
                    row.sample_count === 1 ? '' : 's'
                  }`}
                  status={working[row.student.id]?.status}
                  disabled={pending}
                  onToggle={toggle}
                />
              ))}
            </ul>
          </section>

          {proposal.not_enrolled.length > 0 && (
            <section className="hierarchy-level" aria-labelledby="not-enrolled-heading">
              <header className="hierarchy-header">
                <h2 id="not-enrolled-heading">
                  No registered face ({proposal.not_enrolled.length})
                </h2>
              </header>
              <p className="hierarchy-hint">
                Recognition could not look for these students at all — nobody has
                registered their face yet, so they will never be matched. Mark them by
                hand, and ask an admin to enrol them.
              </p>
              <ul className="hierarchy-items">
                {proposal.not_enrolled.map((row) => (
                  <StudentStatusRow
                    key={row.student.id}
                    student={row.student}
                    detail="Not enrolled for recognition"
                    status={working[row.student.id]?.status}
                    disabled={pending}
                    onToggle={toggle}
                  />
                ))}
              </ul>
            </section>
          )}

          <div className="hierarchy-form">
            <button type="button" onClick={() => persist(false)} disabled={pending}>
              Save attendance
            </button>
            {conflict && (
              <button
                type="button"
                className="danger"
                onClick={() => setReplacePrompt(true)}
                disabled={pending}
              >
                Replace the existing record
              </button>
            )}
          </div>
        </>
      )}

      <ConfirmDialog
        open={replacePrompt}
        title="Replace recorded attendance?"
        message={`The attendance already recorded for this class on ${date} will be overwritten by the list above.`}
        confirmLabel="Replace"
        pending={pending}
        onConfirm={async () => {
          setReplacePrompt(false)
          await persist(true)
        }}
        onCancel={() => setReplacePrompt(false)}
      />
    </AppShell>
  )
}

export default AttendanceCapture
