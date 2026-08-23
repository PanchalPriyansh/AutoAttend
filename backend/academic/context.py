"""Resolving a class's place in the academic hierarchy to readable names.

Separate from service.py because that module owns the generic CRUD over
the five levels, driven by levels.py; this answers one specific question
that the levels machinery does not: given some classes, what are the
course, semester, department, and institute they sit under called?

It lives in `academic` rather than in the features that call it because
the question is about the hierarchy, not about attendance. Both the
faculty class list (attendance/service.py::list_assigned_classes) and the
student attendance overview (attendance/summary.py) need a class the
reader can recognise, and one definition is what keeps them from
labelling the same class two different ways.

Pure reads -- no Flask objects, nothing written.
"""

from database.schema import COURSES, DEPARTMENTS, INSTITUTES, SEMESTERS


def class_hierarchy_context(db, classes):
    """Resolve each class's course/semester/department/institute names.

    Four batched `$in` lookups, one per level, rather than walking the
    chain per class: a faculty member holding eight classes would
    otherwise cost thirty-two queries to render one dropdown.

    A missing parent leaves the name as None instead of raising. The
    hierarchy blocks deletes while children exist, so this should be
    unreachable; one orphaned row should not take down the whole class
    list if it ever happens.
    """
    # (name in the result, collection to read, field pointing one level
    # further up). The chain starts at each class's course_id below.
    levels = [
        ("course", COURSES, "semester_id"),
        ("semester", SEMESTERS, "department_id"),
        ("department", DEPARTMENTS, "institute_id"),
        ("institute", INSTITUTES, None),
    ]

    # Maps each class to the id it currently points at, one level up per
    # iteration: first its course, then that course's semester, and so on.
    parent_ids = {
        document["_id"]: document.get("course_id") for document in classes
    }
    context = {document["_id"]: {} for document in classes}

    for name, collection, next_field in levels:
        wanted = [value for value in parent_ids.values() if value is not None]
        documents = (
            {
                document["_id"]: document
                for document in db[collection].find({"_id": {"$in": wanted}})
            }
            if wanted
            else {}
        )

        next_parent_ids = {}
        for class_id, parent_id in parent_ids.items():
            parent = documents.get(parent_id)
            context[class_id][name] = parent.get("name") if parent else None
            next_parent_ids[class_id] = (
                parent.get(next_field) if parent and next_field else None
            )

        parent_ids = next_parent_ids

    return context
