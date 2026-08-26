/**
 * One student with a present/absent toggle.
 *
 * Shared by the capture review and the history editor: both show the same
 * control over the same domain object, and a second copy would drift into
 * two screens that disagree about what marking somebody present looks like.
 *
 * The status shown is always the working value, so a flipped row reads back
 * what the faculty member just chose rather than what was proposed or stored.
 *
 * Styled by styles/faculty-attendance.css (.fa-row*), not by the shared
 * scaffolding classes it used to borrow -- .user-identity and .user-name are
 * still rendered by three admin screens, so this row stopped using them
 * rather than having them restyled underneath those screens.
 *
 * `detail` is rendered as a child rather than interpolated into a string, so
 * a caller can hand it a marked-up node -- which is how the capture screen
 * makes a weak match the one row that stands out. A plain string still works
 * and is what the history screen passes.
 */
function StudentStatusRow({ student, detail, status, disabled, onToggle }) {
  const present = status === 'present'

  return (
    <li className={`fa-row${student.is_active ? '' : ' fa-row--inactive'}`}>
      <span className="fa-row-identity">
        <span className="fa-row-name">{student.name}</span>
        <span className="fa-row-meta">
          <span className="fa-row-email">{student.email}</span>
          {!student.is_active && <span className="pill fa-flag fa-flag--muted">Deactivated</span>}
          {detail && <span className="fa-row-detail">{detail}</span>}
        </span>
      </span>
      <button
        type="button"
        className={`btn fa-toggle ${present ? 'fa-toggle--present' : 'fa-toggle--absent'}`}
        onClick={() => onToggle(student.id)}
        disabled={disabled}
      >
        {present ? 'Present' : 'Absent'} — mark {present ? 'absent' : 'present'}
      </button>
    </li>
  )
}

export default StudentStatusRow
