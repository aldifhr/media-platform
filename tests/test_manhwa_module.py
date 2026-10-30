from pathlib import Path

from modules.manhwa import MODULE_NAME, SOURCES

ROOT = Path(__file__).resolve().parents[1]


def test_manhwa_module_meta():
    assert MODULE_NAME == "manhwa"
    assert SOURCES == ["ikiru", "shinigami"]


def test_manhwa_migrations_present():
    migs = list((ROOT / "migrations" / "manhwa").glob("*.sql"))
    assert len(migs) >= 80, f"expected manhwa schema history, found {len(migs)}"
