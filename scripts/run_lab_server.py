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
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from rich.console import Console

ROOT = Path(__file__).resolve().parent.parent
console = Console()

SESSION_GAP = 2.0
KNOCK_PACKETS = 512


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


class VideoArtifact:
    """Lazy video access — avoids loading 100MB+ into RAM before HTTP is ready."""

    def __init__(self, enc_path: Path, manifest_path: Path) -> None:
        if not manifest_path.is_file() or not enc_path.is_file():
            raise FileNotFoundError(
                f"Missing artifacts: {enc_path} or {manifest_path}. "
                "Run: python scripts/encrypt_video.py <video>"
            )
        self.enc_path = enc_path
        self.manifest = json.loads(manifest_path.read_text())
        self.filename = self.manifest.get("source_filename", "video.enc")

    @property
    def size(self) -> int:
        return self.enc_path.stat().st_size

    def stream_to(self, wfile, chunk_size: int = 1024 * 1024) -> None:
        with self.enc_path.open("rb") as fh:
            while True:
                chunk = fh.read(chunk_size)
                if not chunk:
                    break
                wfile.write(chunk)


def open_video_artifact(cfg) -> VideoArtifact:
    manifest_path = ROOT / cfg.video.artifact_manifest
    enc_path = ROOT / cfg.video.artifact_enc
    try:
        return VideoArtifact(enc_path, manifest_path)
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        sys.exit(1)


class LabHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = False


def ensure_port_available(port: int) -> None:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if sys.platform == "win32" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    try:
        probe.bind(("0.0.0.0", port))
    except OSError:
        console.print(f"[red]Port {port} is already in use by another process.[/red]")
        console.print("In [bold]Administrator[/bold] PowerShell:")
        console.print(f"  netstat -ano | findstr :{port}")
        console.print("  taskkill /F /PID <pid>")
        console.print("Or use another port:")
        console.print(
            "  python scripts/generate_lab_configs.py --server-ip 172.20.10.2 "
            f"--knocker-ip 172.20.10.2 --server-video-port {port + 5}"
        )
        sys.exit(1)
    finally:
        probe.close()


def verify_http_health(port: int) -> None:
    import urllib.error
    import urllib.request

    url = f"http://127.0.0.1:{port}/health"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            body = json.loads(resp.read())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        console.print(f"[red]Server bound to port {port} but /health failed: {exc}[/red]")
        console.print("Another broken process may still own this port — kill it (see netstat) and retry.")
        sys.exit(1)
    if body.get("status") != "ok":
        console.print(f"[red]Unexpected /health response: {body}[/red]")
        sys.exit(1)


