"""Test-only loopback RTSP/RTP-over-TCP server. No external binary or network."""

import base64
import re
import socketserver
import struct
import subprocess
import threading
import time
from pathlib import Path


class RTSPFixture:
    def __init__(
        self, video: Path, frame_limit: int = 80, delay: float = 0.025
    ) -> None:
        data = subprocess.check_output(
            [
                "ffmpeg",
                "-v",
                "error",
                "-i",
                str(video),
                "-an",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-tune",
                "zerolatency",
                "-g",
                "20",
                "-x264-params",
                "aud=1:repeat-headers=1",
                "-f",
                "h264",
                "pipe:1",
            ],
            timeout=20,
        )
        units = [u for u in re.split(b"\x00\x00(?:\x00)?\x01", data) if u]
        self.sps = next(u for u in units if u[0] & 31 == 7)
        self.pps = next(u for u in units if u[0] & 31 == 8)
        self.frames: list[list[bytes]] = []
        current: list[bytes] = []
        for unit in units:
            if unit[0] & 31 == 9 and current:
                self.frames.append(current)
                current = []
            current.append(unit)
        if current:
            self.frames.append(current)
        self.frame_limit, self.delay = frame_limit, delay
        self.connections = 0
        owner = self

        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                owner.connections += 1
                self.lock = threading.Lock()
                self.running = True
                stream = self.request.makefile("rb")
                try:
                    while self.running:
                        line = stream.readline()
                        if not line:
                            break
                        if line.startswith(b"$"):
                            break
                        method = line.decode().split(" ")[0]
                        headers = {}
                        while (line := stream.readline()) not in (b"\r\n", b"\n", b""):
                            key, value = line.decode().split(":", 1)
                            headers[key.lower()] = value.strip()
                        body = b""
                        extra = ""
                        if method == "DESCRIBE":
                            sprop = (
                                base64.b64encode(owner.sps).decode()
                                + ","
                                + base64.b64encode(owner.pps).decode()
                            )
                            body = (
                                "v=0\r\no=- 0 0 IN IP4 127.0.0.1\r\ns=Fixture\r\nt=0 0\r\n"
                                "a=control:*\r\nm=video 0 RTP/AVP 96\r\na=rtpmap:96 H264/90000\r\n"
                                f"a=fmtp:96 packetization-mode=1;sprop-parameter-sets={sprop}\r\n"
                                "a=control:trackID=0\r\n"
                            ).encode()
                            extra = f"Content-Type: application/sdp\r\nContent-Base: {owner.url}/\r\n"
                        if method == "SETUP":
                            extra = "Transport: RTP/AVP/TCP;unicast;interleaved=0-1\r\n"
                        response = (
                            f"RTSP/1.0 200 OK\r\nCSeq: {headers.get('cseq', '1')}\r\n"
                            "Session: fixture\r\nPublic: OPTIONS, DESCRIBE, SETUP, PLAY, TEARDOWN\r\n"
                            f"{extra}Content-Length: {len(body)}\r\n\r\n"
                        ).encode() + body
                        with self.lock:
                            self.request.sendall(response)
                        if method == "PLAY":
                            threading.Thread(
                                target=self.send_frames, daemon=True
                            ).start()
                        if method == "TEARDOWN":
                            break
                except (OSError, ValueError):
                    pass
                finally:
                    self.running = False
                    stream.close()

            def send_frames(self):
                sequence = 0
                try:
                    for index in range(owner.frame_limit):
                        if not self.running:
                            break
                        units = owner.frames[index % len(owner.frames)]
                        for ui, unit in enumerate(units):
                            payloads = [unit]
                            if len(unit) > 1000:
                                chunks = [
                                    unit[n : n + 998] for n in range(1, len(unit), 998)
                                ]
                                payloads = [
                                    bytes(
                                        [
                                            (unit[0] & 0xE0) | 28,
                                            (unit[0] & 31)
                                            | (0x80 if n == 0 else 0)
                                            | (0x40 if n == len(chunks) - 1 else 0),
                                        ]
                                    )
                                    + part
                                    for n, part in enumerate(chunks)
                                ]
                            for pi, payload in enumerate(payloads):
                                marker = (
                                    ui == len(units) - 1 and pi == len(payloads) - 1
                                )
                                rtp = (
                                    struct.pack(
                                        "!BBHII",
                                        0x80,
                                        96 | (0x80 if marker else 0),
                                        sequence % 65536,
                                        index * 4500,
                                        12345,
                                    )
                                    + payload
                                )
                                with self.lock:
                                    self.request.sendall(
                                        b"$\x00" + struct.pack("!H", len(rtp)) + rtp
                                    )
                                sequence += 1
                        time.sleep(owner.delay)
                    self.request.shutdown(2)
                except OSError:
                    pass

        class Server(socketserver.ThreadingTCPServer):
            daemon_threads = True
            allow_reuse_address = True

        self.server = Server(("127.0.0.1", 0), Handler)
        self.url = f"rtsp://127.0.0.1:{self.server.server_address[1]}/sky"

    def __enter__(self):
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
