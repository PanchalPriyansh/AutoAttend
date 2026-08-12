"""Descriptors for the five academic hierarchy levels.

`CLAUDE.md` fixes the structure as a strict top-down chain:

    Institute -> Department -> Semester -> Course -> Class -> Student

The five levels differ only in which collection they live in, which
parent scopes them, which children block their deletion, and which
fields they carry. Capturing exactly those differences here lets
service.py implement list/create/update/delete once instead of five
times, while routes/academic.py stays explicit -- one readable handler
per endpoint.

Deliberately *not* generalized here: the semester `start_date`/`end_date`
pair. Only one level has date fields, so encoding a field-type system in
this table would buy nothing and push the module toward the generic
resource framework the feature spec rules out. Those two fields are
handled explicitly in validators.py and the semester route handlers.

Collection names come from database/schema.py -- never spelled as string
literals here.
"""

from dataclasses import dataclass, field
from typing import Optional

from database.schema import (
    CLASS_ENROLLMENTS,
    CLASSES,
    COURSES,
    DEPARTMENTS,
    INSTITUTES,
    SEMESTERS,
)


@dataclass(frozen=True)
class ParentRef:
    """The level above: `field` is the ObjectId reference stored on this
    level's documents, and `collection` is where that id must resolve.
    """

    collection: str
    field: str
    label: str


@dataclass(frozen=True)
class ChildRef:
    """The level below: documents in `collection` pointing back through
    `field` block this level's deletion. `singular`/`plural` name them in
    the resulting error message, which quotes the blocking count.
    """

    collection: str
    field: str
    singular: str
    plural: str


@dataclass(frozen=True)
class Level:
    name: str
    plural: str
    collection: str
    parent: Optional[ParentRef]
    child: Optional[ChildRef]
    # Client-settable string fields, in the order they should be serialized.
    fields: tuple = ()
    # Fields returned to clients but never accepted from them.
    read_only_fields: tuple = ()
    # Fields written on create with a fixed value, never client-supplied.
    create_defaults: dict = field(default_factory=dict)
    # Date fields (BSON dates), serialized as ISO strings.
    date_fields: tuple = ()


INSTITUTE = Level(
    name="institute",
    plural="institutes",
    collection=INSTITUTES,
    parent=None,
    child=ChildRef(
        collection=DEPARTMENTS, field="institute_id",
        singular="department", plural="departments",
    ),
    fields=("name", "code"),
)

DEPARTMENT = Level(
    name="department",
    plural="departments",
    collection=DEPARTMENTS,
    parent=ParentRef(collection=INSTITUTES, field="institute_id", label="Institute"),
    child=ChildRef(
        collection=SEMESTERS, field="department_id",
        singular="semester", plural="semesters",
    ),
    fields=("name", "code"),
)

SEMESTER = Level(
    name="semester",
    plural="semesters",
    collection=SEMESTERS,
    parent=ParentRef(collection=DEPARTMENTS, field="department_id", label="Department"),
    child=ChildRef(
        collection=COURSES, field="semester_id",
        singular="course", plural="courses",
    ),
    fields=("name",),
    date_fields=("start_date", "end_date"),
)

COURSE = Level(
    name="course",
    plural="courses",
    collection=COURSES,
    parent=ParentRef(collection=SEMESTERS, field="semester_id", label="Semester"),
    child=ChildRef(
        collection=CLASSES, field="course_id",
        singular="class", plural="classes",
    ),
    fields=("name", "code"),
)

CLASS = Level(
    name="class",
    plural="classes",
    collection=CLASSES,
    parent=ParentRef(collection=COURSES, field="course_id", label="Course"),
    # Enrollments are the "Student" tier of the hierarchy. This feature only
    # reads them to block a delete -- creating them belongs to a later spec.
    child=ChildRef(
        collection=CLASS_ENROLLMENTS, field="class_id",
        singular="enrollment", plural="enrollments",
    ),
    fields=("name",),
    # Faculty assignment is out of scope for this feature: faculty_id is
    # written as null on create and never updated here.
    read_only_fields=("faculty_id",),
    create_defaults={"faculty_id": None},
)
