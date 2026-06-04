#!/usr/bin/env python3
"""
NPS LAB EL — Web Dashboard
Runs at http://localhost:7777

Usage:
  python scripts/lab_ui.py
  python scripts/lab_ui.py --port 7777
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

app = FastAPI(title="NPS LAB EL Dashboard")

# ---------------------------------------------------------------------------
# Global state
# ---------------------------------------------------------------------------

class State:
    server_proc: subprocess.Popen | None = None
    client_proc: subprocess.Popen | None = None
    encrypt_proc: subprocess.Popen | None = None
    ws_clients: list[WebSocket] = []
    log_history: list[dict] = []   # last 500 log lines

state = State()


# ---------------------------------------------------------------------------
# WebSocket broadcast
# ---------------------------------------------------------------------------

async def _broadcast(msg: dict) -> None:
    state.log_history.append(msg)
    if len(state.log_history) > 500:
        state.log_history.pop(0)
    dead = []
    for ws in state.ws_clients:
        try:
            await ws.send_json(msg)
        except Exception:
            dead.append(ws)
    for ws in dead:
        state.ws_clients.remove(ws)


def broadcast_sync(msg: dict) -> None:
    """Thread-safe broadcast from a background thread."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.run_coroutine_threadsafe(_broadcast(msg), loop)
    except RuntimeError:
        pass


def log(kind: str, text: str, source: str = "system") -> None:
    broadcast_sync({"type": "log", "kind": kind, "text": text, "source": source, "ts": time.time()})


# ---------------------------------------------------------------------------
# Subprocess streaming helper
# ---------------------------------------------------------------------------

def _stream_proc(proc: subprocess.Popen, source: str, on_done=None) -> None:
    """Stream stdout/stderr of a subprocess to WebSocket clients."""
    assert proc.stdout is not None
    for raw in proc.stdout:
        line = raw.rstrip("\n")
        if not line:
            continue
        kind = "error" if any(w in line.lower() for w in ["error", "fail", "denied", "mismatch"]) \
               else "success" if any(w in line.lower() for w in ["authorized", "verified", "done", "saved", "sent", "received", "ok"]) \
               else "info"
        log(kind, line, source)
    proc.wait()
    exit_ok = proc.returncode == 0
    log("success" if exit_ok else "error",
        f"Process exited (code {proc.returncode})", source)
    if on_done:
        on_done(exit_ok)


def _run_script(cmd: list[str], source: str, proc_attr: str, on_done=None) -> subprocess.Popen:
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        cwd=str(ROOT),
        env={**os.environ, "PYTHONPATH": str(ROOT / "src"), "PYTHONUNBUFFERED": "1"},
    )
    setattr(state, proc_attr, proc)
    t = threading.Thread(target=_stream_proc, args=(proc, source, on_done), daemon=True)
    t.start()
    return proc


# ---------------------------------------------------------------------------
# API models
# ---------------------------------------------------------------------------

class EncryptRequest(BaseModel):
    video_path: str

class ServerStartRequest(BaseModel):
    config: str = "config/generated/server.yaml"
    simulation: bool = False
    http_only: bool = False

class ClientRunRequest(BaseModel):
    config: str = "config/generated/knocker.yaml"
    simulation: bool = False
    skip_knock: bool = False
    key_fragment: str = ""
    output: str = "artifacts/received_video.mp4"


# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------

@app.get("/api/status")
def get_status() -> JSONResponse:
    server_running = state.server_proc is not None and state.server_proc.poll() is None
    client_running = state.client_proc is not None and state.client_proc.poll() is None
    encrypt_running = state.encrypt_proc is not None and state.encrypt_proc.poll() is None

    # Scan available configs
    gen_dir = ROOT / "config" / "generated"
    configs = sorted(str(p.relative_to(ROOT)) for p in gen_dir.glob("*.yaml")) if gen_dir.is_dir() else []

    # Scan available artifacts
    artifacts_dir = ROOT / "artifacts"
    manifests = sorted(p.name for p in artifacts_dir.glob("*.manifest.json")) if artifacts_dir.is_dir() else []
    segments = {}
    for m in manifests:
        stem = m.replace(".manifest.json", "")
        seg_files = list(artifacts_dir.glob(f"{stem}.seg.*.enc"))
        segments[stem] = len(seg_files)

    return JSONResponse({
        "server": {"running": server_running, "pid": state.server_proc.pid if server_running else None},
        "client": {"running": client_running},
        "encrypt": {"running": encrypt_running},
        "configs": configs,
        "artifacts": {"manifests": manifests, "segments": segments},
    })


@app.post("/api/encrypt")
async def encrypt_video(req: EncryptRequest) -> JSONResponse:
    if state.encrypt_proc and state.encrypt_proc.poll() is None:
        return JSONResponse({"ok": False, "error": "Encryption already running"}, status_code=409)
    path = Path(req.video_path)
    if not path.is_absolute():
        path = ROOT / req.video_path
    if not path.exists():
        return JSONResponse({"ok": False, "error": f"File not found: {path}"}, status_code=400)
    log("info", f"Encrypting: {path}", "encrypt")
    _run_script([sys.executable, "scripts/encrypt_video.py", str(path)], "encrypt", "encrypt_proc")
    return JSONResponse({"ok": True})


