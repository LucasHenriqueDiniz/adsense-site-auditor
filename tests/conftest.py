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

# Ver o comentário em _sobe_servidor.
POLL_INTERVAL = 0.01


class _Handler(BaseHTTPRequestHandler):
    routes: dict = {}
    # Cada requisição atendida entra aqui como (método, caminho, headers). É o
    # que permite um teste assertar o que chegou NA WIRE em vez de comparar duas
    # constantes de módulo entre si — a diferença entre testar o User-Agent que o
    # crawler envia e testar que a constante é igual a si mesma.
    received: list = []

    def log_message(self, *args):  # silencia o log no stderr durante os testes
        pass

    def _respond(self, body_allowed: bool):
        self.received.append((self.command, self.path, dict(self.headers)))
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


def _sobe_servidor():
    """Sobe um servidor numa porta livre. Devolve (httpd, base_url, rotas)."""

    # dict comum não aceita atributo, e os testes existentes desempacotam dois
    # valores — então o registro viaja pendurado no próprio mapa de rotas.
    class _Routes(dict):
        received: list

    routes = _Routes()
    routes.received = []
    handler = type("H", (_Handler,), {"routes": routes, "received": routes.received})
    httpd = HTTPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    # serve_forever espera POLL_INTERVAL antes de notar o shutdown, e o padrão da
    # stdlib é 0.5s. Com um servidor por teste isso era ~0.45s de teardown cada,
    # medido: quase 45s dos ~57s que a suíte levava, em testes que não fazem I/O
    # de rede de verdade.
    t = threading.Thread(target=lambda: httpd.serve_forever(POLL_INTERVAL), daemon=True)
    t.start()
    return httpd, f"http://127.0.0.1:{port}", routes


@pytest.fixture
def server():
    """Sobe um servidor e devolve (base_url, rotas). Mutar `rotas` afeta o servidor."""
    # `routes` e `routes.received` são os mesmos objetos que o handler usa:
    # mutar o primeiro configura o servidor, ler o segundo mostra o que chegou.
    httpd, base, routes = _sobe_servidor()
    yield base, routes
    httpd.shutdown()
    httpd.server_close()


@pytest.fixture
def outro_servidor():
    """Uma segunda ORIGEM, com a mesma interface de `server`.

    Mesma máquina, outra porta — e a porta faz parte da identidade de um site
    (ver `crawl.site_host`), então para todo efeito deste repo isto é outro site.

    Existe para os casos em que a origem muda no meio do caminho: apex -> www é
    o caso real e o mais comum na web, e não dá para reproduzi-lo com um
    servidor só. Com uma origem apenas, uma checagem que resolve URLs contra a
    string digitada e outra que resolve contra a resposta final chegam ao mesmo
    lugar, e o teste passa nas duas.
    """
    httpd, base, routes = _sobe_servidor()
    yield base, routes
    httpd.shutdown()
    httpd.server_close()
