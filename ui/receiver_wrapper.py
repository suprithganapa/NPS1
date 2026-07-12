#!/usr/bin/env python3
"""
Wrapper for video_receiver.py.
Patches scapy to skip BPF filter compilation when libpcap is unavailable.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'src'))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))

# ---- scapy patch: remove BPF filter, let code do SW filtering ----
try:
    import scapy.sendrecv as _sr
    _orig_sniff = _sr.sniff
    def _patched_sniff(*args, **kwargs):
        kwargs.pop('filter', None)
        return _orig_sniff(*args, **kwargs)
    _sr.sniff = _patched_sniff
except Exception as e:
    print(f"[wrapper] scapy patch warning: {e}", file=sys.stderr)

# ---- run the real receiver ------------------------------------------------
sys.argv[0] = os.path.join(ROOT, 'scripts', 'video_receiver.py')
import runpy
runpy.run_path(os.path.join(ROOT, 'scripts', 'video_receiver.py'), run_name='__main__')
