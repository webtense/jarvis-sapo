"""Base de datos del router: una fila por pregunta, con a quién se preguntó,
cuánto tardó, cuánto costó y si la respuesta fue buena.

Es la memoria que luego permite aprender (qué tipo de pregunta sale mejor
en qué destino) y proponer cosas sin actuar sola. Solo SQLite estándar.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS questions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL    NOT NULL,
    text        TEXT    NOT NULL,
    route       TEXT    NOT NULL,
    reason      TEXT    NOT NULL,
    latency_ms  INTEGER,
    cost_usd    REAL,
    ok          INTEGER,            -- 1 buena, 0 mala, NULL sin valorar
    repeated    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS questions_route ON questions(route);
"""


@dataclass(frozen=True)
class RouteStats:
    route: str
    total: int
    rated: int
    good: int

    @property
    def success_rate(self) -> float | None:
        """Tasa de éxito sobre las preguntas valoradas. None si no hay valoraciones."""
        return self.good / self.rated if self.rated else None


class QuestionStore:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(self.path))
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)

    def close(self) -> None:
        self._db.close()

    def log(self, text: str, route: str, reason: str, *, latency_ms: int | None = None,
            cost_usd: float | None = None, now: float | None = None) -> int:
        """Guarda una pregunta y devuelve su id. Marca como repetida si la misma
        pregunta llegó en los últimos 5 minutos: suele significar que la primera
        respuesta no sirvió."""
        ts = time.time() if now is None else now
        repeated = self._db.execute(
            "SELECT 1 FROM questions WHERE text = ? AND ts >= ? LIMIT 1",
            (text, ts - 300),
        ).fetchone() is not None
        cur = self._db.execute(
            "INSERT INTO questions (ts, text, route, reason, latency_ms, cost_usd, repeated)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (ts, text, route, reason, latency_ms, cost_usd, int(repeated)),
        )
        self._db.commit()
        return int(cur.lastrowid)

    def rate(self, question_id: int, good: bool) -> None:
        """Registra si la respuesta fue buena (tu «sí» o «perfecto») o mala («mal», «no»)."""
        cur = self._db.execute(
            "UPDATE questions SET ok = ? WHERE id = ?", (int(good), question_id)
        )
        self._db.commit()
        if cur.rowcount == 0:
            raise KeyError(f"no existe la pregunta {question_id}")

    def stats(self) -> list[RouteStats]:
        """Resumen por destino. Es lo que usará el aprendizaje."""
        rows = self._db.execute(
            "SELECT route,"
            " COUNT(*) AS total,"
            " COUNT(ok) AS rated,"
            " SUM(CASE WHEN ok = 1 THEN 1 ELSE 0 END) AS good"
            " FROM questions GROUP BY route ORDER BY total DESC"
        ).fetchall()
        return [RouteStats(r["route"], r["total"], r["rated"], r["good"] or 0) for r in rows]
