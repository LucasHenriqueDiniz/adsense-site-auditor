"""Testes da camada de fetch — cada um corresponde a um defeito reproduzido."""

import math
import time

from adsense_checks.http import MAX_WAIT_SECONDS, fetch
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


def test_o_timeout_padrao_publicado_e_15_segundos():
    """DEFAULT_TIMEOUT pelo valor. Aqui não dá para fixar por comportamento sem
    a suíte esperar quinze segundos, então o que se prende é o default que a
    assinatura publica e que `scripts/README.md` documenta — mudá-lo em silêncio
    é o defeito, e isto o pega."""
    import inspect

    from adsense_checks.http import DEFAULT_TIMEOUT, fetch

    assert DEFAULT_TIMEOUT == 15
    assert inspect.signature(fetch).parameters["timeout"].default == 15


# --------------------------------------------------------------------------
# URLs que o cliente não consegue parsear, e que não são a URL pedida
# --------------------------------------------------------------------------

# Um label de DNS para em 63 caracteres. O 64º faz `str.encode("idna")` levantar
# UnicodeError, e urllib3 — que o chama dentro de `create_connection`, na hora de
# conectar — transforma isso em LocationParseError. Um label vazio no meio do
# host falha no mesmo ponto, com a mesma mensagem.
HOST_COM_LABEL_LONGO_DEMAIS = "a" * 64 + ".invalid"
HOST_COM_LABEL_VAZIO = "a..invalid"

# Hosts entre colchetes que não são literais IPv6 válidos. Quem levanta aqui é a
# stdlib, não urllib3: `urlsplit`/`urlparse` checam o conteúdo dos colchetes e
# levantam ValueError PELADO — nem LocationValueError, nem RequestException. Como
# é stdlib, nenhum pin de dependência cerca o caso.
HOSTS_ENTRE_COLCHETES_INVALIDOS = [
    "http://[foo]/",
    "//[foo]/",
    "http://user@[foo]/",
    "http://[%3A%3A1]/",
    "http://[::1:99999]/",
    "http://[:::1]/",
    "http://[g::1]/",
    "http://[]/",
    "http://[1.2.3.4]/",
    "http://[::1/",
]


@pytest.mark.parametrize("host", [HOST_COM_LABEL_LONGO_DEMAIS, HOST_COM_LABEL_VAZIO])
def test_host_que_o_cliente_nao_consegue_parsear_vira_erro_no_Fetch(host):
    """O mesmo defeito de `test_url_malformada_nao_levanta`, uma camada abaixo.

    Este host passa pela guarda de `urlsplit` no topo de `fetch` e passa pelo
    prepare do próprio requests; quem o recusa é urllib3, na hora de conectar, e
    o LocationParseError que ele levanta NÃO é RequestException — saía de
    `fetch` e levava junto todo o `check_sitemap`. O robots.txt de um estranho
    dizendo `Sitemap: http://<64 a's>.invalid/sitemap.xml` é o exploit inteiro.
    """
    r = fetch(f"http://{host}/sitemap.xml", timeout=2)
    assert r.error is not None
    # Reportado como URL ruim e não como falha de rede: o host não está
    # inalcançável, é um que este cliente não consegue pronunciar. Mesmo prefixo
    # que a guarda de netloc no topo de `fetch` já usa.
    assert r.error.startswith("InvalidURL:")
    assert r.ok is False
    assert r.status is Status.ERROR


@pytest.mark.parametrize("destino", HOSTS_ENTRE_COLCHETES_INVALIDOS)
def test_redirect_para_host_entre_colchetes_nao_levanta(server, destino):
    """A terceira porta para o mesmo defeito, e a que nenhuma classe de exceção
    de biblioteca cerca.

    `resolve_redirects` do requests chama `urlparse` no alvo do redirect sem
    guarda nenhuma, e a checagem de host entre colchetes da CPython levanta
    ValueError PELADO. Não é RequestException e não é LocationValueError, então
    saía de `fetch` com traceback cru e sem relatório — e não precisa nem do
    robots.txt de um estranho: basta a home do site auditado redirecionar para
    cá. São 18 chamadores de `fetch` no pacote, todos herdando isso.
    """
    base, routes = server
    routes["/"] = (302, {"Location": destino}, "")

    r = fetch(base + "/", timeout=2)

    assert r.error is not None
    assert r.error.startswith("InvalidURL:")
    assert r.ok is False
    assert r.status is Status.ERROR


