#!/usr/bin/env python3
"""
Point server.yaml and knocker.yaml at encrypted artifacts for any video stem.

After encrypting:
  python scripts/encrypt_video.py "G:\\VEDIOS\\my_movie.avi"
  python scripts/sync_lab_video.py --stem my_movie

Both generated YAMLs will use artifacts/my_movie.enc, manifest, and keyfrag.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from lab_config_common import GENERATED_DIR, patch_yaml_video, write_lab_info_video_stem


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync video artifact paths in lab YAML configs")
    parser.add_argument(
        "--stem",
        required=True,
        help="Filename stem from encrypt_video (e.g. my_movie for my_movie.enc)",
    )
    args = parser.parse_args()
    stem = args.stem.strip().removesuffix(".enc")

    enc = ROOT / "artifacts" / f"{stem}.enc"
    manifest = ROOT / "artifacts" / f"{stem}.manifest.json"
    keyfrag = ROOT / "server_secrets" / f"{stem}.keyfrag"
    missing = [p for p in (enc, manifest, keyfrag) if not p.is_file()]
    if missing:
        print("Missing files (run encrypt_video first):")
        for p in missing:
            print(f"  {p}")
        sys.exit(1)

    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    for name in ("server.yaml", "knocker.yaml"):
        path = GENERATED_DIR / name
        if not path.is_file():
            print(f"Missing {path} — run generate_lab_configs.py first")
            sys.exit(1)
        patch_yaml_video(path, stem)
        print(f"Updated {path.relative_to(ROOT)}")

    write_lab_info_video_stem(stem)
    print()
    print(f"Ready: server + knocker both reference artifacts/{stem}.enc")
    print("Restart run_lab_server.py, then run video_receiver.py")


if __name__ == "__main__":
    main()
