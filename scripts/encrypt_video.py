#!/usr/bin/env python3
"""
Encrypt a video file for segment-based DRM protection.

The video is split into 256 KB segments; each segment gets its own AES-256-GCM
key.  NO key material is stored in the manifest — all keys live only in
server_secrets/<name>.segkeys (gitignored).

Produces:
  artifacts/<name>.seg.<N>.enc      — per-segment AES-256-GCM ciphertext
  artifacts/<name>.manifest.json    — segment metadata (nonces, sha256, sizes) — NO keys
  server_secrets/<name>.segkeys     — newline-separated base64 keys, one per segment

Usage:
  python scripts/encrypt_video.py <path/to/video.mp4>
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SEGMENT_SIZE = 262144  # 256 KB per segment


def encrypt_video(video_path: Path, segment_size: int = SEGMENT_SIZE) -> None:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    plaintext = video_path.read_bytes()
    aad = b"nps-lab-el-drm-v2-segment"

    raw_segments = [
        plaintext[i: i + segment_size]
        for i in range(0, len(plaintext), segment_size)
    ]

    artifacts = ROOT / "artifacts"
    artifacts.mkdir(exist_ok=True)
    secrets_dir = ROOT / "server_secrets"
    secrets_dir.mkdir(exist_ok=True)

    stem = video_path.stem
    segment_meta = []
    all_keys_b64 = []

    for idx, seg in enumerate(raw_segments):
        key = os.urandom(32)
        nonce = os.urandom(12)
        ciphertext = AESGCM(key).encrypt(nonce, seg, aad)

        seg_path = artifacts / f"{stem}.seg.{idx}.enc"
        seg_path.write_bytes(ciphertext)

        segment_meta.append({
            "index": idx,
            "file": seg_path.name,
            "nonce_b64": base64.b64encode(nonce).decode(),
            "plaintext_sha256": hashlib.sha256(seg).hexdigest(),
            "plaintext_size": len(seg),
            "ciphertext_size": len(ciphertext),
        })
        all_keys_b64.append(base64.b64encode(key).decode())

    manifest = {
        "source_filename": video_path.name,
        "aad_b64": base64.b64encode(aad).decode(),
        "total_plaintext_sha256": hashlib.sha256(plaintext).hexdigest(),
        "total_plaintext_size": len(plaintext),
        "segment_count": len(raw_segments),
        "segments": segment_meta,
        # NO key material — all keys stored only in server_secrets/
    }
    manifest_path = artifacts / f"{stem}.manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")

    segkeys_path = secrets_dir / f"{stem}.segkeys"
    segkeys_path.write_text("\n".join(all_keys_b64) + "\n")

    print(f"Source    : {video_path}  ({len(plaintext)} bytes)")
    print(f"Segments  : {len(raw_segments)}  ({segment_size} bytes each)")
    print(f"Manifest  : {manifest_path}  (no keys)")
    print(f"Seg keys  : {segkeys_path}  (server only — DO NOT share)")
    print(f"SHA-256   : {manifest['total_plaintext_sha256']}")
    print()
    print("Point lab configs at this video (any format — mp4, avi, mkv, …):")
    print(f"  python scripts/sync_lab_video.py --stem {stem}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/encrypt_video.py <path/to/video.mp4>")
        sys.exit(1)

    video = Path(sys.argv[1])
    if not video.exists():
        print(f"File not found: {video}")
        sys.exit(1)

    encrypt_video(video)
