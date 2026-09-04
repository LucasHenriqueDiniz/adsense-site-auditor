"""Testes do CLI de crawl.

Terceiro dos CLIs que nada importava e nenhum teste tocava: `return
exit_code(overall)` podia virar `return 0` sem quebrar nada, e o despejo de
evidência do `-v` — a única saída em que as páginas parseadas aparecem — nunca
tinha sido lido por ninguém além de um humano. Tudo aqui roda contra o servidor
real do conftest, com `--delay 0` para a suíte não pagar a cortesia do crawler.
"""

import time

import crawl_site

from adsense_checks.crawl import DEFAULT_DELAY

HTML = {"Content-Type": "text/html; charset=utf-8"}
TEXTO = {"Content-Type": "text/plain"}
INALCANCAVEL = "http://127.0.0.1:1/"


def pagina(titulo, corpo="", cabeca=""):
    return (
        f"<html><head><title>{titulo}</title>{cabeca}</head>"
        f"<body><h1>{titulo}</h1>{corpo}</body></html>"
    )


def _caminhos(routes):
    return [caminho for _metodo, caminho, _headers in routes.received]


def test_relatorio_traz_a_linha_de_crawl_e_os_tres_requisitos(server, monkeypatch, capsys):
    """Os três IDs que o script promete, cada um com um nome legível.

    Um ID que some da coluna é lido pelo Completeness Gate como requisito não
    checado. E o PASS do ADS-CRAWL-04 sai com a evidência que o produziu: um
    passe sem linha nenhuma embaixo não deixa distinguir "observado e correto"
    de "nunca rodou".
    """
    base, routes = server
    routes["/"] = (200, HTML, pagina("Bancada", '<a href="/sobre">Sobre</a>'))
    routes["/sobre"] = (200, HTML, pagina("Sobre", "<p>Trabalho com madeira</p>"))

    monkeypatch.setattr("sys.argv", ["crawl_site.py", "--delay", "0", base + "/"])
    codigo = crawl_site.main()
    saida = capsys.readouterr().out

    assert "[PASS] crawl" in saida
    assert "2 pages fetched, 2 readable HTML" in saida
    assert "ADS-CRAWL-01 pages reachable" in saida
    assert "ADS-CRAWL-04 redirect chains" in saida
    assert "ADS-CRAWL-05 stable URLs" in saida
    assert "max_hops_seen=0, redirected_pages=0, verified_stateless=False" in saida
    assert "4 checks:" in saida
    # Nenhum canonical foi declarado, então a metade do ADS-CRAWL-05 que compara
    # canonical com URL final não foi observada — e o processo não sai com 0.
    assert "[MISS] ADS-CRAWL-05 stable URLs" in saida
    assert codigo == 1


def test_urls_barradas_pelo_robots_aparecem_sem_o_verbose(server, monkeypatch, capsys):
    """Meio site pulado por robots.txt não pode depender do `-v` para aparecer.

    O número morava só num dict de detalhes que o `render` imprime quando a
    checagem NÃO tem achados, então um único 404 sem relação silenciava o aviso
    e o relatório padrão dizia "robots" zero vezes.
    """
    base, routes = server
    # O grupo é o do Mediapartners-Google: o crawler do AdSense ignora `*`, e um
    # Disallow global não o barraria.
    routes["/robots.txt"] = (
        200, TEXTO, "User-agent: Mediapartners-Google\nDisallow: /privado\n"
    )
    routes["/"] = (
        200,
        HTML,
        pagina("Bancada", '<a href="/sobre">Sobre</a> <a href="/privado">Privado</a>'),
    )
    routes["/sobre"] = (200, HTML, pagina("Sobre", "<p>madeira</p>"))
    routes["/privado"] = (200, HTML, pagina("Privado"))

    monkeypatch.setattr("sys.argv", ["crawl_site.py", "--delay", "0", base + "/"])
    crawl_site.main()
    saida = capsys.readouterr().out

    assert f"1 URL(s) not fetched, disallowed by robots.txt: {base}/privado" in saida
    # E a URL barrada não foi pedida: o aviso fala de uma requisição que não saiu.
    assert "/privado" not in _caminhos(routes)


def test_site_que_nao_respondeu_diz_que_o_robots_nunca_foi_pedido(monkeypatch, capsys):
    """"read, 0 group(s)" sobre um arquivo que ninguém buscou é a mesma mentira
    das outras: indistinguível de um 200 legítimo sem regra nenhuma."""
    monkeypatch.setattr("sys.argv", ["crawl_site.py", "--delay", "0", "-v", INALCANCAVEL])
    codigo = crawl_site.main()
    saida = capsys.readouterr().out

    assert "robots.txt: never requested: the first fetch did not complete" in saida
    assert "read, 0 group(s)" not in saida
    assert "Site identity: (unknown) (from no response)" in saida
    assert saida.count("[ERR ]") == 4
    assert codigo == 1


