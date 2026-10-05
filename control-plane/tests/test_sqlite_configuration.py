from sqlalchemy import text

from squash_control_plane.database import Database


def test_file_sqlite_uses_reliable_single_instance_pragmas(tmp_path) -> None:
    path = (tmp_path / "squash-rmm.db").as_posix()
    database = Database(f"sqlite+pysqlite:///{path}")

    with database.engine.connect() as connection:
        foreign_keys = connection.execute(text("PRAGMA foreign_keys")).scalar_one()
        busy_timeout = connection.execute(text("PRAGMA busy_timeout")).scalar_one()
        journal_mode = connection.execute(text("PRAGMA journal_mode")).scalar_one()
        synchronous = connection.execute(text("PRAGMA synchronous")).scalar_one()

    assert foreign_keys == 1
    assert busy_timeout == 30_000
    assert journal_mode == "wal"
    assert synchronous == 1
    database.close()
