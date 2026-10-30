"""PostgREST-style query builder — compiles to parameterized SQL.

Split out of app/db_adapter.py. Mirrors the `get_supabase().table(...)...`
surface the rest of the codebase uses, so call sites do NOT need rewriting.
Each builder compiles to parameterized SQL and runs through a pooled psycopg2
connection borrowed from modules.manhwa.db.adapter.
"""
from __future__ import annotations

import re
import threading

from modules.manhwa.logger import get_logger
from modules.manhwa.db.adapter import get_conn, put_conn

logger = get_logger("db")


def _jsonable(val):
    """Convert Postgres/Python types that json.dumps can't handle."""
    from datetime import datetime, date, time
    from decimal import Decimal
    import uuid
    if isinstance(val, (datetime, date, time)):
        return val.isoformat()
    if isinstance(val, Decimal):
        return float(val)
    if isinstance(val, uuid.UUID):
        return str(val)
    if isinstance(val, (bytes, bytearray)):
        return val.decode("utf-8", "replace")
    return val


def _row_to_jsonable(row):
    return {k: _jsonable(v) for k, v in dict(row).items()}


class _Result:
    def __init__(self, data):
        self.data = data
        self.count = 0


class _Query:
    def __init__(self, table: str, op: str):
        self.table = table
        self.op = op  # select|insert|update|delete|upsert
        self._cols = "*"
        self._values = None          # dict for insert/update/upsert
        self._on_conflict = None      # column name(s) for upsert
        self._where: list[tuple] = [] # (sql_fragment, [params])
        self._or_groups: list[list[list[tuple]]] = []  # list of OR-groups, each group = list of branches (AND-lists)
        self._order: list[tuple] = [] # (col, desc)
        self._limit = None
        self._offset = 0
        self._single = False
        self._maybe_single = False
        self._first = False
        self._want_count = False

    # ---- WHERE builders ----
    _ID_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")

    @staticmethod
    def _ident(col: str) -> str:
        """Validate a column identifier before it reaches the SQL string.
        Writes already had an allowlist (_compile_write); reads had none.
        One guard, every WHERE/ORDER BY/SELECT path covered."""
        if not isinstance(col, str) or not _Query._ID_RE.match(col):
            raise ValueError(f"invalid column name (rejected): {col!r}")
        return col

    def _w(self, frag, params):
        self._where.append((frag, params))

    def eq(self, col, val):
        self._w(f"{self._ident(col)} = %s", [val]); return self

    def neq(self, col, val):
        self._w(f"{self._ident(col)} <> %s", [val]); return self

    def gt(self, col, val):
        self._w(f"{self._ident(col)} > %s", [val]); return self

    def lt(self, col, val):
        self._w(f"{self._ident(col)} < %s", [val]); return self

    def gte(self, col, val):
        self._w(f"{self._ident(col)} >= %s", [val]); return self

    def lte(self, col, val):
        self._w(f"{self._ident(col)} <= %s", [val]); return self

    def like(self, col, pat):
        self._w(f"{self._ident(col)} LIKE %s", [pat]); return self

    def ilike(self, col, pat):
        self._w(f"{self._ident(col)} ILIKE %s", [pat]); return self

    def is_(self, col, val):
        # IS takes SQL literals (NULL / TRUE / FALSE / UNKNOWN), not parameters.
        # Validate against a tiny allowlist so a future caller can't inject
        # arbitrary SQL through this path.
        _IS_ALLOWED = {"null", "true", "false", "unknown"}
        if not isinstance(val, str) or val.lower() not in _IS_ALLOWED:
            raise ValueError(f"invalid IS operand (rejected): {val!r}")
        self._w(f"{self._ident(col)} IS {val.upper()}", []); return self

    def in_(self, col, vals):
        if not vals:
            # PostgREST .in_([ ]) => no rows
            self._w("1 = 0", []); return self
        ph = ",".join(["%s"] * len(vals))
        self._w(f"{self._ident(col)} IN ({ph})", list(vals)); return self

    # Known JSONB columns (so .contains()/@> compiles correctly). Anything
    # not in this set is treated as TEXT (substring match via LIKE) — the old
    # code hardcoded `::jsonb` which crashed on TEXT columns (operator does
    # not exist: text @> jsonb). Callers that hit a TEXT column with
    # .contains() now get a safe LIKE instead of a 500.
    _JSONB_COLS = {
        "dashboard_snapshot.payload",
        "failed_dispatches.metadata",
        "whitelist.genres",
        "recent_chapters.genres",
        "series_meta.genres",
        "series_meta.payload",
    }

    def contains(self, col, val):
        import json
        col = self._ident(col)
        full = f"{self.table}.{col}"
        if full in self._JSONB_COLS:
            import json as _json
            if isinstance(val, str):
                try:
                    _parsed = _json.loads(val)
                except Exception:
                    _parsed = None
            else:
                _parsed = val
            # PostgREST .contains() on a jsonb ARRAY column expects an array
            # value (e.g. '["Action"]'), not a bare scalar. Wrap scalars so
            # `genres @> 'Action'` becomes `genres @> '["Action"]'::jsonb`.
            if not isinstance(_parsed, (list, dict)):
                _parsed = [val]
            self._w(f"{col} @> %s::jsonb", [_json.dumps(_parsed)])
        else:
            # TEXT containment → substring match (PostgREST .contains on text)
            like = val if isinstance(val, str) else json.dumps(val)
            self._w(f"{col} LIKE %s", [f"%{like}%"])
        return self

    def filter(self, col, op, val):
        # generic: op in eq,neq,gt,lt,gte,lte,like,ilike,cs(contains),cd
        col = self._ident(col)
        full = f"{self.table}.{col}"
        if op in ("cs", "cd") and full not in self._JSONB_COLS:
            # TEXT column: PostgREST maps cs/cd on text to substring match
            self._w(f"{col} LIKE %s", [f"%{val}%"]); return self
        m = {
            "eq": f"{col} = %s", "neq": f"{col} <> %s",
            "gt": f"{col} > %s", "lt": f"{col} < %s",
            "gte": f"{col} >= %s", "lte": f"{col} <= %s",
            "like": f"{col} LIKE %s", "ilike": f"{col} ILIKE %s",
            "cs": f"{col} @> %s::jsonb", "cd": f"{col} <@ %s::jsonb",
        }.get(op)
        if not m:
            raise ValueError(f"unsupported filter op {op}")
        self._w(m, [val]); return self

    def or_(self, *chains):
        """Emulate PostgREST .or_(): each chain is a callable that applies
        filters to a cloned builder. Each chain becomes a parenthesized
        AND-branch; branches are joined with OR.
        e.g. .or_(lambda q: q.eq('a',1).eq('b',2), lambda q: q.eq('c',3))
             → ((a=1) AND (b=2)) OR ((c=3))
        """
        branches: list[list[tuple]] = []
        for ch in chains:
            b = _Query(self.table, self.op)
            b._cols = self._cols
            ch(b)
            if b._where:
                branches.append(list(b._where))
        if branches:
            self._or_groups.append(branches)
        return self

    # ---- modifiers ----
    def select(self, cols="*", count=None):
        self._cols = cols
        if count:
            self._want_count = True
        return self

    def order(self, col, desc=False, ascending=None):
        if ascending is not None:
            desc = not ascending
        self._order.append((self._ident(col), desc)); return self

    def limit(self, n):
        self._limit = n; return self

    def offset(self, n):
        self._offset = n; return self

    def single(self):
        # Old code fetched ALL rows (catastrophic over-fetch). LIMIT 2
        # preserves Supabase .single() semantics while bounding cost.
        self._single = True
        if self._limit is None or self._limit > 2:
            self._limit = 2
        return self

    def first(self):
        self._first = True
        self._limit = 1
        return self

    def maybe_single(self):
        self._maybe_single = True
        if self._limit is None or self._limit > 2:
            self._limit = 2
        return self

    # ---- data setters ----
    def insert(self, row):
        self.op = "insert"; self._values = row; return self

    def update(self, row):
        self.op = "update"; self._values = row; return self

    def upsert(self, row, on_conflict=None):
        self.op = "upsert"; self._values = row
        if on_conflict:
            self._on_conflict = on_conflict
        return self

    def delete(self):
        self.op = "delete"; return self
    def _compile_write(self, upsert: bool):
        """Build INSERT / INSERT..ON CONFLICT for either a single dict
        or a list of dicts (batch). All rows must share the same columns;
        we take columns from the first row and validate the rest match.
        """
        # --- Defense-in-depth: column-name allowlist ---
        # Values are parameterized (%s), but column NAMES are interpolated
        # into the SQL string. They come from dict KEYS in app code
        # today (never request body), so there's no live injection — but
        # a future endpoint that does ``entry = dict(request.json())``
        # would open a column-name SQLi. Reject any identifier that
        # isn't a safe Postgres identifier (letters/digits/underscore,
        # <=63 chars) so a bad key can never reach the SQL string.
        import re as _re
        _ID_RE = _re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
        rows = self._values if isinstance(self._values, list) else [self._values]
        if not rows:
            raise ValueError("upsert/insert called with empty values")
        cols = list(rows[0].keys())
        for c in cols:
            if not _ID_RE.match(c):
                raise ValueError(f"invalid column name (rejected): {c!r}")
        # multi-row VALUES
        val_rows = []
        params: list = []
        import json as _json
        for r in rows:
            missing = [c for c in cols if c not in r]
            if missing:
                raise ValueError(f"row missing columns: {missing}")
            # jsonb columns: a list/dict value must be cast ::jsonb, not
            # sent as a Postgres ARRAY (psycopg2 would emit text[] and
            # Postgres rejects "column X is of type jsonb but expression
            # is of type text[]"). Serialize to JSON + cast.
            _ph = []
            for c in cols:
                v = r[c]
                if isinstance(v, (list, dict)):
                    _ph.append("%s::jsonb")
                    params.append(_json.dumps(v))
                else:
                    _ph.append("%s")
                    params.append(v)
            val_rows.append("(" + ", ".join(_ph) + ")")
        sql = (
            f"INSERT INTO {self.table} ({', '.join(cols)}) VALUES "
            + ", ".join(val_rows)
        )
        if upsert:
            if self._on_conflict:
                oc = self._on_conflict
                parts = [p.strip() for p in oc.split(",") if p.strip()]
                if not parts or not all(_ID_RE.match(p) for p in parts):
                    raise ValueError(f"invalid on_conflict spec: {oc!r}")
                ucols = [c for c in cols if c not in parts]
                if ucols:
                    upd = ", ".join(f"{c} = EXCLUDED.{c}" for c in ucols)
                    sql += f" ON CONFLICT ({oc}) DO UPDATE SET {upd}"
                else:
                    sql += f" ON CONFLICT ({oc}) DO NOTHING"
            else:
                sql += " ON CONFLICT DO NOTHING"
        sql += " RETURNING *"
        return sql, params

    # ---- compile ----
    def _build_where(self):
        clauses = []
        params: list = []
        for frag, p in self._where:
            clauses.append(f"({frag})")
            params.extend(p)
        for branches in self._or_groups:
            # Each branch is an AND-list; branches are ORed together.
            or_parts = []
            for branch in branches:
                and_sql = " AND ".join(f"({frag})" for frag, _ in branch)
                # single-condition branch: (cond), multi: ((a) AND (b))
                or_parts.append(f"({and_sql})" if len(branch) > 1 else and_sql)
                for _, p in branch:
                    params.extend(p)
            clauses.append(f"({' OR '.join(or_parts)})")
        return (" WHERE " + " AND ".join(clauses)) if clauses else "", params

    def _col_list(self):
        if self._cols in ("*", None):
            return "*"
        # Validate each column identifier
        return ", ".join(self._ident(c.strip()) for c in self._cols.split(","))

    def compile(self):
        w, p = self._build_where()
        if self.op == "select":
            sql = f"SELECT {self._col_list()} FROM {self.table}{w}"
            for col, desc in self._order:
                sql += f" ORDER BY {col} {'DESC' if desc else 'ASC'}"
            if self._limit is not None:
                sql += f" LIMIT {int(self._limit)}"
            if self._offset:
                sql += f" OFFSET {int(self._offset)}"
            return sql, p
        if self.op == "insert":
            return self._compile_write(False)
        if self.op == "update":
            import json as _json
            sets = []
            params = []
            for c, v in self._values.items():
                _ident_c = self._ident(c)
                if isinstance(v, (list, dict)):
                    sets.append(f"{_ident_c} = %s::jsonb")
                    params.append(_json.dumps(v))
                else:
                    sets.append(f"{_ident_c} = %s")
                    params.append(v)
            # FAIL-CLOSED: an UPDATE without a WHERE predicate would
            # mutate the ENTIRE table (full-table wipe). Supabase's
            # PostgREST forbids this; the builder must too. Callers
            # that build .eq() inside an `if` (optional filter) are
            # exactly the danger — a None filter silently drops
            # the WHERE and wipes the table.
            if not (self._where or self._or_groups):
                raise ValueError(
                    f"Refusing UPDATE on '{self.table}' without a WHERE "
                    f"filter (would mutate every row)"
                )
            sql = f"UPDATE {self.table} SET {', '.join(sets)}{w} RETURNING *"
            return sql, params + p
        if self.op == "upsert":
            return self._compile_write(True)
        if self.op == "delete":
            sql = f"DELETE FROM {self.table}{w} RETURNING *"
            return sql, p
        raise ValueError(f"unknown op {self.op}")

    # ---- execute ----
    def execute(self):
        sql, params = self.compile()
        conn = get_conn()
        try:
            cur = conn.cursor()
            cur.execute(sql, params)
            if self.op == "select":
                rows = cur.fetchall()
                if self._first:
                    data = _row_to_jsonable(rows[0]) if rows else None
                elif self._single:
                    if not rows:
                        raise Exception("JSON object requested, but 0 rows returned")
                    if len(rows) > 1:
                        # Supabase's .single() hard-fails (406) on multiple
                        # rows; mirror that so a caller that assumes a
                        # unique row can't silently read the WRONG one.
                        raise Exception(
                            f"JSON object requested, but {len(rows)} rows returned "
                            f"(expected exactly 1)"
                        )
                    data = _row_to_jsonable(rows[0])
                elif self._maybe_single:
                    if len(rows) > 1:
                        # .maybe_single() allows 0 or 1, but >1 is
                        # ambiguous — fail closed like Supabase.
                        raise Exception(
                            f"maybe_single got {len(rows)} rows (expected 0 or 1)"
                        )
                    data = _row_to_jsonable(rows[0]) if rows else None
                else:
                    data = [_row_to_jsonable(r) for r in rows]
            else:
                rows = cur.fetchall()
                data = [_row_to_jsonable(r) for r in rows]
            conn.commit()
            res = _Result(data)
            if self.op == "select" and self._want_count:
                # separate count (respects WHERE, ignores LIMIT/OFFSET)
                # C3 FIX: Run count query on the SAME connection to avoid
                # deadlock when pool is saturated (2nd get_conn() would block)
                try:
                    cc = conn.cursor()
                    csql, cparams = self._build_where()
                    cc.execute(f"SELECT count(*) AS count FROM {self.table}{csql}", cparams)
                    row = cc.fetchone()
                    res.count = row.get("count", 0) if row else 0
                    # Don't commit twice — same transaction
                except Exception as e:
                    logger.warn("count query failed", err=str(e)[:160])
                    res.count = 0
            return res
        except Exception:
            conn.rollback()
            raise
        finally:
            put_conn(conn)


