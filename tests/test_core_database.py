from core.database import _async_url, list_migrations


def test_list_migrations_sorted_and_sql_only(tmp_path):
    (tmp_path / "002_b.sql").write_text("")
    (tmp_path / "001_a.sql").write_text("")
    (tmp_path / "notes.txt").write_text("")
    (tmp_path / "003_c.SQL").write_text("")  # wrong case, ignored
    got = [p.name for p in list_migrations(tmp_path)]
    assert got == ["001_a.sql", "002_b.sql"]


def test_async_url_normalizes_to_asyncpg():
    assert _async_url("postgresql://u:p@h:5432/db") == "postgresql+asyncpg://u:p@h:5432/db"
    assert _async_url("postgresql+psycopg2://u:p@h/db") == "postgresql+asyncpg://u:p@h/db"
    # already-async and non-postgres URLs pass through unchanged
    assert _async_url("postgresql+asyncpg://u:p@h/db") == "postgresql+asyncpg://u:p@h/db"
    assert _async_url("sqlite+aiosqlite:///x.db") == "sqlite+aiosqlite:///x.db"
