"""Testes da camada de fetch — cada um corresponde a um defeito reproduzido."""

import math
import time

from adsense_checks.http import MAX_WAIT_SECONDS, _declared_charset, _web_encoding, fetch
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
        float("nan"),
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


# ---------------------------------------------------------------------------
# Decodificação: uma vez, no fetch, para todo mundo que lê `Fetch.text`.
#
# O requests lê um corpo text/* sem charset como ISO-8859-1. Só o text.py
# consertava isso, e os outros leitores recebiam mojibake: o placeholder
# "em construção" do completeness parava de casar e uma regra `Disallow:
# /promoção/` do robots.txt deixava de bloquear o caminho que ela fecha.
# ---------------------------------------------------------------------------


def test_html_sem_charset_em_utf8_chega_decodificado(server):
    base, routes = server
    routes["/pt"] = (200, {"Content-Type": "text/html"}, "<p>Esta seção está em construção.</p>")
    r = fetch(base + "/pt")
    assert "construção" in r.text
    assert "Ã" not in r.text
    assert r.charset_error is None


def test_texto_puro_sem_charset_em_utf8_chega_decodificado(server):
    """O robots.txt do nginx: `text/plain` e nenhum charset."""
    base, routes = server
    routes["/robots.txt"] = (
        200, {"Content-Type": "text/plain"}, "User-agent: *\nDisallow: /promoção/\n"
    )
    r = fetch(base + "/robots.txt")
    assert "Disallow: /promoção/" in r.text


def test_meta_charset_vale_quando_o_header_nao_diz(server):
    """windows-1252 declarado na própria página. As aspas curvas 0x93 e 0x94 não
    são UTF-8 nem letras em ISO-8859-1: só a declaração as lê direito."""
    base, routes = server
    corpo = (
        '<html><head><meta charset="windows-1252"></head><body><p>'
        "\u201ca\u00e7\u00e3o\u201d</p></body></html>"
    ).encode("windows-1252")
    routes["/legado"] = (200, {"Content-Type": "text/html"}, corpo)
    r = fetch(base + "/legado")
    assert "\u201ca\u00e7\u00e3o\u201d" in r.text


def test_charset_do_header_ganha_da_meta(server):
    """O transporte disse o que mandou. Uma meta errada não reabre a questão."""
    base, routes = server
    html = '<html><head><meta charset="iso-8859-1"></head><body><p>ação</p></body></html>'
    routes["/pt"] = (200, {"Content-Type": "text/html; charset=utf-8"}, html)
    r = fetch(base + "/pt")
    assert "ação" in r.text


def test_pagina_legada_sem_declaracao_continua_em_iso_8859_1(server):
    """Bytes que não são UTF-8 e nenhuma declaração: a leitura do requests fica,
    e as letras de um latin-1 continuam letras."""
    base, routes = server
    routes["/legado"] = (
        200, {"Content-Type": "text/html"}, "<p>informação</p>".encode("iso-8859-1")
    )
    r = fetch(base + "/legado")
    assert "informação" in r.text
    assert r.charset_error is None


def test_meta_fora_de_html_nao_declara_nada(server):
    """Uma `<meta charset>` só declara alguma coisa num documento HTML. Num
    robots.txt é texto, e obedecê-la decodificaria o arquivo como grego."""
    base, routes = server
    texto = "# <meta charset='iso-8859-7'>\nUser-agent: *\nDisallow: /ação/\n"
    routes["/robots.txt"] = (200, {"Content-Type": "text/plain"}, texto)
    r = fetch(base + "/robots.txt")
    assert "Disallow: /ação/" in r.text


def test_charset_declarado_que_nao_decodifica_fica_registrado_no_fetch(server):
    """O texto fica o palpite do requests, e o motivo viaja junto para quem
    for transformar o texto num veredito."""
    base, routes = server
    # ISO-2022-JP só tem bytes abaixo de 0x80, e o corpo é UTF-8 acentuado.
    html = '<html><head><meta charset="iso-2022-jp"></head><body><p>ação</p></body></html>'
    routes["/ruim"] = (200, {"Content-Type": "text/html"}, html)
    r = fetch(base + "/ruim")
    assert r.charset_error is not None
    assert "iso-2022-jp" in r.charset_error