def test_a_mensagem_do_salto_impossivel_cita_a_requisicao_e_o_culpado(server):
    """A frase precisa ser verdadeira, e nada a prendia.

    Trocar a mensagem inteira por uma constante — sem o `{url!r}` e sem o
    `({exc})` — passava por todos os testes desta guarda: os de sitemap batem no
    host porque `sitemap.py` monta a própria razão com a URL candidata dentro,
    e os daqui só olhavam o prefixo `InvalidURL:`.

    Pior, a frase herdada da guarda de netloc dizia que a URL PEDIDA não tem
    host parseável. Num salto de redirect ela tem: quem não parseia é o hop 2.
    O operador lia "a URL do sitemap que declarei está malformada" sobre uma URL
    que estava perfeita.
    """
    base, routes = server
    destino = f"http://{HOST_COM_LABEL_LONGO_DEMAIS}/x"
    routes["/mapa.xml"] = (302, {"Location": destino}, "")
    pedida = base + "/mapa.xml"

    r = fetch(pedida, timeout=2)

    assert r.error.startswith("InvalidURL:")
    # A URL pedida aparece — mas como o que se pediu, não como a culpada.
    assert repr(pedida) in r.error
    assert "has no host this client can parse" not in r.error
    # E o culpado de verdade aparece, pelo `({exc})`. Ele não está em `pedida`,
    # então esta asserção morre se o `({exc})` sumir da mensagem.
    assert HOST_COM_LABEL_LONGO_DEMAIS not in pedida
    assert HOST_COM_LABEL_LONGO_DEMAIS in r.error


def test_a_guarda_de_netloc_continua_acusando_a_url_pedida():
    """A contraparte do teste acima: aqui `url` É a culpada, e dizê-lo é certo.

    As duas mensagens começam com `InvalidURL:` e precisam continuar diferentes
    no resto — é a diferença entre mandar o operador conferir a URL que ele
    escreveu e mandá-lo conferir uma que está certa.
    """
    url = "http://[::1:99999]/x"

    r = fetch(url, timeout=1)

    assert r.error == f"InvalidURL: {url!r} has no host this client can parse"


def test_url_que_o_proprio_requests_recusa_mantem_a_mensagem_do_requests():
    """A ordem das duas cláusulas de `fetch`, que deixou de ser livre.

    `InvalidURL`, `MissingSchema`, `InvalidSchema` e `InvalidHeader` do requests
    são ao mesmo tempo RequestException e ValueError. Enquanto a cláusula larga
    pegava só LocationValueError as duas eram disjuntas e trocá-las de lugar não
    mudava nada; agora ela pega ValueError, e vindo primeiro engoliria todas —
    devolvendo "reached a URL this client cannot parse" no lugar da descrição
    que o requests já tem, e dizendo isso até sobre um header malformado.
    """
    # A porta não é um número: o requests recusa em `prepare_url`, antes de
    # qualquer socket, e a exceção é das que herdam dos dois lados.
    r = fetch("http://exemplo.com:porta/", timeout=1)

    assert r.error.startswith("InvalidURL: ")
    assert "reached a URL this client cannot parse" not in r.error


def test_timeout_zero_continua_levantando_em_vez_de_virar_erro_de_rede():
    """O contrapeso dos testes acima, e o que mantém o `except ValueError` honesto.

    LocationParseError É um ValueError, e o ValueError pelado do host entre
    colchetes também — mas `timeout=0` levanta outro, do validador de timeout do
    urllib3. Enquanto o catch estava dentro do try junto com o request, pegar
    ValueError devolvia o bug do chamador como "o site não pôde ser alcançado",
    sobre um site que esteve no ar o tempo todo. O que impede isso hoje é o
    timeout ser validado ANTES do try; se aquela validação sumir, isto pega.
    """
    with pytest.raises(ValueError):
        fetch("http://127.0.0.1:1/", timeout=0)


@pytest.mark.parametrize(
    "timeout",
    [
        0,
        -1,
        "dois",
        True,
        (0, 5),
        (5, 0),
        (1, 2, 3),
        float("inf"),
        MAX_WAIT_SECONDS,
        (MAX_WAIT_SECONDS, 5),
        (5, MAX_WAIT_SECONDS),
    ],
)
def test_timeout_inutilizavel_levanta_antes_de_qualquer_requisicao(server, timeout):
    """O mesmo contrato do teste acima, agora com o `except ValueError` largo.

    `fetch` promete não levantar sobre a rede e sobre URLs — o que um estranho
    controla. O `timeout` é argumento do chamador, e continua sendo bug dele.
    Como agora a cláusula de baixo pega ValueError inteiro, o que separa os dois
    é a ORDEM: o timeout é validado antes do try. Por isso o servidor daqui não
    pode ter recebido nada.
    """
    base, routes = server
    routes["/"] = (200, {}, "<html></html>")

    with pytest.raises(ValueError):
        fetch(base + "/", timeout=timeout)

    assert routes.received == []


def test_o_maior_timeout_que_a_maquina_aguenta_continua_sendo_aceito(server):
    """O contrapeso de `MAX_WAIT_SECONDS` na lista acima, fixado pelo valor.

    Sem este teste o teto pode encolher à vontade — recusar `inf` continuaria
    passando com um teto de um segundo. `socket.settimeout` converte segundos
    para nanossegundos num inteiro de 64 bits com sinal, então o último valor
    representável é o vizinho de baixo de 2**63 ns em float. Medido por bisseção,
    não lido de manual: 9223372036.854774 passa, 9223372036.854776 não.
    """
    base, routes = server
    routes["/"] = (200, {"Content-Type": "text/html"}, "<html>ok</html>")

    r = fetch(base + "/", timeout=math.nextafter(MAX_WAIT_SECONDS, 0))

    assert r.ok
    assert routes.received != []
