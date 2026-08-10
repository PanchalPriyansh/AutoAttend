"""Collection names, `$jsonSchema` validators, and index definitions for
AutoAttend's academic hierarchy (Institute -> Department -> Semester ->
Course -> Class) plus `users` and `class_enrollments`.

Pure data -- no pymongo or Flask imports, no side effects. `init_db.py`
consumes `COLLECTIONS` to create/update collections and indexes. Later
features should import the name constants below rather than hardcoding
collection name strings.

Notes for insert code written by later features:
  - `created_at`/`updated_at`/date fields must be real BSON dates (Python
    `datetime` objects), not ISO strings -- `bsonType: "date"` requires it.
  - `is_active` has no schema-level default; the inserting code must set it.
  - `email` is not lowercased or deduplicated case-insensitively by the
    schema; that is the Authentication feature's responsibility.
"""

USERS = "users"
INSTITUTES = "institutes"
DEPARTMENTS = "departments"
SEMESTERS = "semesters"
COURSES = "courses"
CLASSES = "classes"
CLASS_ENROLLMENTS = "class_enrollments"

USERS_VALIDATOR = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": [
            "name",
            "email",
            "password_hash",
            "role",
            "is_active",
            "created_at",
            "updated_at",
        ],
        "properties": {
            "name": {"bsonType": "string", "minLength": 1},
            "email": {"bsonType": "string", "pattern": "^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$"},
            "password_hash": {"bsonType": "string", "minLength": 1},
            "role": {"enum": ["admin", "faculty", "student"]},
            "institute_id": {"bsonType": ["objectId", "null"]},
            "is_active": {"bsonType": "bool"},
            "created_at": {"bsonType": "date"},
            "updated_at": {"bsonType": "date"},
        },
    }
}

INSTITUTES_VALIDATOR = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": ["name", "code", "created_at"],
        "properties": {
            "name": {"bsonType": "string", "minLength": 1},
            "code": {"bsonType": "string", "minLength": 1},
            "created_at": {"bsonType": "date"},
        },
    }
}

DEPARTMENTS_VALIDATOR = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": ["institute_id", "name", "code", "created_at"],
        "properties": {
            "institute_id": {"bsonType": "objectId"},
            "name": {"bsonType": "string", "minLength": 1},
            "code": {"bsonType": "string", "minLength": 1},
            "created_at": {"bsonType": "date"},
        },
    }
}

SEMESTERS_VALIDATOR = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": ["department_id", "name", "start_date", "end_date", "created_at"],
        "properties": {
            "department_id": {"bsonType": "objectId"},
            "name": {"bsonType": "string", "minLength": 1},
            "start_date": {"bsonType": "date"},
            "end_date": {"bsonType": "date"},
            "created_at": {"bsonType": "date"},
        },
    }
}

COURSES_VALIDATOR = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": ["semester_id", "name", "code", "created_at"],
        "properties": {
            "semester_id": {"bsonType": "objectId"},
            "name": {"bsonType": "string", "minLength": 1},
            "code": {"bsonType": "string", "minLength": 1},
            "created_at": {"bsonType": "date"},
        },
    }
}

CLASSES_VALIDATOR = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": ["course_id", "name", "created_at"],
        "properties": {
            "course_id": {"bsonType": "objectId"},
            "name": {"bsonType": "string", "minLength": 1},
            "faculty_id": {"bsonType": ["objectId", "null"]},
            "created_at": {"bsonType": "date"},
        },
    }
}

CLASS_ENROLLMENTS_VALIDATOR = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": ["class_id", "student_id", "enrolled_at"],
        "properties": {
            "class_id": {"bsonType": "objectId"},
            "student_id": {"bsonType": "objectId"},
            "enrolled_at": {"bsonType": "date"},
        },
    }
}

COLLECTIONS = [
    {
        "name": USERS,
        "validator": USERS_VALIDATOR,
        "indexes": [
            {"keys": [("email", 1)], "unique": True, "name": "uniq_email"},
            {"keys": [("role", 1)], "unique": False, "name": "idx_role"},
        ],
    },
    {
        "name": INSTITUTES,
        "validator": INSTITUTES_VALIDATOR,
        "indexes": [
            {"keys": [("code", 1)], "unique": True, "name": "uniq_code"},
        ],
    },
    {
        "name": DEPARTMENTS,
        "validator": DEPARTMENTS_VALIDATOR,
        "indexes": [
            {
                "keys": [("institute_id", 1), ("code", 1)],
                "unique": True,
                "name": "uniq_institute_id_code",
            },
        ],
    },
    {
        "name": SEMESTERS,
        "validator": SEMESTERS_VALIDATOR,
        "indexes": [
            {
                "keys": [("department_id", 1), ("name", 1)],
                "unique": True,
                "name": "uniq_department_id_name",
            },
        ],
    },
    {
        "name": COURSES,
        "validator": COURSES_VALIDATOR,
        "indexes": [
            {
                "keys": [("semester_id", 1), ("code", 1)],
                "unique": True,
                "name": "uniq_semester_id_code",
            },
        ],
    },
    {
        "name": CLASSES,
        "validator": CLASSES_VALIDATOR,
        "indexes": [
            {
                "keys": [("course_id", 1), ("name", 1)],
                "unique": True,
                "name": "uniq_course_id_name",
            },
            {"keys": [("faculty_id", 1)], "unique": False, "name": "idx_faculty_id"},
        ],
    },
    {
        "name": CLASS_ENROLLMENTS,
        "validator": CLASS_ENROLLMENTS_VALIDATOR,
        "indexes": [
            {
                "keys": [("class_id", 1), ("student_id", 1)],
                "unique": True,
                "name": "uniq_class_id_student_id",
            },
            {"keys": [("student_id", 1)], "unique": False, "name": "idx_student_id"},
        ],
    },
]
