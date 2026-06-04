# NPS LAB EL — Stealthy ICMP Single-Packet Authorization + Segment DRM

A research framework that combines covert-channel authentication (ICMP jitter timing) with segment-based video DRM. A client must complete a valid ICMP knock before the server will deliver decryption keys; the video is never served in plaintext.

> **Ethics** — Lab/research use only on networks and hosts you control.

---

## How It Works

```
Client                                   Server
  │                                         │
  │── 512 ICMP echo-requests (jitter) ────► │  AuthEngine verifies TOTP + HMAC
  │                                         │  Creates session token
  │◄─ 8 ICMP echo-replies (32-byte key) ───│  Sends segment 0 key via 8 ICMP replies
  │                                         │
  │── GET /video/segment/0 + token ────────►│  Validates session token
  │◄─ encrypted segment 0 (.enc) ──────────│  Fires segment 1 key via 8 ICMP replies
  │                                         │
  │  decrypt seg 0 with received key        │
  │                                         │
  │── GET /video/segment/1 + token ────────►│  Validates token, checks order
  │◄─ encrypted segment 1 (.enc) ──────────│  Fires segment 2 key via 8 ICMP replies
  │                                         │
  │  decrypt seg 1 ...                      │
  │  reassemble full MP4                    │
```

### Key Security Properties

| Property | Mechanism |
|---|---|
| Covert authentication | Data encoded in ICMP inter-arrival timing (20 ms = bit 0, 25 ms = bit 1) |
| Replay protection | TOTP counter (60 s window) + 128-bit random payload nonce |
| Forgery prevention | HMAC-SHA256 over payload + counter + channel mask |
| Full 256-bit key space | Entire 32-byte AES-256 key delivered via ICMP — not stored anywhere client-accessible |
| Per-segment keys | Each 256 KB segment has its own key; revoking access stops future segments |
| No plaintext on wire | Server serves only `.enc` ciphertext; decryption is local on client |

---

## Project Structure

```
NPS1/
├── scripts/
│   ├── encrypt_video.py          # Segment-encrypt an MP4 (produces .seg.N.enc + .segkeys)
│   ├── run_lab_server.py         # ICMP sniffer + HTTP segment server
│   ├── video_receiver.py         # Knock client + segment downloader + reassembler
│   ├── generate_lab_configs.py   # Generate server.yaml + knocker.yaml for a lab pair
│   ├── sync_lab_video.py         # Update configs to point at a different video stem
│   └── lab_config_common.py      # Shared display/config utilities
├── src/nps_lab_el/
│   ├── crypto/
│   │   ├── aes_gcm.py            # AES-256-GCM encrypt/decrypt helpers
│   │   └── totp.py               # TOTP counter + replay cache
│   ├── protocol/
│   │   ├── encode_jitter.py      # Encode token bits as IAT delays
│   │   ├── decode.py             # Decode IAT delays back to bits
│   │   ├── encode_header.py      # Alternate channel: LFSR-scrambled IP/ICMP headers
│   │   └── fec.py                # Reed-Solomon FEC (n=32, k=28)
│   ├── services/
│   │   ├── auth_engine.py        # Verifies knock sessions (TOTP + HMAC + replay)
│   │   └── drm_responder.py      # Builds / parses 8-packet ICMP key delivery
│   └── models/
│       ├── auth.py               # AuthToken, KnockSession, KeyMaterial
│       └── config.py             # Pydantic config models
├── config/
│   ├── defaults.yaml             # Default parameters
│   └── examples/                 # Example server/knocker YAML pairs
├── tests/                        # Unit + integration tests
└── artifacts/                    # Generated encrypted segments (gitignored)
```

---

## Dashboard UI

A single-page web dashboard lets you run all backend scripts without touching the terminal.

```bash
# Install dependencies first
pip install -r requirements.txt

# Start the dashboard (no root needed)
python scripts/lab_ui.py
# → Open http://localhost:8080 in your browser
```

Features:
- **Encrypt** — pick an MP4, choose segment size, watch encryption progress live
- **Server** — start/stop `run_lab_server.py` with simulation and HTTP-only toggles
- **Client** — run `video_receiver.py`, visualize the knock → key → segments → reassemble flow
- **Config** — generate `server.yaml` + `knocker.yaml` from IP inputs
- **Live terminal** — color-coded WebSocket log stream from all subprocesses

---

## Quick Start

### 1. Install dependencies

```bash
pip install -e .
# or
pip install -r requirements.txt
```

