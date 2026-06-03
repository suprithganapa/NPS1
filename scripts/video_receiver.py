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


def _sniff_for_fragment(
    knock_target: str,
    server_ip: str,
    session_nonce: int,
    stop_event: threading.Event,
    result: list[bytes],
    iface: str | None = None,
) -> None:
    from scapy.all import ICMP, IP, sniff

    from nps_lab_el.services.drm_responder import DrmResponder

    allowed_src = _reply_sources(knock_target, server_ip)

    def _on_reply(pkt) -> None:
        if not pkt.haslayer(IP) or not pkt.haslayer(ICMP) or pkt[ICMP].type != 0:
            return
        from nps_lab_el.services.drm_responder import DRM_ICMP_CODE

        # Lab DRM reply: seq=0 and code=0x5A; OS replies use code 0 and often ttl=128.
        if pkt[ICMP].seq != 0 or pkt[ICMP].code != DRM_ICMP_CODE:
            return
        if pkt.ttl == 128 and pkt.tos == 0:
            return
        if pkt[IP].src not in allowed_src:
            return
        try:
            fragment = DrmResponder.extract_key_fragment(
                reply_ttl=pkt.ttl,
                reply_tos=pkt.tos,
                reply_icmp_id=pkt[ICMP].id,
                session_nonce=session_nonce,
            )
        except Exception:
            return
        if len(fragment) != 4:
            return
        result.append(fragment)
        stop_event.set()

    sniff_kwargs: dict = {
        "filter": "icmp",
        "prn": _on_reply,
        "store": 0,
        "stop_filter": lambda _: stop_event.is_set(),
        "timeout": 45,
    }
    if iface:
        sniff_kwargs["iface"] = iface
    sniff(**sniff_kwargs)


def send_knock(cfg, server_ip: str, simulation: bool) -> bytes | None:
    """Send ICMP knock and return the 4-byte key fragment from the server reply, or None on simulation."""
    from nps_lab_el.crypto.totp import get_counter
    from nps_lab_el.models.auth import AuthToken
    from nps_lab_el.platform.permissions import require_privileges
    from nps_lab_el.protocol.encode_jitter import encode_with_fec

    require_privileges(simulation=simulation)
    counter = get_counter(cfg.auth.window_seconds)
    payload = os.urandom(16)
    channel_mask = 1
    mac_input = payload + counter.to_bytes(8, "big") + channel_mask.to_bytes(1, "big")
    mac = hmac.new(cfg.auth.totp_secret.encode(), mac_input, hashlib.sha256).digest()[:16]
    token = AuthToken(payload=payload, totp_counter=counter, channel_mask=channel_mask, mac=mac)
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

    from scapy.all import ICMP, IP, send

    knock_target = resolve_knock_target(server_ip, cfg.client.ip)
    session_nonce = int.from_bytes(os.urandom(2), "big")
    est_sec = _estimate_knock_seconds(jitter_sequence)
    console.print(
        f"[green]Sending knock to {knock_target}[/green] "
        f"({len(jitter_sequence)} packets, ~{est_sec:.0f}s)..."
    )
    if knock_target != server_ip:
        console.print(f"[dim]  (same PC — using loopback; server IP in config is {server_ip})[/dim]")
    console.print("[dim]Server must be running (Admin) with ICMP sniffer — not --http-only[/dim]")

    stop_sniff = threading.Event()
    fragment_result: list[bytes] = []
    sniff_iface = r"\Device\NPF_Loopback" if sys.platform == "win32" and knock_target == "127.0.0.1" else None
    sniffer = threading.Thread(
        target=_sniff_for_fragment,
        args=(knock_target, server_ip, session_nonce, stop_sniff, fragment_result),
        kwargs={"iface": sniff_iface},
        daemon=True,
    )
    sniffer.start()
    time.sleep(0.5)

    total = len(jitter_sequence)
    for i, delay in enumerate(jitter_sequence):
        send(IP(dst=knock_target) / ICMP(type=8, id=session_nonce, seq=i), verbose=False)
        precise_sleep(delay)
        if (i + 1) % 64 == 0 or i + 1 == total:
            pct = 100 * (i + 1) // total
            console.print(f"[dim]  knock progress: {i + 1}/{total} ({pct}%)[/dim]")

    console.print("[green]Knock sent — waiting for server ICMP reply (key fragment)...[/green]")
    console.print("[dim]  server needs ~2s after last packet to authorize[/dim]")
    sniffer.join(timeout=45)

    if fragment_result:
        key_fragment = fragment_result[0]
        console.print(f"[green]Key fragment received ({key_fragment.hex()})[/green]")
        return key_fragment

    console.print("[red]No ICMP reply with key fragment — knock failed or server not sniffing[/red]")
    console.print("[yellow]Check:[/yellow] server Admin? sniffer shows AUTHORIZED? same TOTP in both YAMLs?")
    console.print("[yellow]  t0_ms should be 20 (not 1000) — re-run generate_lab_configs.py[/yellow]")
    return None