@app.post("/api/server/start")
async def server_start(req: ServerStartRequest) -> JSONResponse:
    if state.server_proc and state.server_proc.poll() is None:
        return JSONResponse({"ok": False, "error": "Server already running"}, status_code=409)
    cmd = [sys.executable, "scripts/run_lab_server.py", "--config", req.config]
    if req.simulation:
        cmd.append("--simulation")
    if req.http_only:
        cmd.append("--http-only")
    log("info", f"Starting server: {' '.join(cmd[2:])}", "server")
    _run_script(cmd, "server", "server_proc")
    return JSONResponse({"ok": True})


@app.post("/api/server/stop")
async def server_stop() -> JSONResponse:
    if state.server_proc and state.server_proc.poll() is None:
        state.server_proc.terminate()
        log("info", "Server stopped", "server")
        return JSONResponse({"ok": True})
    return JSONResponse({"ok": False, "error": "Server not running"}, status_code=400)


@app.post("/api/client/run")
async def client_run(req: ClientRunRequest) -> JSONResponse:
    if state.client_proc and state.client_proc.poll() is None:
        return JSONResponse({"ok": False, "error": "Client already running"}, status_code=409)
    cmd = [sys.executable, "scripts/video_receiver.py", "--config", req.config,
           "--output", req.output]
    if req.simulation:
        cmd.append("--simulation")
    if req.skip_knock:
        cmd += ["--skip-knock", "--key-fragment", req.key_fragment]
    log("info", f"Starting client: {' '.join(cmd[2:])}", "client")
    _run_script(cmd, "client", "client_proc")
    return JSONResponse({"ok": True})


@app.post("/api/client/stop")
async def client_stop() -> JSONResponse:
    if state.client_proc and state.client_proc.poll() is None:
        state.client_proc.terminate()
        log("info", "Client stopped", "client")
        return JSONResponse({"ok": True})
    return JSONResponse({"ok": False, "error": "Client not running"}, status_code=400)


@app.post("/api/generate-configs")
async def generate_configs(data: dict) -> JSONResponse:
    cmd = [sys.executable, "scripts/generate_lab_configs.py",
           "--server-ip", data.get("server_ip", "127.0.0.1"),
           "--knocker-ip", data.get("knocker_ip", "127.0.0.1"),
           "--server-video-port", str(data.get("video_port", 8765))]
    log("info", f"Generating configs: server={data.get('server_ip')} knocker={data.get('knocker_ip')}", "config")
    _run_script(cmd, "config", "encrypt_proc")
    return JSONResponse({"ok": True})


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    state.ws_clients.append(ws)
    # Send history on connect
    for msg in state.log_history[-100:]:
        await ws.send_json(msg)
    try:
        while True:
            await ws.receive_text()   # keep alive
    except WebSocketDisconnect:
        if ws in state.ws_clients:
            state.ws_clients.remove(ws)


