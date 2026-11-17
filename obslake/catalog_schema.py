CATALOG_SCHEMA_VERSION = 1

_DDL = (
    "CREATE TABLE IF NOT EXISTS batches (batch_id TEXT PRIMARY KEY, digest TEXT NOT NULL, descriptor TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS snapshots (snapshot_id TEXT PRIMARY KEY)",
    """CREATE TABLE IF NOT EXISTS snapshot_members (
        snapshot_id TEXT NOT NULL REFERENCES snapshots(snapshot_id),
        ordinal INTEGER NOT NULL, batch_id TEXT NOT NULL REFERENCES batches(batch_id),
        digest TEXT NOT NULL, descriptor TEXT NOT NULL,
        PRIMARY KEY(snapshot_id,ordinal), UNIQUE(snapshot_id,batch_id))""",
    """CREATE TABLE IF NOT EXISTS current_snapshot (
        singleton INTEGER PRIMARY KEY CHECK(singleton=1),
        snapshot_id TEXT NOT NULL REFERENCES snapshots(snapshot_id))""",
)


def check_catalog_schema(connection):
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    if version != CATALOG_SCHEMA_VERSION:
        raise ValueError("unsupported catalog schema version: " + str(version))


def initialize_catalog_schema(connection):
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    if version not in (0, CATALOG_SCHEMA_VERSION):
        raise ValueError("unsupported catalog schema version: " + str(version))
    if version == 0:
        tables = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()
        if tables:
            raise ValueError("unversioned nonempty database")
    connection.execute("BEGIN IMMEDIATE")
    try:
        for statement in _DDL:
            connection.execute(statement)
        connection.execute("PRAGMA user_version=1")
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    check_catalog_schema(connection)
