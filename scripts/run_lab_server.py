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
import base64
import json
import secrets as _secrets_mod
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from rich.console import Console

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from lab_config_common import (  # noqa: E402
    cross_host_display,
    display_knock_label,
    knock_mode_label,
    lab_banner_lines,
    load_display_overlay,
    overlay_banner_lines,
    peer_generated_config,
    print_display_ips,
    show_layout_banner,
    terminal_peer_ip,
    terminal_server_ip,
    totp_tag,
    validate_generated_pair,
)

console = Console()

SESSION_GAP = 2.0
# Jitter knock: 1 leading echo + one packet per jitter gap
KNOCK_PACKETS = 513
# Header knock (same-host loopback): 21 packets from encode_header_sequence
KNOCK_HEADER_PACKETS = 21


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


# ---------------------------------------------------------------------------
# Segment DRM: session registry + segment key store
# ---------------------------------------------------------------------------

class _SessionEntry:
    def __init__(self, client_ip: str, ttl: int, segment_count: int) -> None:
        self.client_ip = client_ip
        self.ttl = ttl
        self.expires_at = time.time() + ttl
        self.segment_count = segment_count
        self.next_segment = 0
        self.revoked = False

    def is_valid(self) -> bool:
        return not self.revoked and time.time() < self.expires_at

    def touch(self) -> None:
        self.expires_at = time.time() + self.ttl

    def advance(self) -> None:
        self.next_segment += 1
        self.touch()


class SegmentSessionRegistry:
    """Maps session tokens → client sessions for segment-gated delivery."""

    def __init__(self) -> None:
        self._sessions: dict[str, _SessionEntry] = {}
        self._ip_to_token: dict[str, str] = {}
        self._lock = threading.Lock()

    def create(self, client_ip: str, ttl: int, segment_count: int) -> str:
        token = _secrets_mod.token_hex(32)
        with self._lock:
            self._sessions[token] = _SessionEntry(client_ip, ttl, segment_count)
            self._ip_to_token[client_ip] = token
        return token

    def get(self, token: str) -> _SessionEntry | None:
        with self._lock:
            entry = self._sessions.get(token)
            if entry and not entry.is_valid():
                del self._sessions[token]
                return None
            return entry

    def get_token_for_ip(self, ip: str) -> str | None:
        with self._lock:
            token = self._ip_to_token.get(ip)
            if token and token in self._sessions and self._sessions[token].is_valid():
                return token
            return None

    def revoke_ip(self, ip: str) -> None:
        with self._lock:
            token = self._ip_to_token.get(ip)
            if token and token in self._sessions:
                self._sessions[token].revoked = True


def load_segment_keys(cfg, stem: str) -> list[bytes]:
    segkeys_path = ROOT / cfg.video.segkeys
    # Fall back to deriving path from stem if config default wasn't updated
    if not Path(segkeys_path).is_file():
        segkeys_path = ROOT / "server_secrets" / f"{stem}.segkeys"
    if not Path(segkeys_path).is_file():
        console.print(f"[red]Missing segment keys: {segkeys_path}[/red]")
        console.print("Run: python scripts/encrypt_video.py <video.mp4>")
        sys.exit(1)
    return [base64.b64decode(line.strip()) for line in Path(segkeys_path).read_text().splitlines() if line.strip()]


class LabHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = False

    def handle_error(self, request, client_address) -> None:
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionAbortedError, BrokenPipeError, ConnectionResetError)):
            console.print(f"[dim]HTTP client {client_address[0]} disconnected early[/dim]")
            return
        super().handle_error(request, client_address)


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


