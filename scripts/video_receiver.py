#!/usr/bin/env python3
"""
Lab knocker/client: send ICMP knock, then download video from server HTTP port.

Usage:
  python scripts/video_receiver.py --config config/generated/knocker.yaml
  python scripts/video_receiver.py --config config/generated/knocker.yaml --simulation
  python scripts/video_receiver.py --config config/generated/knocker.yaml --skip-knock
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from rich.console import Console

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from lab_config_common import (  # noqa: E402
    cross_host_display,
    display_ips_differ,
    display_knocker_ip,
    display_knock_label,
    display_server_ip,
    http_hosts_for,
    lab_banner_lines,
    load_display_overlay,
    overlay_banner_lines,
    peer_generated_config,
    print_display_ips,
    show_layout_banner,
    terminal_http_host,
    terminal_peer_ip,
    totp_tag,
    validate_generated_pair,
)

console = Console()


def precise_sleep(seconds: float) -> None:
    """More accurate inter-packet delay than time.sleep on Windows."""
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        pass


def _estimate_knock_seconds(jitter_sequence: list[float]) -> float:
    return sum(jitter_sequence)


def resolve_knock_target(server_ip: str, client_ip: str) -> str:
    """Same PC: use loopback so Npcap sees packets; LAN IP self-ping is often invisible."""
    if server_ip == client_ip:
        return "127.0.0.1"
    return server_ip


def _reply_sources(knock_target: str, server_ip: str) -> set[str]:
    sources = {knock_target, server_ip}
    if knock_target == "127.0.0.1":
        sources.add("127.0.0.1")
    return sources


def _sniff_for_full_key(
    knock_target: str,
    server_ip: str,
    session_nonce: int,
    result: list[bytes],
    iface: str | None = None,
) -> None:
    """Collect 8 DRM ICMP reply packets and reassemble the 32-byte segment key."""
    from scapy.all import ICMP, IP, sniff
    from nps_lab_el.services.drm_responder import DRM_ICMP_CODE, FULL_KEY_REPLY_PACKETS, DrmResponder

    allowed_src = _reply_sources(knock_target, server_ip)
    collected: list = []

    def _on_reply(pkt) -> None:
        if not pkt.haslayer(IP) or not pkt.haslayer(ICMP) or pkt[ICMP].type != 0:
            return
        if pkt[ICMP].code != DRM_ICMP_CODE:
            return
        if pkt[IP].src not in allowed_src:
            return
        collected.append(pkt)
        if len(collected) >= FULL_KEY_REPLY_PACKETS:
            key = DrmResponder.extract_full_key_from_replies(collected, session_nonce)
            if key is not None:
                result.append(key)

    sniff_kwargs: dict = {
        "filter": "icmp",
        "prn": _on_reply,
        "store": 0,  # don't accumulate all packets in RAM
        "stop_filter": lambda _: bool(result),
        "timeout": 45,
        # No count limit — OS auto-replies to the knock fill a small count
        # before the server's 8 DRM reply packets arrive.
    }
    if iface:
        sniff_kwargs["iface"] = iface
    sniff(**sniff_kwargs)


def _build_auth_token(cfg, channel_mask: int) -> "AuthToken":
    from nps_lab_el.crypto.totp import get_counter
    from nps_lab_el.models.auth import AuthToken

    counter = get_counter(cfg.auth.window_seconds)
    payload = os.urandom(16)
    mac_input = payload + counter.to_bytes(8, "big") + channel_mask.to_bytes(1, "big")
    mac = hmac.new(cfg.auth.totp_secret.encode(), mac_input, hashlib.sha256).digest()[:16]
    return AuthToken(payload=payload, totp_counter=counter, channel_mask=channel_mask, mac=mac)


def send_knock(cfg, server_ip: str, simulation: bool) -> bytes | None:
    """Send ICMP knock and return the 32-byte segment 0 key from 8 ICMP replies, or None on simulation."""
    from nps_lab_el.platform.permissions import require_privileges
    from nps_lab_el.protocol.encode_jitter import encode_with_fec

    require_privileges(simulation=simulation)

    overlay = load_display_overlay()
    show_cross = overlay is not None and display_ips_differ(overlay)
    show_ip = lambda ip: terminal_peer_ip(
        ip,
        role="knocker",
        overlay=overlay,
        cfg_server_ip=server_ip,
        cfg_client_ip=cfg.client.ip,
    )

    knock_target = resolve_knock_target(server_ip, cfg.client.ip)
    use_header = knock_target == "127.0.0.1" and cfg.channels.header.enabled

    if use_header:
        from nps_lab_el.protocol.encode_header import encode_header_sequence

        channel_mask = 0x02
        token = _build_auth_token(cfg, channel_mask)
        header_pairs = encode_header_sequence(
            token.to_bits(), lfsr_seed=cfg.channels.header.lfsr_seed
        )
        if simulation:
            console.print(f"[cyan]Simulation header knock[/cyan] ({len(header_pairs)} packets)")
            return None
        est_sec = len(header_pairs) * 0.01
        if show_cross:
            mode, _ = display_knock_label(overlay)
            kc = overlay["knocker_client_ip"]
            si = overlay["server_ip"]
            console.print(
                f"[green]Sending knock from {kc} to {si}[/green] "
                f"({mode}, {len(header_pairs)} packets, ~{est_sec:.1f}s)..."
            )
        else:
            console.print(
                f"[green]Sending header knock to {show_ip(knock_target)}[/green] "
                f"({len(header_pairs)} packets, ~{est_sec:.1f}s)..."
            )
    else:
        channel_mask = 0x01
        token = _build_auth_token(cfg, channel_mask)
        jitter_sequence = encode_with_fec(
            token.to_bits(),
            n=cfg.fec.n,
            k=cfg.fec.k,
            t0_ms=cfg.channels.jitter.t0_ms,
            delta_ms=cfg.channels.jitter.delta_ms,
        )
        if simulation:
            console.print(f"[cyan]Simulation knock[/cyan] ({len(jitter_sequence)} intervals, not sent)")
            return None
        est_sec = _estimate_knock_seconds(jitter_sequence)
        packet_count = len(jitter_sequence) + 1
        if show_cross:
            kc = overlay["knocker_client_ip"]
            si = overlay["server_ip"]
            console.print(
                f"[green]Sending jitter knock from {kc} to {si}[/green] "
                f"({packet_count} packets, ~{est_sec:.0f}s)..."
            )
        else:
            console.print(
                f"[green]Sending jitter knock to {show_ip(knock_target)}[/green] "
                f"({packet_count} packets, ~{est_sec:.0f}s)..."
            )

    console.print("[dim]Server must be running (Admin) with ICMP sniffer — not --http-only[/dim]")

    from scapy.all import ICMP, IP, send

    session_nonce = int.from_bytes(os.urandom(2), "big")
    key_result: list[bytes] = []
    sniff_iface = r"\Device\NPF_Loopback" if sys.platform == "win32" and knock_target == "127.0.0.1" else None
    sniffer = threading.Thread(
        target=_sniff_for_full_key,
        args=(knock_target, server_ip, session_nonce, key_result),
        kwargs={"iface": sniff_iface},
        daemon=True,
    )
    sniffer.start()
    time.sleep(0.5)

    if use_header:
        for i, (ip_id, icmp_seq) in enumerate(header_pairs):
            send(
                IP(dst=knock_target, id=ip_id) / ICMP(type=8, id=session_nonce, seq=icmp_seq),
                verbose=False,
            )
            precise_sleep(0.01)
    else:
        send(IP(dst=knock_target) / ICMP(type=8, id=session_nonce, seq=0), verbose=False)
        total = len(jitter_sequence)
        for i, delay in enumerate(jitter_sequence):
            precise_sleep(delay)
            send(IP(dst=knock_target) / ICMP(type=8, id=session_nonce, seq=i + 1), verbose=False)
            if (i + 1) % 64 == 0 or i + 1 == total:
                pct = 100 * (i + 1) // total
                console.print(f"[dim]  knock progress: {i + 1}/{total} gaps ({pct}%)[/dim]")

    console.print("[green]Knock sent — waiting for 8 ICMP replies (32-byte segment key)...[/green]")
    console.print("[dim]  server needs ~2s after last packet to authorize[/dim]")
    sniffer.join(timeout=60)

    if key_result:
        seg0_key = key_result[0]
        console.print(f"[green]Segment 0 key received ({seg0_key.hex()[:16]}…)[/green]")
        return seg0_key

    console.print("[red]No full-key ICMP replies received — knock failed or server not sniffing[/red]")
    console.print("[yellow]Check:[/yellow] server Admin? sniffer shows AUTHORIZED? same TOTP in both YAMLs?")
    console.print("[yellow]  t0_ms should be 20 (not 1000) — re-run generate_lab_configs.py[/yellow]")
    return None


def fetch_segment(
    http_host: str, video_port: int, seg_idx: int, session_token: str
) -> tuple[bytes, str, bool]:
    """Download one encrypted segment. Returns (ciphertext, updated_token, is_last)."""
    url = f"http://{http_host}:{video_port}/video/segment/{seg_idx}"
    req = urllib.request.Request(url)
    req.add_header("X-Session-Token", session_token)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = resp.read()
            new_token = resp.headers.get("X-Session-Token", session_token)
            is_last = resp.headers.get("X-Last-Segment", "false").lower() == "true"
        return data, new_token, is_last
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        console.print(f"[red]HTTP {exc.code} on segment {seg_idx}: {body}[/red]")
        sys.exit(1)
    except (urllib.error.URLError, TimeoutError) as exc:
        console.print(f"[red]Cannot reach server for segment {seg_idx}: {exc}[/red]")
        sys.exit(1)


def decrypt_segment(ciphertext: bytes, key: bytes, nonce: bytes, aad: bytes) -> bytes:
    from nps_lab_el.crypto.aes_gcm import decrypt_artifact
    try:
        return decrypt_artifact(ciphertext, key, nonce, aad)
    except Exception:
        console.print("[red]Segment decryption failed — wrong key or corrupted data[/red]")
        sys.exit(1)


def fetch_key_fragment_http(http_host: str, video_port: int) -> bytes | None:
    """After a successful knock, server may expose the fragment to authorized clients."""
    url = f"http://{http_host}:{video_port}/drm/key-fragment"
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            data = json.loads(resp.read())
        hex_frag = data.get("key_fragment_hex", "")
        fragment = bytes.fromhex(hex_frag)
        if len(fragment) == 4:
            console.print(f"[green]Key fragment from HTTP ({fragment.hex()})[/green]")
            return fragment
    except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
        console.print(f"[yellow]HTTP key fragment not available: {exc}[/yellow]")
    return None


def fetch_session_token(http_host: str, video_port: int) -> str | None:
    """Retrieve the session token for this client IP from the server after a successful knock."""
    url = f"http://{http_host}:{video_port}/session/token"
    for attempt in range(1, 4):
        try:
            with urllib.request.urlopen(url, timeout=15) as resp:
                data = json.loads(resp.read())
            token = data.get("token", "")
            if token:
                console.print(f"[green]Session token retrieved ({token[:8]}…)[/green]")
                return token
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            pass
        if attempt < 3:
            time.sleep(1)
    console.print("[yellow]Could not retrieve session token from server[/yellow]")
    return None


def fetch_encrypted_video(
    http_host: str, video_port: int, enc_path: Path, *, display_host: str | None = None
) -> None:
    """Download the encrypted .enc file — no auth required, it's just ciphertext."""
    url = f"http://{http_host}:{video_port}/video/stream"
    shown = display_host or http_host
    console.print(f"[bold]Downloading encrypted video from http://{shown}:{video_port}/video/stream[/bold]")
    try:
        with urllib.request.urlopen(url, timeout=600) as resp:
            data = resp.read()
        enc_path.parent.mkdir(parents=True, exist_ok=True)
        enc_path.write_bytes(data)
        console.print(f"[green]Saved encrypted blob: {len(data)} bytes -> {enc_path}[/green]")
    except (urllib.error.URLError, TimeoutError) as exc:
        console.print(f"[red]Cannot reach server: {exc}[/red]")
        sys.exit(1)