Requires Python 3.10+. On Windows, install [Npcap](https://npcap.com/) for raw socket support.

### 2. Generate lab configs

```bash
# Same PC (loopback knock)
python scripts/generate_lab_configs.py \
    --server-ip 127.0.0.1 \
    --knocker-ip 127.0.0.1 \
    --server-video-port 8765

# Two PCs (server 192.168.1.10, client 192.168.1.20)
python scripts/generate_lab_configs.py \
    --server-ip 192.168.1.10 \
    --knocker-ip 192.168.1.20 \
    --server-video-port 8765
```

Produces `config/generated/server.yaml` and `config/generated/knocker.yaml`.

### 3. Encrypt your video

```bash
python scripts/encrypt_video.py /path/to/your_video.mp4
```

This produces:
- `artifacts/your_video.seg.0.enc`, `.seg.1.enc`, … — encrypted segments
- `artifacts/your_video.manifest.json` — nonces + SHA-256, **no key material**
- `server_secrets/your_video.segkeys` — one 32-byte key per segment (**server only**)

Update your configs to use this video:

```bash
python scripts/sync_lab_video.py --stem your_video
```

### 4. Start the server (requires Admin / root for raw sockets)

```bash
# Windows — run as Administrator
python scripts/run_lab_server.py --config config/generated/server.yaml

# Simulation mode (no raw sockets needed)
python scripts/run_lab_server.py --config config/generated/server.yaml --simulation
```

### 5. Run the client

```bash
# Windows — run as Administrator
python scripts/video_receiver.py --config config/generated/knocker.yaml

# Simulation mode
python scripts/video_receiver.py --config config/generated/knocker.yaml --simulation

# Output saved to artifacts/received_video.mp4 by default
python scripts/video_receiver.py --config config/generated/knocker.yaml --output my_video.mp4
```

---

## AuthToken Structure (328 bits over ICMP)

```
┌──────────────────┬────────────────────┬──────────────┬──────────────────────────┐
│  128-bit payload │ 64-bit TOTP counter│ 8-bit channel│   128-bit HMAC-SHA256    │
│  (random nonce)  │  (time window id)  │    mask      │   MAC (truncated)        │
└──────────────────┴────────────────────┴──────────────┴──────────────────────────┘
```

- **Payload** — `os.urandom(16)`: makes every knock unique; fed into MAC so identical counters produce different MACs
- **TOTP counter** — `floor(time() / 60)`: knock expires after the time window; replay in a later window fails
- **Channel mask** — selects jitter (0x01) or header (0x02) channel
- **MAC** — `HMAC-SHA256(secret, payload ‖ counter ‖ mask)[:16]`: prevents forged knocks without the shared secret

The 328 bits are Reed-Solomon encoded (n=32, k=28) → 512 bits → transmitted as 512 ICMP packets with 20 ms (bit 0) or 25 ms (bit 1) inter-arrival gaps.

---

## ICMP Jitter Channel

```
ping ──20ms── ping ──25ms── ping ──20ms── ping ──25ms── ...
               bit=0          bit=1          bit=0          bit=1
```

- Gaps look like normal network jitter to passive observers
- `t0_ms=20`, `delta_ms=5`, `tau_ms=2` (configurable in YAML)
- FEC corrects up to `(n-k)/2 = 2` symbol errors per RS block

---

## 32-byte Key Delivery via ICMP

After a valid knock, the server sends 8 ICMP echo-reply packets. Each packet carries 4 bytes of the segment key encoded in normally-ignored header fields:

```
Packet seq=0 → key[0:4]   (TTL=key[0], TOS=key[1], ICMP_ID XOR nonce = key[2:4])
Packet seq=1 → key[4:8]
...
Packet seq=7 → key[28:32]
```

All 8 packets are marked with `code=0x5A` to distinguish them from OS echo-replies.

---

## Segment DRM Flow

```
encrypt_video.py splits video into 256 KB segments
Each segment → unique AES-256-GCM key → .seg.N.enc file
All keys stored ONLY in server_secrets/*.segkeys

On knock AUTHORIZED:
  server creates session token
  sends segment 0 key via 8 ICMP replies

Client requests /video/segment/0 with token:
  server validates token + order (must be sequential)
  serves encrypted segment 0
  fires segment 1 key via 8 ICMP replies (background thread)

Client decrypts segment 0, requests segment 1 ...
  repeat until last segment

Client reassembles and SHA-256 verifies full video
Session revoked after all segments delivered or TTL expires
```

---

## Configuration Reference

```yaml
server:
  ip: "192.168.1.10"
  iface: "eth0"              # network interface to sniff
  video_port: 8765

auth:
  totp_secret: "BASE32SECRET" # shared between server and knocker
  window_seconds: 60          # TOTP time window
  ttl_auth_seconds: 300       # how long an authorized IP stays valid

channels:
  jitter:
    enabled: true
    t0_ms: 20                 # gap for bit 0 (ms)
    delta_ms: 5               # extra gap for bit 1 (ms)
    tau_ms: 2                 # tolerance window
  header:
    enabled: false            # alternate covert channel (same-host)

fec:
  n: 32                       # RS codeword length (symbols)
  k: 28                       # RS data symbols per block

video:
  artifact_manifest: "artifacts/sample_video.manifest.json"
  segkeys: "server_secrets/sample_video.segkeys"
  segment_size_bytes: 262144  # 256 KB per segment
  session_ttl_seconds: 120    # session expires after this many idle seconds
```

---

## Running Tests

```bash
pytest tests/
```

---

## Security Notes

- The server **never decrypts** the video; it only holds segment keys server-side
- Without completing the ICMP knock, a client can download `.enc` segments but cannot decrypt them (full AES-256 keyspace)
- Session tokens are bound to client IP; they cannot be used from a different address
- Segments must be fetched in order; skipping or replaying is rejected with HTTP 409
- Session is revoked after all segments are delivered, preventing re-download without a new knock
