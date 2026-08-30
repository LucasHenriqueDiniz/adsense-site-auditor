"""Testes da camada de fetch — cada um corresponde a um defeito reproduzido."""

from adsense_checks.http import fetch
from adsense_checks.status import Status


def test_segue_redirect_e_reporta_a_url_final(server):
    base, routes = server
    routes["/"] = (301, {"Location": "/destino"}, "")
    routes["/destino"] = (200, {"Content-Type": "text/html"}, "<html>ok</html>")
    r = fetch(base + "/")
    assert r.status_code == 200
    assert r.final_url.endswith("/destino")
    assert r.redirect_chain and r.redirect_chain[0][0] == 301
    assert "ok" in r.text


def test_headers_vem_da_resposta_final_e_nao_do_301(server):
    """O defeito: session.head() sem allow_redirects lia os headers do 301."""
    base, routes = server
    routes["/"] = (301, {"Location": "/final"}, "")
    routes["/final"] = (
        200,
        {
            "Content-Security-Policy": "default-src 'self'",
            "X-Frame-Options": "DENY",
            "X-Content-Type-Options": "nosniff",
        },
        "<html></html>",
    )
    r = fetch(base + "/")
    assert r.headers.get("content-security-policy") == "default-src 'self'"
    assert r.headers.get("x-frame-options") == "DENY"


def test_head_recusado_cai_para_get(server):
    """Servidor atrás de WAF respondendo 405 a HEAD não é um site quebrado."""
    base, routes = server

    def rota(metodo):
        if metodo == "HEAD":
            return (405, {}, "")
        return (200, {"Content-Type": "text/html"}, "<html>conteudo</html>")

    routes["/"] = rota
    r = fetch(base + "/", method="HEAD")
    assert r.ok is True
    assert r.status_code == 200
    assert r.status is Status.OK


def test_https_vem_do_esquema_servido_e_nao_da_string_de_entrada(server):
    base, routes = server
    routes["/"] = (200, {}, "<html></html>")
    r = fetch(base + "/")
    # Servidor local é http; o importante é que a decisão venha de final_url.
    assert r.is_https is False
    assert r.final_scheme == "http"


def test_erro_de_rede_vira_ERROR_e_nunca_ok():
    """analyze_text_depth contava fetch falho como aprovação."""
    r = fetch("http://127.0.0.1:1/", timeout=2)
    assert r.error is not None
    assert r.ok is False
    assert r.status is Status.ERROR


def test_5xx_e_FAIL_e_404_e_MISSING(server):
    base, routes = server
    routes["/erro"] = (503, {}, "")
    routes["/ausente"] = (404, {}, "")
    assert fetch(base + "/erro").status is Status.FAIL
    assert fetch(base + "/ausente").status is Status.MISSING


def test_head_nao_devolve_corpo_de_outra_resposta(server):
    base, routes = server
    routes["/"] = (200, {}, "<html>corpo</html>")
    r = fetch(base + "/", method="HEAD")
    assert r.text == ""
