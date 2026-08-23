/**
 * One student with a present/absent toggle.
 *
 * Shared by the capture review and the history editor: both show the same
 * control over the same domain object, and a second copy would drift into
 * two screens that disagree about what marking somebody present looks like.
 *
 * The status shown is always the working value, so a flipped row reads back
 * what the faculty member just chose rather than what was proposed or stored.
 */
function StudentStatusRow({ student, detail, status, disabled, onToggle }) {
  const present = status === 'present'

  return (
    <li className={student.is_active ? undefined : 'is-inactive'}>
      <span className="user-identity">
        <span className="user-name">{student.name}</span>
        <span className="hierarchy-hint">
          {student.email}
          {!student.is_active && ' · Deactivated'}
          {detail && ` · ${detail}`}
        </span>
      </span>
      <button type="button" onClick={() => onToggle(student.id)} disabled={disabled}>
        {present ? 'Present' : 'Absent'} — mark {present ? 'absent' : 'present'}
      </button>
    </li>
  )
}

export default StudentStatusRow
