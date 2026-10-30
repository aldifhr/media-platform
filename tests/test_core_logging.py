import logging

from core.logging import get_logger


def test_structured_logger_accepts_fields(caplog):
    # Regression: stdlib Logger.debug/warn would TypeError on arbitrary kwargs.
    log = get_logger("test:log")
    with caplog.at_level(logging.INFO):
        log.warn("hello", key="k", err="e")
        log.info("world", count=3)
    msgs = [r.getMessage() for r in caplog.records]
    assert "hello" in msgs
    assert "world" in msgs
    # fields ride on the record without crashing
    rec = next(r for r in caplog.records if r.getMessage() == "hello")
    assert getattr(rec, "fields", {}) == {"key": "k", "err": "e"}
