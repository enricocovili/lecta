"""Container healthchecks: `backend` pings /api/health, `worker` checks its heartbeat file."""

import sys
import time
import urllib.request

role = sys.argv[1] if len(sys.argv) > 1 else "backend"
try:
    if role == "worker":
        with open("/tmp/worker-alive") as f:
            sys.exit(0 if time.time() - float(f.read()) < 60 else 1)
    with urllib.request.urlopen("http://127.0.0.1:8000/api/health", timeout=4) as r:
        sys.exit(0 if r.status == 200 else 1)
except Exception:
    sys.exit(1)
