#!/usr/bin/env python3
"""
Lab server: ICMP sniffer + HTTP video sender on server.video_port.

Authorized knocker IPs (after valid knock) can download decrypted video at GET /video/stream.

Usage:
  python scripts/run_lab_server.py --config config/generated/server.yaml
  python scripts/run_lab_server.py --config config/generated/server.yaml --simulation
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from rich.console import Console

ROOT = Path(__file__).resolve().parent.parent
console = Console()

SESSION_GAP = 30.0


class AuthRegistry:
    """Thread-safe authorized client IPs after successful knock."""

    def __init__(self, ttl_seconds: int = 300) -> None:
        self._ttl = ttl_seconds
        self._entries: dict[str, float] = {}
        self._lock = threading.Lock()
        self._allow_all = False

    def authorize(self, ip: str) -> None:
        with self._lock:
            self._entries[ip] = time.time() + self._ttl

    def allow_all(self) -> None:
        self._allow_all = True

    def is_authorized(self, ip: str) -> bool:
        if self._allow_all:
            return True
        now = time.time()
        with self._lock:
            exp = self._entries.get(ip)
            if exp is None:
                return False
            if now > exp:
                del self._entries[ip]
                return False
            return True

    def simulate_authorize(self, ip: str) -> None:
        self.authorize(ip)


def load_encrypted_video(cfg) -> tuple[bytes, str]:
    manifest_path = ROOT / cfg.video.artifact_manifest
    enc_path = ROOT / cfg.video.artifact_enc

    if not manifest_path.is_file() or not enc_path.is_file():
        console.print("[red]Missing video artifacts. Run:[/red]")
        console.print("  python scripts/encrypt_video.py /path/to/your_video.mp4")
        sys.exit(1)

    manifest = json.loads(manifest_path.read_text())
    ciphertext = enc_path.read_bytes()
    filename = manifest.get("source_filename", "video.enc")
    return ciphertext, filename


def make_handler(registry: AuthRegistry, video_bytes: bytes, filename: str):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args) -> None:
            console.print(f"[dim]HTTP {self.address_string()} {fmt % args}[/dim]")

        def do_GET(self) -> None:
            client_ip = self.client_address[0]
            if self.path in ("/", "/health"):
                body = json.dumps({
                    "status": "ok",
                    "authorized": registry.is_authorized(client_ip),
                    "client_ip": client_ip,
                }).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            if self.path == "/video/info":
                body = json.dumps({
                    "filename": filename,
                    "size": len(video_bytes),
                    "requires_auth": True,
                }).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            if self.path == "/video/stream":
                # Always serves the encrypted blob — anyone can download it.
                # Without the key fragment (delivered only via ICMP knock reply),
                # the ciphertext cannot be decrypted.
                enc_filename = filename.rsplit(".", 1)[0] + ".enc"
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(len(video_bytes)))
                self.send_header("Content-Disposition", f'attachment; filename="{enc_filename}"')
                self.end_headers()
                self.wfile.write(video_bytes)
                authorized = registry.is_authorized(client_ip)
                console.print(
                    f"[green]Encrypted video sent to {client_ip} ({len(video_bytes)} bytes)"
                    f"{'  [authorized — key fragment already sent via ICMP]' if authorized else '  [NOT authorized — no key fragment]'}[/green]"
                )
                return

            self.send_response(404)
            self.end_headers()

    return Handler


def run_sniffer(cfg, registry: AuthRegistry, simulation: bool, knocker_ip: str) -> None:
    from nps_lab_el.models.auth import AuthorizationState, CaptureEvent
    from nps_lab_el.platform.capture import CaptureBackend, SimulationCapture
    from nps_lab_el.platform.permissions import require_privileges
    from nps_lab_el.services.auth_engine import AuthEngine
    from nps_lab_el.services.drm_responder import DrmResponder

    require_privileges(simulation=simulation)
    engine = AuthEngine(cfg)
    drm = DrmResponder(cfg) if cfg.drm.enabled else None
    events_by_src: dict[str, list[CaptureEvent]] = {}

    if simulation:
        capture = SimulationCapture(events=[])
        registry.allow_all()
        console.print("[cyan]Simulation: all clients authorized for video download[/cyan]")
    else:
        capture = CaptureBackend(iface=cfg.server.iface, server_ip=cfg.server.ip)

    def process(src_ip: str, events: list[CaptureEvent]) -> None:
        session = engine.process_events(events)
        console.print(f"[bold]Knock from {src_ip}: {session.state}[/bold]")
        if session.state == AuthorizationState.AUTHORIZED:
            registry.authorize(src_ip)
            if drm is not None and not capture.simulation:
                from scapy.all import send as scapy_send

                fragment = drm.compute_fragment(session)
                reply = drm.build_reply_packet(
                    src_ip=cfg.server.ip,
                    dst_ip=src_ip,
                    fragment=fragment,
                    session_nonce=events[0].icmp_id if events else 0,
                )
                scapy_send(reply, verbose=False)
                console.print(f"[green]DRM ICMP reply sent to {src_ip}[/green]")

    def callback(packet) -> None:
        event = capture.parse_packet(packet)
        if event is None:
            return
        src = event.src
        buf = events_by_src.setdefault(src, [])
        if buf and (event.ts - buf[-1].ts) > SESSION_GAP:
            process(src, buf)
            events_by_src[src] = []
            buf = events_by_src[src]
        buf.append(event)

    console.print(f"[green]Sniffer listening on {cfg.server.iface} for {cfg.server.ip}[/green]")
    try:
        capture.start_capture(callback=callback)
    except KeyboardInterrupt:
        pass
    finally:
        for src, buf in events_by_src.items():
            if buf:
                process(src, buf)


def main() -> None:
    parser = argparse.ArgumentParser(description="Lab server: sniffer + video HTTP sender")
    parser.add_argument("--config", required=True, help="Server YAML (config/generated/server.yaml)")
    parser.add_argument("--simulation", action="store_true", help="No raw sockets; pre-authorize knocker")
    args = parser.parse_args()

    sys.path.insert(0, str(ROOT / "src"))
    from nps_lab_el.models.config import load_config

    cfg = load_config(args.config)

    registry = AuthRegistry(ttl_seconds=cfg.auth.ttl_auth_seconds)
    video_bytes, video_name = load_encrypted_video(cfg)

    port = cfg.server.video_port
    bind_ip = "0.0.0.0"
    handler = make_handler(registry, video_bytes, video_name)
    httpd = ThreadingHTTPServer((bind_ip, port), handler)

    console.print(f"[bold green]Video server on http://{cfg.server.ip}:{port}/video/stream[/bold green]")
    console.print(f"[dim]Health: /health | Info: /video/info[/dim]")

    sniffer_thread = threading.Thread(
        target=run_sniffer,
        args=(cfg, registry, args.simulation, cfg.client.ip),
        daemon=True,
    )
    sniffer_thread.start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        console.print("[yellow]Shutting down server[/yellow]")
        httpd.shutdown()


if __name__ == "__main__":
    main()