def test_verbose_despeja_evidencia_por_pagina_identidade_e_sitemaps(
    server, monkeypatch, capsys
):
    """Título, descrição, H1, palavras visíveis e canonical são a evidência que
    o requisito manda um humano julgar, e até este despejo existir cada página
    era parseada para um relatório que ninguém podia ver."""
    base, routes = server
    routes["/robots.txt"] = (
        200, TEXTO, f"User-agent: *\nAllow: /\nSitemap: {base}/sitemap.xml\n"
    )
    routes["/"] = (
        200,
        HTML,
        pagina(
            "Bancada",
            '<a href="/sobre">Sobre</a> <a href="mailto:eu@exemplo.com">Fale</a>'
            ' <a href="https://outro.example/x">Fora</a> <a href="/manual.pdf">PDF</a>'
            "<h1>Segundo</h1>",
            cabeca=(
                '<meta name="description" content="Oficina de marcenaria">'
                f'<link rel="canonical" href="{base}/">'
            ),
        ),
    )
    routes["/sobre"] = (200, HTML, pagina("Sobre", "<p>Trabalho com madeira</p>"))

    monkeypatch.setattr("sys.argv", ["crawl_site.py", "--delay", "0", "-v", base + "/"])
    crawl_site.main()
    verboso = capsys.readouterr().out

    assert f"Site identity: {base.removeprefix('http://')} (from {base})" in verboso
    assert "robots.txt: read, 1 group(s)" in verboso
    assert f"Sitemaps declared in robots.txt (1): {base}/sitemap.xml" in verboso
    assert "Pages (2):" in verboso
    assert f"[200] d0 {base}/" in verboso
    assert "title: Bancada" in verboso
    assert "h1: Bancada (+1 more)" in verboso
    assert "description: Oficina de marcenaria" in verboso
    assert "visible words in" in verboso
    assert f"canonical: {base}/" in verboso
    assert "Off-site links (1): https://outro.example/x" in verboso
    assert f"Assets skipped (1): {base}/manual.pdf" in verboso
    assert "Non-http links (1): mailto:eu@exemplo.com" in verboso

    # Sem `-v` nada disso sai: o despejo é evidência sob demanda, não o relatório.
    monkeypatch.setattr("sys.argv", ["crawl_site.py", "--delay", "0", base + "/"])
    crawl_site.main()
    curto = capsys.readouterr().out

    assert "Site identity:" not in curto
    assert "Pages (2):" not in curto
    assert "ADS-CRAWL-01 pages reachable" in curto


def test_max_pages_limita_o_crawl_e_o_veredito_diz_que_e_parcial(server, monkeypatch, capsys):
    """"todas as páginas respondem 2xx" vira uma frase sobre a amostra.

    Lia como PASS enquanto três 500 estavam logo depois do teto, e subir o
    `--max-pages` no mesmo site trocava o veredito para FAIL.
    """
    base, routes = server
    routes["/"] = (200, HTML, pagina("Bancada", '<a href="/a">a</a> <a href="/b">b</a>'))
    routes["/a"] = (200, HTML, pagina("A"))
    routes["/b"] = (200, HTML, pagina("B"))

    monkeypatch.setattr(
        "sys.argv", ["crawl_site.py", "--delay", "0", "--max-pages", "2", base + "/"]
    )
    crawl_site.main()
    saida = capsys.readouterr().out

    assert "stopped at max_pages=2" in saida
    assert "[MISS] ADS-CRAWL-01 pages reachable" in saida
    assert "this verdict covers the sample and not the site" in saida
    assert len(_caminhos(routes)) == 3  # robots.txt, / e uma das duas linkadas


def test_verify_stateless_troca_o_MISSING_do_ADS_CRAWL_04_por_observacao(
    server, monkeypatch, capsys
):
    """A metade cara do ADS-CRAWL-04 é opcional; chamá-la de satisfeita quando
    não foi feita, não. A flag tem de chegar à checagem nos dois sentidos."""
    base, routes = server
    routes["/"] = (200, HTML, pagina("Bancada", '<a href="/velho">velho</a>'))
    routes["/velho"] = (301, {"Location": base + "/novo"}, "")
    routes["/novo"] = (200, HTML, pagina("Novo", "<p>madeira</p>"))

    monkeypatch.setattr("sys.argv", ["crawl_site.py", "--delay", "0", base + "/"])
    crawl_site.main()
    sem_flag = capsys.readouterr().out

    assert "[MISS] ADS-CRAWL-04 redirect chains" in sem_flag
    assert "the cookie-less re-request was not made (verify_stateless=False)" in sem_flag

    monkeypatch.setattr(
        "sys.argv", ["crawl_site.py", "--delay", "0", "--verify-stateless", base + "/"]
    )
    crawl_site.main()
    com_flag = capsys.readouterr().out

    assert "[PASS] ADS-CRAWL-04 redirect chains" in com_flag
    assert "verified_stateless=True" in com_flag
    assert "verify_stateless=False" not in com_flag


