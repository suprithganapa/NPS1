#!/usr/bin/env python3
"""
Wrapper for run_lab_server.py.
Patches scapy to skip BPF filter compilation when libpcap is unavailable.
All ICMP filtering is done in software by parse_packet() instead.
"""
import os
import sys

# Force UTF-8 stdout/stderr on Windows so Rich unicode chars don't crash
if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
if sys.stderr and hasattr(sys.stderr, 'reconfigure'):
    try:
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'src'))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))

# ---- scapy patch: remove BPF filter, let parse_packet do SW filtering ----
try:
    import scapy.sendrecv as _sr
    _orig_sniff = _sr.sniff
    def _patched_sniff(*args, **kwargs):
        kwargs.pop('filter', None)
        return _orig_sniff(*args, **kwargs)
    _sr.sniff = _patched_sniff
except Exception as e:
    print(f"[wrapper] scapy patch warning: {e}", file=sys.stderr)

# ---- run the real server ---------------------------------------------------
sys.argv[0] = os.path.join(ROOT, 'scripts', 'run_lab_server.py')
import runpy
runpy.run_path(os.path.join(ROOT, 'scripts', 'run_lab_server.py'), run_name='__main__')
