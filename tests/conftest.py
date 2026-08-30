"""Servidor HTTP local para os testes.

Os defeitos de redirect e de HEAD só aparecem contra um servidor real: um mock
de requests reproduziria a suposição errada em vez de o comportamento do
protocolo. O servidor abaixo é minúsculo e responde conforme um mapa de rotas
que cada teste monta.
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest


class _Handler(BaseHTTPRequestHandler):
    routes: dict = {}

    def log_message(self, *args):  # silencia o log no stderr durante os testes
        pass

    def _respond(self, body_allowed: bool):
        route = self.routes.get(self.path)
        if route is None:
            self.send_response(404)
            self.end_headers()
            if body_allowed:
                self.wfile.write(b"not found")
            return
        status, headers, body = route(self.command) if callable(route) else route
        self.send_response(status)
        for k, v in headers.items():
            self.send_header(k, v)
        self.end_headers()
        if body_allowed and body:
            self.wfile.write(body.encode())

    def do_GET(self):
        self._respond(True)

    def do_HEAD(self):
        self._respond(False)


@pytest.fixture
def server():
    """Sobe um servidor e devolve (base_url, rotas). Mutar `rotas` afeta o servidor."""
    routes: dict = {}
    handler = type("H", (_Handler,), {"routes": routes})
    httpd = HTTPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{port}", routes
    httpd.shutdown()
    httpd.server_close()