class _Table:
    def __init__(self, name):
        self.name = name

    def select(self, cols="*", count=None):
        return _Query(self.name, "select").select(cols, count=count)

    def insert(self, row):
        return _Query(self.name, "insert").insert(row)

    def update(self, row):
        return _Query(self.name, "update").update(row)

    def upsert(self, row, on_conflict=None):
        return _Query(self.name, "upsert").upsert(row, on_conflict)

    def delete(self):
        return _Query(self.name, "delete").delete()


class _Client:
    """Mirrors supabase.Client surface used in the codebase."""

    def table(self, name):
        return _Table(name)

    def rpc(self, name, params=None):
        return _RpcCall(name, params or {})


class _RpcCall:
    def __init__(self, name, params):
        self.name = name
        self.params = params

    def execute(self):
        conn = get_conn()
        try:
            cur = conn.cursor()
            # psycopg2 calls function with kwargs via SELECT * FROM fn(p=>v)
            keys = list(self.params.keys())
            if keys:
                args = ", ".join(f"{k} => %s" for k in keys)
                sql = f"SELECT * FROM {self.name}({args})"
                cur.execute(sql, [self.params[k] for k in keys])
            else:
                cur.execute(f"SELECT * FROM {self.name}()")
            rows = cur.fetchall()
            conn.commit()
            # rpc returns rows; .data = list of dicts
            return _Result([dict(r) for r in rows])
        except Exception:
            conn.rollback()
            raise
        finally:
            put_conn(conn)


_client: _Client | None = None
_client_lock = threading.Lock()


def get_supabase() -> _Client:
    """Backwards-compatible name. Returns the direct-DB client."""
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                # touch pool to validate config at first use
                from modules.manhwa.db.adapter import _get_pool
                _get_pool()
                _client = _Client()
    return _client
