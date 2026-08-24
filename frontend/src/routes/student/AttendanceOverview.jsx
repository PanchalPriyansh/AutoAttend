import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { getMyAttendance, getMyClassAttendance } from '../../api/attendance'
import AttendanceBar from '../../components/student/AttendanceBar'
import AttendanceTrend from '../../components/student/AttendanceTrend'
import LectureStrip from '../../components/student/LectureStrip'
import ThresholdNote from '../../components/student/ThresholdNote'
import { describeClass } from '../../utils/lecture'

/**
 * Weakest attendance first, with classes that have no attendance recorded
 * last. Sorting here rather than in the query because it is a presentation
 * decision: the API returns classes in a stable reading order, and which one
 * needs the student's attention is a question the screen asks of that answer.
 */
function byNeedForAttention(classes) {
  return [...classes].sort((a, b) => {
    if (a.percentage === null && b.percentage === null) return 0
    if (a.percentage === null) return 1
    if (b.percentage === null) return -1
    return a.percentage - b.percentage
  })
}

function AttendanceOverview() {
  const [overview, setOverview] = useState(null)
  const [openClassId, setOpenClassId] = useState('')
  const [detail, setDetail] = useState(null)

  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')

  const [loading, setLoading] = useState(true)
  const [detailLoading, setDetailLoading] = useState(false)
  const [error, setError] = useState('')
  const [detailError, setDetailError] = useState('')

  useEffect(() => {
    let cancelled = false
    getMyAttendance()
      .then((body) => {
        if (!cancelled) setOverview(body)
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
  }, [])

  const loadDetail = useCallback(() => {
    if (!openClassId) {
      setDetail(null)
      return undefined
    }

    let cancelled = false
    setDetailLoading(true)
    setDetailError('')
    getMyClassAttendance(openClassId, { from, to })
      .then((body) => {
        if (!cancelled) setDetail(body)
      })
      .catch((err) => {
        if (cancelled) return
        // The panel is cleared as well as the message shown: leaving the
        // previous class's figures on screen under a new error would let a
        // student read numbers that belong to something else.
        setDetail(null)
        setDetailError(err.message)
      })
      .finally(() => {
        if (!cancelled) setDetailLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [openClassId, from, to])

  useEffect(loadDetail, [loadDetail])

  const ordered = useMemo(
    () => byNeedForAttention(overview?.classes ?? []),
    [overview],
  )

  function openClass(classId) {
    // Re-opening the class that is already open closes it, and clears the
    // date filter with it so the next class opens unfiltered rather than
    // inheriting a range the student set for a different one.
    const next = classId === openClassId ? '' : classId
    setOpenClassId(next)
    setFrom('')
    setTo('')
    setDetailError('')
  }

  if (loading) return <p>Loading your attendance…</p>

  return (
    <div>
      <h1>My Attendance</h1>
      <p className="hierarchy-hint">
        <Link to="/student">Back to dashboard</Link>
      </p>

      {error ? <p className="hierarchy-error">{error}</p> : null}

      {overview && !error ? (
        <>
          <section className="hierarchy-level">
            <h2 className="hierarchy-subheading">Overall</h2>
            <AttendanceBar
              size="large"
              percentage={overview.overall.percentage}
              presentCount={overview.overall.present_count}
              totalCount={overview.overall.total_count}
            />
          </section>

          <section className="hierarchy-level">
            <div className="hierarchy-header">
              <h2>My classes</h2>
              <span className="hierarchy-hint">Lowest attendance first</span>
            </div>

            {ordered.length === 0 ? (
              <p className="hierarchy-hint">
                You are not enrolled in any classes yet. Your college admin
                assigns these.
              </p>
            ) : (
              <ul className="hierarchy-items">
                {ordered.map((item) => (
                  <li
                    key={item.id}
                    className={item.id === openClassId ? 'is-selected' : undefined}
                  >
                    <span className="user-identity">
                      <span className="user-name">{describeClass(item)}</span>
                      <span className="hierarchy-hint">
                        {[item.semester, item.department].filter(Boolean).join(' · ')}
                      </span>
                      <AttendanceBar
                        percentage={item.percentage}
                        presentCount={item.present_count}
                        totalCount={item.total_count}
                        threshold={overview.threshold}
                      />
                      <ThresholdNote
                        threshold={overview.threshold}
                        meetsThreshold={item.meets_threshold}
                        lecturesToReach={item.lectures_to_reach}
                      />
                    </span>

                    <button
                      type="button"
                      className="hierarchy-select"
                      onClick={() => openClass(item.id)}
                    >
                      {item.id === openClassId ? 'Close' : 'View lectures'}
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </>
      ) : null}

      {openClassId ? (
        <section className="hierarchy-level">
          <div className="hierarchy-header">
            <h2>{detail ? describeClass(detail.class) : 'Lectures'}</h2>
          </div>

          <div className="hierarchy-form">
            <div className="field">
              <label htmlFor="attendance-from">From</label>
              <input
                id="attendance-from"
                type="date"
                value={from}
                onChange={(event) => setFrom(event.target.value)}
              />
            </div>
            <div className="field">
              <label htmlFor="attendance-to">To</label>
              <input
                id="attendance-to"
                type="date"
                value={to}
                onChange={(event) => setTo(event.target.value)}
              />
            </div>
          </div>

          {detailError ? <p className="hierarchy-error">{detailError}</p> : null}
          {detailLoading ? <p>Loading lectures…</p> : null}

          {detail && !detailLoading ? (
            <>
              <AttendanceBar
                percentage={detail.percentage}
                presentCount={detail.present_count}
                totalCount={detail.total_count}
                threshold={detail.threshold}
              />
              <ThresholdNote
                threshold={detail.threshold}
                meetsThreshold={detail.meets_threshold}
                lecturesToReach={detail.lectures_to_reach}
              />

              {detail.total_count === 0 ? (
                <p className="hierarchy-hint">
                  No attendance has been recorded for this class yet.
                </p>
              ) : (
                <>
                  <AttendanceTrend months={detail.monthly} />

                  <h3 className="hierarchy-subheading">Every lecture</h3>
                  <LectureStrip sessions={detail.sessions} />

                  <ul className="hierarchy-items">
                    {detail.sessions.map((lecture) => (
                      <li key={lecture.date}>
                        <span className="user-identity">
                          <span className="user-name">{lecture.date}</span>
                        </span>
                        <span className={`lecture-status lecture-status--${lecture.status}`}>
                          {lecture.status === 'present' ? 'Present' : 'Absent'}
                        </span>
                      </li>
                    ))}
                  </ul>
                </>
              )}
            </>
          ) : null}
        </section>
      ) : null}
    </div>
  )
}

export default AttendanceOverview
