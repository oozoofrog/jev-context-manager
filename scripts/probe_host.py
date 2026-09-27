"""Capture a bounded synthetic Codex hook probe, never used for production."""
import json
import os
import sys
from pathlib import Path

os.umask(0o077)
payload = json.load(sys.stdin)
target = Path(__file__).resolve().parents[1] / 'evidence' / 'host-probe.jsonl'
with target.open('a') as output:
    output.write(json.dumps(payload, ensure_ascii=False) + '\n')
    output.flush()
    os.fsync(output.fileno())
print('{}')
