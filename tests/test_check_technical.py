"""Testes do CLI técnico.

Nenhum dos cinco scripts tinha teste, que é como a leitura do robots.txt no
diretório errado atravessou a suíte inteira. O servidor real do conftest é o que
permite assertar em qual caminho a requisição de fato caiu, em vez de comparar
duas constantes entre si.
"""

import check_technical
import pytest

from adsense_checks.http import Fetch
from adsense_checks.status import Status

BLOQUEIA_TUDO = "User-agent: *\nDisallow: /\n"
# Conexão recusada, na hora. Um argumento recusado não pode chegar à rede, e
# este endereço prova isso sem fazer a suíte esperar.
INALCANCAVEL = "http://127.0.0.1:1/"


def _caminhos(routes):
    return [caminho for _metodo, caminho, _headers in routes.received]


def test_robots_e_lido_na_raiz_da_origem_e_nao_no_subdiretorio(server):
    """Um install em subdiretório pedia /blog/robots.txt, levava 404 e relatava
    'absent: everything is crawlable' sobre um arquivo que nunca foi buscado."""
    base, routes = server
    routes["/robots.txt"] = (200, {"Content-Type": "text/plain"}, BLOQUEIA_TUDO)

    linha, robots = check_technical._robots(f"{base}/blog", 5)

    assert _caminhos(routes) == ["/robots.txt"]
    assert robots.missing is False
    assert linha.status is Status.FAIL
    # Googlebot, e só ele: Mediapartners-Google e AdsBot ignoram o grupo `*`,
    # documentado pelo Google. O site não é indexável e mesmo assim serve anúncios.
    assert any("Googlebot is disallowed" in f for f in linha.findings)
    assert not any("Mediapartners-Google is disallowed" in f for f in linha.findings)


def test_ausencia_de_robots_e_relatada_a_partir_da_raiz_da_origem(server):
    """A ausência também precisa ser uma afirmação sobre a URL certa: dizer
    'absent' depois de perguntar no lugar errado é a mesma mentira."""
    base, routes = server

    linha, robots = check_technical._robots(f"{base}/blog/", 5)

    assert _caminhos(routes) == ["/robots.txt"]
    assert robots.missing is True
    assert linha.status is Status.INFO