def make_handler(
    registry: AuthRegistry,
    video: VideoArtifact,
    keyfrag_path: Path,
    *,
    log_client_ip=None,
    seg_registry: "SegmentSessionRegistry | None" = None,
    segment_keys: "list[bytes] | None" = None,
    manifest: "dict | None" = None,
    cfg=None,
):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args) -> None:
            addr = self.client_address[0]
            shown = log_client_ip(addr) if log_client_ip else addr
            console.print(f"[dim]HTTP {shown} {fmt % args}[/dim]")

        def do_GET(self) -> None:
            client_ip = self.client_address[0]
            shown_ip = log_client_ip(client_ip) if log_client_ip else client_ip
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
                    console.print(f"[yellow]Denied key fragment to unauthorized {shown_ip}[/yellow]")
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
                console.print(f"[green]Key fragment delivered via HTTP to {shown_ip}[/green]")
                return

            if self.path == "/session/token":
                if seg_registry is None:
                    self._json(404, {"error": "segment DRM not active"})
                    return
                # Try both the real client IP and loopback alias (same-host case)
                token = seg_registry.get_token_for_ip(client_ip)
                if token is None:
                    token = seg_registry.get_token_for_ip("127.0.0.1")
                if token is None:
                    self._json(403, {"error": "no active session for this IP — complete ICMP knock first"})
                    return
                self._json(200, {"token": token})
                console.print(f"[green]Session token issued to {shown_ip}[/green]")
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
                    f"[green]Encrypted video sent to {shown_ip} ({video.size} bytes)"
                    f"{'  [authorized — key fragment already sent via ICMP]' if authorized else '  [NOT authorized — no key fragment]'}[/green]"
                )
                return

            # --- Segment DRM: GET /video/segment/<N> ---
            if self.path.startswith("/video/segment/") and seg_registry is not None:
                try:
                    seg_idx = int(self.path.split("/")[-1])
                except ValueError:
                    self._json(400, {"error": "invalid segment index"})
                    return

                segs = manifest.get("segments", []) if manifest else []
                if seg_idx < 0 or seg_idx >= len(segs):
                    self._json(404, {"error": "segment not found"})
                    return

                token = self.headers.get("X-Session-Token", "")
                entry = seg_registry.get(token)
                if entry is None:
                    self._json(403, {"error": "invalid or expired session — complete ICMP knock first"})
                    return
                if entry.client_ip != client_ip:
                    self._json(403, {"error": "session token bound to different IP"})
                    return
                if seg_idx != entry.next_segment:
                    self._json(409, {"error": f"out of order — expected segment {entry.next_segment}"})
                    return

                seg_meta = segs[seg_idx]
                seg_path = ROOT / "artifacts" / seg_meta["file"]
                if not seg_path.is_file():
                    self._json(500, {"error": "segment file missing on server"})
                    return

                seg_data = seg_path.read_bytes()
                entry.advance()
                is_last = entry.next_segment >= len(segs)

                # Deliver next segment's key via 8 ICMP replies BEFORE sending the
                # HTTP response body.  The client starts sniffing, then fires the HTTP
                # request; the server must transmit the ICMP packets while the client
                # is still in the sniff window — i.e. before the HTTP response arrives.
                if cfg is not None and segment_keys is not None and not getattr(cfg, '_simulation', False):
                    next_idx = entry.next_segment  # already advanced
                    if next_idx < len(segment_keys):
                        from nps_lab_el.services.drm_responder import DrmResponder
                        from scapy.all import send as scapy_send
                        session_nonce = int(token[:4], 16) & 0xFFFF
                        same_host = client_ip in ("127.0.0.1", cfg.server.ip)
                        # On same-host, send to 127.0.0.1 so the packet flows through
                        # \Device\NPF_Loopback (packets to the real IP go to real adapter)
                        reply_src = "127.0.0.1" if same_host else cfg.server.ip
                        reply_dst = "127.0.0.1" if same_host else client_ip
                        drm = DrmResponder(cfg)
                        pkts = drm.build_reply_packets_full_key(reply_src, reply_dst, segment_keys[next_idx], session_nonce)
                        for p in pkts:
                            scapy_send(p, verbose=False)
                        console.print(f"[green]Seg {next_idx} key sent via 8 ICMP replies → {shown_ip}[/green]")

                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(len(seg_data)))
                self.send_header("X-Segment-Index", str(seg_idx))
                self.send_header("X-Segment-Count", str(len(segs)))
                self.send_header("X-Session-Token", token)
                self.send_header("X-Last-Segment", "true" if is_last else "false")
                self.end_headers()
                self.wfile.write(seg_data)
                console.print(f"[green]Segment {seg_idx}/{len(segs)-1} → {shown_ip} ({len(seg_data)} bytes){'  [last]' if is_last else ''}[/green]")
                if is_last:
                    entry.revoked = True
                return

            self.send_response(404)
            self.end_headers()

        def _json(self, code: int, body: dict) -> None:
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return Handler


