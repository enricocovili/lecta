import json
import os
import socket
import sys

path = os.environ.get("LECTA_LATEX_SOCKET", "/run/lecta/latex.sock")
try:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(4)
    s.connect(path)
    s.sendall(b"GET /health HTTP/1.1\r\nHost: latex\r\nContent-Length: 0\r\n\r\n")
    data = b""
    while chunk := s.recv(65536):
        data += chunk
    body = json.loads(data.split(b"\r\n\r\n", 1)[1])
    sys.exit(0 if body.get("ok") else 1)
except Exception:
    sys.exit(1)