def test_o_charset_declarado_e_procurado_so_no_primeiro_kib():
    """_DECLARATION_WINDOW. O HTML5 exige a declaração nos primeiros 1024 bytes,
    e procurar além disso passa a casar prosa: um artigo SOBRE codificações diz
    "charset=utf-8" no corpo sem declarar nada.

    Os offsets anteriores eram 900 e 1100, uma folga de 100 de cada lado: medido,
    `_DECLARATION_WINDOW 1024 -> 1099` sobrevivia. Aqui a declaração é posicionada
    no byte, e o par 1023/1024 prende a janela em qualquer direção.
    """
    declaracao = "<meta charset='iso-8859-7'>"
    # O casamento de `_META_CHARSET` termina no último caractere do VALOR, então
    # ele consome os 25 primeiros caracteres da declaração e o `-7` final tem de
    # caber dentro da janela.
    assert len("<meta charset='iso-8859-7") == 25

    def documento(inicio):
        """Um documento em que a `<meta>` começa exatamente no índice `inicio`."""
        enchimento = "<!-- " + "x" * (inicio - 9) + " -->"
        assert len(enchimento) == inicio
        return enchimento + declaracao

    # A declaração termina no índice 1023, o último byte da janela: encontrada.
    # Uma janela menor derruba esta — com 1023 o valor volta cortado.
    assert _declared_charset(documento(999)) == "iso-8859-7"

    # Deslocada 10 bytes: `charset='` acaba em 1023 e o primeiro caractere do
    # valor cai no índice 1024, fora da janela. Sem valor não há casamento.
    # Uma janela maior derruba esta — com 1025 o valor volta como "i".
    assert _declared_charset(documento(1009)) == ""

    # E o motivo de a janela existir: um artigo SOBRE codificações mostra a tag
    # como exemplo no meio do texto, sem declarar nada. Sem a janela, o exemplo
    # de outra pessoa viraria a declaração desta página.
    artigo = ("<html><head><title>Codificacoes</title></head><body>"
              + "<p>x</p>" * 200
              + "<pre>&lt;meta charset=\"shift_jis\"&gt;</pre>"
              "<p><meta charset='shift_jis'> era o exemplo acima.</p>")
    assert artigo.index("<meta charset=") > 1024
    assert _declared_charset(artigo) == ""


# ---------------------------------------------------------------------------
# A ordem do HTML5: BOM, header, meta, UTF-8. E só rótulos de codificação da web.
# ---------------------------------------------------------------------------


def test_meta_charset_undefined_e_ignorada_e_nao_vira_url_invalida(server):
    """Template que renderiza variável vazia escreve `<meta charset="undefined">`.
    O Python tem um codec com esse nome, que levanta UnicodeError puro a cada
    byte; ele saía de `_decode` e caía na cláusula de URL do `fetch`: a página
    vinha como "InvalidURL", inalcançável, para o crawl e o completeness."""
    base, routes = server
    html = '<html><head><meta charset="undefined"></head><body><p>ação</p></body></html>'
    routes["/t"] = (200, {"Content-Type": "text/html"}, html)
    r = fetch(base + "/t")
    assert r.error is None
    assert r.charset_error is None
    assert "ação" in r.text


def test_rotulo_desconhecido_na_meta_e_ignorado(server):
    """Um navegador ignora o rótulo que não conhece e segue para o próximo passo.
    Aqui ele virava `charset_error`, e a contagem recusava a página."""
    base, routes = server
    html = '<html><head><meta charset="x-desconhecido"></head><body><p>ação</p></body></html>'
    routes["/t"] = (200, {"Content-Type": "text/html"}, html)
    r = fetch(base + "/t")
    assert r.charset_error is None
    assert "ação" in r.text


def test_rotulo_desconhecido_no_header_cede_a_vez_para_a_meta(server):
    """`charset=utf8mb4` é o nome do MySQL, não um rótulo da web. O requests
    decodificava com UTF-8 e caracteres de substituição; um navegador ignora o
    rótulo e lê a meta, que aqui diz windows-1252."""
    base, routes = server
    corpo = (
        '<html><head><meta charset="windows-1252"></head><body><p>'
        "\u201ca\u00e7\u00e3o\u201d</p></body></html>"
    ).encode("windows-1252")
    routes["/t"] = (200, {"Content-Type": "text/html; charset=utf8mb4"}, corpo)
    r = fetch(base + "/t")
    assert "\u201ca\u00e7\u00e3o\u201d" in r.text


