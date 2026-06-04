#!/usr/bin/env python3
"""
Encrypt a video file for DRM protection.
Produces:  artifacts/<name>.enc   — AES-256-GCM ciphertext
           artifacts/<name>.manifest.json — metadata (key_partial, nonce, aad, sha256)
The key_fragment (4 bytes) is NOT stored in the manifest — it must be
delivered at runtime via the ICMP knock reply.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def encrypt_video(video_path: Path) -> None:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    plaintext = video_path.read_bytes()
    key = os.urandom(32)
    key_partial = key[:28]
    key_fragment = key[28:]

    nonce = os.urandom(12)
    aad = b"nps-lab-el-drm-v1"

    ciphertext = AESGCM(key).encrypt(nonce, plaintext, aad)

    artifacts = ROOT / "artifacts"
    artifacts.mkdir(exist_ok=True)

    stem = video_path.stem
    enc_path = artifacts / f"{stem}.enc"
    manifest_path = artifacts / f"{stem}.manifest.json"

    enc_path.write_bytes(ciphertext)

    manifest = {
        "artifact": enc_path.name,
        "source_filename": video_path.name,
        "nonce_b64": base64.b64encode(nonce).decode(),
        "key_partial_b64": base64.b64encode(key_partial).decode(),
        # key_fragment intentionally omitted — delivered via ICMP knock reply
        "aad_b64": base64.b64encode(aad).decode(),
        "plaintext_sha256": hashlib.sha256(plaintext).hexdigest(),
        "plaintext_size": len(plaintext),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")

    print(f"Encrypted : {enc_path}  ({len(ciphertext)} bytes)")
    print(f"Manifest  : {manifest_path}")
    print(f"SHA-256   : {manifest['plaintext_sha256']}")
    print()
    print("Key fragment (4 bytes) stored ONLY in server — NOT in manifest.")
    print("Clients must complete the ICMP knock to receive it.")

    # Write key_fragment to a server-side secrets file (gitignored)
    secrets_dir = ROOT / "server_secrets"
    secrets_dir.mkdir(exist_ok=True)
    secret_path = secrets_dir / f"{stem}.keyfrag"
    secret_path.write_bytes(key_fragment)
    print(f"Server key fragment saved to: {secret_path}  (DO NOT share)")
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