def run_sniffer(
    cfg, registry: AuthRegistry, simulation: bool, knocker_ip: str, config_path: Path,
    seg_registry: "SegmentSessionRegistry | None" = None,
    segment_keys: "list[bytes] | None" = None,
) -> None:
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
        overlay = load_display_overlay()
        if same_host:
            sniff_iface = r"\Device\NPF_Loopback" if sys.platform == "win32" else "lo"
            if cross_host_display(overlay):
                si = str(overlay["server_ip"])
                kc = str(overlay["knocker_client_ip"])
                console.print(
                    f"[cyan]Cross-host display (one PC): client {kc} → server {si}[/cyan]"
                )
                console.print(
                    f"[dim]  Sniffer on {sniff_iface}; generated YAML same-host for loopback knock.[/dim]"
                )
            else:
                knock_via = terminal_peer_ip(
                    "127.0.0.1",
                    role="server",
                    overlay=overlay,
                    cfg_server_ip=cfg.server.ip,
                    cfg_client_ip=cfg.client.ip,
                )
                console.print(
                    f"[cyan]Same-host lab: sniffing on {sniff_iface}, knock via {knock_via}[/cyan]"
                )
                console.print(
                    "[dim]  Keep generated knocker.yaml client.ip equal to server.ip on one PC.[/dim]"
                )
        elif overlay and cross_host_display(overlay):
            si = str(overlay["server_ip"])
            kc = str(overlay["knocker_client_ip"])
            console.print(f"[cyan]Cross-host lab: client {kc} → server {si}[/cyan]")
        capture = CaptureBackend(
            iface=sniff_iface,
            server_ip=cfg.server.ip,
            same_host=same_host,
        )

    def process(src_ip: str, events: list[CaptureEvent]) -> None:
        try:
            session = engine.process_events(events)
            if session.state == AuthorizationState.DENIED:
                for relaxed_tau in (25, 50):
                    retry = engine.process_events(events, jitter_tau_ms=relaxed_tau)
                    if retry.state == AuthorizationState.AUTHORIZED:
                        console.print(f"[dim]  jitter decode ok with tau_ms={relaxed_tau}[/dim]")
                        session = retry
                        break
        except Exception as exc:
            console.print(f"[red]Knock processing error from {src_ip}: {exc}[/red]")
            return
        overlay = load_display_overlay()
        shown_src = terminal_peer_ip(
            src_ip,
            role="server",
            overlay=overlay,
            cfg_server_ip=cfg.server.ip,
            cfg_client_ip=cfg.client.ip,
        )
        if overlay is not None:
            si = overlay["server_ip"]
            kc = overlay["knocker_client_ip"]
            mode, _ = display_knock_label(overlay)
            console.print(
                f"[bold]Knock from {shown_src}: {session.state}[/bold]  ({len(events)} packets)  "
                f"[dim]server.ip={si} client.ip={kc} mode={mode}[/dim]"
            )
        else:
            peer_cfg, _ = peer_generated_config("server", config_path)
            kc = peer_cfg.client.ip if peer_cfg is not None else cfg.client.ip
            mode, _ = knock_mode_label(cfg.server.ip, kc)
            console.print(
                f"[bold]Knock from {shown_src}: {session.state}[/bold]  ({len(events)} packets)  "
                f"[dim]server.ip={cfg.server.ip} client.ip={kc} mode={mode}[/dim]"
            )
        if session.state == AuthorizationState.DENIED:
            peer_cfg, _ = peer_generated_config("server", config_path)
            if peer_cfg is not None and cfg.auth.totp_secret != peer_cfg.auth.totp_secret:
                console.print(
                    f"[yellow]  DENIED: TOTP mismatch — server …{totp_tag(cfg.auth.totp_secret)} "
                    f"vs knocker …{totp_tag(peer_cfg.auth.totp_secret)}[/yellow]"
                )
            else:
                console.print(
                    "[dim]  DENIED: bad timing decode or MAC mismatch "
                    "(cross-host: use jitter; same-host: check header/TOTP match)[/dim]"
                )
        if session.state == AuthorizationState.AUTHORIZED:
            registry.authorize(src_ip)
            # Client often talks HTTP via 127.0.0.1 while knock src is LAN/loopback.
            registry.authorize("127.0.0.1")
            if cfg.server.ip == cfg.client.ip and src_ip == "127.0.0.1":
                registry.authorize(cfg.server.ip)
            if drm is not None and not capture.simulation:
                from scapy.all import send as scapy_send

                knock_events = [e for e in events if e.icmp_type == 8]
                session_nonce = knock_events[0].icmp_id if knock_events else 0
                reply_src = "127.0.0.1" if src_ip in ("127.0.0.1", cfg.server.ip) else cfg.server.ip

                # Segment DRM: create session token and deliver segment 0 key via 8 ICMP replies
                if seg_registry is not None and segment_keys:
                    seg_count = len(segment_keys)
                    token = seg_registry.create(src_ip, cfg.video.session_ttl_seconds, seg_count)
                    # Also authorize the loopback alias so HTTP requests work on same host
                    seg_registry.create("127.0.0.1", cfg.video.session_ttl_seconds, seg_count)
                    console.print(f"[green]Session created for {shown_src}  token={token[:8]}…[/green]")
                    pkts = drm.build_reply_packets_full_key(reply_src, src_ip, segment_keys[0], session_nonce)
                    for p in pkts:
                        scapy_send(p, verbose=False)
                    console.print(f"[green]Segment 0 key sent via 8 ICMP replies → {shown_src}[/green]")
                else:
                    # Legacy single-file mode: send 4-byte key fragment
                    keyfrag_path = ROOT / cfg.video.keyfrag
                    if keyfrag_path.is_file():
                        fragment = keyfrag_path.read_bytes()
                        if len(fragment) != 4:
                            console.print(f"[red]Key fragment must be 4 bytes: {keyfrag_path}[/red]")
                            return
                    else:
                        fragment = drm.compute_fragment(session)
                    reply = drm.build_reply_packet(
                        src_ip=reply_src,
                        dst_ip=src_ip,
                        fragment=fragment,
                        session_nonce=session_nonce,
                    )
                    scapy_send(reply, verbose=False)
                    console.print(f"[green]DRM ICMP reply sent to {shown_src}[/green]")

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
        flush_at = KNOCK_HEADER_PACKETS if same_host else KNOCK_PACKETS
        if len(buf) >= flush_at:
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

    cfg_path = ROOT / args.config if not Path(args.config).is_absolute() else Path(args.config)
    validate_generated_pair(cfg_path, "server", console=console)
    cfg = load_config(str(cfg_path))
    peer_cfg, _ = peer_generated_config("server", cfg_path)
    if show_layout_banner():
        display_overlay = load_display_overlay()
        if display_overlay:
            lines = overlay_banner_lines("server", display_overlay, cfg, cfg_path, peer_cfg=peer_cfg)
        else:
            lines = lab_banner_lines("server", cfg, cfg_path, peer_cfg=peer_cfg)
        for line in lines:
            style = "yellow" if line.startswith("  WARNING") or "MISMATCH" in line else "cyan"
            console.print(f"[{style}]{line}[/{style}]")
    print_display_ips(console)

    registry = AuthRegistry(ttl_seconds=cfg.auth.ttl_auth_seconds)
    video = open_video_artifact(cfg)

    # Load segment keys if available (segment DRM mode)
    manifest = json.loads((ROOT / cfg.video.artifact_manifest).read_text()) if (ROOT / cfg.video.artifact_manifest).is_file() else {}
    stem = Path(manifest.get("source_filename", "sample_video")).stem
    segment_keys: list[bytes] | None = None
    seg_registry: SegmentSessionRegistry | None = None
    if manifest.get("segment_count"):
        segment_keys = load_segment_keys(cfg, stem)
        seg_registry = SegmentSessionRegistry()
        console.print(f"[green]Segment DRM: {len(segment_keys)} segment keys loaded[/green]")
        if args.simulation and seg_registry is not None:
            # Pre-create a simulation session so the client can fetch segments without knock
            sim_token = seg_registry.create("127.0.0.1", cfg.video.session_ttl_seconds, len(segment_keys))
            console.print(f"[cyan]Simulation session token: {sim_token}[/cyan]")

    port = cfg.server.video_port
    ensure_port_available(port)
    bind_ip = "0.0.0.0"
    display_overlay = load_display_overlay()
    log_client_ip = lambda ip: terminal_peer_ip(
        ip,
        role="server",
        overlay=display_overlay,
        cfg_server_ip=cfg.server.ip,
        cfg_client_ip=cfg.client.ip,
    )
    handler = make_handler(
        registry, video, ROOT / cfg.video.keyfrag,
        log_client_ip=log_client_ip,
        seg_registry=seg_registry,
        segment_keys=segment_keys,
        manifest=manifest,
        cfg=cfg,
    )
    httpd = LabHTTPServer((bind_ip, port), handler)
    httpd_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    httpd_thread.start()
    verify_http_health(port)

    health_host = terminal_server_ip(
        "127.0.0.1",
        overlay=display_overlay,
        cfg_server_ip=cfg.server.ip,
    )
    console.print(f"[bold green]Video server ready on port {port}[/bold green]")
    console.print(f"[bold green]  http://{health_host}:{port}/health[/bold green]")
    console.print(f"[bold green]  http://{cfg.server.ip}:{port}/video/stream[/bold green]")
    if seg_registry is not None:
        console.print(f"[bold green]  http://{cfg.server.ip}:{port}/video/segment/<N>[/bold green]")
    console.print(f"[dim]Encrypted artifact: {video.filename} ({video.size} bytes)[/dim]")

    if not args.http_only:
        sniffer_thread = threading.Thread(
            target=run_sniffer,
            args=(cfg, registry, args.simulation, cfg.client.ip, cfg_path),
            kwargs={"seg_registry": seg_registry, "segment_keys": segment_keys},
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