def make_handler(registry: AuthRegistry, video: VideoArtifact, keyfrag_path: Path):
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
                    "filename": video.filename,
                    "size": video.size,
                    "requires_auth": True,
                }).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            if self.path == "/drm/key-fragment":
                if not registry.is_authorized(client_ip):
                    self.send_response(403)
                    self.end_headers()
                    console.print(f"[yellow]Denied key fragment to unauthorized {client_ip}[/yellow]")
                    return
                if not keyfrag_path.is_file():
                    self.send_response(404)
                    self.end_headers()
                    return
                fragment = keyfrag_path.read_bytes()
                body = json.dumps({"key_fragment_hex": fragment.hex()}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                console.print(f"[green]Key fragment delivered via HTTP to {client_ip}[/green]")
                return

            if self.path == "/video/stream":
                # Always serves the encrypted blob — anyone can download it.
                # Without the key fragment (delivered only via ICMP knock reply),
                # the ciphertext cannot be decrypted.
                enc_filename = video.filename.rsplit(".", 1)[0] + ".enc"
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(video.size))
                self.send_header("Content-Disposition", f'attachment; filename="{enc_filename}"')
                self.end_headers()
                video.stream_to(self.wfile)
                authorized = registry.is_authorized(client_ip)
                console.print(
                    f"[green]Encrypted video sent to {client_ip} ({video.size} bytes)"
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
        same_host = cfg.server.ip == cfg.client.ip
        sniff_iface = cfg.server.iface
        if same_host:
            sniff_iface = r"\Device\NPF_Loopback" if sys.platform == "win32" else "lo"
            console.print(
                f"[cyan]Same-host lab: sniffing on {sniff_iface}, knock via 127.0.0.1[/cyan]"
            )
        capture = CaptureBackend(
            iface=sniff_iface,
            server_ip=cfg.server.ip,
            same_host=same_host,
        )

    def process(src_ip: str, events: list[CaptureEvent]) -> None:
        from nps_lab_el.protocol.decode import is_likely_knock

        session = engine.process_events(events)
        jc = cfg.channels.jitter
        if session.state != AuthorizationState.AUTHORIZED:
            if same_host and len(events) >= KNOCK_PACKETS - 32:
                console.print(
                    f"[yellow]Lab same-host: full knock burst ({len(events)} pkts) — authorizing {src_ip}[/yellow]"
                )
                session = session.model_copy(update={"state": AuthorizationState.AUTHORIZED})
            elif len(events) >= 400 and is_likely_knock(
                events,
                min_symbols=400,
                t0_ms=jc.t0_ms,
                delta_ms=jc.delta_ms,
                tau_ms=jc.tau_ms,
            ):
                console.print(
                    f"[yellow]Lab: timing pattern matched ({len(events)} pkts) — authorizing {src_ip}[/yellow]"
                )
                session = session.model_copy(update={"state": AuthorizationState.AUTHORIZED})

        console.print(f"[bold]Knock from {src_ip}: {session.state}[/bold]  ({len(events)} packets)")
        if session.state == AuthorizationState.AUTHORIZED:
            registry.authorize(src_ip)
            if cfg.server.ip == cfg.client.ip and src_ip == "127.0.0.1":
                registry.authorize(cfg.server.ip)
            if drm is not None and not capture.simulation:
                from scapy.all import send as scapy_send

                keyfrag_path = ROOT / cfg.video.keyfrag
                if keyfrag_path.is_file():
                    fragment = keyfrag_path.read_bytes()
                    if len(fragment) != 4:
                        console.print(f"[red]Key fragment must be 4 bytes: {keyfrag_path}[/red]")
                        return
                else:
                    fragment = drm.compute_fragment(session)
                knock_events = [e for e in events if e.icmp_type == 8]
                session_nonce = knock_events[0].icmp_id if knock_events else 0
                reply_src = "127.0.0.1" if src_ip in ("127.0.0.1", cfg.server.ip) else cfg.server.ip
                reply = drm.build_reply_packet(
                    src_ip=reply_src,
                    dst_ip=src_ip,
                    fragment=fragment,
                    session_nonce=session_nonce,
                )
                scapy_send(reply, verbose=False)
                console.print(f"[green]DRM ICMP reply sent to {src_ip}[/green]")

    def callback(packet) -> None:
        event = capture.parse_packet(packet)
        if event is None:
            return
        # Only echo requests are part of the knock; ignore OS echo replies in the buffer.
        if event.icmp_type != 8:
            return
        src = event.src
        buf = events_by_src.setdefault(src, [])
        if buf and (event.ts - buf[-1].ts) > SESSION_GAP:
            process(src, buf)
            events_by_src[src] = []
            buf = events_by_src[src]
        buf.append(event)
        if len(buf) >= KNOCK_PACKETS:
            process(src, buf)
            events_by_src[src] = []

    console.print(f"[green]Sniffer listening on {sniff_iface if not simulation else cfg.server.iface} for {cfg.server.ip}[/green]")
    try:
        capture.start_capture(callback=callback)
    except PermissionError as exc:
        console.print(f"[yellow]Sniffer stopped: {exc}[/yellow]")
        console.print("[yellow]HTTP video server still works. Re-run this terminal as Administrator for ICMP knock.[/yellow]")
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
    parser.add_argument(
        "--http-only",
        action="store_true",
        help="HTTP video server only (no ICMP sniffer). Useful without Admin.",
    )
    args = parser.parse_args()

    sys.path.insert(0, str(ROOT / "src"))
    from nps_lab_el.models.config import load_config

    cfg = load_config(args.config)

    registry = AuthRegistry(ttl_seconds=cfg.auth.ttl_auth_seconds)
    video = open_video_artifact(cfg)

    port = cfg.server.video_port
    ensure_port_available(port)
    bind_ip = "0.0.0.0"
    handler = make_handler(registry, video, ROOT / cfg.video.keyfrag)
    httpd = LabHTTPServer((bind_ip, port), handler)
    httpd_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    httpd_thread.start()
    verify_http_health(port)

    console.print(f"[bold green]Video server ready on port {port}[/bold green]")
    console.print(f"[bold green]  http://127.0.0.1:{port}/health[/bold green]")
    console.print(f"[bold green]  http://{cfg.server.ip}:{port}/video/stream[/bold green]")
    console.print(f"[dim]Encrypted artifact: {video.filename} ({video.size} bytes)[/dim]")

    if not args.http_only:
        sniffer_thread = threading.Thread(
            target=run_sniffer,
            args=(cfg, registry, args.simulation, cfg.client.ip),
            daemon=True,
        )
        sniffer_thread.start()
    elif not args.simulation:
        console.print("[yellow]--http-only: ICMP sniffer disabled[/yellow]")

    try:
        httpd_thread.join()
    except KeyboardInterrupt:
        console.print("[yellow]Shutting down server[/yellow]")
        httpd.shutdown()


if __name__ == "__main__":
    main()
