from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_top_level_layout():
    for d in ["apps", "modules", "core", "migrations", "packages", "tests", "deploy", "docs"]:
        assert (ROOT / d).is_dir(), f"missing top-level dir: {d}"


def test_apps_present():
    for d in ["api", "discord-bot", "worker", "frontend", "ops"]:
        assert (ROOT / "apps" / d).is_dir(), f"missing app: {d}"


def test_core_present():
    for d in [
        "discord",
        "notifications",
        "scheduler",
        "queue",
        "database",
        "cache",
        "logging",
        "config",
        "plugins",
    ]:
        assert (ROOT / "core" / d).is_dir(), f"missing core: {d}"


def test_module_sources_present():
    assert (ROOT / "modules/manhwa/sources/ikiru").is_dir()
    assert (ROOT / "modules/manhwa/sources/shinigami").is_dir()
    assert (ROOT / "modules/media/sources/pahe").is_dir()
