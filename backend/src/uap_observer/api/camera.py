"""Local, ephemeral credential handoff; response/error bodies never echo input."""

import json
from urllib.parse import quote, urlsplit, urlunsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, SecretStr, ValidationError

from ..capture.tools import validate_input
from .runtime import Runtime


class ConnectionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: SecretStr
    username: SecretStr = SecretStr("")
    password: SecretStr = SecretStr("")


def connection_url(data: ConnectionInput) -> str:
    source = data.url.get_secret_value().strip()
    if not source.startswith(("rtsp://", "rtsps://")):
        raise ValueError("RTSP required")
    validate_input(source, allow_remote=True)
    user, password = data.username.get_secret_value(), data.password.get_secret_value()
    if user or password:
        parts = urlsplit(source)
        if parts.username is not None or not user:
            raise ValueError("Use either an authenticated URL or separate login fields")
        source = urlunsplit(
            (
                parts.scheme,
                f"{quote(user, safe='')}:{quote(password, safe='')}@{parts.netloc}",
                parts.path,
                parts.query,
                "",
            )
        )
    return source


def camera_router(runtime: Runtime) -> APIRouter:
    router = APIRouter(prefix="/api/v1/camera")

    @router.get("/status")
    def status() -> dict[str, object]:
        return (
            runtime.live.status() if runtime.live else {"state": "ready", "connection": "stopped"}
        )

    @router.post("/start")
    async def start(request: Request) -> dict[str, str]:
        # Browsers must submit from the local application itself. No request-body logging.
        if request.headers.get("origin") not in ("http://127.0.0.1:8000", "http://localhost:8000"):
            raise HTTPException(403, "Open the local setup screen to start commissioning")
        body = bytearray()
        try:
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 8192:
                    raise ValueError("oversized request")
            data = ConnectionInput.model_validate_json(bytes(body))
            source = connection_url(data)
        except ValueError, ValidationError, json.JSONDecodeError:
            raise HTTPException(
                422, "Check the private numeric LAN RTSP address and login fields"
            ) from None
        finally:
            body.clear()
        try:
            return {"job_id": runtime.start_live(source)}
        except ValueError:
            raise HTTPException(
                409, "Capture unavailable: check active jobs and storage reserve"
            ) from None

    @router.post("/stop")
    def stop() -> dict[str, str]:
        if runtime.live:
            runtime.live.stop()
        return {"state": "stop_requested"}

    @router.get("/preview.jpg")
    def preview() -> Response:
        if not runtime.live:
            raise HTTPException(404, "No commissioning preview")
        with runtime.live.preview_lock:
            image = runtime.live.preview
        if not image:
            raise HTTPException(404, "Waiting for an indexed source segment")
        return Response(image, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    return router
