from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import cv2
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .api.review import review_router
from .api.runtime import Runtime
from .config import load_station
from .models import FramePacket, SourceHealth, StreamInfo
from .sources import CameraSource, DecodedFrame, SyntheticSource, VideoFileSource

ROOT = Path(__file__).resolve().parents[3]


def create_app(root: Path = ROOT, data_directory: Path | None = None) -> FastAPI:
    # This finite fixture preview deliberately does not implement live capture queues.
    runtime = Runtime(root, data_directory)
    cache: dict[str, tuple[StreamInfo, list[DecodedFrame], SourceHealth]] = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
        load_station(root / "config/station.example.yaml")
        sources: dict[str, CameraSource] = {"synthetic": SyntheticSource()}
        file = root / "artifacts/fixtures/sky.mkv"
        if file.exists():
            sources["video-file"] = VideoFileSource(file)
        for name, source in sources.items():
            try:
                info = source.probe()
                source.start()
                frames = list(source.frames())
                cache[name] = info, frames, source.get_health()
            finally:
                source.stop()
        yield
        runtime.close()
        cache.clear()

    app = FastAPI(
        title="UAP Observer — offline beta",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
    )

    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]"])

    @app.get("/docs", include_in_schema=False)
    def local_docs() -> HTMLResponse:
        return HTMLResponse(
            """<!doctype html><html lang="en"><meta charset="utf-8"><title>UAP Observer API</title><body><h1>Local API documentation</h1><p>This page and the application work offline. <a href="/openapi.json">Download the OpenAPI JSON schema</a>.</p><ul><li>GET /api/v1/health and /stats</li><li>POST /api/v1/demo/start; GET /api/v1/jobs/{job_id}</li><li>GET /api/v1/events?page=1&amp;q=</li><li>GET /api/v1/events/{event_id}</li><li>POST /api/v1/events/{event_id}/reviews</li><li>POST /api/v1/events/{event_id}/export</li><li>GET and PUT /api/v1/settings</li><li>WS /api/v1/ws/updates</li></ul><p>Binding is local-only. Real-camera commissioning is gated.</p></body></html>"""
        )

    app.state.runtime = runtime
    app.include_router(review_router(runtime))

    @app.middleware("http")
    async def local_origin(request, call_next):  # type: ignore[no-untyped-def]
        if request.method in ("POST", "PUT", "DELETE"):
            origin = request.headers.get("origin")
            if origin and origin not in (
                "http://127.0.0.1:8000",
                "http://localhost:8000",
                "http://127.0.0.1:5173",
                "http://localhost:5173",
            ):
                return Response("Cross-origin mutation rejected", status_code=403)
        return await call_next(request)

    @app.get("/api/v1/health")
    def health() -> dict[str, object]:
        return {
            "status": "ok",
            "mode": "offline-fixture",
            "capabilities": [
                "preview",
                "file_capture",
                "detection",
                "tracking",
                "review",
                "export",
            ],
            "sources": {key: value[2].model_dump() for key, value in cache.items()},
        }

    @app.get("/api/v1/sources", response_model=list[StreamInfo])
    def sources() -> list[StreamInfo]:
        return [entry[0] for entry in cache.values()]

    def frame(source: str, index: int) -> DecodedFrame:
        if source not in cache:
            raise HTTPException(404, "Source unavailable; run make fixtures")
        frames = cache[source][1]
        if not 0 <= index < len(frames):
            raise HTTPException(404, "Frame outside fixture")
        return frames[index]

    @app.get("/api/v1/preview/{source}/{index}", response_model=FramePacket)
    def packet(source: Literal["synthetic", "video-file"], index: int) -> FramePacket:
        return frame(source, index).packet

    @app.get("/api/v1/preview/{source}/{index}/image")
    def image(source: Literal["synthetic", "video-file"], index: int) -> Response:
        decoded = frame(source, index)
        ok, encoded = cv2.imencode(".jpg", decoded.pixels)
        if not ok:
            raise HTTPException(500, "Preview encoding failed")
        return Response(
            encoded.tobytes(), media_type="image/jpeg", headers={"Cache-Control": "no-store"}
        )

    dist = root / "frontend/dist"
    if dist.exists():
        app.mount("/", StaticFiles(directory=dist, html=True), name="frontend")
    return app
