"""Endpoints del router de preguntas, para que Home Assistant pregunte a
JARVIS a qué destino va una pregunta y, después, valore la respuesta.

Seguridad: las rutas heredan la protección de `web_auth.OriginGuard` (un
POST necesita un Origin permitido o el token de herramientas) y, desde la
red, el acceso pasa por Caddy con autenticación básica. No ejecutan nada:
solo deciden y registran.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from agents.router import route as decide_route
from agents.store import QuestionStore


class AskBody(BaseModel):
    text: str = Field(min_length=1, max_length=1000)


class RateBody(BaseModel):
    id: int = Field(ge=1)
    good: bool


def mount_router_routes(app: FastAPI, db_path: Path | str | None = None) -> QuestionStore:
    """Añade `/api/router/decide` y `/api/router/rate` a la aplicación.

    `db_path` por defecto es `router.db` dentro de la carpeta de datos de JARVIS.
    Devuelve el almacén para que quien lo monte pueda cerrarlo.
    """
    if db_path is None:
        from data_paths import data_dir

        db_path = data_dir() / "router.db"
    store = QuestionStore(db_path)

    @app.post("/api/router/decide")
    async def router_decide(body: AskBody) -> dict:
        decision = decide_route(body.text)
        question_id = store.log(body.text, decision.route, decision.reason)
        return {"id": question_id, "route": decision.route, "reason": decision.reason}

    @app.post("/api/router/rate")
    async def router_rate(body: RateBody) -> dict:
        try:
            store.rate(body.id, body.good)
        except KeyError:
            raise HTTPException(status_code=404, detail="pregunta no encontrada") from None
        return {"ok": True}

    return store
