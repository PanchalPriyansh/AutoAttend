"""Collection names, `$jsonSchema` validators, and index definitions for
AutoAttend's academic hierarchy (Institute -> Department -> Semester ->
Course -> Class) plus `users`, `class_enrollments`, and `face_encodings`.

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
FACE_ENCODINGS = "face_encodings"
ATTENDANCE_SESSIONS = "attendance_sessions"
ATTENDANCE_RECORDS = "attendance_records"

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

FACE_ENCODINGS_VALIDATOR = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": [
            "student_id",
            "encoding",
            "model",
            "source",
            "created_at",
            "created_by",
        ],
        "properties": {
            "student_id": {"bsonType": "objectId"},
            # One document per captured sample, never an array of samples on
            # the student: a per-student array would grow unbounded and would
            # make deleting a single sample a rewrite of the whole document.
            #
            # 128 is the descriptor length produced by `face_recognition`;
            # `recognition/encoder.py` mirrors it as ENCODING_LENGTH. Pinning
            # both ends of the range means a truncated or concatenated vector
            # is rejected by the database, not just by application code.
            "encoding": {
                "bsonType": "array",
                "minItems": 128,
                "maxItems": 128,
                "items": {"bsonType": "double"},
            },
            # Which detector produced the vector. Encodings from different
            # models are not comparable, so storing this is what makes a
            # future model change a detectable migration rather than a silent
            # accuracy regression.
            "model": {"bsonType": "string", "minLength": 1},
            "source": {"enum": ["upload", "camera"]},
            "created_at": {"bsonType": "date"},
            "created_by": {"bsonType": "objectId"},
        },
    }
}

# NOTE: the source image is deliberately absent from the shape above. Only
# the derived encoding is persisted -- there is no field here for a photo,
# a thumbnail, or a file path, and none should be added.

ATTENDANCE_SESSIONS_VALIDATOR = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": ["class_id", "date", "source", "taken_by", "created_at", "updated_at"],
        "properties": {
            "class_id": {"bsonType": "objectId"},
            # Normalised to UTC midnight by attendance/validators.py, so one
            # calendar day is exactly one value. Without that normalisation
            # two captures of the same lecture would carry different times
            # and uniq_class_id_date below would let both through.
            "date": {"bsonType": "date"},
            # How the session was produced. "manual" covers a roster marked
            # without a capture; it is provenance for review, never a claim
            # about how accurate the result is.
            "source": {"enum": ["photo", "video", "manual"]},
            "taken_by": {"bsonType": "objectId"},
            "created_at": {"bsonType": "date"},
            "updated_at": {"bsonType": "date"},
        },
    }
}

ATTENDANCE_RECORDS_VALIDATOR = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": [
            "session_id",
            "class_id",
            "student_id",
            "status",
            "marked_by",
            "created_at",
        ],
        "properties": {
            "session_id": {"bsonType": "objectId"},
            # Denormalised from the parent session so a later feature can ask
            # "this student's attendance in this class" without joining
            # sessions first. It must always equal the session's class_id;
            # attendance/service.py copies it from there rather than from
            # anything the client sent.
            "class_id": {"bsonType": "objectId"},
            "student_id": {"bsonType": "objectId"},
            # Absent students are stored explicitly rather than inferred from
            # a missing row: "absent" and "attendance was never taken" are
            # different facts and later features must be able to tell them
            # apart.
            "status": {"enum": ["present", "absent"]},
            # Whether the recognition pipeline proposed this status or a
            # human set it. Audit provenance for reviewing how well matching
            # performs -- never an authorization signal.
            "marked_by": {"enum": ["recognition", "faculty"]},
            "created_at": {"bsonType": "date"},
        },
    }
}

# NOTE: the captured photo or video is deliberately absent from both shapes
# above, for the same reason it is absent from face_encodings -- and more
# so, since a classroom frame holds many people at once. Only the reviewed
# present/absent decision is persisted.

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
    {
        "name": FACE_ENCODINGS,
        "validator": FACE_ENCODINGS_VALIDATOR,
        "indexes": [
            # Non-unique: multiple samples per student is the point -- several
            # angles/lighting conditions produce better matching than one.
            {"keys": [("student_id", 1)], "unique": False, "name": "idx_student_id"},
        ],
    },
    {
        "name": ATTENDANCE_SESSIONS,
        "validator": ATTENDANCE_SESSIONS_VALIDATOR,
        "indexes": [
            # One lecture is one session. Enforced here rather than only in
            # application code so two concurrent saves cannot both succeed.
            {
                "keys": [("class_id", 1), ("date", 1)],
                "unique": True,
                "name": "uniq_class_id_date",
            },
        ],
    },
    {
        "name": ATTENDANCE_RECORDS,
        "validator": ATTENDANCE_RECORDS_VALIDATOR,
        "indexes": [
            {
                "keys": [("session_id", 1), ("student_id", 1)],
                "unique": True,
                "name": "uniq_session_id_student_id",
            },
            # For the per-student queries the dashboard, risk model, and
            # low-attendance notifications will each make.
            {
                "keys": [("student_id", 1), ("class_id", 1)],
                "unique": False,
                "name": "idx_student_id_class_id",
            },
        ],
    },
]