def test_bom_ganha_da_meta(server):
    """Página regravada em UTF-8 com BOM e a meta antiga ainda dizendo
    windows-1252. Para o navegador é UTF-8; lida pela meta, voltava o mesmo
    mojibake que esta camada existe para tirar."""
    base, routes = server
    corpo = b"\xef\xbb\xbf" + (
        '<html><head><meta charset="windows-1252"></head><body><p>em construção</p>'
        "</body></html>"
    ).encode()
    routes["/t"] = (200, {"Content-Type": "text/html"}, corpo)
    r = fetch(base + "/t")
    assert "em construção" in r.text
    assert not r.text.startswith(("\ufeff", "ï»¿"))


def test_bom_ganha_do_header(server):
    """No HTML5 o BOM vem antes até do Content-Type."""
    base, routes = server
    corpo = b"\xef\xbb\xbf" + "<p>em construção</p>".encode()
    routes["/t"] = (200, {"Content-Type": "text/html; charset=iso-8859-1"}, corpo)
    r = fetch(base + "/t")
    assert "em construção" in r.text


@pytest.mark.parametrize("bom, codec", [(b"\xfe\xff", "utf-16-be"), (b"\xff\xfe", "utf-16-le")])
def test_bom_utf16_decide_a_codificacao(server, bom, codec):
    base, routes = server
    routes["/t"] = (200, {"Content-Type": "text/plain"}, bom + "Disallow: /ação/".encode(codec))
    r = fetch(base + "/t")
    assert r.text == "Disallow: /ação/"


def test_meta_utf16_e_lida_como_utf8(server):
    """O HTML5 manda ler uma meta UTF-16 como UTF-8: um documento que chegou a
    ser varrido atrás de `<meta>` é compatível com ASCII, então o rótulo está
    errado. Aqui o rótulo era seguido, a decodificação falhava e a página saía
    como ERROR numa ferramenta e como texto adivinhado nas outras."""
    base, routes = server
    html = '<html><head><meta charset="utf-16"></head><body><p>ação</p></body></html>'
    assert len(html.encode()) % 2 == 1
    routes["/t"] = (200, {"Content-Type": "text/html"}, html)
    r = fetch(base + "/t")
    assert r.charset_error is None
    assert "ação" in r.text


# Cada codificação do padrão WHATWG, por um rótulo seu, e o codec com que é lida.
CODIFICACOES_DA_WEB = {
    "utf-8": "utf-8", "utf8": "utf-8", "ibm866": "cp866",
    "iso-8859-1": "cp1252", "latin1": "cp1252", "ascii": "cp1252", "us-ascii": "cp1252",
    "iso-8859-2": "iso8859-2", "iso-8859-3": "iso8859-3", "iso-8859-4": "iso8859-4",
    "iso-8859-5": "iso8859-5", "iso-8859-6": "iso8859-6", "iso-8859-7": "iso8859-7",
    "iso-8859-8": "iso8859-8", "iso-8859-8-i": "iso8859-8", "iso-8859-9": "cp1254",
    "iso-8859-10": "iso8859-10", "iso-8859-11": "cp874", "tis-620": "cp874",
    "iso-8859-13": "iso8859-13", "iso-8859-14": "iso8859-14", "iso-8859-15": "iso8859-15",
    "iso-8859-16": "iso8859-16", "koi8-r": "koi8-r", "koi8-u": "koi8-u",
    "macintosh": "mac-roman", "x-mac-cyrillic": "mac-cyrillic", "windows-874": "cp874",
    "windows-1250": "cp1250", "windows-1251": "cp1251", "windows-1252": "cp1252",
    "windows-1253": "cp1253", "windows-1254": "cp1254", "windows-1255": "cp1255",
    "windows-1256": "cp1256", "windows-1257": "cp1257", "windows-1258": "cp1258",
    "gbk": "gbk", "gb2312": "gbk", "gb18030": "gb18030", "big5": "big5hkscs",
    "big5-hkscs": "big5hkscs", "euc-jp": "euc_jp", "iso-2022-jp": "iso2022_jp",
    "shift_jis": "cp932", "sjis": "cp932", "windows-31j": "cp932", "euc-kr": "cp949",
    "windows-949": "cp949",
    "utf-16": "utf-16-le", "utf-16le": "utf-16-le", "utf-16be": "utf-16-be",
    " UTF-8 ": "utf-8",
}


def test_cada_codificacao_da_web_e_lida_com_o_codec_do_padrao():
    for rotulo, codec in CODIFICACOES_DA_WEB.items():
        assert _web_encoding(rotulo) == codec, rotulo


