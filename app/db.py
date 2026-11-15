"""SQLite-Zugriff. Bewusst ohne ORM: wenige Tabellen, einfache Abfragen."""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
DB_PATH = DATA_DIR / "strom.db"
PHOTO_DIR = DATA_DIR / "photos"

SCHEMA = """
CREATE TABLE IF NOT EXISTS readings (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      TEXT    NOT NULL,              -- ISO 8601 mit Zeitzone
    value   REAL    NOT NULL,              -- Zählerstand in kWh
    kind    TEXT    NOT NULL DEFAULT 'ablesung'
            CHECK (kind IN ('ablesung', 'versorger', 'schaetzung')),
    note    TEXT,
    photo   TEXT,
    power_w REAL,                          -- am Zähler angezeigte Leistung (W), optional
    baseload INTEGER NOT NULL DEFAULT 0    -- 1 = nur Grundlast aktiv
);
CREATE INDEX IF NOT EXISTS idx_readings_ts ON readings(ts);

CREATE TABLE IF NOT EXISTS tariffs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    valid_from    TEXT NOT NULL,           -- Datum YYYY-MM-DD
    name          TEXT,
    energy_ct     REAL NOT NULL,           -- Arbeitspreis netto ct/kWh
    tax_ct        REAL NOT NULL DEFAULT 2.05, -- Stromsteuer netto ct/kWh
    base_eur_year REAL NOT NULL,           -- Grundpreis netto €/Jahr
    vat_pct       REAL NOT NULL DEFAULT 19
);

CREATE TABLE IF NOT EXISTS periods (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    start       TEXT NOT NULL,             -- erster Tag des Abrechnungszeitraums
    end         TEXT NOT NULL,             -- letzter Tag (inklusive)
    start_value REAL NOT NULL,             -- vom Versorger angesetzter Anfangsstand
    note        TEXT
);

CREATE TABLE IF NOT EXISTS ha_entities (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    role            TEXT NOT NULL,         -- verbraucher | wetter | aussentemperatur | kaltwasser | warmwasser | bad_feuchte
    name            TEXT NOT NULL,
    entity_id       TEXT NOT NULL,         -- bei Verbrauchern: Energiezähler in kWh
    power_entity_id TEXT                   -- optional: aktuelle Leistung in W
);

-- Messwerte aus Home Assistant (Energiezähler, Temperaturen …)
CREATE TABLE IF NOT EXISTS samples (
    entity_id TEXT NOT NULL,
    ts        TEXT NOT NULL,
    value     REAL NOT NULL,
    PRIMARY KEY (entity_id, ts)
);

CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS installments (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    valid_from TEXT NOT NULL,              -- ab welcher Fälligkeit dieser Betrag gilt
    amount     REAL NOT NULL,              -- Abschlag brutto €
    due_day    INTEGER NOT NULL DEFAULT 1  -- Fälligkeitstag im Monat
);
"""


def init() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PHOTO_DIR.mkdir(parents=True, exist_ok=True)
    with connect() as con:
        con.executescript(SCHEMA)
        # Migrationen für bestehende Datenbanken
        cols = {r[1] for r in con.execute("PRAGMA table_info(readings)")}
        if "power_w" not in cols:
            con.execute("ALTER TABLE readings ADD COLUMN power_w REAL")
        if "baseload" not in cols:
            con.execute("ALTER TABLE readings ADD COLUMN baseload INTEGER NOT NULL DEFAULT 0")


@contextmanager
def connect():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    try:
        yield con
        con.commit()
    finally:
        con.close()


def rows(sql: str, params: tuple = ()) -> list[dict]:
    with connect() as con:
        return [dict(r) for r in con.execute(sql, params).fetchall()]


def execute(sql: str, params: tuple = ()) -> int:
    with connect() as con:
        cur = con.execute(sql, params)
        return cur.lastrowid


TABLES = {
    "readings": ["ts", "value", "kind", "note", "photo", "power_w", "baseload"],
    "tariffs": ["valid_from", "name", "energy_ct", "tax_ct", "base_eur_year", "vat_pct"],
    "periods": ["start", "end", "start_value", "note"],
    "installments": ["valid_from", "amount", "due_day"],
    "ha_entities": ["role", "name", "entity_id", "power_entity_id"],
}


def insert(table: str, data: dict) -> int:
    cols = [c for c in TABLES[table] if c in data]
    sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})"
    return execute(sql, tuple(data[c] for c in cols))


def delete(table: str, row_id: int) -> None:
    assert table in TABLES
    execute(f"DELETE FROM {table} WHERE id = ?", (row_id,))


def export_all() -> dict:
    return {t: rows(f"SELECT * FROM {t} ORDER BY id") for t in TABLES}


def import_all(data: dict, replace: bool = False) -> dict:
    counts = {}
    with connect() as con:
        for table, cols in TABLES.items():
            items = data.get(table) or []
            if replace:
                con.execute(f"DELETE FROM {table}")
            for item in items:
                use = [c for c in cols if c in item]
                con.execute(
                    f"INSERT INTO {table} ({', '.join(use)}) VALUES ({', '.join('?' for _ in use)})",
                    tuple(item[c] for c in use),
                )
            counts[table] = len(items)
    return counts


def add_samples(points: list[tuple[str, str, float]]) -> int:
    """(entity_id, ts, value) – doppelte Zeitpunkte werden ignoriert."""
    if not points:
        return 0
    with connect() as con:
        cur = con.executemany(
            "INSERT OR IGNORE INTO samples (entity_id, ts, value) VALUES (?, ?, ?)", points)
        return cur.rowcount


def kv_set(key: str, value: str) -> None:
    execute("INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value))


def kv_get(key: str, default=None):
    r = rows("SELECT value FROM kv WHERE key = ?", (key,))
    return r[0]["value"] if r else default