# ---------------------------------------------------------------------------
# Dashboard HTML
# ---------------------------------------------------------------------------

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>NPS LAB EL — Dashboard</title>
<style>
  :root {
    --bg:      #0a0e1a;
    --bg2:     #0f1629;
    --bg3:     #151d35;
    --border:  #1e2d52;
    --accent:  #00d4ff;
    --accent2: #7c3aed;
    --green:   #00ff9d;
    --red:     #ff4757;
    --yellow:  #ffd32a;
    --text:    #c8d6f5;
    --text2:   #6b7fa3;
    --glow:    0 0 20px rgba(0,212,255,0.15);
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text); font-family: 'Segoe UI', system-ui, sans-serif; height: 100vh; display: flex; flex-direction: column; overflow: hidden; }

  /* ── Header ── */
  header {
    background: var(--bg2);
    border-bottom: 1px solid var(--border);
    padding: 0 24px;
    height: 56px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-shrink: 0;
  }
  .logo { display: flex; align-items: center; gap: 10px; }
  .logo-icon { width: 28px; height: 28px; }
  .logo h1 { font-size: 15px; font-weight: 700; letter-spacing: 0.08em; color: #fff; }
  .logo span { font-size: 11px; color: var(--accent); letter-spacing: 0.15em; text-transform: uppercase; }
  .header-right { display: flex; align-items: center; gap: 16px; }
  .status-pill {
    display: flex; align-items: center; gap: 6px;
    padding: 4px 12px; border-radius: 20px;
    background: var(--bg3); border: 1px solid var(--border);
    font-size: 12px; color: var(--text2);
  }
  .dot { width: 7px; height: 7px; border-radius: 50%; background: var(--text2); }
  .dot.on  { background: var(--green); box-shadow: 0 0 6px var(--green); animation: pulse 2s infinite; }
  .dot.off { background: var(--text2); }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.5} }

  /* ── Layout ── */
  .main { display: flex; flex: 1; overflow: hidden; }
  .sidebar {
    width: 200px; background: var(--bg2); border-right: 1px solid var(--border);
    padding: 16px 0; flex-shrink: 0; display: flex; flex-direction: column; gap: 2px;
  }
  .nav-item {
    display: flex; align-items: center; gap: 10px;
    padding: 10px 20px; cursor: pointer; font-size: 13px; color: var(--text2);
    transition: all 0.15s; border-left: 3px solid transparent; user-select: none;
  }
  .nav-item:hover { background: var(--bg3); color: var(--text); }
  .nav-item.active { background: var(--bg3); color: var(--accent); border-left-color: var(--accent); }
  .nav-icon { font-size: 16px; width: 20px; text-align: center; }
  .nav-section { padding: 16px 20px 6px; font-size: 10px; color: var(--text2); letter-spacing: 0.1em; text-transform: uppercase; }

  /* ── Content ── */
  .content { flex: 1; overflow: hidden; display: flex; flex-direction: column; }
  .panel { display: none; flex: 1; overflow-y: auto; padding: 24px; flex-direction: column; gap: 20px; }
  .panel.active { display: flex; }

  /* ── Cards ── */
  .card {
    background: var(--bg2); border: 1px solid var(--border); border-radius: 10px;
    padding: 20px; box-shadow: var(--glow);
  }
  .card-title { font-size: 12px; text-transform: uppercase; letter-spacing: 0.1em; color: var(--text2); margin-bottom: 16px; display: flex; align-items: center; gap: 8px; }
  .card-title::after { content: ''; flex: 1; height: 1px; background: var(--border); }
  .grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
  .grid3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 16px; }

  /* ── Form elements ── */
  label { display: block; font-size: 12px; color: var(--text2); margin-bottom: 6px; }
  input[type=text], select {
    width: 100%; padding: 9px 12px; background: var(--bg3); border: 1px solid var(--border);
    border-radius: 6px; color: var(--text); font-size: 13px; outline: none;
    transition: border-color 0.2s;
  }
  input[type=text]:focus, select:focus { border-color: var(--accent); }
  select option { background: var(--bg3); }

  .toggle-row { display: flex; align-items: center; justify-content: space-between; padding: 8px 0; }
  .toggle-row label { margin: 0; font-size: 13px; color: var(--text); }
  .toggle {
    width: 40px; height: 22px; background: var(--bg3); border: 1px solid var(--border);
    border-radius: 11px; cursor: pointer; position: relative; transition: background 0.2s;
  }
  .toggle.on { background: var(--accent2); border-color: var(--accent2); }
  .toggle-knob {
    position: absolute; top: 2px; left: 2px; width: 16px; height: 16px;
    background: #fff; border-radius: 50%; transition: left 0.2s; box-shadow: 0 1px 3px rgba(0,0,0,0.4);
  }
  .toggle.on .toggle-knob { left: 20px; }

  /* ── Buttons ── */
  .btn {
    display: inline-flex; align-items: center; gap: 8px;
    padding: 10px 20px; border-radius: 7px; border: none; cursor: pointer;
    font-size: 13px; font-weight: 600; transition: all 0.15s; letter-spacing: 0.02em;
  }
  .btn-primary { background: var(--accent); color: #000; }
  .btn-primary:hover { background: #00bbee; transform: translateY(-1px); box-shadow: 0 4px 15px rgba(0,212,255,0.3); }
  .btn-danger  { background: var(--red); color: #fff; }
  .btn-danger:hover { background: #ff2d3f; transform: translateY(-1px); }
  .btn-success { background: var(--green); color: #000; }
  .btn-success:hover { background: #00e88d; transform: translateY(-1px); box-shadow: 0 4px 15px rgba(0,255,157,0.3); }
  .btn-ghost { background: var(--bg3); color: var(--text); border: 1px solid var(--border); }
  .btn-ghost:hover { border-color: var(--accent); color: var(--accent); }
  .btn:disabled { opacity: 0.45; cursor: not-allowed; transform: none !important; }
  .btn-row { display: flex; gap: 10px; flex-wrap: wrap; }

  /* ── Stats ── */
  .stat-card {
    background: var(--bg3); border: 1px solid var(--border); border-radius: 8px;
    padding: 16px; text-align: center;
  }
  .stat-value { font-size: 28px; font-weight: 700; color: var(--accent); }
  .stat-label { font-size: 11px; color: var(--text2); text-transform: uppercase; letter-spacing: 0.08em; margin-top: 4px; }

  /* ── Terminal / Log ── */
  .log-panel {
    background: var(--bg); border-top: 1px solid var(--border);
    height: 200px; flex-shrink: 0; display: flex; flex-direction: column;
  }
  .log-header {
    display: flex; align-items: center; justify-content: space-between;
    padding: 8px 16px; border-bottom: 1px solid var(--border); background: var(--bg2);
  }
  .log-title { font-size: 11px; color: var(--text2); text-transform: uppercase; letter-spacing: 0.1em; display: flex; align-items: center; gap: 8px; }
  .log-live { width: 6px; height: 6px; border-radius: 50%; background: var(--green); animation: pulse 1.5s infinite; }
  #log-body {
    flex: 1; overflow-y: auto; padding: 8px 16px;
    font-family: 'Consolas', 'Monaco', monospace; font-size: 12px; line-height: 1.6;
  }
  .log-line { display: flex; gap: 10px; align-items: flex-start; }
  .log-ts { color: var(--text2); flex-shrink: 0; font-size: 11px; }
  .log-src { flex-shrink: 0; width: 52px; text-align: right; font-size: 11px; }
  .log-src.server { color: #7c3aed; }
  .log-src.client { color: #2563eb; }
  .log-src.encrypt { color: #d97706; }
  .log-src.config  { color: #059669; }
  .log-src.system  { color: var(--text2); }
  .log-text { color: var(--text); }
  .log-text.success { color: var(--green); }
  .log-text.error   { color: var(--red); }
  .log-text.info    { color: var(--text); }

  /* ── Overview ── */
  .overview-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; }
  .proc-card {
    background: var(--bg3); border: 1px solid var(--border); border-radius: 10px;
    padding: 18px; display: flex; flex-direction: column; gap: 12px;
  }
  .proc-header { display: flex; align-items: center; justify-content: space-between; }
  .proc-name { font-size: 13px; font-weight: 600; }
  .badge {
    padding: 3px 10px; border-radius: 12px; font-size: 11px; font-weight: 600;
  }
  .badge-on  { background: rgba(0,255,157,0.15); color: var(--green); border: 1px solid rgba(0,255,157,0.3); }
  .badge-off { background: rgba(107,127,163,0.15); color: var(--text2); border: 1px solid var(--border); }
  .proc-desc { font-size: 12px; color: var(--text2); line-height: 1.5; }

  .artifact-row {
    display: flex; align-items: center; justify-content: space-between;
    padding: 10px 14px; background: var(--bg3); border-radius: 7px;
    border: 1px solid var(--border); margin-bottom: 8px;
  }
  .artifact-name { font-size: 13px; font-weight: 500; }
  .artifact-meta { font-size: 11px; color: var(--text2); margin-top: 2px; }
  .seg-badge { padding: 3px 8px; border-radius: 10px; font-size: 11px; background: rgba(0,212,255,0.1); color: var(--accent); border: 1px solid rgba(0,212,255,0.2); }

  /* scrollbar */
  ::-webkit-scrollbar { width: 5px; height: 5px; }
  ::-webkit-scrollbar-track { background: transparent; }
  ::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }
</style>
</head>
<body>

<!-- Header -->
<header>
  <div class="logo">
    <svg class="logo-icon" viewBox="0 0 28 28" fill="none">
      <circle cx="14" cy="14" r="13" stroke="#00d4ff" stroke-width="1.5"/>
      <path d="M7 14 Q14 4 21 14 Q14 24 7 14Z" fill="none" stroke="#00d4ff" stroke-width="1.5"/>
      <circle cx="14" cy="14" r="3" fill="#00d4ff"/>
      <line x1="14" y1="1" x2="14" y2="6" stroke="#00d4ff" stroke-width="1.5"/>
      <line x1="14" y1="22" x2="14" y2="27" stroke="#00d4ff" stroke-width="1.5"/>
    </svg>
    <div>
      <h1>NPS LAB EL</h1>
      <span>ICMP Covert DRM</span>
    </div>
  </div>
  <div class="header-right">
    <div class="status-pill"><div class="dot" id="hdr-server-dot"></div><span id="hdr-server-txt">Server offline</span></div>
    <div class="status-pill"><div class="dot" id="hdr-ws-dot"></div><span id="hdr-ws-txt">Connecting…</span></div>
  </div>
</header>

<!-- Main -->
<div class="main">
  <!-- Sidebar -->
  <nav class="sidebar">
    <div class="nav-section">Overview</div>
    <div class="nav-item active" data-panel="overview"><span class="nav-icon">⬡</span>Dashboard</div>
    <div class="nav-section">Operations</div>
    <div class="nav-item" data-panel="encrypt"><span class="nav-icon">🔒</span>Encrypt</div>
    <div class="nav-item" data-panel="server"><span class="nav-icon">⚙</span>Server</div>
    <div class="nav-item" data-panel="client"><span class="nav-icon">📡</span>Client</div>
    <div class="nav-section">Setup</div>
    <div class="nav-item" data-panel="config"><span class="nav-icon">⚡</span>Config</div>
  </nav>

  <!-- Panels -->
  <div class="content">

    <!-- OVERVIEW -->
    <div class="panel active" id="panel-overview">
      <div class="overview-grid">
        <div class="proc-card">
          <div class="proc-header"><span class="proc-name">Lab Server</span><span class="badge badge-off" id="ov-server-badge">Offline</span></div>
          <div class="proc-desc">ICMP sniffer + HTTP segment delivery. Authorizes knocks and releases segment keys.</div>
          <button class="btn btn-success" onclick="nav('server')" style="font-size:12px;padding:7px 14px;">Open →</button>
        </div>
        <div class="proc-card">
          <div class="proc-header"><span class="proc-name">Knock Client</span><span class="badge badge-off" id="ov-client-badge">Idle</span></div>
          <div class="proc-desc">Sends ICMP knock, collects 32-byte key via 8 ICMP replies, downloads and decrypts segments.</div>
          <button class="btn btn-ghost" onclick="nav('client')" style="font-size:12px;padding:7px 14px;">Open →</button>
        </div>
        <div class="proc-card">
          <div class="proc-header"><span class="proc-name">Encryption</span><span class="badge badge-off" id="ov-enc-badge">Idle</span></div>
          <div class="proc-desc">Splits video into 256 KB segments, each encrypted with a unique AES-256-GCM key.</div>
          <button class="btn btn-ghost" onclick="nav('encrypt')" style="font-size:12px;padding:7px 14px;">Open →</button>
        </div>
        <div class="proc-card">
          <div class="proc-header"><span class="proc-name">Artifacts</span><span class="badge" id="ov-art-badge" style="background:rgba(0,212,255,0.1);color:var(--accent);border:1px solid rgba(0,212,255,0.2)">0 videos</span></div>
          <div class="proc-desc">Encrypted segment files ready for DRM delivery. Keys stored server-side only.</div>
          <button class="btn btn-ghost" onclick="nav('encrypt')" style="font-size:12px;padding:7px 14px;">View →</button>
        </div>
      </div>

      <div class="grid3">
        <div class="card">
          <div class="card-title">AuthToken</div>
          <div style="font-size:12px;color:var(--text2);line-height:1.8;">
            <div>🔑 <b>128-bit</b> random payload nonce</div>
            <div>⏱ <b>64-bit</b> TOTP counter (60s window)</div>
            <div>📶 <b>8-bit</b> channel mask</div>
            <div>🛡 <b>128-bit</b> HMAC-SHA256 MAC</div>
          </div>
        </div>
        <div class="card">
          <div class="card-title">ICMP Channel</div>
          <div style="font-size:12px;color:var(--text2);line-height:1.8;">
            <div>📦 512 packets per knock</div>
            <div>⏳ 20 ms = bit 0 / 25 ms = bit 1</div>
            <div>🔄 Reed-Solomon FEC (n=32, k=28)</div>
            <div>🔑 Key via 8 ICMP reply packets</div>
          </div>
        </div>
        <div class="card">
          <div class="card-title">Segment DRM</div>
          <div style="font-size:12px;color:var(--text2);line-height:1.8;">
            <div>🗂 256 KB per segment</div>
            <div>🔐 Unique AES-256-GCM key/segment</div>
            <div>🎫 Session token gates delivery</div>
            <div>🚫 No plaintext ever on wire</div>
          </div>
        </div>
      </div>

      <div class="card">
        <div class="card-title">Artifacts</div>
        <div id="ov-artifacts-list"><p style="color:var(--text2);font-size:13px;">No encrypted artifacts found.</p></div>
      </div>
    </div>

    <!-- ENCRYPT -->
    <div class="panel" id="panel-encrypt">
      <div class="card">
        <div class="card-title">Encrypt Video File</div>
        <div class="grid2" style="gap:20px;align-items:start;">
          <div>
            <label>Video File Path</label>
            <input type="text" id="enc-path" placeholder="/path/to/video.mp4 or artifacts/sample.mp4">
            <p style="font-size:11px;color:var(--text2);margin-top:6px;">Provide an absolute path or a path relative to the project root. Any format (mp4, mkv, avi).</p>
          </div>
          <div>
            <label>Segment Size</label>
            <select id="enc-seg-size" style="width:100%">
              <option value="262144">256 KB (default)</option>
              <option value="524288">512 KB</option>
              <option value="1048576">1 MB</option>
            </select>
            <p style="font-size:11px;color:var(--text2);margin-top:6px;">Each segment gets its own 32-byte AES key delivered via ICMP.</p>
          </div>
        </div>
        <div class="btn-row" style="margin-top:20px;">
          <button class="btn btn-primary" id="enc-btn" onclick="doEncrypt()">🔒 Encrypt</button>
        </div>
      </div>
      <div class="card">
        <div class="card-title">Encrypted Artifacts</div>
        <div id="enc-artifacts-list"><p style="color:var(--text2);font-size:13px;">No encrypted artifacts yet.</p></div>
      </div>
    </div>

    <!-- SERVER -->
    <div class="panel" id="panel-server">
      <div class="grid2">
        <div class="card">
          <div class="card-title">Server Configuration</div>
          <div style="display:flex;flex-direction:column;gap:14px;">
            <div>
              <label>Config File</label>
              <select id="srv-config"><option value="config/generated/server.yaml">config/generated/server.yaml</option></select>
            </div>
            <div class="toggle-row">
              <label>Simulation Mode <span style="color:var(--text2);font-size:11px;">(no raw sockets)</span></label>
              <div class="toggle" id="srv-sim-toggle" onclick="toggleEl(this)"><div class="toggle-knob"></div></div>
            </div>
            <div class="toggle-row">
              <label>HTTP Only <span style="color:var(--text2);font-size:11px;">(no ICMP sniffer)</span></label>
              <div class="toggle" id="srv-http-toggle" onclick="toggleEl(this)"><div class="toggle-knob"></div></div>
            </div>
          </div>
          <div class="btn-row" style="margin-top:20px;">
            <button class="btn btn-success" id="srv-start-btn" onclick="serverStart()">▶ Start Server</button>
            <button class="btn btn-danger"  id="srv-stop-btn"  onclick="serverStop()" disabled>■ Stop</button>
          </div>
        </div>
        <div class="card">
          <div class="card-title">Status</div>
          <div style="display:flex;flex-direction:column;gap:12px;">
            <div class="stat-card"><div class="stat-value" id="srv-status-val">—</div><div class="stat-label">Server State</div></div>
            <div style="font-size:12px;color:var(--text2);line-height:2;">
              <div>🌐 Video port: <span style="color:var(--text)">8765</span></div>
              <div>📡 Route: <span style="color:var(--text)">/video/segment/&lt;N&gt;</span></div>
              <div>🔐 Auth: <span style="color:var(--text)">ICMP knock required</span></div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- CLIENT -->
    <div class="panel" id="panel-client">
      <div class="grid2">
        <div class="card">
          <div class="card-title">Knock Configuration</div>
          <div style="display:flex;flex-direction:column;gap:14px;">
            <div>
              <label>Config File</label>
              <select id="cli-config"><option value="config/generated/knocker.yaml">config/generated/knocker.yaml</option></select>
            </div>
            <div>
              <label>Output File</label>
              <input type="text" id="cli-output" value="artifacts/received_video.mp4">
            </div>
            <div class="toggle-row">
              <label>Simulation Mode <span style="color:var(--text2);font-size:11px;">(no raw sockets)</span></label>
              <div class="toggle" id="cli-sim-toggle" onclick="toggleEl(this)"><div class="toggle-knob"></div></div>
            </div>
            <div class="toggle-row">
              <label>Skip Knock <span style="color:var(--text2);font-size:11px;">(reuse existing auth)</span></label>
              <div class="toggle" id="cli-skip-toggle" onclick="toggleEl(this);toggleSkip()"><div class="toggle-knob"></div></div>
            </div>
            <div id="cli-keyfrag-row" style="display:none;">
              <label>Key Fragment (hex)</label>
              <input type="text" id="cli-keyfrag" placeholder="e.g. ab12cd34">
            </div>
          </div>
          <div class="btn-row" style="margin-top:20px;">
            <button class="btn btn-primary" id="cli-run-btn" onclick="clientRun()">📡 Send Knock + Download</button>
            <button class="btn btn-danger"  id="cli-stop-btn" onclick="clientStop()" disabled>■ Stop</button>
          </div>
        </div>
        <div class="card">
          <div class="card-title">Flow</div>
          <div id="flow-steps" style="display:flex;flex-direction:column;gap:8px;">
            <div class="flow-step" id="step-knock">
              <div style="display:flex;align-items:center;gap:10px;padding:10px 14px;border-radius:7px;background:var(--bg3);border:1px solid var(--border);">
                <span style="font-size:18px;">📡</span>
                <div><div style="font-size:13px;font-weight:500;">ICMP Knock</div><div style="font-size:11px;color:var(--text2);">512 packets · jitter timing</div></div>
                <div class="flow-dot" style="margin-left:auto;width:8px;height:8px;border-radius:50%;background:var(--text2);"></div>
              </div>
            </div>
            <div style="text-align:center;color:var(--text2);font-size:11px;">↓</div>
            <div class="flow-step" id="step-key">
              <div style="display:flex;align-items:center;gap:10px;padding:10px 14px;border-radius:7px;background:var(--bg3);border:1px solid var(--border);">
                <span style="font-size:18px;">🔑</span>
                <div><div style="font-size:13px;font-weight:500;">Key Delivery</div><div style="font-size:11px;color:var(--text2);">8 ICMP replies · 32-byte key</div></div>
                <div class="flow-dot" style="margin-left:auto;width:8px;height:8px;border-radius:50%;background:var(--text2);"></div>
              </div>
            </div>
            <div style="text-align:center;color:var(--text2);font-size:11px;">↓</div>
            <div class="flow-step" id="step-seg">
              <div style="display:flex;align-items:center;gap:10px;padding:10px 14px;border-radius:7px;background:var(--bg3);border:1px solid var(--border);">
                <span style="font-size:18px;">📦</span>
                <div><div style="font-size:13px;font-weight:500;">Segment Download</div><div style="font-size:11px;color:var(--text2);">Encrypted segments · per-segment keys</div></div>
                <div class="flow-dot" style="margin-left:auto;width:8px;height:8px;border-radius:50%;background:var(--text2);"></div>
              </div>
            </div>
            <div style="text-align:center;color:var(--text2);font-size:11px;">↓</div>
            <div class="flow-step" id="step-done">
              <div style="display:flex;align-items:center;gap:10px;padding:10px 14px;border-radius:7px;background:var(--bg3);border:1px solid var(--border);">
                <span style="font-size:18px;">✅</span>
                <div><div style="font-size:13px;font-weight:500;">Reassemble</div><div style="font-size:11px;color:var(--text2);">Decrypt · verify SHA-256 · save MP4</div></div>
                <div class="flow-dot" style="margin-left:auto;width:8px;height:8px;border-radius:50%;background:var(--text2);"></div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- CONFIG -->
    <div class="panel" id="panel-config">
      <div class="card">
        <div class="card-title">Generate Lab Configs</div>
        <div class="grid3" style="gap:16px;">
          <div>
            <label>Server IP</label>
            <input type="text" id="cfg-server-ip" value="127.0.0.1">
          </div>
          <div>
            <label>Knocker IP</label>
            <input type="text" id="cfg-knocker-ip" value="127.0.0.1">
          </div>
          <div>
            <label>Video Port</label>
            <input type="text" id="cfg-video-port" value="8765">
          </div>
        </div>
        <p style="font-size:12px;color:var(--text2);margin-top:12px;">Same IP = loopback (header channel). Different IPs = Wi-Fi jitter knock.</p>
        <div class="btn-row" style="margin-top:20px;">
          <button class="btn btn-primary" onclick="generateConfigs()">⚡ Generate Configs</button>
        </div>
      </div>
      <div class="card">
        <div class="card-title">Available Configs</div>
        <div id="cfg-list"><p style="color:var(--text2);font-size:13px;">No configs generated yet. Use the form above.</p></div>
      </div>
    </div>

  </div><!-- /content -->
</div><!-- /main -->

<!-- Log Panel -->
<div class="log-panel">
  <div class="log-header">
    <div class="log-title"><div class="log-live"></div>Live Event Log</div>
    <button class="btn btn-ghost" onclick="clearLog()" style="font-size:11px;padding:4px 10px;">Clear</button>
  </div>
  <div id="log-body"></div>
</div>

<script>
// ── Navigation ──
function nav(panel) {
  document.querySelectorAll('.nav-item').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('.panel').forEach(el => el.classList.remove('active'));
  document.querySelector(`[data-panel="${panel}"]`).classList.add('active');
  document.getElementById(`panel-${panel}`).classList.add('active');
}
document.querySelectorAll('.nav-item').forEach(el => {
  el.addEventListener('click', () => nav(el.dataset.panel));
});

// ── Toggles ──
function toggleEl(el) {
  el.classList.toggle('on');
}
function toggleSkip() {
  const row = document.getElementById('cli-keyfrag-row');
  row.style.display = document.getElementById('cli-skip-toggle').classList.contains('on') ? 'block' : 'none';
}

// ── WebSocket ──
let ws;
function connectWS() {
  ws = new WebSocket(`ws://${location.host}/ws`);
  ws.onopen = () => {
    setWsStatus(true);
    appendLog({type:'log',kind:'info',text:'Connected to dashboard',source:'system',ts:Date.now()/1000});
  };
  ws.onclose = () => {
    setWsStatus(false);
    setTimeout(connectWS, 2000);
  };
  ws.onmessage = e => {
    const msg = JSON.parse(e.data);
    if (msg.type === 'log') appendLog(msg);
    updateFlowStep(msg);
  };
}
function setWsStatus(on) {
  document.getElementById('hdr-ws-dot').className = 'dot ' + (on ? 'on' : 'off');
  document.getElementById('hdr-ws-txt').textContent = on ? 'Live' : 'Disconnected';
}

// ── Log ──
const logBody = document.getElementById('log-body');
function appendLog(msg) {
  const d = new Date(msg.ts * 1000);
  const ts = d.toTimeString().slice(0,8);
  const div = document.createElement('div');
  div.className = 'log-line';
  div.innerHTML = `<span class="log-ts">${ts}</span><span class="log-src ${msg.source}">${msg.source}</span><span class="log-text ${msg.kind}">${escHtml(msg.text)}</span>`;
  logBody.appendChild(div);
  logBody.scrollTop = logBody.scrollHeight;
}
function clearLog() { logBody.innerHTML = ''; }
function escHtml(t) { return t.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

// ── Flow step highlighting ──
function updateFlowStep(msg) {
  if (msg.source !== 'client') return;
  const t = msg.text.toLowerCase();
  const activate = id => {
    const el = document.getElementById(id);
    if (!el) return;
    el.querySelector('.flow-dot').style.background = 'var(--accent)';
    el.querySelector('.flow-dot').style.boxShadow = '0 0 6px var(--accent)';
  };
  if (t.includes('knock') && (t.includes('sending') || t.includes('sent'))) activate('step-knock');
  if (t.includes('key') && (t.includes('received') || t.includes('reply'))) activate('step-key');
  if (t.includes('segment') && t.includes('decrypted')) activate('step-seg');
  if (t.includes('reassembled') || t.includes('done —')) activate('step-done');
}
function resetFlow() {
  document.querySelectorAll('.flow-dot').forEach(d => {
    d.style.background = 'var(--text2)'; d.style.boxShadow = '';
  });
}

// ── Status polling ──
async function pollStatus() {
  try {
    const r = await fetch('/api/status');
    const s = await r.json();

    // Server
    const sOn = s.server.running;
    document.getElementById('hdr-server-dot').className = 'dot ' + (sOn ? 'on' : 'off');
    document.getElementById('hdr-server-txt').textContent = sOn ? 'Server online' : 'Server offline';
    document.getElementById('ov-server-badge').textContent = sOn ? 'Running' : 'Offline';
    document.getElementById('ov-server-badge').className = 'badge ' + (sOn ? 'badge-on' : 'badge-off');
    document.getElementById('srv-status-val').textContent = sOn ? 'RUNNING' : 'STOPPED';
    document.getElementById('srv-status-val').style.color = sOn ? 'var(--green)' : 'var(--text2)';
    document.getElementById('srv-start-btn').disabled = sOn;
    document.getElementById('srv-stop-btn').disabled = !sOn;

    // Client
    const cOn = s.client.running;
    document.getElementById('ov-client-badge').textContent = cOn ? 'Running' : 'Idle';
    document.getElementById('ov-client-badge').className = 'badge ' + (cOn ? 'badge-on' : 'badge-off');
    document.getElementById('cli-run-btn').disabled = cOn;
    document.getElementById('cli-stop-btn').disabled = !cOn;

    // Encrypt
    const eOn = s.encrypt.running;
    document.getElementById('ov-enc-badge').textContent = eOn ? 'Running' : 'Idle';
    document.getElementById('ov-enc-badge').className = 'badge ' + (eOn ? 'badge-on' : 'badge-off');
    document.getElementById('enc-btn').disabled = eOn;

    // Artifacts
    const manifests = s.artifacts.manifests || [];
    document.getElementById('ov-art-badge').textContent = `${manifests.length} video${manifests.length !== 1 ? 's' : ''}`;
    renderArtifacts(s.artifacts);

    // Configs
    updateConfigSelects(s.configs);
    renderConfigList(s.configs);

  } catch(e) {}
}

function renderArtifacts(art) {
  const list = document.getElementById('ov-artifacts-list');
  const list2 = document.getElementById('enc-artifacts-list');
  if (!art.manifests.length) {
    list.innerHTML = list2.innerHTML = '<p style="color:var(--text2);font-size:13px;">No encrypted artifacts found. Encrypt a video first.</p>';
    return;
  }
  let html = '';
  for (const m of art.manifests) {
    const stem = m.replace('.manifest.json','');
    const segs = art.segments[stem] || 0;
    html += `<div class="artifact-row">
      <div><div class="artifact-name">${stem}</div><div class="artifact-meta">${segs} segment${segs!==1?'s':''} encrypted · keys in server_secrets/</div></div>
      <span class="seg-badge">${segs} segs</span>
    </div>`;
  }
  list.innerHTML = html;
  list2.innerHTML = html;
}

function updateConfigSelects(configs) {
  const sels = ['srv-config','cli-config'];
  sels.forEach(id => {
    const sel = document.getElementById(id);
    if (!sel) return;
    const cur = sel.value;
    sel.innerHTML = '';
    const defaults = id === 'srv-config' ? ['config/generated/server.yaml'] : ['config/generated/knocker.yaml'];
    const all = configs.length ? configs : defaults;
    all.forEach(c => { const o = document.createElement('option'); o.value = c; o.textContent = c; sel.appendChild(o); });
    if (all.includes(cur)) sel.value = cur;
  });
}

function renderConfigList(configs) {
  const el = document.getElementById('cfg-list');
  if (!configs.length) { el.innerHTML = '<p style="color:var(--text2);font-size:13px;">No configs generated yet.</p>'; return; }
  el.innerHTML = configs.map(c => `<div class="artifact-row"><span class="artifact-name" style="font-family:monospace;font-size:12px;">${c}</span></div>`).join('');
}

// ── Actions ──
async function doEncrypt() {
  const path = document.getElementById('enc-path').value.trim();
  if (!path) { alert('Enter a video file path.'); return; }
  nav('encrypt');
  await fetch('/api/encrypt', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({video_path:path})});
}

async function serverStart() {
  const config = document.getElementById('srv-config').value;
  const simulation = document.getElementById('srv-sim-toggle').classList.contains('on');
  const http_only = document.getElementById('srv-http-toggle').classList.contains('on');
  nav('server');
  await fetch('/api/server/start', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({config, simulation, http_only})});
}

async function serverStop() {
  await fetch('/api/server/stop', {method:'POST'});
}

async function clientRun() {
  const config = document.getElementById('cli-config').value;
  const simulation = document.getElementById('cli-sim-toggle').classList.contains('on');
  const skip_knock = document.getElementById('cli-skip-toggle').classList.contains('on');
  const key_fragment = document.getElementById('cli-keyfrag').value.trim();
  const output = document.getElementById('cli-output').value.trim() || 'artifacts/received_video.mp4';
  resetFlow();
  nav('client');
  await fetch('/api/client/run', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({config, simulation, skip_knock, key_fragment, output})});
}

async function clientStop() {
  await fetch('/api/client/stop', {method:'POST'});
}

async function generateConfigs() {
  const server_ip = document.getElementById('cfg-server-ip').value.trim() || '127.0.0.1';
  const knocker_ip = document.getElementById('cfg-knocker-ip').value.trim() || '127.0.0.1';
  const video_port = parseInt(document.getElementById('cfg-video-port').value) || 8765;
  nav('config');
  await fetch('/api/generate-configs', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({server_ip, knocker_ip, video_port})});
}

// ── Init ──
connectWS();
pollStatus();
setInterval(pollStatus, 2000);
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
async def root():
    return DASHBOARD_HTML


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="NPS LAB EL Web Dashboard")
    parser.add_argument("--port", type=int, default=7777)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    print(f"\n  NPS LAB EL Dashboard → http://{args.host}:{args.port}\n")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
