from __future__ import annotations

import json
import os

from modules.manhwa.config import settings
from modules.manhwa.logger import get_logger

logger = get_logger("tasks.queue")

QUEUE_KEY = "beag:tasks"
DLQ_KEY = "beag:tasks:dlq"
CRON_QUEUE_KEY = "beag:cron"
CRON_QUEUE_SET = "beag:cron:set"
# processing lists for crash-safety (BRPOPLPUSH) — job stays visible until ack
QUEUE_PROCESSING_KEY = "beag:tasks:processing"
CRON_PROCESSING_KEY = "beag:cron:processing"
# Per-job start timestamps for the processing list, so a periodic reaper can
# tell a legitimately-running job from one whose worker died mid-job.
CRON_PROCESSING_TS = "beag:cron:processing:ts"

_redis = None


def _get_redis():
    global _redis
    if _redis is None:
        if not getattr(settings, "REDIS_URL", ""):
            # local dev without Redis (REDIS_URL="" in .env) -> fail fast, caller will fallback to DB/inline
            raise RuntimeError("REDIS_URL empty — Redis disabled for local dev")
        import redis
        _redis = redis.Redis.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=0.2,
            socket_timeout=0.2,
            socket_keepalive=True,
            retry_on_timeout=False,
        )
    return _redis


def enqueue_cron(action: str, source: str = "", title: str = "") -> None:
    """Push a cron pipeline job onto the Redis cron queue. Dedup by payload — atomic without RPOP compensation.

    ORDERING: this LPUSHes and the worker BRPOPLPUSHes, which together are
    FIFO. It used to RPUSH, and since BRPOPLPUSH takes from the same end, the
    queue behaved as a LIFO stack: every newly scheduled job jumped ahead of
    the ones already waiting. With `update` enqueued every 120s and three
    rss-fetch jobs every 300s, `update` was buried by each new arrival and
    never ran — a 7-hour notification outage with the job sitting in the queue
    the whole time. Oldest-first is the only ordering under which a
    repeatedly-enqueued job is guaranteed to reach the front.
    """
    payload: dict = {"action": action}
    if source:
        payload["source"] = source
    if title:
        payload["title"] = title
    payload_json = json.dumps(payload, sort_keys=True)
    try:
        r = _get_redis()
        # Atomic SADD+LPUSH+EXPIRE via Lua — no outer RPOP needed (RPOP would pop чужой tail on race)
        # EXPIRE 86400 is orphan safety net for CRON_QUEUE_SET only (queue dedup), NOT dispatch_history ledger (30d in retention.py)
        _lua = """
if redis.call('SADD', KEYS[1], ARGV[1]) == 1 then
  redis.call('LPUSH', KEYS[2], ARGV[1])
  redis.call('EXPIRE', KEYS[1], 86400)
  return 1
else
  return 0
end
"""
        try:
            # redis-py eval signature: eval(script, numkeys, *keys_and_args)
            added = r.eval(_lua, 2, CRON_QUEUE_SET, CRON_QUEUE_KEY, payload_json)
            # fakeredis / StrictRedis may return int directly
            added = int(added) if added is not None else 0
        except AttributeError:
            # FakeRedis / mock without EVAL — fallback to SADD-then-RPUSH (no RPOP)
            added = None
        except Exception as e:
            # real redis EVAL unknown / NOSCRIPT / permission → fallback; otherwise bubble to outer except (redis down)
            msg = str(e).lower()
            if "unknown" in msg or "eval" in msg or "nosript" in msg or "not supported" in msg:
                added = None
            else:
                raise
        if added is None:
            # Fallback path: SADD first; only RPUSH if we won. No compensation RPOP.
            added = r.sadd(CRON_QUEUE_SET, payload_json)
            if added == 0:
                logger.info("cron job already in queue (dedup set), skipping", action=action, source=source)
                return
            try:
                if hasattr(r, "pipeline"):
                    pipe = r.pipeline(transaction=True)
                    pipe.lpush(CRON_QUEUE_KEY, payload_json)
                    # expire may not exist on FakeRedis — best-effort
                    try:
                        pipe.expire(CRON_QUEUE_SET, 86400)
                    except Exception:
                        pass
                    pipe.execute()
                else:
                    r.lpush(CRON_QUEUE_KEY, payload_json)
                    if hasattr(r, "expire"):
                        try:
                            r.expire(CRON_QUEUE_SET, 86400)
                        except Exception:
                            pass
            except Exception as pe:
                # rollback SET — next try must be able to re-enqueue
                try:
                    r.srem(CRON_QUEUE_SET, payload_json)
                except Exception:
                    pass
                raise pe
        if added == 0:
            logger.info("cron job already in queue (dedup set), skipping", action=action, source=source)
            return
        logger.info("enqueued cron job", action=action, source=source)
    except Exception as e:
        _role = (os.environ.get("ROLE") or "api").lower()
        if _role == "cron":
            logger.warn("enqueue cron failed (redis down), running inline", err=str(e)[:120], action=action)
            from modules.manhwa.tasks.lifecycle import run_cron_inline
            run_cron_inline(action)
        else:
            logger.warn("enqueue cron failed (redis down), API mode — returning error", err=str(e)[:120], action=action)
            raise


def _cleanup_orphaned_set_entries() -> None:
    """Remove SET entries no longer present in CRON_QUEUE_KEY (phantom dedup cleanup)."""
    try:
        r = _get_redis()
        queue_members = set(r.lrange(CRON_QUEUE_KEY, 0, -1) or [])
        set_members = r.smembers(CRON_QUEUE_SET)
        orphaned = set_members - queue_members
        if orphaned:
            r.srem(CRON_QUEUE_SET, *orphaned)
            logger.info("cleaned orphaned dedup set entries", count=len(orphaned))
    except Exception as e:
        logger.warn("orphan cleanup failed", err=str(e)[:120])


def enqueue_add(title: str, url: str, interaction: dict | None = None) -> None:
    """Push an add-to-whitelist job onto the Redis queue."""
    payload = {
        "kind": "add",
        "title": title,
        "url": url,
        "interaction": interaction or {},
        "attempts": 0,
    }
    try:
        _get_redis().rpush(QUEUE_KEY, json.dumps(payload))
        logger.info("enqueued add task", title=title)
    except Exception as e:
        logger.warn("enqueue failed (redis down), falling back to direct DB write", err=str(e)[:120], title=title)
        try:
            from modules.manhwa.tasks.lifecycle import do_add
            do_add(payload)
        except Exception as e2:
            logger.error("direct add fallback failed", exc=e2)
            raise