def decrypt_video(enc_path: Path, manifest_path: Path, key_fragment: bytes, out_path: Path) -> bool:
    """Decrypt the downloaded .enc file using key_partial from manifest + key_fragment from knock."""
    from nps_lab_el.crypto.aes_gcm import decrypt_artifact
    from nps_lab_el.models.auth import KeyMaterial

    manifest = json.loads(manifest_path.read_text())
    ciphertext = enc_path.read_bytes()
    key_partial = base64.b64decode(manifest["key_partial_b64"])
    nonce = base64.b64decode(manifest["nonce_b64"])
    aad = base64.b64decode(manifest["aad_b64"])

    km = KeyMaterial(key_partial=key_partial, key_fragment=key_fragment)
    try:
        plaintext = decrypt_artifact(ciphertext, km.key_full, nonce, aad)
    except Exception:
        console.print("[red]Decryption failed — key fragment is wrong or data is corrupt[/red]")
        return False

    expected = manifest.get("plaintext_sha256")
    if expected and hashlib.sha256(plaintext).hexdigest() != expected:
        console.print("[red]SHA-256 mismatch after decryption — something went wrong[/red]")
        return False

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(plaintext)
    console.print(f"[green]Decrypted video saved -> {out_path}  (SHA-256 verified)[/green]")
    return True