def test_codec_do_python_que_nao_e_codificacao_da_web_nao_e_rotulo():
    """O Python decodifica muita coisa que nenhuma página pode estar: `undefined`
    levanta a cada byte, `punycode` e `idna` são de nomes de domínio, `hex` nem
    é texto. Um rótulo desses é ignorado, como o navegador ignora."""
    for rotulo in ("undefined", "punycode", "idna", "unicode_escape", "raw_unicode_escape",
                   "utf-7", "utf-32", "hex", "rot13", "x-user-defined", "x-desconhecido", ""):
        assert _web_encoding(rotulo) == "", rotulo


def test_charset_do_header_que_nao_decodifica_fica_registrado(server):
    """Latin-1 sob `charset=utf-8`. O texto é o que o navegador mostra, com
    caractere de substituição; o motivo viaja junto, como já viajava quando a
    declaração errada estava na `<meta>`."""
    base, routes = server
    routes["/t"] = (
        200, {"Content-Type": "text/html; charset=utf-8"}, "<p>informação</p>".encode("latin-1")
    )
    r = fetch(base + "/t")
    assert r.charset_error is not None
    assert "header charset 'utf-8'" in r.charset_error
    assert "informa\ufffd\ufffdo" in r.text


def test_meta_que_nao_decodifica_mostra_o_que_o_navegador_mostra(server):
    """O navegador obedece à declaração e substitui o que ela não lê. O texto
    era o palpite ISO-8859-1 do requests, que não é o que ninguém vê."""
    base, routes = server
    html = '<html><head><meta charset="iso-2022-jp"></head><body><p>ação</p></body></html>'
    routes["/t"] = (200, {"Content-Type": "text/html"}, html)
    r = fetch(base + "/t")
    assert r.charset_error is not None
    assert "\ufffd" in r.text
    assert "Ã" not in r.text


def test_sem_declaracao_e_sem_utf8_le_como_windows_1252(server):
    """O último passo é o padrão do WHATWG, windows-1252. As aspas curvas 0x93 e
    0x94 eram caracteres de controle no ISO-8859-1 do requests."""
    base, routes = server
    routes["/t"] = (
        200, {"Content-Type": "text/html"}, "<p>\u201ca\u00e7\u00e3o\u201d</p>".encode("cp1252")
    )
    r = fetch(base + "/t")
    assert "\u201ca\u00e7\u00e3o\u201d" in r.text


def test_rotulo_desconhecido_no_header_sem_meta_cai_no_windows_1252(server):
    """`charset=utf8mb4` sobre Latin-1, sem `<meta>`. O último passo era
    `resp.text`, e o requests tentava o próprio rótulo desconhecido, caía em UTF-8
    com substituição e devolvia "informa\ufffd\ufffdo"."""
    base, routes = server
    routes["/t"] = (
        200, {"Content-Type": "text/html; charset=utf8mb4"}, "<p>informação</p>".encode("latin-1")
    )
    r = fetch(base + "/t")
    assert "informação" in r.text
    assert r.charset_error is None


# Os cinco bytes que o cp1252 do Python deixa indefinidos e o WHATWG não.
BURACOS_DO_CP1252 = bytes([0x81, 0x8D, 0x8F, 0x90, 0x9D])


def test_windows_1252_declarado_decodifica_os_cinco_bytes_do_padrao(server):
    """`iso-8859-1` é lido como windows-1252, e o cp1252 do Python falhava em
    0x81, 0x8D, 0x8F, 0x90 e 0x9D. Um "Á" em UTF-8 é C3 81: a mesma página UTF-8
    servida como iso-8859-1 virava ERROR quando dizia "Área" e passava quando
    não dizia."""
    base, routes = server
    corpo = b"<p>x" + BURACOS_DO_CP1252 + b"</p>"
    routes["/t"] = (200, {"Content-Type": "text/html; charset=iso-8859-1"}, corpo)
    r = fetch(base + "/t")
    assert r.charset_error is None
    assert "x\x81\x8d\x8f\x90\x9d" in r.text


def test_ultimo_passo_le_os_cinco_bytes_como_o_padrao(server):
    """Sem declaração e sem UTF-8 válido, o windows-1252 do WHATWG mapeia cada
    um desses bytes no próprio código, em vez de substituí-lo."""
    base, routes = server
    routes["/t"] = (200, {"Content-Type": "text/html"}, b"<p>ol\xe1" + BURACOS_DO_CP1252 + b"</p>")
    r = fetch(base + "/t")
    assert "ol\u00e1\x81\x8d\x8f\x90\x9d" in r.text
    assert "\ufffd" not in r.text
