"""База на SQLite: люди, спонсоры, настройки, снимки уровня и подарков.

Одно соединение на весь процесс: aiosqlite сериализует запросы сам, а
бот и веб-сервер живут в одном цикле событий.
"""

from __future__ import annotations

import json
import time
from typing import Any

import aiosqlite

import config

_db: aiosqlite.Connection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY,
    username    TEXT,
    first_name  TEXT,
    lang        TEXT,
    premium     INTEGER DEFAULT 0,
    joined_at   INTEGER,
    ref_by      INTEGER,
    ref_counted INTEGER DEFAULT 0,
    passed_at   INTEGER,
    opened_at   INTEGER,
    views       INTEGER DEFAULT 0,
    shared      INTEGER DEFAULT 0,
    last_seen   INTEGER,
    blocked     INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS users_ref ON users(ref_by);

CREATE TABLE IF NOT EXISTS sponsors (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id   INTEGER,            -- NULL у простых ссылок (боты, сайты)
    title     TEXT,
    username  TEXT,
    link      TEXT,
    goal      INTEGER DEFAULT 0,  -- снять после стольких вступлений, 0 — не снимать
    joined    INTEGER DEFAULT 0,
    added_at  INTEGER,
    active    INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS sponsor_joins (
    sponsor_id INTEGER,
    user_id    INTEGER,
    at         INTEGER,
    PRIMARY KEY (sponsor_id, user_id)
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- Снимки уровня и подарков: в итогах следующего года покажем «было → стало».
CREATE TABLE IF NOT EXISTS snapshots (
    user_id INTEGER,
    day     TEXT,
    level   INTEGER,
    rating  INTEGER,
    gifts   INTEGER,
    PRIMARY KEY (user_id, day)
);

CREATE TABLE IF NOT EXISTS results (
    user_id INTEGER PRIMARY KEY,
    data    TEXT,
    at      INTEGER
);

-- План на следующий год: ключи целей из facts.PLAN_GOALS.
CREATE TABLE IF NOT EXISTS plans (
    user_id INTEGER,
    year    INTEGER,
    goals   TEXT,
    at      INTEGER,
    PRIMARY KEY (user_id, year)
);

CREATE TABLE IF NOT EXISTS cards (
    user_id  INTEGER PRIMARY KEY,
    digest   TEXT,
    file_id  TEXT,
    at       INTEGER
);
"""


async def open_db() -> None:
    global _db
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    _db = await aiosqlite.connect(config.DB_PATH)
    _db.row_factory = aiosqlite.Row
    await _db.executescript(SCHEMA)
    await _db.execute("PRAGMA journal_mode=WAL")
    await _db.commit()


async def close_db() -> None:
    if _db is not None:
        await _db.close()


def _c() -> aiosqlite.Connection:
    assert _db is not None, "база не открыта"
    return _db


async def _one(sql: str, *args: Any) -> dict | None:
    async with _c().execute(sql, args) as cur:
        row = await cur.fetchone()
    return dict(row) if row else None


async def _all(sql: str, *args: Any) -> list[dict]:
    async with _c().execute(sql, args) as cur:
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def _run(sql: str, *args: Any) -> int:
    cur = await _c().execute(sql, args)
    await _c().commit()
    return cur.lastrowid or 0


# ---------------------------------------------------------------- люди


async def touch_user(user: Any, ref_by: int | None = None) -> tuple[dict, bool]:
    """Записать или обновить человека. Второе значение — новенький ли он."""
    now = int(time.time())
    old = await get_user(user.id)
    if old is None:
        ref = ref_by if ref_by and ref_by != user.id and await get_user(ref_by) else None
        await _run(
            "INSERT INTO users (id, username, first_name, lang, premium, joined_at, ref_by, last_seen)"
            " VALUES (?,?,?,?,?,?,?,?)",
            user.id, user.username, user.first_name, getattr(user, "language_code", None),
            int(bool(getattr(user, "is_premium", False))), now, ref, now,
        )
        return await get_user(user.id), True  # type: ignore[return-value]
    await _run(
        "UPDATE users SET username=?, first_name=?, lang=?, premium=?, last_seen=?, blocked=0 WHERE id=?",
        user.username, user.first_name, getattr(user, "language_code", None),
        int(bool(getattr(user, "is_premium", False))), now, user.id,
    )
    old.update(username=user.username, first_name=user.first_name)
    return old, False


async def get_user(user_id: int) -> dict | None:
    return await _one("SELECT * FROM users WHERE id=?", user_id)


async def older_share(user_id: int) -> int | None:
    """Скольким процентам пользователей бота аккаунт старше (по id: меньше id — старше)."""
    row = await _one("SELECT COUNT(*) AS n, SUM(id > ?) AS newer FROM users", user_id)
    if not row or int(row["n"] or 0) < 30:
        return None
    return int(round(100 * int(row["newer"] or 0) / int(row["n"])))


async def rank_of(user_id: int) -> int:
    """Каким по счёту человек пришёл в бота."""
    row = await _one(
        "SELECT COUNT(*) AS n FROM users WHERE joined_at < (SELECT joined_at FROM users WHERE id=?)"
        " OR (joined_at = (SELECT joined_at FROM users WHERE id=?) AND id <= ?)",
        user_id, user_id, user_id,
    )
    return int(row["n"]) if row else 0


async def mark_passed(user_id: int) -> int | None:
    """Человек прошёл подписку. Если пришёл по ссылке друга — засчитать другу.

    Возвращает id пригласившего, если засчитали именно сейчас.
    """
    now = int(time.time())
    await _run("UPDATE users SET passed_at=COALESCE(passed_at, ?) WHERE id=?", now, user_id)
    row = await get_user(user_id)
    if row and row["ref_by"] and not row["ref_counted"]:
        await _run("UPDATE users SET ref_counted=1 WHERE id=?", user_id)
        return int(row["ref_by"])
    return None


async def invited_count(user_id: int) -> int:
    row = await _one("SELECT COUNT(*) AS n FROM users WHERE ref_by=? AND ref_counted=1", user_id)
    return int(row["n"]) if row else 0


async def mark_opened(user_id: int) -> None:
    now = int(time.time())
    await _run(
        "UPDATE users SET opened_at=COALESCE(opened_at, ?), views=views+1 WHERE id=?", now, user_id
    )


async def mark_shared(user_id: int) -> None:
    await _run("UPDATE users SET shared=shared+1 WHERE id=?", user_id)


async def mark_blocked(user_id: int) -> None:
    await _run("UPDATE users SET blocked=1 WHERE id=?", user_id)


async def audience() -> list[int]:
    rows = await _all("SELECT id FROM users WHERE blocked=0")
    return [r["id"] for r in rows]


async def stats() -> dict:
    day = int(time.time()) - 86400
    q = {
        "users": "SELECT COUNT(*) n FROM users",
        "today": f"SELECT COUNT(*) n FROM users WHERE joined_at > {day}",
        "passed": "SELECT COUNT(*) n FROM users WHERE passed_at IS NOT NULL",
        "opened": "SELECT COUNT(*) n FROM users WHERE opened_at IS NOT NULL",
        "shared": "SELECT COUNT(*) n FROM users WHERE shared > 0",
        "refs": "SELECT COUNT(*) n FROM users WHERE ref_counted = 1",
        "blocked": "SELECT COUNT(*) n FROM users WHERE blocked = 1",
        "premium": "SELECT COUNT(*) n FROM users WHERE premium = 1",
    }
    out = {}
    for key, sql in q.items():
        row = await _one(sql)
        out[key] = int(row["n"]) if row else 0
    return out


# ---------------------------------------------------------------- спонсоры


async def sponsors(active_only: bool = True) -> list[dict]:
    sql = "SELECT * FROM sponsors" + (" WHERE active=1" if active_only else "") + " ORDER BY id"
    return await _all(sql)


async def sponsor(sponsor_id: int) -> dict | None:
    return await _one("SELECT * FROM sponsors WHERE id=?", sponsor_id)


async def add_sponsor(chat_id: int | None, title: str, username: str | None, link: str, goal: int = 0) -> int:
    return await _run(
        "INSERT INTO sponsors (chat_id, title, username, link, goal, added_at) VALUES (?,?,?,?,?,?)",
        chat_id, title, username, link, goal, int(time.time()),
    )


async def set_sponsor(sponsor_id: int, **fields: Any) -> None:
    if not fields:
        return
    keys = ", ".join(f"{k}=?" for k in fields)
    await _run(f"UPDATE sponsors SET {keys} WHERE id=?", *fields.values(), sponsor_id)


async def drop_sponsor(sponsor_id: int) -> None:
    await _run("DELETE FROM sponsors WHERE id=?", sponsor_id)
    await _run("DELETE FROM sponsor_joins WHERE sponsor_id=?", sponsor_id)


async def sponsor_joined(sponsor_id: int, user_id: int) -> bool:
    """Отметить вступление через бота. True — это новое вступление."""
    cur = await _c().execute(
        "INSERT OR IGNORE INTO sponsor_joins (sponsor_id, user_id, at) VALUES (?,?,?)",
        (sponsor_id, user_id, int(time.time())),
    )
    fresh = cur.rowcount > 0
    if fresh:
        await _c().execute("UPDATE sponsors SET joined=joined+1 WHERE id=?", (sponsor_id,))
    await _c().commit()
    return fresh


async def sponsor_today(sponsor_id: int) -> int:
    row = await _one(
        "SELECT COUNT(*) n FROM sponsor_joins WHERE sponsor_id=? AND at > ?",
        sponsor_id, int(time.time()) - 86400,
    )
    return int(row["n"]) if row else 0


# ---------------------------------------------------------------- настройки


async def get_setting(key: str) -> str | None:
    row = await _one("SELECT value FROM settings WHERE key=?", key)
    return row["value"] if row else None


async def set_setting(key: str, value: str | None) -> None:
    if value is None:
        await _run("DELETE FROM settings WHERE key=?", key)
    else:
        await _run("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", key, value)


# ---------------------------------------------------------------- итоги и карточки


async def save_snapshot(user_id: int, level: int | None, rating: int | None, gifts: int | None) -> None:
    day = time.strftime("%Y-%m-%d")
    await _run(
        "INSERT OR REPLACE INTO snapshots (user_id, day, level, rating, gifts) VALUES (?,?,?,?,?)",
        user_id, day, level, rating, gifts,
    )


async def first_snapshot(user_id: int) -> dict | None:
    return await _one("SELECT * FROM snapshots WHERE user_id=? ORDER BY day LIMIT 1", user_id)


async def save_plan(user_id: int, year: int, goals: list[str]) -> None:
    await _run(
        "INSERT OR REPLACE INTO plans (user_id, year, goals, at) VALUES (?,?,?,?)",
        user_id, year, json.dumps(goals), int(time.time()),
    )


async def get_plan(user_id: int, year: int) -> list[str]:
    row = await _one("SELECT goals FROM plans WHERE user_id=? AND year=?", user_id, year)
    return json.loads(row["goals"]) if row else []


async def get_results(user_id: int, max_age: int) -> dict | None:
    row = await _one("SELECT * FROM results WHERE user_id=?", user_id)
    if not row or time.time() - row["at"] > max_age:
        return None
    return json.loads(row["data"])


async def put_results(user_id: int, data: dict) -> None:
    await _run(
        "INSERT OR REPLACE INTO results (user_id, data, at) VALUES (?,?,?)",
        user_id, json.dumps(data, ensure_ascii=False), int(time.time()),
    )


async def card_file(user_id: int, digest: str) -> str | None:
    row = await _one("SELECT file_id FROM cards WHERE user_id=? AND digest=?", user_id, digest)
    return row["file_id"] if row else None


async def put_card_file(user_id: int, digest: str, file_id: str) -> None:
    await _run(
        "INSERT OR REPLACE INTO cards (user_id, digest, file_id, at) VALUES (?,?,?,?)",
        user_id, digest, file_id, int(time.time()),
    )
