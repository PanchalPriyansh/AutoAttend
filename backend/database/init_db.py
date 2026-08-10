"""Idempotent MongoDB collection/validator/index setup for AutoAttend.

Not run automatically at app startup -- invoked explicitly via the
`flask init-db` CLI command (see app.py) so a temporarily unreachable
MongoDB never prevents the Flask process itself from starting.
"""

from database.schema import COLLECTIONS


def init_database(db):
    """Create/update every collection in COLLECTIONS with its validator,
    then ensure every declared index exists. Safe to call repeatedly:
    `create_collection` is skipped in favor of `collMod` for collections
    that already exist, and `create_index` is a no-op for an index that
    already exists with the same keys/options.

    Collections are created (with their validator attached) in a separate
    pass before any indexes are created, since creating an index against a
    not-yet-existing collection would implicitly create it with no
    validator attached.
    """
    existing = set(db.list_collection_names())

    for spec in COLLECTIONS:
        name = spec["name"]
        if name not in existing:
            db.create_collection(name, validator=spec["validator"], validationLevel="strict")
        else:
            db.command("collMod", name, validator=spec["validator"], validationLevel="strict")

    for spec in COLLECTIONS:
        collection = db[spec["name"]]
        for index_spec in spec["indexes"]:
            # NOTE: if an index's key shape changes later while keeping the
            # same `name`, MongoDB raises IndexOptionsConflict rather than
            # updating it in place -- a manual drop_index would be needed.
            collection.create_index(
                index_spec["keys"],
                unique=index_spec["unique"],
                name=index_spec["name"],
            )

    return {"collections": [spec["name"] for spec in COLLECTIONS]}