def _sitemap(base):
    """URLs no próprio host: um <loc> de fora agora é achado, não silêncio."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"<url><loc>{base}/a</loc></url>"
        f"<url><loc>{base}/b</loc></url>"
        "</urlset>"
    )


def test_robots_e_sitemap_saem_da_origem_que_respondeu_e_nao_da_digitada(
    server, outro_servidor, monkeypatch, capsys
):
    """Apex -> www, o redirect mais comum da web.

    O robots.txt já lia a origem final; o sitemap continuava resolvendo os
    caminhos convencionais contra a URL digitada, então as duas metades de um
    mesmo relatório perguntavam a hosts diferentes. O robots.txt aqui não
    declara `Sitemap:` de propósito: uma URL absoluta declarada salvaria a
    implementação errada e o teste não conseguiria falhar.
    """
    apex, rotas_apex = server
    servido, rotas_servido = outro_servidor

    rotas_apex["/"] = (301, {"Location": servido + "/"}, "")
    rotas_servido["/"] = (200, {"Content-Type": "text/html"}, "<html></html>")
    livre = "User-agent: *\nAllow: /\n"
    rotas_servido["/robots.txt"] = (200, {"Content-Type": "text/plain"}, livre)
    rotas_servido["/sitemap.xml"] = (
        200,
        {"Content-Type": "application/xml"},
        _sitemap(servido),
    )
    # As URLs que o sitemap anuncia têm de existir: o check agora busca uma
    # amostra delas em vez de afirmar PASS sem ter olhado nenhuma.
    for caminho in ("/a", "/b"):
        rotas_servido[caminho] = (200, {"Content-Type": "text/html"}, "<html></html>")

    monkeypatch.setattr("sys.argv", ["check_technical.py", apex + "/"])
    check_technical.main()

    # Nada além do redirect inicial pode ter batido no host digitado.
    assert _caminhos(rotas_apex) == ["/"]
    # /a e /b vem depois: ADS-CRAWL-07 pede que as URLs anunciadas respondam
    # 200, e o check agora busca uma amostra delas em vez de afirmar sem olhar.
    assert _caminhos(rotas_servido) == ["/", "/robots.txt", "/sitemap.xml", "/a", "/b"]
    assert "[PASS] ADS-CRAWL-07 sitemap" in capsys.readouterr().out


def _home_fetch(final_url, *, elapsed_ms=120.0, url=None, chain=()):
    return Fetch(
        url=url or final_url,
        final_url=final_url,
        status_code=200,
        text="<html></html>",
        headers={"content-type": "text/html"},
        redirect_chain=list(chain),
        elapsed_ms=elapsed_ms,
    )


def _texto(linha):
    return " | ".join(linha.findings)


def test_availability_nomeia_as_quatro_partes_do_requisito():
    """ADS-CRAWL-06 pede DNS, TLS, uptime e tempo de resposta.

    O que existia era um teste de esquema carregando o ID do requisito inteiro e
    imprimindo PASS — três quartos afirmados sem terem sido olhados.
    """
    linha = check_technical._availability(_home_fetch("https://exemplo.com/"))
    texto = _texto(linha)
    assert "TLS: served over HTTPS" in texto
    assert "DNS: host exemplo.com resolved" in texto
    assert "response time: 120ms" in texto
    assert "uptime: not observed" in texto
    # INFO e não MISSING: a lacuna é dita sem tornar a checagem impossível de
    # satisfazer, então o script continua servindo de gate.
    assert linha.status is Status.INFO


def test_availability_reprova_downgrade_para_http_e_avisa_de_lentidao():
    baixado = _home_fetch("http://exemplo.com/", url="https://exemplo.com/", chain=[(301, "x")])
    assert check_technical._availability(baixado).status is Status.FAIL

    lento = _home_fetch(
        "https://exemplo.com/", elapsed_ms=check_technical.SLOW_RESPONSE_MS + 1
    )
    linha = check_technical._availability(lento)
    assert linha.status is Status.WARNING
    assert "over the" in _texto(linha)


def test_availability_sem_medida_de_tempo_e_ERROR_e_nao_um_numero_inventado():
    linha = check_technical._availability(_home_fetch("https://exemplo.com/", elapsed_ms=None))
    assert linha.status is Status.ERROR
    assert "response time: not measured" in _texto(linha)


def test_robots_403_nao_vira_afirmacao_sobre_permissoes(server):
    """403 caía direto no parser: um HTML de erro não tem linha `user-agent:`,
    então saía como "todo mundo liberado" — uma afirmação sobre permissões lida
    de um documento que não é o robots.txt, com o status HTTP em lugar nenhum."""
    base, routes = server
    routes["/robots.txt"] = (403, {"Content-Type": "text/html"}, "<html>Forbidden</html>")

    linha, robots = check_technical._robots(base, 5)

    assert robots.missing is True
    assert "403" in _texto(linha)
    assert "all allowed" not in _texto(linha)
    assert linha.status is Status.INFO  # 4xx: Google rastreia como se não houvesse arquivo


def test_robots_200_servido_como_html_e_soft_404(server):
    """A rota catch-all de uma SPA responde 200 com o shell do app."""
    base, routes = server
    routes["/robots.txt"] = (200, {"Content-Type": "text/html"}, "<html><body>App</body></html>")

    linha, robots = check_technical._robots(base, 5)

    assert robots.missing is True
    assert "soft 404" in _texto(linha)


def test_robots_429_para_o_rastreamento_como_um_5xx(server):
    """Google lê 429 e 5xx como erro temporário e para de rastrear o site."""
    base, routes = server
    routes["/robots.txt"] = (429, {"Content-Type": "text/plain"}, "")

    linha, _robots = check_technical._robots(base, 5)

    assert linha.status is Status.FAIL
    assert "crawling stops site-wide" in _texto(linha)


def test_todo_caminho_de_falha_do_robots_carrega_o_id_do_requisito(server):
    """Só o caminho de sucesso levava o ID, então ADS-CRAWL-02 sumia da coluna
    justamente quando o requisito estava em apuros — e o Completeness Gate do
    SKILL.md lê um ID ausente como um requisito que ninguém checou."""
    base, routes = server
    html = {"Content-Type": "text/html"}
    for rota in ((503, {}, ""), (403, {}, ""), (404, {}, ""), (200, html, "<html></html>")):
        routes["/robots.txt"] = rota
        assert check_technical._robots(base, 5)[0].requirement == "ADS-CRAWL-02"
    # E o caminho de erro de transporte, que nem chega a ter status.
    assert check_technical._robots("http://127.0.0.1:1/", 2)[0].requirement == "ADS-CRAWL-02"


def test_bloqueio_curinga_nao_reprova_o_crawler_do_adsense_no_relatorio(server):
    """`User-agent: * / Disallow: /` reprovava com "o crawler do AdSense não
    consegue ler este site", o que é falso: o Google documenta que o
    Mediapartners-Google ignora o grupo global. O site não é INDEXÁVEL, que é
    outro achado, sobre outro crawler."""
    base, routes = server
    routes["/robots.txt"] = (200, {"Content-Type": "text/plain"}, BLOQUEIA_TUDO)

    texto = _texto(check_technical._robots(base, 5)[0])

    assert "Googlebot is disallowed" in texto
    assert "the AdSense crawler cannot read this site" not in texto
    assert "AdsBot is disallowed" not in texto


def test_ADS_CRAWL_06_e_reportado_mesmo_quando_a_home_nao_responde(monkeypatch, capsys):
    """A linha de availability morava só no `else`, então em todo caminho de erro
    o requisito não saía como ERROR — não saía de jeito nenhum, e o Completeness
    Gate lia um ADS-CRAWL-06 ausente como um requisito que ninguém checou."""
    monkeypatch.setattr("sys.argv", ["check_technical.py", "http://127.0.0.1:1/"])
    check_technical.main()
    saida = capsys.readouterr().out

    assert "ADS-CRAWL-06" in saida
    assert "[ERR ] ADS-CRAWL-06 availability" in saida


def test_o_codigo_de_saida_e_lido(server, monkeypatch, capsys):
    """`return exit_code(overall)` -> `return 0` sobrevivia à suíte inteira: os
    dois testes que chamam `main()` descartavam o retorno, então o script podia
    sair 0 sobre um site que não respondeu."""
    base, routes = server
    routes["/robots.txt"] = (200, {"Content-Type": "text/plain"}, "User-agent: *\nAllow: /\n")
    routes["/"] = (200, {"Content-Type": "text/html"}, "<html><body>ok</body></html>")

    monkeypatch.setattr("sys.argv", ["check_technical.py", base + "/"])
    assert check_technical.main() == 1  # http local: TLS reprova, então 1
    capsys.readouterr()

    monkeypatch.setattr("sys.argv", ["check_technical.py", "http://127.0.0.1:1/"])
    assert check_technical.main() == 1
    assert "[ERR ] ADS-CRAWL-01" in capsys.readouterr().out


def test_home_com_erro_http_nao_e_alcancavel(server, monkeypatch, capsys):
    """`home.status` forçado para OK imprimia `[PASS] reachable — HTTP 500`, e
    nada na suíte assertava o status dessa linha. O 400 é o caso que nenhum
    teste alimentava: `>= 400` virando `> 400` passava batido."""
    base, routes = server
    routes["/robots.txt"] = (200, {"Content-Type": "text/plain"}, "User-agent: *\nAllow: /\n")
    for codigo in (400, 500):
        routes["/"] = (codigo, {"Content-Type": "text/html"}, "")
        monkeypatch.setattr("sys.argv", ["check_technical.py", base + "/"])
        check_technical.main()
        saida = capsys.readouterr().out
        assert "[FAIL] ADS-CRAWL-01 reachable" in saida, codigo
        assert f"HTTP {codigo}" in saida


def test_sitemap_ausente_nao_imprime_pass(server, monkeypatch, capsys):
    """`sitemap.status` forçado para OK imprimia
    `[PASS] ADS-CRAWL-07 sitemap — no sitemap found`. A única asserção existente
    sobre essa linha era um `[PASS]`, e uma asserção de PASS não detecta forçar
    PASS."""
    base, routes = server
    routes["/robots.txt"] = (200, {"Content-Type": "text/plain"}, "User-agent: *\nAllow: /\n")
    routes["/"] = (200, {"Content-Type": "text/html"}, "<html><body>ok</body></html>")

    monkeypatch.setattr("sys.argv", ["check_technical.py", base + "/"])
    check_technical.main()
    saida = capsys.readouterr().out

    assert "[MISS] ADS-CRAWL-07 sitemap" in saida
    assert "no sitemap found" in saida


def test_url_anunciada_pelo_sitemap_que_da_404_reprova_a_linha(server, monkeypatch, capsys):
    """A amostragem passou a existir nesta rodada e nada fixava que o veredito
    dela entra no status: apagar a escalação deixava os 404 aparecerem na nota com
    a linha do sitemap ainda em PASS — o defeito que esta mesma rodada diz ter
    corrigido, um passo adiante."""
    base, routes = server
    routes["/robots.txt"] = (200, {"Content-Type": "text/plain"}, "User-agent: *\nAllow: /\n")
    routes["/"] = (200, {"Content-Type": "text/html"}, "<html><body>ok</body></html>")
    routes["/sitemap.xml"] = (
        200,
        {"Content-Type": "application/xml"},
        _sitemap(base),  # anuncia /a e /b, que este fixture nao serve
    )

    monkeypatch.setattr("sys.argv", ["check_technical.py", base + "/"])
    codigo = check_technical.main()
    saida = capsys.readouterr().out

    assert "[PASS] ADS-CRAWL-07 sitemap" not in saida
    assert "0 of 2 sampled URL(s) answered 200" in saida
    assert codigo == 1


def test_availability_avisa_quando_o_site_nao_e_https():
    """Só o downgrade https->http estava fixado. Um site servido em http puro
    escalava para WARNING e nada observava: apagar a escalação deixava a linha
    em INFO, que sai com exit 0."""
    linha = check_technical._availability(_home_fetch("http://exemplo.com/"))
    assert linha.status is Status.WARNING
    assert any("not HTTPS" in f for f in linha.findings)


def test_availability_nao_cronometra_uma_pagina_de_erro():
    """A função roda no `else` de `home.error is None`, que é tão verdadeiro para
    um 500 quanto para um 200. Sem a escalação ela respondia três quartos de
    ADS-CRAWL-06 sobre uma resposta que o mesmo relatório estava reprovando."""
    erro = _home_fetch("https://exemplo.com/")
    erro.status_code = 500
    linha = check_technical._availability(erro)
    assert linha.status is Status.MISSING
    assert any("no served page to time" in f for f in linha.findings)


def test_a_fronteira_de_resposta_lenta_e_2500ms():
    """SLOW_RESPONSE_MS pelo valor, nos dois lados. O teste vizinho passava
    `SLOW_RESPONSE_MS + 1`, uma cota derivada da própria constante sob teste:
    ela se movia junto com a mutação e o teste passava sempre."""
    rapido = check_technical._availability(_home_fetch("https://x/", elapsed_ms=2499.0))
    lento = check_technical._availability(_home_fetch("https://x/", elapsed_ms=2501.0))

    assert rapido.status is Status.INFO  # só a lacuna de uptime
    assert lento.status is Status.WARNING
    assert any("over the" in f for f in lento.findings)


def test_as_fronteiras_de_status_do_robots_sao_exatas(server):
    """`code >= 400` e `code >= 500`, nos valores. 399 é legível, 400 não é;
    499 é 4xx e o Google rastreia como se não houvesse arquivo, 500 para o
    rastreamento do site inteiro."""
    base, routes = server
    for codigo, esperado in ((399, Status.OK), (400, Status.INFO),
                             (499, Status.INFO), (500, Status.FAIL)):
        routes["/robots.txt"] = (codigo, {"Content-Type": "text/plain"},
                                 "User-agent: *\nAllow: /\n")
        assert check_technical._robots(base, 5)[0].status is esperado, codigo


def test_a_fronteira_de_home_com_erro_e_400_exato():
    """`home.status_code >= 400` no availability: 399 é uma resposta servida e
    pode ser cronometrada; 400 não."""
    for codigo, esperado in ((399, Status.INFO), (400, Status.MISSING)):
        f = _home_fetch("https://x/")
        f.status_code = codigo
        assert check_technical._availability(f).status is esperado, codigo


def test_exatamente_2500ms_ainda_nao_e_lento():
    """`elapsed_ms > SLOW_RESPONSE_MS`: no valor exato não avisa."""
    igual = check_technical._availability(_home_fetch("https://x/", elapsed_ms=2500.0))
    assert igual.status is Status.INFO


def test_timeout_fora_da_faixa_do_socket_e_recusado_na_linha_de_comando(monkeypatch, capsys):
    """As duas pontas terminavam em traceback cru vindo da camada de socket.

    `--timeout 0` chegava à urllib3 e voltava como ValueError. `--timeout inf` —
    a grafia plausível de "sem timeout", e no que `Infinity` e `1e400` também se
    transformam sob `type=float` — passava por uma guarda escrita `not ... > 0`
    e voltava como "OverflowError: timestamp out of range for platform time_t"
    de dentro de `socket.settimeout`. `fetch` repassa as duas de propósito, então
    é a CLI que tem de recusá-las: saída 2 e a razão no stderr. Nenhum dos
    valores abaixo pode virar requisição.
    """
    for valor in ("inf", "Infinity", "1e400", "nan", "0", "-5", "9223372036.854776"):
        monkeypatch.setattr(
            "sys.argv", ["check_technical.py", "--timeout", valor, INALCANCAVEL]
        )
        with pytest.raises(SystemExit) as saida:
            check_technical.main()

        assert saida.value.code == 2, valor
        erro = capsys.readouterr().err
        assert "--timeout must be greater than 0 and less than" in erro, valor


def test_o_maior_timeout_que_o_socket_aceita_nao_e_recusado(server, monkeypatch, capsys):
    """O teto é o do mecanismo e nem um float abaixo dele.

    9223372036.854774 é o último valor que `socket.settimeout` segura — o
    seguinte, 9223372036.854776, é 2**63 nanossegundos e estoura. Uma guarda com
    um teto redondo qualquer recusaria entrada que funciona; esta deixa passar, e
    a requisição sai.
    """
    base, routes = server
    routes["/"] = (200, {"Content-Type": "text/html"}, "<html><body>oi</body></html>")

    monkeypatch.setattr(
        "sys.argv", ["check_technical.py", "--timeout", "9223372036.854774", base + "/"]
    )
    check_technical.main()

    assert "/" in [caminho for _metodo, caminho, _headers in routes.received]
