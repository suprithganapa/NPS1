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


def send_knock(cfg, target: str, simulation: bool) -> None:
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
        return

    from scapy.all import ICMP, IP, send

    console.print(f"[green]Sending knock to {target}[/green] ({len(jitter_sequence)} packets, ~8+ min)...")
    for i, delay in enumerate(jitter_sequence):
        send(IP(dst=target) / ICMP(type=8, seq=i), verbose=False)
        time.sleep(delay)
    console.print("[green]Knock complete[/green]")


def fetch_video(server_ip: str, video_port: int, out_path: Path, max_retries: int = 5) -> None:
    url = f"http://{server_ip}:{video_port}/video/stream"
    console.print(f"[bold]Downloading video from {url}[/bold]")

    for attempt in range(1, max_retries + 1):
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:
                data = resp.read()
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(data)
            console.print(f"[green]Saved {len(data)} bytes -> {out_path}[/green]")
            return
        except urllib.error.HTTPError as exc:
            if exc.code == 403 and attempt < max_retries:
                console.print(f"[yellow]403 — not authorized yet, retry {attempt}/{max_retries} in 5s[/yellow]")
                time.sleep(5)
                continue
            body = exc.read().decode(errors="replace")
            console.print(f"[red]HTTP {exc.code}: {body}[/red]")
            sys.exit(1)
        except urllib.error.URLError as exc:
            console.print(f"[red]Cannot reach server: {exc}[/red]")
            sys.exit(1)


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
    parser = argparse.ArgumentParser(description="Knock + receive video from lab server")
    parser.add_argument("--config", required=True, help="Knocker YAML (config/generated/knocker.yaml)")
    parser.add_argument("--simulation", action="store_true", help="Simulate knock (no ICMP)")
    parser.add_argument("--skip-knock", action="store_true", help="Only download (knock already done)")
    parser.add_argument(
        "--output",
        default="artifacts/received_video.mp4",
        help="Where to save downloaded video",
    )
    args = parser.parse_args()

    sys.path.insert(0, str(ROOT / "src"))
    from nps_lab_el.models.config import load_config

    cfg_path = ROOT / args.config if not Path(args.config).is_absolute() else Path(args.config)
    cfg = load_config(str(cfg_path))

    server_ip = cfg.server.ip
    video_port = cfg.server.video_port
    out_path = ROOT / args.output

    console.print(f"[bold]Knocker IP[/bold]  : {cfg.client.ip}  (control port {cfg.client.control_port})")
    console.print(f"[bold]Server IP[/bold]   : {server_ip}  (video port {video_port})")
    console.print()

    check_health(server_ip, video_port)

    if not args.skip_knock:
        send_knock(cfg, target=server_ip, simulation=args.simulation)
        if not args.simulation:
            console.print("[dim]Waiting 3s for server to process knock…[/dim]")
            time.sleep(3)

    fetch_video(server_ip, video_port, out_path)

    manifest_path = ROOT / cfg.video.artifact_manifest
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text())
        expected = manifest.get("plaintext_sha256")
        if expected:
            actual = hashlib.sha256(out_path.read_bytes()).hexdigest()
            if actual == expected:
                console.print("[green]SHA-256 verified — video matches original[/green]")
            else:
                console.print("[yellow]SHA-256 mismatch (server may use different keyfrag)[/yellow]")


if __name__ == "__main__":
    main()
