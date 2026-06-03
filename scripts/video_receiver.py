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
import time
import urllib.error
import urllib.request
from pathlib import Path

from rich.console import Console

ROOT = Path(__file__).resolve().parent.parent
console = Console()


def send_knock(cfg, target: str, simulation: bool) -> bytes | None:
    """Send ICMP knock and return the 4-byte key fragment from the server reply, or None on simulation."""
    from nps_lab_el.crypto.totp import get_counter
    from nps_lab_el.models.auth import AuthToken
    from nps_lab_el.platform.permissions import require_privileges
    from nps_lab_el.protocol.encode_jitter import encode_with_fec
    from nps_lab_el.services.drm_responder import DrmResponder

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

    from scapy.all import ICMP, IP, send, sniff

    session_nonce = int.from_bytes(os.urandom(2), "big")
    console.print(f"[green]Sending knock to {target}[/green] ({len(jitter_sequence)} packets, ~8+ min)...")
    for i, delay in enumerate(jitter_sequence):
        send(IP(dst=target) / ICMP(type=8, id=session_nonce, seq=i), verbose=False)
        time.sleep(delay)
    console.print("[green]Knock sent — waiting for ICMP reply with key fragment[/green]")

    # Sniff for the ICMP echo-reply carrying the key fragment
    replies = sniff(
        filter=f"icmp and src host {target}",
        timeout=10,
        count=1,
        lfilter=lambda p: p.haslayer(ICMP) and p[ICMP].type == 0,
    )
    if not replies:
        console.print("[red]No ICMP reply received — knock may have failed[/red]")
        return None

    reply = replies[0]
    key_fragment = DrmResponder.extract_key_fragment(
        reply_ttl=reply.ttl,
        reply_tos=reply.tos,
        reply_icmp_id=reply[ICMP].id,
        session_nonce=session_nonce,
    )
    console.print(f"[green]Key fragment received ({key_fragment.hex()})[/green]")
    return key_fragment


def fetch_encrypted_video(server_ip: str, video_port: int, enc_path: Path) -> None:
    """Download the encrypted .enc file — no auth required, it's just ciphertext."""
    url = f"http://{server_ip}:{video_port}/video/stream"
    console.print(f"[bold]Downloading encrypted video from {url}[/bold]")
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            data = resp.read()
        enc_path.parent.mkdir(parents=True, exist_ok=True)
        enc_path.write_bytes(data)
        console.print(f"[green]Saved encrypted blob: {len(data)} bytes -> {enc_path}[/green]")
    except urllib.error.URLError as exc:
        console.print(f"[red]Cannot reach server: {exc}[/red]")
        sys.exit(1)


def decrypt_video(enc_path: Path, manifest_path: Path, key_fragment: bytes, out_path: Path) -> None:
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
        sys.exit(1)

    expected = manifest.get("plaintext_sha256")
    if expected and hashlib.sha256(plaintext).hexdigest() != expected:
        console.print("[red]SHA-256 mismatch after decryption — something went wrong[/red]")
        sys.exit(1)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(plaintext)
    console.print(f"[green]Decrypted video saved -> {out_path}  (SHA-256 verified)[/green]")


def check_health(server_ip: str, video_port: int) -> None:
    url = f"http://{server_ip}:{video_port}/health"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            info = json.loads(resp.read())
        console.print(f"[dim]Server health: {info}[/dim]")
    except urllib.error.URLError as exc:
        console.print(f"[red]Server not reachable at {url}: {exc}[/red]")
        console.print("Start server first: python scripts/run_lab_server.py --config config/generated/server.yaml")
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

    check_health(server_ip, video_port)

    key_fragment: bytes | None = None

    if args.skip_knock:
        if args.key_fragment:
            key_fragment = bytes.fromhex(args.key_fragment)
        else:
            console.print("[red]--skip-knock requires --key-fragment <hex>[/red]")
            sys.exit(1)
    else:
        key_fragment = send_knock(cfg, target=server_ip, simulation=args.simulation)
        if not args.simulation:
            console.print("[dim]Waiting 3s for server to process knock…[/dim]")
            time.sleep(3)

    # Download the encrypted blob (open to anyone — useless without the key fragment)
    fetch_encrypted_video(server_ip, video_port, enc_path)

    if key_fragment is None:
        console.print("[yellow]Simulation mode: no key fragment — skipping decryption.[/yellow]")
        console.print(f"[dim]Encrypted file at: {enc_path}[/dim]")
        return

    # Decrypt locally — only possible because the knock delivered the key fragment
    if not manifest_path.is_file():
        console.print(f"[red]Manifest not found at {manifest_path}[/red]")
        sys.exit(1)

    decrypt_video(enc_path, manifest_path, key_fragment, out_path)
    console.print(f"[bold green]Done — decrypted video at {out_path}[/bold green]")


if __name__ == "__main__":
    main()
