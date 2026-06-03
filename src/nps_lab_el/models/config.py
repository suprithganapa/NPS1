from __future__ import annotations

import copy
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field


class JitterConfig(BaseModel):
    enabled: bool
    t0_ms: int = 1000
    delta_ms: int = 50
    tau_ms: int = 25


class HeaderConfig(BaseModel):
    enabled: bool = False
    lfsr_seed: int = 44257
    windows_compat: bool = False


class DnsConfig(BaseModel):
    enabled: bool = False


class ChannelsConfig(BaseModel):
    jitter: JitterConfig
    header: HeaderConfig = Field(default_factory=HeaderConfig)
    dns: DnsConfig = Field(default_factory=DnsConfig)


class FecConfig(BaseModel):
    enabled: bool = True
    n: int = 32
    k: int = 28


class DrmConfig(BaseModel):
    enabled: bool = True
    fragment_bits: int = 32


class ServerConfig(BaseModel):
    ip: str
    iface: str = "eth0"
    video_port: int = 8765


class ClientConfig(BaseModel):
    ip: str = "127.0.0.1"
    control_port: int = 8766


class AuthConfig(BaseModel):
    totp_secret: str
    window_seconds: int = 60
    ttl_auth_seconds: int = 300


class TargetsConfig(BaseModel):
    allowlist: list[str] = Field(default_factory=list)


class FirewallConfig(BaseModel):
    mode: Literal["log_only", "iptables", "netsh"] = "log_only"


class VideoConfig(BaseModel):
    enabled: bool = True
    artifact_enc: str = "artifacts/sample_video.enc"
    artifact_manifest: str = "artifacts/sample_video.manifest.json"
    keyfrag: str = "server_secrets/sample_video.keyfrag"


class AppConfig(BaseModel):
    server: ServerConfig
    auth: AuthConfig
    channels: ChannelsConfig
    client: ClientConfig = Field(default_factory=ClientConfig)
    fec: FecConfig = Field(default_factory=FecConfig)
    drm: DrmConfig = Field(default_factory=DrmConfig)
    video: VideoConfig = Field(default_factory=VideoConfig)
    targets: TargetsConfig = Field(default_factory=TargetsConfig)
    firewall: FirewallConfig = Field(default_factory=FirewallConfig)


def _deep_merge(base: dict, overlay: dict) -> dict:
    result = copy.deepcopy(base)
    for key, value in overlay.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def load_config(path: str) -> AppConfig:
    defaults_candidates = [
        Path(__file__).resolve().parent.parent.parent.parent / "config" / "defaults.yaml",
        Path("config") / "defaults.yaml",
    ]
    defaults: dict = {}
    for candidate in defaults_candidates:
        if candidate.is_file():
            defaults = yaml.safe_load(candidate.read_text()) or {}
            break

    user_path = Path(path)
    user_data: dict = {}
    if user_path.is_file():
        user_data = yaml.safe_load(user_path.read_text()) or {}

    merged = _deep_merge(defaults, user_data)
    return AppConfig.model_validate(merged)
