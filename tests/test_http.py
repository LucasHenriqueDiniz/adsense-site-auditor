"""Testes da camada de fetch — cada um corresponde a um defeito reproduzido."""

import time

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
    # O corpo do GET que substituiu o HEAD recusado. A guarda testava o método
    # PEDIDO, então descartava justamente o documento que o fallback foi buscar,
    # e o retorno era 200 com corpo vazio — igual a uma página realmente vazia.
    assert r.text == "<html>conteudo</html>"


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


def test_elapsed_cobre_a_cadeia_inteira_e_nao_so_o_ultimo_salto(server):
    """`resp.elapsed` cronometra só a requisição final.

    Num site que manda apex para www — a maior parte da web — uma resposta de
    dois saltos era reportada como a duração do último. O salto lento aqui dura
    300ms contra ~1ms do final: a margem é de duas ordens de grandeza, não um
    limiar apertado.
    """
    base, routes = server

    def lento(_metodo):
        time.sleep(0.3)
        return (302, {"Location": base + "/fim"}, "")

    routes["/lento"] = lento
    routes["/fim"] = (200, {"Content-Type": "text/html"}, "<html></html>")

    r = fetch(base + "/lento")
    assert r.status_code == 200
    assert len(r.redirect_chain) == 1
    assert r.elapsed_ms >= 300


def test_url_malformada_nao_levanta_e_vira_erro_no_Fetch():
    """`urlsplit("http://[::1:99999]/")` levanta ValueError, e ValueError não é
    RequestException: ele saía de `fetch` e derrubava quatro dos cinco CLIs.
    Dois deles precisavam só de UM href malformado na página auditada."""
    r = fetch("http://[::1:99999]/x", timeout=1)
    assert r.error is not None
    assert r.ok is False
    assert r.status is Status.ERROR


def test_split_url_e_join_url_devolvem_vazio_no_lugar_de_levantar():
    from adsense_checks.http import join_url, split_url

    mau = "http://[::1:99999]/x"
    assert split_url(mau).netloc == ""
    assert split_url(mau).scheme == ""
    assert join_url("http://ok.com/", mau) == ""
    assert join_url(mau, "/a") == ""
    # E o caminho bom segue intacto.
    assert split_url("https://ok.com/a").netloc == "ok.com"
    assert join_url("https://ok.com/blog/", "a") == "https://ok.com/blog/a"


def test_user_agent_nomeia_a_ferramenta_alem_do_token_do_mediapartners():
    """A string era cópia literal da do Google. Isso era discutível enquanto o
    crawler parava onde o `*` mandava parar, e deixou de ser quando ele passou a
    atravessar um `Disallow: /` justamente por se dizer Mediapartners-Google."""
    from adsense_checks.http import ADSENSE_UA

    # O token continua lá: é o que faz o site servir a variante do AdSense.
    assert "Mediapartners-Google" in ADSENSE_UA
    # E quem lê o log do próprio site consegue ver quem de fato chamou.
    assert "adsense-site-auditor" in ADSENSE_UA
    assert "google.com/bot.html" not in ADSENSE_UA


def test_metodo_em_minusculas_ainda_cai_no_fallback(server):
    """A grafia do chamador não é veredito: `method="head"` não batia com a
    comparação literal e devolvia o 405 do servidor como resposta."""
    base, routes = server

    def rota(metodo):
        if metodo == "HEAD":
            return (405, {}, "")
        return (200, {"Content-Type": "text/html"}, "<html>corpo</html>")

    routes["/"] = rota
    r = fetch(base + "/", method="head")
    assert r.status_code == 200
    assert r.text == "<html>corpo</html>"


import pytest  # noqa: E402


@pytest.mark.parametrize("codigo", [399, 400, 401, 404, 500])
def test_a_fronteira_de_legivel_e_400_nas_duas_propriedades(server, codigo):
    """`Fetch.ok` e `Fetch.status` decidem a mesma coisa e eram fixados por
    testes diferentes: uma rodada anterior prendeu o `>= 400` do `status` e
    deixou o `< 400` do `ok` solto, então trocá-lo por `<= 400` fazia um HTTP 400
    contar como página legível. Mesmo número, propriedade vizinha."""
    base, routes = server
    routes["/x"] = (codigo, {"Content-Type": "text/html"}, "")

    r = fetch(base + "/x")

    assert r.status_code == codigo
    assert r.ok is (codigo < 400)
    # E as duas propriedades não podem discordar sobre a mesma resposta.
    assert r.ok is (r.status is Status.OK)


def test_head_recusado_com_400_tambem_cai_para_get(server):
    """A mesma fronteira, no fallback: `>= 400` virando `>` deixava um 400 em
    resposta a HEAD passar como veredito em vez de disparar o GET."""
    base, routes = server

    def rota(metodo):
        if metodo == "HEAD":
            return (400, {}, "")
        return (200, {"Content-Type": "text/html"}, "<html>corpo</html>")

    routes["/"] = rota
    r = fetch(base + "/", method="HEAD")
    assert r.status_code == 200
    assert r.text == "<html>corpo</html>"
