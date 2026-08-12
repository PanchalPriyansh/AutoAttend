import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { classes, courses, departments, institutes, semesters } from '../../api/academic'
import HierarchyLevel from '../../components/admin/HierarchyLevel'

const NAME_AND_CODE = [
  { name: 'name', label: 'Name' },
  { name: 'code', label: 'Code' },
]

const NAME_ONLY = [{ name: 'name', label: 'Name' }]

const SEMESTER_FIELDS = [
  { name: 'name', label: 'Name' },
  { name: 'start_date', label: 'Start date', type: 'date' },
  { name: 'end_date', label: 'End date', type: 'date' },
]

const EMPTY_SELECTION = {
  institute: null,
  department: null,
  semester: null,
  course: null,
  class: null,
}

/**
 * Loads and mutates one level, scoped to its parent. Passing a null
 * `parentId` for a scoped level empties it, which is what makes an
 * ancestor selection change cascade down and clear every level below it.
 */
function useHierarchyLevel(resource, parentId) {
  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [reloadToken, setReloadToken] = useState(0)

  const scoped = resource.parentParam !== null
  const ready = !scoped || Boolean(parentId)

  useEffect(() => {
    if (!ready) {
      setItems([])
      setError('')
      setLoading(false)
      return undefined
    }

    let cancelled = false
    setLoading(true)
    setError('')

    resource
      .list(parentId)
      .then((rows) => {
        if (!cancelled) setItems(rows)
      })
      .catch((err) => {
        if (cancelled) return
        setError(err.message)
        setItems([])
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [resource, parentId, ready, reloadToken])

  const refresh = useCallback(() => setReloadToken((token) => token + 1), [])

  // Mutations deliberately do not catch: HierarchyLevel surfaces the
  // backend's message inline.
  const create = useCallback(
    async (values) => {
      await resource.create(parentId, values)
      refresh()
    },
    [resource, parentId, refresh],
  )

  const update = useCallback(
    async (id, values) => {
      await resource.update(id, values)
      refresh()
    },
    [resource, refresh],
  )

  const remove = useCallback(
    async (id) => {
      await resource.remove(id)
      refresh()
    },
    [resource, refresh],
  )

  return { items, loading, error, create, update, remove }
}

function AcademicHierarchy() {
  const [selection, setSelection] = useState(EMPTY_SELECTION)

  const instituteLevel = useHierarchyLevel(institutes, null)
  const departmentLevel = useHierarchyLevel(departments, selection.institute)
  const semesterLevel = useHierarchyLevel(semesters, selection.department)
  const courseLevel = useHierarchyLevel(courses, selection.semester)
  const classLevel = useHierarchyLevel(classes, selection.course)

  /** Selecting at one level clears every level below it. */
  const select = useCallback((level, id) => {
    setSelection((current) => {
      const next = { ...current }
      let below = false
      for (const key of Object.keys(EMPTY_SELECTION)) {
        if (key === level) {
          next[key] = current[key] === id ? null : id
          below = true
        } else if (below) {
          next[key] = null
        }
      }
      return next
    })
  }, [])

  /** A deleted item must not stay selected and keep its children mounted. */
  const removeAndDeselect = useCallback(
    (level, remove) => async (id) => {
      await remove(id)
      setSelection((current) => (current[level] === id ? { ...current, [level]: null } : current))
    },
    [],
  )

  return (
    <div>
      <h1>Academic Hierarchy</h1>
      <p>
        <Link to="/admin">← Back to Admin Portal</Link>
      </p>
      <p className="hierarchy-hint">
        Institute → Department → Semester → Course → Class. Select an item to manage the level
        beneath it.
      </p>

      <HierarchyLevel
        title="Institutes"
        noun="institute"
        fields={NAME_AND_CODE}
        items={instituteLevel.items}
        loading={instituteLevel.loading}
        error={instituteLevel.error}
        selectedId={selection.institute}
        onSelect={(id) => select('institute', id)}
        onCreate={instituteLevel.create}
        onUpdate={instituteLevel.update}
        onDelete={removeAndDeselect('institute', instituteLevel.remove)}
      />

      <HierarchyLevel
        title="Departments"
        noun="department"
        fields={NAME_AND_CODE}
        items={departmentLevel.items}
        loading={departmentLevel.loading}
        error={departmentLevel.error}
        disabled={!selection.institute}
        disabledHint="Select an institute to manage its departments."
        selectedId={selection.department}
        onSelect={(id) => select('department', id)}
        onCreate={departmentLevel.create}
        onUpdate={departmentLevel.update}
        onDelete={removeAndDeselect('department', departmentLevel.remove)}
      />

      <HierarchyLevel
        title="Semesters"
        noun="semester"
        fields={SEMESTER_FIELDS}
        items={semesterLevel.items}
        loading={semesterLevel.loading}
        error={semesterLevel.error}
        disabled={!selection.department}
        disabledHint="Select a department to manage its semesters."
        selectedId={selection.semester}
        onSelect={(id) => select('semester', id)}
        onCreate={semesterLevel.create}
        onUpdate={semesterLevel.update}
        onDelete={removeAndDeselect('semester', semesterLevel.remove)}
      />

      <HierarchyLevel
        title="Courses"
        noun="course"
        fields={NAME_AND_CODE}
        items={courseLevel.items}
        loading={courseLevel.loading}
        error={courseLevel.error}
        disabled={!selection.semester}
        disabledHint="Select a semester to manage its courses."
        selectedId={selection.course}
        onSelect={(id) => select('course', id)}
        onCreate={courseLevel.create}
        onUpdate={courseLevel.update}
        onDelete={removeAndDeselect('course', courseLevel.remove)}
      />

      <HierarchyLevel
        title="Classes"
        noun="class"
        fields={NAME_ONLY}
        items={classLevel.items}
        loading={classLevel.loading}
        error={classLevel.error}
        disabled={!selection.course}
        disabledHint="Select a course to manage its classes."
        selectedId={selection.class}
        onSelect={(id) => select('class', id)}
        onCreate={classLevel.create}
        onUpdate={classLevel.update}
        onDelete={removeAndDeselect('class', classLevel.remove)}
      />
    </div>
  )
}

export default AcademicHierarchy
