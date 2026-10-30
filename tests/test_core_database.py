from core.database import list_migrations


def test_list_migrations_sorted_and_sql_only(tmp_path):
    (tmp_path / "002_b.sql").write_text("")
    (tmp_path / "001_a.sql").write_text("")
    (tmp_path / "notes.txt").write_text("")
    (tmp_path / "003_c.SQL").write_text("")  # wrong case, ignored
    got = [p.name for p in list_migrations(tmp_path)]
    assert got == ["001_a.sql", "002_b.sql"]
