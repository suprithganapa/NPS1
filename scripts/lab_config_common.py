"""Shared helpers for generated lab server/knocker YAML and console banners."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
GENERATED_DIR = ROOT / "config" / "generated"
DEFAULT_TOTP = "JBSWY3DPEHPK3PXP"


def video_block_for_stem(stem: str) -> dict[str, str]:
    return {
        "enabled": True,
        "artifact_enc": f"artifacts/{stem}.enc",
        "artifact_manifest": f"artifacts/{stem}.manifest.json",
        "keyfrag": f"server_secrets/{stem}.keyfrag",
    }


def patch_yaml_video(path: Path, stem: str) -> None:
    data = yaml.safe_load(path.read_text())
    data["video"] = video_block_for_stem(stem)
    path.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False))


def totp_tag(secret: str) -> str:
    """Short suffix to compare secrets in logs without printing the full value."""
    return secret[-4:] if len(secret) >= 4 else "????"


def peer_generated_config(role: str, cfg_path: Path):
    from nps_lab_el.models.config import load_config

    peer_name = "knocker.yaml" if role == "server" else "server.yaml"
    peer_path = cfg_path.parent / peer_name
    if peer_path.is_file():
        return load_config(str(peer_path)), peer_path
    return None, peer_path


def knock_mode_label(server_ip: str, client_ip: str) -> tuple[str, str]:
    if server_ip == client_ip:
        knock = (
            f"{server_ip} (header knock)"
            if server_ip != "127.0.0.1"
            else "127.0.0.1 (header knock)"
        )
        return "same-host", knock
    return "cross-host", f"{server_ip} (jitter knock)"


def http_hosts_for(server_ip: str, client_ip: str) -> list[str]:
    """Same PC: try loopback first. Cross-host: only the server LAN IP (not 127.0.0.1)."""
    if client_ip == server_ip:
        hosts = ["127.0.0.1"]
        if server_ip != "127.0.0.1" and server_ip not in hosts:
            hosts.append(server_ip)
        return hosts
    return [server_ip]


def print_test_matrix() -> None:
    print()
    print("=== Lab test matrix (config/generated/*.yaml) ===")
    print()
    print("  A) One laptop (only option without a second PC)")
    print("     server.yaml + knocker.yaml: server.ip = X, client.ip = X (same in BOTH files)")
    print("     Knock: loopback header (shown as server LAN IP)  |  Server: sniffing on Loopback")
    print("     Start server first (Admin), then video_receiver (Admin).")
    print()
    print("  B) Two laptops (different IP, same TOTP)")
    print("     server.yaml:  server.ip = SERVER,  client.ip = KNOCKER")
    print("     knocker.yaml: server.ip = SERVER,  client.ip = KNOCKER")
    print("     auth.totp_secret: identical in BOTH files")
    print("     Run server on SERVER machine; run video_receiver on KNOCKER machine only.")
    print("     If /health shows client_ip=SERVER, you are on the wrong PC for cross-host YAML.")
    print("     Regenerate: python scripts/generate_lab_configs.py --server-ip SERVER --knocker-ip KNOCKER")
    print()
    print("  C) Wrong TOTP (DENIED expected)")
    print("     Keep IPs as in A or B; change auth.totp_secret in knocker.yaml ONLY (or server only)")
    print("     Last 4 chars shown in logs as ...XXXX for comparison")
    print()
    print("  Any video file:")
    print("     python scripts/encrypt_video.py <path/to/video.mp4|avi|mkv|...>")
    print("     python scripts/sync_lab_video.py --stem <filename_stem>")
    print()


CONFIG_IP_PATH = ROOT / "config" / "config_ip.yaml"


def load_display_overlay() -> dict | None:
    if not CONFIG_IP_PATH.is_file():
        return None
    data = yaml.safe_load(CONFIG_IP_PATH.read_text())
    if not isinstance(data, dict):
        return None
    if "server_ip" not in data or "knocker_client_ip" not in data:
        return None
    return data


def show_layout_banner() -> bool:
    overlay = load_display_overlay()
    if overlay is None:
        return True
    return bool(overlay.get("show_layout", False))


def display_ips_summary(overlay: dict) -> tuple[str, str]:
    si = str(overlay["server_ip"])
    kc = str(overlay["knocker_client_ip"])
    if display_ips_differ(overlay):
        return (
            f"server.ip {si}   client.ip {kc}",
            "different client IPs",
        )
    return (
        f"server.ip {si}   client.ip {kc}",
        "same host",
    )


def print_display_ips(console) -> None:
    """Short IP line from config/config_ip.yaml when full layout is hidden."""
    overlay = load_display_overlay()
    if overlay is None or show_layout_banner():
        return
    main, mode = display_ips_summary(overlay)
    console.print(f"[cyan]{main}[/cyan]")
    console.print(f"[dim]{mode}[/dim]")
    console.print()


def display_ips_differ(overlay: dict) -> bool:
    """Same host when knocker client IP equals server IP (ignore server_client_ip)."""
    return str(overlay["knocker_client_ip"]) != str(overlay["server_ip"])


def cross_host_display(overlay: dict | None) -> bool:
    return overlay is not None and display_ips_differ(overlay)


def terminal_peer_ip(
    real_ip: str,
    *,
    role: str,
    overlay: dict | None = None,
    cfg_server_ip: str | None = None,
    cfg_client_ip: str | None = None,
) -> str:
    """
    Map real addresses to config_ip.yaml for terminal output only.
    role: 'knocker' (video_receiver) or 'server' (run_lab_server).
    """
    if overlay is not None:
        si = str(overlay["server_ip"])
        kc = str(overlay["knocker_client_ip"])
        if cross_host_display(overlay):
            # One-PC demo: traffic is loopback/LAN .2 but show knocker as .1
            if role in ("knocker", "server") and real_ip in ("127.0.0.1", si):
                return kc
            return real_ip
        if real_ip == "127.0.0.1":
            return si
        return real_ip

    if real_ip == "127.0.0.1" and cfg_server_ip and cfg_client_ip == cfg_server_ip:
        return cfg_server_ip
    return real_ip


def display_server_ip(overlay: dict | None, cfg_server_ip: str) -> str:
    return str(overlay["server_ip"]) if overlay else cfg_server_ip


def display_knocker_ip(overlay: dict | None, cfg_client_ip: str) -> str:
    if overlay is not None:
        return str(overlay["knocker_client_ip"])
    return cfg_client_ip


def terminal_server_ip(
    real_ip: str,
    *,
    overlay: dict | None = None,
    cfg_server_ip: str | None = None,
) -> str:
    """Server URLs / server-side addresses — always show server IP, never knocker."""
    si = display_server_ip(overlay, cfg_server_ip or real_ip)
    if real_ip in ("127.0.0.1", si, cfg_server_ip):
        return si
    return real_ip


def terminal_http_host(
    host: str,
    *,
    overlay: dict | None = None,
    cfg_server_ip: str | None = None,
    cfg_client_ip: str | None = None,
    role: str = "knocker",
    lan_ip: str | None = None,
) -> str:
    """HTTP always targets the server; cfg_client_ip/role accepted for call-site compatibility."""
    _ = (role, cfg_client_ip)
    return terminal_server_ip(
        host,
        overlay=overlay,
        cfg_server_ip=cfg_server_ip or lan_ip,
    )


def terminal_ip(
    real_ip: str,
    *,
    role: str = "server",
    overlay: dict | None = None,
    cfg_server_ip: str | None = None,
    cfg_client_ip: str | None = None,
    lan_ip: str | None = None,
) -> str:
    return terminal_peer_ip(
        real_ip,
        role=role,
        overlay=overlay,
        cfg_server_ip=cfg_server_ip or lan_ip,
        cfg_client_ip=cfg_client_ip,
    )


def display_knock_label(overlay: dict) -> tuple[str, str]:
    """Knock path as shown when using config_ip.yaml (knocker → server)."""
    return knock_mode_label(str(overlay["server_ip"]), str(overlay["knocker_client_ip"]))


def overlay_banner_lines(role: str, overlay: dict, cfg, cfg_path: Path, *, peer_cfg=None) -> list[str]:
    """Same format as lab_banner_lines but IPs from config/config_ip.yaml."""
    si = str(overlay["server_ip"])
    kc = str(overlay["knocker_client_ip"])
    peer_role = "knocker" if role == "server" else "server"
    mode, knock_target = display_knock_label(overlay)
    if role == "knocker":
        lines = [
            f"Lab layout ({role}): server.ip={si}  client.ip={kc} (this machine)",
            f"  knock: {mode} → {knock_target}",
        ]
    else:
        lines = [f"Lab layout ({role}): server.ip={si}", f"  knock: {mode} → {knock_target}"]
        if display_ips_differ(overlay):
            lines.insert(1, f"  client.ip={kc}")
    if cfg_path.parent.name == "generated":
        lines.append(f"  video: {Path(cfg.video.artifact_enc).name}")
    if peer_cfg is not None:
        lines.append(f"  peer {peer_role}: server.ip={si}  client.ip={kc}")
        if display_ips_differ(overlay):
            lines.append(f"  layout: different client IPs (server {si} vs client {kc})")
        else:
            lines.append("  layout: same host")
        if cfg.auth.totp_secret == peer_cfg.auth.totp_secret:
            lines.append(f"  TOTP: match (…{totp_tag(cfg.auth.totp_secret)})")
        else:
            lines.append(
                f"  TOTP: MISMATCH — {role} …{totp_tag(cfg.auth.totp_secret)} vs "
                f"{peer_role} …{totp_tag(peer_cfg.auth.totp_secret)} → expect DENIED"
            )
    return lines


def lab_banner_lines(role: str, cfg, cfg_path: Path, *, peer_cfg=None) -> list[str]:
    peer_role = "knocker" if role == "server" else "server"
    kc = peer_cfg.client.ip if peer_cfg is not None and role == "server" else cfg.client.ip
    mode, knock_target = knock_mode_label(cfg.server.ip, kc)
    if role == "knocker":
        lines = [
            f"Lab layout ({role}): server.ip={cfg.server.ip}  client.ip={cfg.client.ip} (this machine)",
            f"  knock: {mode} → {knock_target}",
        ]
    else:
        lines = [
            f"Lab layout ({role}): server.ip={cfg.server.ip}",
            f"  knock: {mode} → {knock_target}",
        ]
    if cfg_path.parent.name == "generated":
        lines.append(f"  video: {Path(cfg.video.artifact_enc).name}")

    if peer_cfg is not None:
        peer_mode, peer_knock = knock_mode_label(peer_cfg.server.ip, peer_cfg.client.ip)
        lines.append(
            f"  peer {peer_role}: server.ip={peer_cfg.server.ip}  client.ip={peer_cfg.client.ip} "
            f"({peer_mode} → {peer_knock})"
        )
        if cfg.auth.totp_secret == peer_cfg.auth.totp_secret:
            lines.append(f"  TOTP: match (…{totp_tag(cfg.auth.totp_secret)})")
        else:
            lines.append(
                f"  TOTP: MISMATCH — {role} …{totp_tag(cfg.auth.totp_secret)} vs "
                f"{peer_role} …{totp_tag(peer_cfg.auth.totp_secret)} → expect DENIED"
            )
        if mode != peer_mode:
            lines.append(
                "  WARNING: sniffer/knock mode differs — align client.ip in server.yaml "
                "with knocker.yaml (same-host: both client.ip == server.ip)"
            )
    return lines


def sniffer_pair_errors(knocker_cfg, server_cfg) -> list[str]:
    """Return human-readable errors when knocker knock path and server sniffer disagree."""
    knocker_loopback = knocker_cfg.client.ip == knocker_cfg.server.ip
    server_loopback = server_cfg.server.ip == server_cfg.client.ip
    if knocker_loopback == server_loopback:
        return []
    if knocker_loopback:
        return [
            "Config mismatch — knocker uses loopback header knock, server sniffs LAN:",
            f"  knocker.yaml: client.ip={knocker_cfg.client.ip} == server.ip → 127.0.0.1",
            f"  server.yaml:  client.ip={server_cfg.client.ip} != server.ip {server_cfg.server.ip}",
            f"  Fix (one laptop): set server.yaml client.ip to {knocker_cfg.client.ip}",
        ]
    return [
        "Config mismatch — knocker uses LAN jitter knock, server sniffs loopback:",
        f"  knocker.yaml: client.ip={knocker_cfg.client.ip} → knock {knocker_cfg.server.ip}",
        f"  server.yaml:  client.ip={server_cfg.client.ip} == server.ip → loopback sniffer",
        f"  Fix: python scripts/generate_lab_configs.py "
        f"--server-ip {knocker_cfg.server.ip} --knocker-ip {knocker_cfg.client.ip}",
    ]


def validate_generated_pair(cfg_path: Path, role: str, console=None) -> None:
    """Exit if server.yaml and knocker.yaml disagree on same-host vs cross-host."""
    from nps_lab_el.models.config import load_config

    cfg = load_config(str(cfg_path))
    peer_cfg, _ = peer_generated_config(role, cfg_path)
    if peer_cfg is None:
        return
    knocker_cfg = cfg if role == "knocker" else peer_cfg
    server_cfg = cfg if role == "server" else peer_cfg
    errors = sniffer_pair_errors(knocker_cfg, server_cfg)
    if not errors:
        return
    header = "Cannot start — YAML sniffer/knock mismatch:"
    if console is not None:
        console.print(f"[red]{header}[/red]")
        for line in errors:
            style = "yellow" if line.startswith("  Fix") else None
            console.print(f"[{style}]{line}[/{style}]" if style else line)
    else:
        print(header)
        for line in errors:
            print(line)
    sys.exit(1)


def write_lab_info_video_stem(stem: str) -> None:
    info_path = GENERATED_DIR / "lab_info.json"
    if info_path.is_file():
        info = json.loads(info_path.read_text())
    else:
        info = {}
    info["video_stem"] = stem
    info["video_paths"] = video_block_for_stem(stem)
    info_path.write_text(json.dumps(info, indent=2) + "\n")