def _reject_cross_host_on_server_pc(
    server_ip: str, configured_client_ip: str, health_client_ip: str
) -> None:
    """Cross-host YAML on the server machine breaks jitter knock + Wi-Fi sniffer."""
    console.print("[red]This PC is the server, not the knocker.[/red]")
    console.print(
        f"  Health shows your HTTP client as [bold]{health_client_ip}[/bold] "
        f"(server), but knocker.yaml says client.ip=[bold]{configured_client_ip}[/bold]."
    )
    console.print(
        f"  Jitter knocks to {server_ip} from this machine are not decoded on the Wi-Fi sniffer."
    )
    console.print()
    console.print("[yellow]Pick one:[/yellow]")
    console.print(
        f"  [bold]Same PC test:[/bold] set client.ip = {server_ip} in [bold]both[/bold] "
        "server.yaml and knocker.yaml, restart server (loopback sniffer)."
    )
    console.print(
        f"  [bold]Two PC test:[/bold] run this script on the machine that has IP {configured_client_ip}."
    )
    console.print()
    console.print(
        f"  Regenerate same-PC: python scripts/generate_lab_configs.py "
        f"--server-ip {server_ip} --knocker-ip {server_ip} --server-video-port 8780"
    )
    sys.exit(1)


def check_health(
    server_ip: str,
    video_port: int,
    client_ip: str,
    *,
    overlay: dict | None = None,
    cfg_client_ip: str | None = None,
) -> tuple[str, str]:
    """Return (http_host, client_ip seen by server /health)."""
    cross_host_cfg = client_ip != server_ip
    show_si = display_server_ip(overlay, server_ip)
    show_kc = display_knocker_ip(overlay, client_ip)
    errors: list[str] = []
    for host in http_hosts_for(server_ip, client_ip):
        url = f"http://{host}:{video_port}/health"
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:
                info = json.loads(resp.read())
            shown_host = terminal_http_host(
                host,
                role="knocker",
                overlay=overlay,
                cfg_server_ip=server_ip,
                cfg_client_ip=cfg_client_ip or client_ip,
            )
            shown_info = dict(info)
            if "client_ip" in shown_info:
                shown_info["client_ip"] = terminal_peer_ip(
                    str(shown_info["client_ip"]),
                    role="knocker",
                    overlay=overlay,
                    cfg_server_ip=server_ip,
                    cfg_client_ip=cfg_client_ip or client_ip,
                )
            console.print(f"[dim]Server health ({shown_host}): {shown_info}[/dim]")
            seen = str(info.get("client_ip", ""))
            if (
                cross_host_cfg
                and seen == server_ip
                and not cross_host_display(overlay)
            ):
                _reject_cross_host_on_server_pc(server_ip, client_ip, seen)
            return host, seen
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            errors.append(f"{url} -> {exc}")

    console.print("[red]Server not reachable.[/red]")
    for line in errors:
        console.print(f"[red]  {line}[/red]")
    if cross_host_cfg or cross_host_display(overlay):
        console.print("[yellow]Cross-host checklist:[/yellow]")
        console.print(
            f"  1. Server must run on [bold]{show_si}[/bold] (Admin): "
            "python scripts/run_lab_server.py --config config/generated/server.yaml"
        )
        console.print(
            f"  2. Run this client on [bold]{show_kc}[/bold] "
            "(one-PC demo: keep generated YAML client.ip == server.ip)"
        )
        console.print(f"  3. On knocker PC: Test-NetConnection {show_si} -Port {video_port}")
        console.print(
            f"  4. On server PC: allow inbound TCP {video_port} "
            "(Windows Firewall → Python → Private networks)"
        )
        console.print(f"  5. Browser on knocker: http://{show_si}:{video_port}/health")
        if cross_host_display(overlay) and not cross_host_cfg:
            console.print(
                f"  One-PC display: config_ip.yaml shows {show_kc} → {show_si}; "
                "generated YAML stays same-host for knock."
            )
    else:
        console.print("[yellow]Same-host fix:[/yellow]")
        console.print(f"  netstat -ano | findstr :{video_port}")
        console.print("  taskkill /F /PID <pid>  then start ONE server (Admin)")
        console.print(f"  Invoke-RestMethod http://{show_si}:{video_port}/health")
    sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Knock + receive + decrypt video from lab server")
    parser.add_argument("--config", required=True, help="Knocker YAML (config/generated/knocker.yaml)")
    parser.add_argument("--simulation", action="store_true", help="Simulate knock (no ICMP)")
    parser.add_argument("--skip-knock", action="store_true", help="Only download+decrypt (knock already done)")
    parser.add_argument("--key-fragment", help="Hex key fragment (4 bytes) — use with --skip-knock")
    parser.add_argument(
        "--output",
        default="artifacts/received_video.mp4",
        help="Where to save the final decrypted MP4",
    )
    args = parser.parse_args()

    sys.path.insert(0, str(ROOT / "src"))
    from nps_lab_el.models.config import load_config

    cfg_path = ROOT / args.config if not Path(args.config).is_absolute() else Path(args.config)
    cfg = load_config(str(cfg_path))
    validate_generated_pair(cfg_path, "knocker", console=console)

    server_ip = cfg.server.ip
    video_port = cfg.server.video_port
    out_path = ROOT / args.output
    enc_path = out_path.with_suffix(".enc")
    manifest_path = ROOT / cfg.video.artifact_manifest

    peer_cfg, _ = peer_generated_config("knocker", cfg_path)
    if show_layout_banner():
        display_overlay = load_display_overlay()
        if display_overlay:
            lines = overlay_banner_lines("knocker", display_overlay, cfg, cfg_path, peer_cfg=peer_cfg)
        else:
            lines = lab_banner_lines("knocker", cfg, cfg_path, peer_cfg=peer_cfg)
        for line in lines:
            style = "yellow" if "MISMATCH" in line or line.startswith("  WARNING") else "bold cyan"
            console.print(f"[{style}]{line}[/{style}]")
        console.print()
    print_display_ips(console)

    display_overlay = load_display_overlay()
    http_host, _health_client_ip = check_health(
        server_ip,
        video_port,
        cfg.client.ip,
        overlay=display_overlay,
        cfg_client_ip=cfg.client.ip,
    )
    http_display = terminal_http_host(
        http_host,
        role="knocker",
        overlay=display_overlay,
        cfg_server_ip=server_ip,
        cfg_client_ip=cfg.client.ip,
    )

    # Determine mode: segment DRM (new) vs single-file (legacy)
    import json as _json
    _manifest_data = _json.loads(manifest_path.read_text()) if manifest_path.is_file() else {}
    _segment_mode = bool(_manifest_data.get("segment_count"))

    seg0_key: bytes | None = None
    session_token: str = ""

    if args.skip_knock:
        if args.key_fragment:
            seg0_key = bytes.fromhex(args.key_fragment)
        else:
            console.print("[red]--skip-knock requires --key-fragment <hex>[/red]")
            sys.exit(1)
    else:
        seg0_key = send_knock(cfg, server_ip=server_ip, simulation=args.simulation)
        if seg0_key is None and not args.simulation:
            console.print("[yellow]No DRM ICMP replies — waiting for server, then trying HTTP…[/yellow]")
            time.sleep(4)
            http_fragment = fetch_key_fragment_http(http_host, video_port)
            if http_fragment is None:
                if peer_cfg is not None and cfg.auth.totp_secret != peer_cfg.auth.totp_secret:
                    console.print(
                        f"[red]Knock denied — TOTP mismatch (knocker …{totp_tag(cfg.auth.totp_secret)} "
                        f"vs server …{totp_tag(peer_cfg.auth.totp_secret)})[/red]"
                    )
                else:
                    console.print("[red]Knock did not authorize (check server log: AUTHORIZED vs DENIED).[/red]")
                sys.exit(1)
            seg0_key = http_fragment  # legacy 4-byte fallback

    # -----------------------------------------------------------------------
    # Segment DRM mode: download each segment, receive its key via ICMP, decrypt
    # -----------------------------------------------------------------------
    if _segment_mode:
        if seg0_key is None and not args.simulation:
            console.print("[red]No segment 0 key — cannot proceed[/red]")
            sys.exit(1)

        import base64 as _b64
        aad = _b64.b64decode(_manifest_data["aad_b64"])
        seg_count = _manifest_data["segment_count"]
        sniff_iface = r"\Device\NPF_Loopback" if sys.platform == "win32" and server_ip == cfg.client.ip else None

        # Fetch session token via HTTP (server issues it after the ICMP knock authorizes the IP)
        current_token = ""
        if not args.simulation:
            time.sleep(1)  # give the server a moment to commit the session
            fetched = fetch_session_token(http_host, video_port)
            if fetched is None:
                console.print("[red]No session token — ICMP knock may not have been received by server[/red]")
                sys.exit(1)
            current_token = fetched

        # Server derives nonce for subsequent key packets from the first 2 bytes of the token
        subsequent_nonce = (int(current_token[:4], 16) & 0xFFFF) if current_token else 0

        plaintext_parts: list[bytes] = []
        current_key = seg0_key

        for seg_idx in range(seg_count):
            seg_meta = _manifest_data["segments"][seg_idx]
            nonce = _b64.b64decode(seg_meta["nonce_b64"])
            expected_sha = seg_meta["plaintext_sha256"]

            # Start sniffer for the NEXT key BEFORE fetching this segment.
            # The server sends key N+1 as soon as it responds to the segment N
            # request, so the sniffer must already be running at that moment.
            next_key_result: list[bytes] = []
            next_sniffer: threading.Thread | None = None
            if not args.simulation and seg_idx + 1 < seg_count:
                next_sniffer = threading.Thread(
                    target=_sniff_for_full_key,
                    args=(server_ip, server_ip, subsequent_nonce, next_key_result),
                    kwargs={"iface": sniff_iface},
                    daemon=True,
                )
                next_sniffer.start()
                time.sleep(0.15)  # let the sniffer initialise before we trigger the HTTP fetch

            console.print(f"[bold]Fetching segment {seg_idx + 1}/{seg_count}[/bold]")
            ciphertext, current_token, is_last = fetch_segment(http_host, video_port, seg_idx, current_token)

            if current_key is None:
                console.print(f"[yellow]Simulation: no key for segment {seg_idx}, skipping decrypt[/yellow]")
                continue

            plaintext = decrypt_segment(ciphertext, current_key, nonce, aad)
            actual_sha = hashlib.sha256(plaintext).hexdigest()
            if actual_sha != expected_sha:
                console.print(f"[red]SHA-256 mismatch on segment {seg_idx}[/red]")
                sys.exit(1)
            plaintext_parts.append(plaintext)
            console.print(f"[green]Segment {seg_idx} decrypted and verified ({len(plaintext)} bytes)[/green]")

            # Wait for the next segment's key (sniffer started before the fetch)
            if next_sniffer is not None:
                next_sniffer.join(timeout=30)
                if next_key_result:
                    current_key = next_key_result[0]
                    console.print(f"[green]Key for segment {seg_idx + 1} received[/green]")
                else:
                    console.print(f"[red]No key received for segment {seg_idx + 1} — aborting[/red]")
                    sys.exit(1)

            if is_last:
                break

        if plaintext_parts:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            video_bytes = b"".join(plaintext_parts)
            out_path.write_bytes(video_bytes)
            total_sha = hashlib.sha256(video_bytes).hexdigest()
            expected_total = _manifest_data.get("total_plaintext_sha256")
            if expected_total and total_sha == expected_total:
                console.print(f"[bold green]Video reassembled and verified: {out_path}[/bold green]")
            else:
                console.print(f"[bold green]Video saved: {out_path}[/bold green]  [yellow](total SHA mismatch)[/yellow]")
        else:
            console.print("[yellow]No segments decrypted (simulation mode)[/yellow]")
        return

    # -----------------------------------------------------------------------
    # Legacy single-file mode
    # -----------------------------------------------------------------------
    key_fragment = seg0_key

    # Download the encrypted blob (open to anyone — useless without the key fragment)
    fetch_encrypted_video(http_host, video_port, enc_path, display_host=http_display)

    if key_fragment is None:
        console.print("[yellow]Simulation mode: no key fragment — skipping decryption.[/yellow]")
        console.print(f"[dim]Encrypted file at: {enc_path}[/dim]")
        return

    # Decrypt locally — only possible because the knock delivered the key fragment
    if not manifest_path.is_file():
        console.print(f"[red]Manifest not found at {manifest_path}[/red]")
        sys.exit(1)

    if not decrypt_video(enc_path, manifest_path, key_fragment, out_path):
        if key_fragment.hex() == "80000000":
            console.print("[yellow]ICMP reply was Windows auto-reply (80000000), not DRM.[/yellow]")
        console.print("[dim]Trying authorized HTTP key-fragment delivery…[/dim]")
        http_fragment = fetch_key_fragment_http(http_host, video_port)
        if http_fragment is None or not decrypt_video(enc_path, manifest_path, http_fragment, out_path):
            console.print("[red]Could not decrypt — knock may have failed (check server for AUTHORIZED).[/red]")
            sys.exit(1)

    console.print(f"[bold green]Done — decrypted video at {out_path}[/bold green]")


if __name__ == "__main__":
    main()