def test_depth_limita_os_niveis_visitados(server, monkeypatch, capsys):
    """`--depth` tem de chegar ao crawler: com o argumento largado no caminho o
    padrão de 2 níveis desce um nível a mais do que o auditor pediu."""
    base, routes = server
    routes["/"] = (200, HTML, pagina("Bancada", '<a href="/a">a</a>'))
    routes["/a"] = (200, HTML, pagina("A", '<a href="/b">b</a>'))
    routes["/b"] = (200, HTML, pagina("B"))

    monkeypatch.setattr(
        "sys.argv", ["crawl_site.py", "--delay", "0", "--depth", "1", base + "/"]
    )
    crawl_site.main()
    capsys.readouterr()

    assert "/a" in _caminhos(routes)
    assert "/b" not in _caminhos(routes)


def test_timeout_da_linha_de_comando_chega_ao_crawler(server, monkeypatch, capsys):
    """Com o `--timeout` largado no caminho valem os 15s padrão, e a página
    lenta que o auditor queria ver estourar volta como um crawl bem-sucedido."""
    base, routes = server

    def lenta(_metodo):
        time.sleep(0.4)
        return (200, HTML, pagina("Bancada"))

    routes["/"] = lenta

    monkeypatch.setattr(
        "sys.argv", ["crawl_site.py", "--delay", "0", "--timeout", "0.1", base + "/"]
    )
    codigo = crawl_site.main()
    saida = capsys.readouterr().out

    assert "[ERR ] crawl" in saida
    assert f"could not reach {base}/: ReadTimeout" in saida
    assert codigo == 1


def test_delay_da_linha_de_comando_chega_ao_crawler(server, monkeypatch, capsys):
    """O oposto do teste acima, no argumento que a suíte inteira depende de
    passar: com `delay=args.delay` fora da chamada, cada teste daqui pagaria a
    cortesia de meio segundo por página e ninguém veria a diferença."""
    base, routes = server
    routes["/"] = (200, HTML, pagina("Bancada", '<a href="/a">a</a> <a href="/b">b</a>'))
    routes["/a"] = (200, HTML, pagina("A"))
    routes["/b"] = (200, HTML, pagina("B"))

    monkeypatch.setattr("sys.argv", ["crawl_site.py", "--delay", "0", base + "/"])
    inicio = time.monotonic()
    crawl_site.main()
    decorrido = time.monotonic() - inicio
    capsys.readouterr()

    # Duas páginas na fila: com o padrão valendo seriam duas pausas inteiras.
    assert decorrido < DEFAULT_DELAY, (
        f"crawl levou {decorrido:.3f}s com --delay 0; o padrão é {DEFAULT_DELAY}s por página"
    )


def test_verify_canonical_liga_de_fato_a_comparacao_de_duas_sessoes(
    server, monkeypatch, capsys
):
    """Apagar `verify_two_sessions=args.verify_canonical` tornava a flag um
    no-op: a comparação nunca rodava, ADS-CRAWL-05 passava sem ela, e nada
    percebia — a flag existe justamente porque sem ela o check fica MISSING e o
    script não tem caminho algum para exit 0.

    A asserção é sobre o fio: com a flag, cada página com canonical é re-buscada
    duas vezes a partir de sessões novas.
    """
    base, routes = server
    pagina = (
        f'<html><head><link rel="canonical" href="{base}/"></head>'
        "<body><p>conteudo</p></body></html>"
    )
    routes["/"] = (200, HTML, pagina)
    routes["/robots.txt"] = (200, TEXTO, "User-agent: *\nAllow: /\n")

    def rodar(*flags):
        routes.received.clear()
        monkeypatch.setattr("sys.argv", ["crawl_site.py", *flags, base + "/"])
        crawl_site.main()
        saida = capsys.readouterr().out
        return saida, sum(1 for _m, caminho, _h in routes.received if caminho == "/")

    sem_flag, pedidos_sem = rodar("--delay", "0")
    com_flag, pedidos_com = rodar("--delay", "0", "--verify-canonical")

    assert "the two-session comparison was not made" in sem_flag
    assert "the two-session comparison was not made" not in com_flag
    # Duas visitas independentes por página com canonical, que sem a flag não acontecem.
    assert pedidos_com == pedidos_sem + 2