def http_hosts_for(server_ip: str) -> list[str]:
    """On the same PC, 127.0.0.1 is more reliable than the hotspot/LAN IP for HTTP."""
    hosts: list[str] = []
    for host in ("127.0.0.1", server_ip):
        if host not in hosts:
            hosts.append(host)
    return hosts


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


def fetch_encrypted_video(http_host: str, video_port: int, enc_path: Path) -> None:
    """Download the encrypted .enc file — no auth required, it's just ciphertext."""
    url = f"http://{http_host}:{video_port}/video/stream"
    console.print(f"[bold]Downloading encrypted video from {url}[/bold]")
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


def check_health(server_ip: str, video_port: int) -> str:
    """Return the HTTP host that responded (127.0.0.1 or server_ip)."""
    errors: list[str] = []
    for host in http_hosts_for(server_ip):
        url = f"http://{host}:{video_port}/health"
        try:
            with urllib.request.urlopen(url, timeout=15) as resp:
                info = json.loads(resp.read())
            console.print(f"[dim]Server health ({host}): {info}[/dim]")
            return host
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            errors.append(f"{url} -> {exc}")

    console.print("[red]Server not reachable.[/red]")
    for line in errors:
        console.print(f"[red]  {line}[/red]")
    console.print("[yellow]Fix:[/yellow]")
    console.print("  1. Administrator PowerShell: netstat -ano | findstr :8765")
    console.print("  2. taskkill /F /PID <pid>   (stop ALL old servers)")
    console.print("  3. Start ONE server (no --simulation):")
    console.print("     python scripts/run_lab_server.py --config config/generated/server.yaml")
    console.print("  4. Test: Invoke-RestMethod http://127.0.0.1:8765/health")
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

    server_ip = cfg.server.ip
    video_port = cfg.server.video_port
    out_path = ROOT / args.output
    enc_path = out_path.with_suffix(".enc")
    manifest_path = ROOT / cfg.video.artifact_manifest

    console.print(f"[bold]Knocker IP[/bold]  : {cfg.client.ip}  (control port {cfg.client.control_port})")
    console.print(f"[bold]Server IP[/bold]   : {server_ip}  (video port {video_port})")
    console.print()

    http_host = check_health(server_ip, video_port)

    key_fragment: bytes | None = None

    if args.skip_knock:
        if args.key_fragment:
            key_fragment = bytes.fromhex(args.key_fragment)
        else:
            console.print("[red]--skip-knock requires --key-fragment <hex>[/red]")
            sys.exit(1)
    else:
        key_fragment = send_knock(cfg, server_ip=server_ip, simulation=args.simulation)
        if key_fragment is None and not args.simulation:
            console.print("[yellow]No DRM ICMP reply — waiting for server, then trying HTTP…[/yellow]")
            time.sleep(4)
            key_fragment = fetch_key_fragment_http(http_host, video_port)
            if key_fragment is None:
                console.print("[red]Knock did not authorize client (check server: AUTHORIZED?).[/red]")
                sys.exit(1)

    # Download the encrypted blob (open to anyone — useless without the key fragment)
    fetch_encrypted_video(http_host, video_port, enc_path)

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
