"""CLI entry point for sending a port-knock sequence via ICMP jitter timing."""
from __future__ import annotations

import hashlib
import hmac
import os

import click
from rich.console import Console

from nps_lab_el.crypto.totp import get_counter
from nps_lab_el.models.auth import AuthToken
from nps_lab_el.models.config import load_config
from nps_lab_el.platform.permissions import require_privileges
from nps_lab_el.protocol.encode_jitter import encode_with_fec

console = Console()


@click.command("knock")
@click.option("--config", "config_path", required=True, type=click.Path(exists=True), help="Path to YAML config file.")
@click.option("--target", required=True, help="Target server IP address.")
@click.option("--simulation", is_flag=True, default=False, help="Simulation mode (no raw sockets).")
def cli(config_path: str, target: str, simulation: bool) -> None:
    """Send a port-knock authentication sequence."""
    cfg = load_config(config_path)

    require_privileges(simulation=simulation)

    # Generate TOTP counter
    counter = get_counter(cfg.auth.window_seconds)

    # Build AuthToken
    payload = os.urandom(16)
    channel_mask = 1  # jitter channel

    mac_input = payload + counter.to_bytes(8, "big") + channel_mask.to_bytes(1, "big")
    mac = hmac.new(cfg.auth.totp_secret.encode(), mac_input, hashlib.sha256).digest()[:16]

    token = AuthToken(
        payload=payload,
        totp_counter=counter,
        channel_mask=channel_mask,
        mac=mac,
    )

    # Encode token bits with FEC and jitter timing
    token_bits = token.to_bits()
    jitter_sequence = encode_with_fec(
        token_bits,
        n=cfg.fec.n,
        k=cfg.fec.k,
        t0_ms=cfg.channels.jitter.t0_ms,
        delta_ms=cfg.channels.jitter.delta_ms,
    )

    if simulation:
        console.print(f"[bold cyan]Simulation mode[/bold cyan] — timing sequence ({len(jitter_sequence)} intervals):")
        console.print(jitter_sequence)
    else:
        import time

        from scapy.all import ICMP, IP, send

        packet_count = len(jitter_sequence) + 1
        console.print(f"[bold green]Sending knock to {target}[/bold green] ({packet_count} packets)...")
        send(IP(dst=target) / ICMP(type=8, seq=0), verbose=False)
        for i, delay in enumerate(jitter_sequence):
            time.sleep(delay)
            pkt = IP(dst=target) / ICMP(type=8, seq=i + 1)
            send(pkt, verbose=False)
        console.print("[bold green]Knock sequence sent.[/bold green]")

    console.print(f"[dim]TOTP counter: {counter} | Token bits: {len(token_bits)}[/dim]")


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
