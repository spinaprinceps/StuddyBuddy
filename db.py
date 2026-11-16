"""Neon (Postgres) sync for StudyBuddy.

data.json stays the source of truth, so the app works offline. Every write goes
through a background queue; anything that fails is kept in neon_pending.json
and retried later. The connection string comes from .env (written by `neon init`).
"""
import datetime as dt
import json
import os
import queue
import threading

BASE = os.path.dirname(os.path.abspath(__file__))
PENDING = os.path.join(BASE, "neon_pending.json")
ENV_KEYS = ("DATABASE_URL", "NEON_DATABASE_URL", "POSTGRES_URL", "DATABASE_URL_UNPOOLED")

SCHEMA = """
create table if not exists sessions (
    id bigserial primary key,
    started_at timestamptz not null,
    ended_at timestamptz not null,
    minutes int not null,
    task text,
    outcome text not null,          -- win | loss:<reason>
    sp_change int default 0,
    unique (ended_at, outcome)
);
create table if not exists tasks (
    day date not null,
    title text not null,
    est_min int,
    done_min int,
    done boolean default false,
    primary key (day, title)
);
create table if not exists daily (
    day date primary key,
    focus_min int default 0,
    xp int default 0,
    wins int default 0,
    losses int default 0,
    distractions int default 0,
    rank text
);
create table if not exists profile (
    id int primary key default 1,
    xp int, tier text, sp int, streak int, best_streak int,
    updated_at timestamptz default now()
);
"""


def _load_env():
    path = os.path.join(BASE, ".env")
    try:
        from dotenv import load_dotenv
        load_dotenv(path)
    except ImportError:  # minimal fallback parser
        if os.path.exists(path):
            for line in open(path, encoding="utf-8"):
                if "=" in line and not line.lstrip().startswith("#"):
                    k, v = line.strip().split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    for k in ENV_KEYS:
        if os.environ.get(k):
            return os.environ[k]
    return None


class Neon:
    def __init__(self):
        self.url = _load_env()
        self.q = queue.Queue()
        self.ok = None          # None = unknown, True/False after the first attempt
        self.last_error = ""
        self._schema_done = False
        self._lock = threading.Lock()
        if self.url:
            threading.Thread(target=self._worker, daemon=True).start()
            self.q.put(("flush", None))

    @property
    def enabled(self):
        return bool(self.url)

    # ---------- low level ----------
    def _connect(self):
        import psycopg
        return psycopg.connect(self.url, connect_timeout=8)

    def _ensure_schema(self, conn):
        if not self._schema_done:
            conn.execute(SCHEMA)
            self._schema_done = True

    def _run(self, op, args):
        with self._connect() as conn:
            self._ensure_schema(conn)
            getattr(self, "_op_" + op)(conn, **args)

    def _worker(self):
        while True:
            op, args = self.q.get()
            try:
                if op == "flush":
                    self._flush()
                else:
                    self._run(op, args)
                    self.ok = True
            except Exception as e:  # network down, auth, missing driver...
                self.ok, self.last_error = False, f"{type(e).__name__}: {e}"[:200]
                if op != "flush":
                    self._park(op, args)

    def _park(self, op, args):
        with self._lock:
            items = self._read_pending()
            items.append([op, args])
            with open(PENDING, "w", encoding="utf-8") as f:
                json.dump(items[-500:], f)

    @staticmethod
    def _read_pending():
        try:
            with open(PENDING, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return []

    def _flush(self):
        with self._lock:
            items = self._read_pending()
            if not items:
                return
            for op, args in items:
                self._run(op, args)
            os.remove(PENDING)
            self.ok = True

    # ---------- operations (run on the worker) ----------
    @staticmethod
    def _op_session(conn, started_at, ended_at, minutes, task, outcome, sp_change):
        conn.execute("insert into sessions (started_at, ended_at, minutes, task, outcome, sp_change) "
                     "values (%s, %s, %s, %s, %s, %s) on conflict (ended_at, outcome) do nothing",
                     (started_at, ended_at, minutes, task, outcome, sp_change))

    @staticmethod
    def _op_day(conn, day, daily, tasks, profile):
        conn.execute(
            "insert into daily (day, focus_min, xp, wins, losses, distractions, rank) "
            "values (%(day)s, %(focus_min)s, %(xp)s, %(wins)s, %(losses)s, %(distractions)s, %(rank)s) "
            "on conflict (day) do update set focus_min = excluded.focus_min, xp = excluded.xp, "
            "wins = excluded.wins, losses = excluded.losses, distractions = excluded.distractions, "
            "rank = excluded.rank", dict(daily, day=day))
        conn.execute("delete from tasks where day = %s", (day,))
        for t in tasks:
            conn.execute("insert into tasks (day, title, est_min, done_min, done) values (%s, %s, %s, %s, %s) "
                         "on conflict (day, title) do nothing",
                         (day, t["title"], t["est_min"], t["done_min"], t["done"]))
        conn.execute(
            "insert into profile (id, xp, tier, sp, streak, best_streak, updated_at) "
            "values (1, %(xp)s, %(tier)s, %(sp)s, %(streak)s, %(best_streak)s, now()) "
            "on conflict (id) do update set xp = excluded.xp, tier = excluded.tier, sp = excluded.sp, "
            "streak = excluded.streak, best_streak = excluded.best_streak, updated_at = now()", profile)

    # ---------- public API (non-blocking) ----------
    def log_session(self, ended_at, minutes, task, outcome, sp_change):
        if not self.enabled:
            return
        started = ended_at - dt.timedelta(minutes=minutes)
        self.q.put(("session", {"started_at": started.isoformat(), "ended_at": ended_at.isoformat(),
                                "minutes": int(minutes), "task": task or None, "outcome": outcome,
                                "sp_change": int(sp_change)}))

    def sync_day(self, day, daily, tasks, profile):
        if self.enabled:
            self.q.put(("day", {"day": day, "daily": daily, "tasks": tasks, "profile": profile}))

    # ---------- reads (blocking; call from a thread) ----------
    def week(self, monday):
        """Per-day rows + totals for the week starting on `monday`, and the week before."""
        prev = monday - dt.timedelta(days=7)
        with self._connect() as conn:
            self._ensure_schema(conn)
            days = conn.execute("select day, focus_min, wins, losses from daily "
                                "where day >= %s and day < %s order by day",
                                (prev, monday + dt.timedelta(days=7))).fetchall()
            done = conn.execute("select day, title from tasks where done and day >= %s and day < %s "
                                "order by day", (monday, monday + dt.timedelta(days=7))).fetchall()
        self.ok = True
        return days, done
