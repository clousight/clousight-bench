"""FC custom runtime entry point for the clousight-bench mock tool server.

Wraps mock_tools.py to:
  - Listen on 0.0.0.0 (FC routes from trigger to this server)
  - Read port from $FC_SERVER_PORT (set by the FC runtime)
  - Read auth token from $CSBENCH_MOCK_TOKEN
  - Resolve data/ relative to this file (works inside /var/task/)

FC custom runtime flow:
  bootstrap -> python3 entry.py -> ThreadingHTTPServer(0.0.0.0:$FC_SERVER_PORT)
  FC HTTP trigger -> forwards every request path+body -> this server -> response
"""
import os
import sys
from http.server import ThreadingHTTPServer
from pathlib import Path

# Ensure mock_tools.py + data/ resolve relative to /var/task/
# FC custom runtime code lives at /code/
sys.path.insert(0, str(Path(__file__).parent))  # works both locally and in /code/

from mock_tools import ToolState, make_handler  # noqa: E402

port = int(os.environ.get("FC_SERVER_PORT", 9000))
token = os.environ.get("CSBENCH_MOCK_TOKEN") or None

state = ToolState()
server = ThreadingHTTPServer(("0.0.0.0", port), make_handler(state, token))
print(f"mock-tools listening on 0.0.0.0:{port}"
      + (" (token-locked)" if token else " (open)"), flush=True)
server.serve_forever()
