"""Local SQLite knowledge base for dish calorie estimates built from ingredients."""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
MAX_KCAL_PER_100G = 900
MAX_DISH_KCAL = 5000
WARN_RATIO = 0.25
HIGH_RATIO = 0.15
SOURCES = ("ingredients", "web", "user")
FTS_ENABLED = True

_CHARS = str.maketrans(
    {
        "ي": "ی",
        "ك": "ک",
        "ة": "ه",
        "أ": "ا",
        "إ": "ا",
        "ؤ": "و",
        "\u200c": " ",
        **{chr(0x0660 + i): str(i) for i in range(10)},
        **{chr(0x06F0 + i): str(i) for i in range(10)},
    }
)
_DIACRITICS = re.compile("[\u064b-\u065f\u0670\u0640]")
_NON_WORD = re.compile(r"[^\w\s]", re.UNICODE)
_WEIGHT = re.compile(r"(\d+(?:[.,٫]\d+)?)\s*(کیلوگرم|کیلو|گرم|گر|kg|g)(?!\w)", re.IGNORECASE)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS ingredients (
    name_key TEXT PRIMARY KEY, name TEXT NOT NULL, kcal_per_100g REAL NOT NULL,
    protein_g REAL, carbs_g REAL, fat_g REAL, typical_portion_g REAL,
    source TEXT, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS dishes (
    key TEXT PRIMARY KEY, title_key TEXT NOT NULL, title TEXT NOT NULL, description TEXT,
    vendor_code TEXT, weight_g REAL, kcal_min REAL NOT NULL, kcal_likely REAL NOT NULL,
    kcal_max REAL NOT NULL, confidence TEXT NOT NULL, source TEXT NOT NULL,
    cross_check_kcal REAL, cross_check_source TEXT, notes TEXT, search_text TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS dishes_title_key ON dishes(title_key);
CREATE TABLE IF NOT EXISTS dish_ingredients (
    dish_key TEXT NOT NULL REFERENCES dishes(key) ON DELETE CASCADE,
    ingredient_key TEXT NOT NULL, grams_min REAL NOT NULL, grams_likely REAL NOT NULL,
    grams_max REAL NOT NULL, note TEXT, PRIMARY KEY (dish_key, ingredient_key)
);
"""


def normalize(text: str | None) -> str:
    """Lowercase, unify Persian/Arabic letters and digits, drop diacritics and punctuation."""
    text = _DIACRITICS.sub("", (text or "").translate(_CHARS).lower())
    return " ".join(_NON_WORD.sub(" ", text).split())


def dish_key(title: str, description: str | None = None, toppings: list[str] | None = None) -> str:
    parts = [normalize(title), normalize(description), "|".join(sorted(normalize(t) for t in toppings or []))]
    return hashlib.sha1("\x1f".join(parts).encode()).hexdigest()[:20]


def parse_weight_g(*texts: str | None) -> float | None:
    """First stated weight in grams, e.g. '350 گرم' -> 350, '2 کیلوگرم' -> 2000."""
    for text in texts:
        match = _WEIGHT.search((text or "").translate(_CHARS))
        if match:
            value = float(match.group(1).replace(",", ".").replace("٫", "."))
            return value * 1000 if match.group(2).lower() in ("کیلوگرم", "کیلو", "kg") else value
    return None


def db_path() -> Path:
    return Path(os.environ.get("SNAPP_MCP_DB") or Path.home() / ".local/share/snapp-mcp/calories.db")


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    target = Path(path) if path else db_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(_SCHEMA)
    if conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0] == 0:
        conn.execute("INSERT INTO schema_version VALUES (?)", (SCHEMA_VERSION,))
    if FTS_ENABLED and _fts_available(conn):
        conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS dish_fts USING fts5(dish_key UNINDEXED, text)")
    conn.commit()
    return conn


def _fts_available(conn: sqlite3.Connection) -> bool:
    try:
        conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS temp.fts_probe USING fts5(x)")
        conn.execute("DROP TABLE temp.fts_probe")
        return True
    except sqlite3.OperationalError:
        return False


def _has_fts(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'dish_fts'").fetchone()
    return bool(FTS_ENABLED and row)


def _num(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a number")
    if value < 0:
        raise ValueError(f"{label} must not be negative")
    return float(value)


def save_ingredient(conn: sqlite3.Connection, item: dict[str, Any]) -> str:
    name = (item.get("name") or "").strip()
    key = normalize(name)
    if not key:
        raise ValueError("ingredient name is required")
    kcal = _num(item.get("kcal_per_100g"), f"{name}: kcal_per_100g")
    if kcal > MAX_KCAL_PER_100G:
        raise ValueError(f"{name}: kcal_per_100g {kcal:g} is above {MAX_KCAL_PER_100G}")
    optional = {
        field: None if item.get(field) is None else _num(item[field], f"{name}: {field}")
        for field in ("protein_g", "carbs_g", "fat_g", "typical_portion_g")
    }
    source = item.get("source")
    existing = conn.execute("SELECT source FROM ingredients WHERE name_key = ?", (key,)).fetchone()
    if existing and existing["source"] == "user" and source != "user":
        return "kept"
    conn.execute(
        """INSERT INTO ingredients (name_key, name, kcal_per_100g, protein_g, carbs_g, fat_g, typical_portion_g, source)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(name_key) DO UPDATE SET name=excluded.name, kcal_per_100g=excluded.kcal_per_100g,
             protein_g=excluded.protein_g, carbs_g=excluded.carbs_g, fat_g=excluded.fat_g,
             typical_portion_g=excluded.typical_portion_g, source=excluded.source, updated_at=CURRENT_TIMESTAMP""",
        (key, name, kcal, *optional.values(), source),
    )
    return "saved"


def lookup_ingredients(conn: sqlite3.Connection, names: list[str]) -> dict[str, Any]:
    found, missing = [], []
    for name in names:
        row = conn.execute("SELECT * FROM ingredients WHERE name_key = ?", (normalize(name),)).fetchone()
        if row:
            found.append({k: row[k] for k in row.keys() if k not in ("name_key", "updated_at") and row[k] is not None})
        else:
            missing.append(name)
    return {"known": found, "missing": missing}


def _confidence(likely: float, cross: float | None, weight: float | None) -> tuple[str, float | None]:
    diff = abs(likely - cross) / cross if cross else None
    if diff is not None and diff <= HIGH_RATIO and weight:
        return "high", diff
    if cross or weight:
        return "medium", diff
    return "low", diff


def save_dish(conn: sqlite3.Connection, dish: dict[str, Any]) -> dict[str, Any]:
    """Validate, compute totals from ingredient ranges, upsert. Raises ValueError on bad input."""
    title = (dish.get("title") or "").strip()
    if not normalize(title):
        raise ValueError("dish title is required")
    source = dish.get("source") or "ingredients"
    if source not in SOURCES:
        raise ValueError(f"{title}: source must be one of {', '.join(SOURCES)}")
    lines = dish.get("ingredients") or []
    if not lines:
        raise ValueError(f"{title}: at least one ingredient is required")

    totals = [0.0, 0.0, 0.0]
    rows = []
    names = []
    for line in lines:
        name = (line.get("name") or "").strip()
        ing = conn.execute(
            "SELECT name, kcal_per_100g FROM ingredients WHERE name_key = ?", (normalize(name),)
        ).fetchone()
        if not ing:
            raise ValueError(f"{title}: unknown ingredient '{name}'; save its kcal_per_100g first")
        grams = [_num(line.get(f), f"{title}/{name}: {f}") for f in ("grams_min", "grams_likely", "grams_max")]
        if not grams[0] <= grams[1] <= grams[2]:
            raise ValueError(f"{title}/{name}: need grams_min <= grams_likely <= grams_max")
        for i, grams_value in enumerate(grams):
            totals[i] += grams_value * ing["kcal_per_100g"] / 100
        rows.append((normalize(name), *grams, line.get("note")))
        names.append(ing["name"])
    if totals[2] > MAX_DISH_KCAL:
        raise ValueError(f"{title}: total {totals[2]:.0f} kcal is above {MAX_DISH_KCAL}; check the grams")

    cross = (
        None if dish.get("cross_check_kcal") is None else _num(dish["cross_check_kcal"], f"{title}: cross_check_kcal")
    )
    weight = None if dish.get("weight_g") is None else _num(dish["weight_g"], f"{title}: weight_g")
    confidence, diff = _confidence(totals[1], cross or None, weight or None)
    warnings = []
    if diff is not None and diff > WARN_RATIO:
        warnings.append(
            f"{title}: estimate {totals[1]:.0f} kcal differs {diff:.0%} from the external figure "
            f"{cross:g} kcal; revisit the ingredients and grams"
        )

    toppings = dish.get("toppings") or []
    key = dish_key(title, dish.get("description"), toppings)
    existing = conn.execute("SELECT source FROM dishes WHERE key = ?", (key,)).fetchone()
    if existing and existing["source"] == "user" and source != "user":
        return {"key": key, "title": title, "status": "kept", "warnings": warnings}

    search_text = normalize(" ".join([title, dish.get("description") or "", *toppings, *names]))
    conn.execute("DELETE FROM dish_ingredients WHERE dish_key = ?", (key,))
    conn.execute(
        """INSERT INTO dishes (key, title_key, title, description, vendor_code, weight_g, kcal_min, kcal_likely,
             kcal_max, confidence, source, cross_check_kcal, cross_check_source, notes, search_text)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(key) DO UPDATE SET title_key=excluded.title_key, title=excluded.title,
             description=excluded.description, vendor_code=excluded.vendor_code, weight_g=excluded.weight_g,
             kcal_min=excluded.kcal_min, kcal_likely=excluded.kcal_likely, kcal_max=excluded.kcal_max,
             confidence=excluded.confidence, source=excluded.source, cross_check_kcal=excluded.cross_check_kcal,
             cross_check_source=excluded.cross_check_source, notes=excluded.notes,
             search_text=excluded.search_text, updated_at=CURRENT_TIMESTAMP""",
        (
            key, normalize(title), title, dish.get("description"), dish.get("vendor_code"), weight,
            *(round(total) for total in totals), confidence, source, cross, dish.get("cross_check_source"),
            dish.get("notes"), search_text,
        ),
    )  # fmt: skip
    conn.executemany(
        "INSERT INTO dish_ingredients (dish_key, ingredient_key, grams_min, grams_likely, grams_max, note)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        [(key, *row) for row in rows],
    )
    if _has_fts(conn):
        conn.execute("DELETE FROM dish_fts WHERE dish_key = ?", (key,))
        conn.execute("INSERT INTO dish_fts (dish_key, text) VALUES (?, ?)", (key, search_text))
    return {
        "key": key,
        "title": title,
        "status": "updated" if existing else "saved",
        "kcal_min": round(totals[0]),
        "kcal_likely": round(totals[1]),
        "kcal_max": round(totals[2]),
        "confidence": confidence,
        "warnings": warnings,
    }


def _summary(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    ingredients = conn.execute(
        """SELECT COALESCE(i.name, d.ingredient_key) AS name, d.grams_likely AS grams
           FROM dish_ingredients d LEFT JOIN ingredients i ON i.name_key = d.ingredient_key
           WHERE d.dish_key = ?""",
        (row["key"],),
    ).fetchall()
    return {
        "title": row["title"],
        "kcal_min": row["kcal_min"],
        "kcal_likely": row["kcal_likely"],
        "kcal_max": row["kcal_max"],
        "confidence": row["confidence"],
        "ingredients": [f"{item['name']} {item['grams']:g}g" for item in ingredients],
    }


def exact(
    conn: sqlite3.Connection, title: str, description: str | None = None, toppings: list[str] | None = None
) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM dishes WHERE key = ?", (dish_key(title, description, toppings),)).fetchone()
    return None if row is None else {"cached": True, **_summary(conn, row)}


def similar(
    conn: sqlite3.Connection,
    title: str,
    description: str | None = None,
    toppings: list[str] | None = None,
    limit: int = 3,
) -> list[dict[str, Any]]:
    text = f"{title} {description or ''} {' '.join(toppings or [])}"
    tokens = [token for token in dict.fromkeys(normalize(text).split()) if len(token) > 1]
    if not tokens:
        return []
    exclude = dish_key(title, description, toppings)
    if _has_fts(conn):
        match = " OR ".join(f'"{token}"' for token in tokens)
        rows = conn.execute(
            "SELECT dish_key FROM dish_fts WHERE dish_fts MATCH ? ORDER BY bm25(dish_fts) LIMIT ?",
            (match, limit + 1),
        ).fetchall()
        keys = [row["dish_key"] for row in rows]
    else:
        wanted = set(tokens)
        scored = []
        for row in conn.execute("SELECT key, search_text FROM dishes"):
            have = set(row["search_text"].split())
            score = len(wanted & have) / len(wanted | have)
            if score > 0:
                scored.append((score, row["key"]))
        keys = [key for _, key in sorted(scored, reverse=True)[: limit + 1]]
    out = []
    for key in keys:
        if key == exclude:
            continue
        row = conn.execute("SELECT * FROM dishes WHERE key = ?", (key,)).fetchone()
        if row:
            out.append(_summary(conn, row))
    return out[:limit]
