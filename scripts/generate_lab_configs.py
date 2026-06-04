#!/usr/bin/env python3
"""
Generate separate server + knocker YAML configs for non-localhost lab testing.

Server listens for ICMP knocks and serves video on server.video_port.
Knocker uses a different IP and control_port (local status endpoint).

Usage:
  python scripts/generate_lab_configs.py --auto
  python scripts/generate_lab_configs.py --server-ip 192.168.1.10 --knocker-ip 192.168.1.20
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "config" / "generated"
DEFAULT_TOTP = "JBSWY3DPEHPK3PXP"

sys.path.insert(0, str(ROOT / "scripts"))
from lab_config_common import print_test_matrix, video_block_for_stem  # noqa: E402


def detect_interfaces() -> list[tuple[str, str]]:
    """Return (ip, iface) pairs for active adapters, best candidates first."""
    try:
        from scapy.all import get_if_addr, get_if_list
    except ImportError:
        return []

    pairs: list[tuple[str, str]] = []
    for iface in get_if_list():
        try:
            addr = get_if_addr(iface)
        except Exception:
            continue
        if addr and addr not in ("0.0.0.0", "127.0.0.1") and not addr.startswith("169.254."):
            pairs.append((addr, iface))
    # fallback: include link-local if nothing else
    if not pairs:
        for iface in get_if_list():
            try:
                addr = get_if_addr(iface)
            except Exception:
                continue
            if addr and addr not in ("0.0.0.0", "127.0.0.1"):
                pairs.append((addr, iface))
    return pairs


def pick_iface_for_ip(ip: str) -> str:
    for addr, iface in detect_interfaces():
        if addr == ip:
            return iface
    if ip == "127.0.0.1":
        return r"\Device\NPF_Loopback" if sys.platform == "win32" else "lo"
    pairs = detect_interfaces()
    if pairs:
        return pairs[0][1]
    return "eth0"


def base_auth_block() -> dict:
    return {
        "auth": {
            "totp_secret": DEFAULT_TOTP,
            "window_seconds": 60,
            "ttl_auth_seconds": 300,
        },
        "channels": {
            "jitter": {"enabled": True, "t0_ms": 20, "delta_ms": 5, "tau_ms": 8},
            "header": {"enabled": True, "lfsr_seed": 44257, "windows_compat": True},
            "dns": {"enabled": False},
        },
        "fec": {"enabled": True, "n": 32, "k": 28},
        "drm": {"enabled": True, "fragment_bits": 32},
        "video": {
            "enabled": True,
            "artifact_enc": "artifacts/sample_video.enc",
            "artifact_manifest": "artifacts/sample_video.manifest.json",
            "keyfrag": "server_secrets/sample_video.keyfrag",
        },
        "targets": {"allowlist": []},
        "firewall": {"mode": "log_only"},
    }


def build_server_yaml(server_ip: str, video_port: int, knocker_ip: str) -> dict:
    cfg = base_auth_block()
    cfg["role"] = "server"
    cfg["server"] = {
        "ip": server_ip,
        "iface": pick_iface_for_ip(server_ip),
        "video_port": video_port,
    }
    cfg["client"] = {"ip": knocker_ip, "control_port": video_port + 1}
    cfg["targets"]["allowlist"] = [_subnet_of(server_ip)]
    return cfg


def build_knocker_yaml(server_ip: str, video_port: int, knocker_ip: str, control_port: int) -> dict:
    cfg = base_auth_block()
    cfg["role"] = "knocker"
    cfg["server"] = {
        "ip": server_ip,
        "iface": pick_iface_for_ip(knocker_ip),
        "video_port": video_port,
    }
    cfg["client"] = {"ip": knocker_ip, "control_port": control_port}
    return cfg


def _subnet_of(ip: str) -> str:
    parts = ip.split(".")
    if len(parts) == 4:
        return f"{parts[0]}.{parts[1]}.{parts[2]}.0/24"
    return "0.0.0.0/0"


def write_configs(
    server_ip: str,
    knocker_ip: str,
    server_video_port: int,
    knocker_control_port: int,
    video_stem: str | None = None,
) -> dict:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    server_cfg = build_server_yaml(server_ip, server_video_port, knocker_ip)
    knocker_cfg = build_knocker_yaml(server_ip, server_video_port, knocker_ip, knocker_control_port)
    if video_stem:
        server_cfg["video"] = video_block_for_stem(video_stem)
        knocker_cfg["video"] = video_block_for_stem(video_stem)

    server_path = OUT_DIR / "server.yaml"
    knocker_path = OUT_DIR / "knocker.yaml"
    info_path = OUT_DIR / "lab_info.json"

    server_path.write_text(yaml.dump(server_cfg, default_flow_style=False, sort_keys=False))
    knocker_path.write_text(yaml.dump(knocker_cfg, default_flow_style=False, sort_keys=False))

    info = {
        "server_ip": server_ip,
        "knocker_ip": knocker_ip,
        "server_video_port": server_video_port,
        "knocker_control_port": knocker_control_port,
        "server_config": str(server_path.relative_to(ROOT)),
        "knocker_config": str(knocker_path.relative_to(ROOT)),
        "server_iface": server_cfg["server"]["iface"],
        "totp_secret": DEFAULT_TOTP,
        "video_stem": video_stem,
        "knock_mode": "same-host" if server_ip == knocker_ip else "cross-host",
        "commands": {
            "prepare_video": "python scripts/generate_sample_video.py && python scripts/encrypt_video.py",
            "server": f"python scripts/run_lab_server.py --config {server_path.relative_to(ROOT)}",
            "server_simulation": (
                f"python scripts/run_lab_server.py --config {server_path.relative_to(ROOT)} --simulation"
            ),
            "knock": f"knock-client --config {knocker_path.relative_to(ROOT)} --target {server_ip}",
            "knock_simulation": (
                f"knock-client --config {knocker_path.relative_to(ROOT)} "
                f"--target {server_ip} --simulation"
            ),
            "receive_video": (
                f"python scripts/video_receiver.py --config {knocker_path.relative_to(ROOT)}"
            ),
            "receive_video_simulation": (
                f"python scripts/video_receiver.py --config {knocker_path.relative_to(ROOT)} "
                f"--simulation"
            ),
        },
    }
    info_path.write_text(json.dumps(info, indent=2) + "\n")
    return info


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate server + knocker lab configs")
    parser.add_argument("--auto", action="store_true", help="Pick first two detected LAN IPs")
    parser.add_argument("--server-ip", help="Server/listener IP (where sniffer + video server run)")
    parser.add_argument("--knocker-ip", help="Knocker/client IP (different from server for remote test)")
    parser.add_argument("--server-video-port", type=int, default=8765, help="HTTP video port on server")
    parser.add_argument(
        "--knocker-control-port",
        type=int,
        default=8766,
        help="Local control port on knocker (different from server video port)",
    )
    parser.add_argument(
        "--video-stem",
        help="Artifact stem after encrypt_video (e.g. my_movie for artifacts/my_movie.enc)",
    )
    args = parser.parse_args()

    if args.auto:
        pairs = detect_interfaces()
        if not pairs:
            print("No LAN interfaces found. Pass --server-ip and --knocker-ip manually.")
            sys.exit(1)
        server_ip = pairs[0][0]
        knocker_ip = pairs[1][0] if len(pairs) > 1 else "127.0.0.1"
        if server_ip == knocker_ip:
            knocker_ip = "127.0.0.1"
        print(f"Auto-detected server IP : {server_ip}")
        print(f"Auto-detected knocker IP: {knocker_ip}")
    else:
        if not args.server_ip or not args.knocker_ip:
            print("Provide --server-ip and --knocker-ip, or use --auto")
            sys.exit(1)
        server_ip = args.server_ip
        knocker_ip = args.knocker_ip

    if server_ip == knocker_ip and server_ip != "127.0.0.1":
        print("Warning: server and knocker share the same IP — use two machines or loopback test.")

    info = write_configs(
        server_ip=server_ip,
        knocker_ip=knocker_ip,
        server_video_port=args.server_video_port,
        knocker_control_port=args.knocker_control_port,
        video_stem=args.video_stem,
    )

    print()
    print("Generated lab configs:")
    print(f"  Server  : {info['server_config']}")
    print(f"  Knocker : {info['knocker_config']}")
    print(f"  Summary : config/generated/lab_info.json")
    print()
    print(f"  Server IP          : {info['server_ip']}  (video HTTP port {info['server_video_port']})")
    print(f"  Knocker IP         : {info['knocker_ip']}  (control port {info['knocker_control_port']})")
    print(f"  Server interface   : {info['server_iface']}")
    print(f"  Knock mode         : {info['knock_mode']}")
    if info.get("video_stem"):
        print(f"  Video stem         : {info['video_stem']}")
    print()
    print("IPs in YAML:")
    print(f"  server.yaml  → server.ip={server_ip}  client.ip={knocker_ip}  (client.ip = knocker machine)")
    print(f"  knocker.yaml → server.ip={server_ip}  client.ip={knocker_ip}")
    print_test_matrix()
    print("Video: python scripts/encrypt_video.py <any_video_file>")
    print("       python scripts/sync_lab_video.py --stem <stem>")


if __name__ == "__main__":
    main()
