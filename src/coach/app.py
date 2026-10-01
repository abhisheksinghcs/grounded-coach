"""FastAPI application: session negotiation + sideband control WebSocket.

Endpoints:
  * ``GET  /``               - serves the browser coaching UI (static).
  * ``GET  /api/config``     - browser-safe runtime flags (no secrets).
  * ``POST /api/session``    - mints a short-lived ephemeral realtime token.
  * ``WS   /ws/sideband``    - control channel (events up, commands down).

Secrets (API key / Entra token) and Azure AI Search execution stay here,
server-side. The browser only ever receives an ephemeral ``ek_...`` token.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from coach.config import Settings, get_settings
from coach.persona import CustomerProfile, resolve_persona
from coach.policy.response_policy import ResponsePolicy
from coach.realtime.controller import SidebandController
from coach.realtime.session import (
    SessionError,
    TokenProvider,
    mint_ephemeral_session,
)
from coach.retrieval.search_adapter import RetrievalError, SearchAdapter

logger = logging.getLogger("coach.app")

STATIC_DIR = Path(__file__).parent / "static"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    # Ensure the `coach` loggers surface at INFO even under uvicorn, which does
    # not configure third-party loggers by default.
    coach_logger = logging.getLogger("coach")
    if coach_logger.level == logging.NOTSET:
        coach_logger.setLevel(logging.INFO)
    if not coach_logger.handlers and not logging.getLogger().handlers:
        _h = logging.StreamHandler()
        _h.setFormatter(logging.Formatter("%(levelname)s:%(name)s:%(message)s"))
        coach_logger.addHandler(_h)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # One shared async HTTP client for minting tokens.
        app.state.http = httpx.AsyncClient(timeout=15.0)
        app.state.token_provider = TokenProvider(settings)
        # Active persona (resolved from the default profile until tailored).
        app.state.profile = CustomerProfile()
        app.state.persona = resolve_persona(settings, app.state.profile)
        yield
        await app.state.http.aclose()

    app = FastAPI(title="Grounded Coach", lifespan=lifespan)
    app.state.settings = settings

    @app.get("/api/config")
    async def api_config() -> JSONResponse:
        # Browser-safe flags only.
        return JSONResponse(
            {
                "spoken_mode": settings.coach_spoken_mode,
                "auto_response": settings.coach_auto_response,
                "persist_transcripts": settings.coach_persist_transcripts,
            }
        )

    @app.post("/api/session")
    async def api_session(request: Request) -> JSONResponse:
        # The browser may send a tailored customer profile to role-play.
        profile = CustomerProfile()
        try:
            body = await request.json()
            if isinstance(body, dict) and isinstance(body.get("profile"), dict):
                profile = CustomerProfile.from_dict(body["profile"])
        except Exception:
            pass
        app.state.profile = profile
        app.state.persona = resolve_persona(settings, profile)
        try:
            session = await mint_ephemeral_session(
                settings,
                app.state.token_provider,
                client=app.state.http,
                instructions=app.state.persona,
            )
        except SessionError as exc:
            status = exc.status_code or 502
            logger.warning("Session mint failed: %s", exc)
            return JSONResponse({"error": str(exc)}, status_code=status)
        return JSONResponse(session.to_public_dict())

    @app.post("/api/search")
    async def api_search(payload: dict) -> JSONResponse:
        """Standalone retrieval endpoint (no audio) for testing grounding."""
        query = (payload or {}).get("query", "")
        adapter = SearchAdapter(settings, client=app.state.http)
        try:
            docs = await adapter.search(query)
        except RetrievalError as exc:
            status = exc.status_code or 502
            return JSONResponse({"error": str(exc)}, status_code=status)
        return JSONResponse({"count": len(docs), "results": [d.as_dict() for d in docs]})

    @app.websocket("/ws/sideband")
    async def ws_sideband(ws: WebSocket) -> None:
        await ws.accept()

        async def send(command: dict) -> None:
            await ws.send_json(command)

        retriever = SearchAdapter(settings, client=app.state.http)
        # Use the persona tailored by the most recent /api/session call.
        persona = getattr(app.state, "persona", None)
        policy = ResponsePolicy(settings, persona=persona)
        controller = SidebandController(
            settings, send, retriever=retriever, response_policy=policy
        )
        try:
            await controller.start()
            while True:
                event = await ws.receive_json()
                await controller.handle_event(event)
        except WebSocketDisconnect:
            logger.debug("Sideband client disconnected")
        finally:
            await controller.close()

    # Static UI (mounted last so API routes take precedence).
    if STATIC_DIR.exists():
        app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")

    return app


def run() -> None:
    """Console entry point (``coach``)."""
    import uvicorn

    settings = get_settings()
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port)


app = create_app  # convenience for ``uvicorn coach.app:app`` via factory
