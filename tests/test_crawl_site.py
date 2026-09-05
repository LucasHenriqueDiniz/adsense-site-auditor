"""Testes do CLI de crawl.

Terceiro dos CLIs que nada importava e nenhum teste tocava: `return
exit_code(overall)` podia virar `return 0` sem quebrar nada, e o despejo de
evidência do `-v` — a única saída em que as páginas parseadas aparecem — nunca
tinha sido lido por ninguém além de um humano. Tudo aqui roda contra o servidor
real do conftest, com `--delay 0` para a suíte não pagar a cortesia do crawler.
"""

import time

import crawl_site
import pytest

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


def test_as_listas_truncadas_so_ganham_reticencias_acima_do_teto(server, monkeypatch, capsys):
    """Dois tetos diferentes imprimem URLs barradas pelo robots: o despejo do -v
    corta em 5, a linha do crawl corta em 3. Um fixture com 3 fica sob os dois e
    não distingue nada — cada teto precisa do seu próprio par de valores."""
    base, routes = server

    def rodar(n_barradas):
        routes.received.clear()
        links = "".join(f'<a href="/priv/{i}">p</a>' for i in range(n_barradas))
        routes["/"] = (200, HTML, f"<html><body><p>oi</p>{links}</body></html>")
        routes["/robots.txt"] = (
            200, TEXTO, "User-agent: Mediapartners-Google\nDisallow: /priv/\n")
        for i in range(n_barradas):
            routes[f"/priv/{i}"] = (200, HTML, "<html><body>x</body></html>")
        monkeypatch.setattr("sys.argv", ["crawl_site.py", "--delay", "0", "-v", base + "/"])
        crawl_site.main()
        saida = capsys.readouterr().out
        return {
            "crawl": next(ln for ln in saida.splitlines() if "not fetched, disallowed" in ln),
            "dump": next(ln for ln in saida.splitlines() if ln.startswith("Blocked by robots.txt")),
        }

    # Teto da linha do crawl: 3.
    assert not rodar(3)["crawl"].rstrip().endswith("...")
    assert rodar(4)["crawl"].rstrip().endswith("...")
    # Teto do despejo do -v: 5.
    assert not rodar(5)["dump"].rstrip().endswith("...")
    assert rodar(6)["dump"].rstrip().endswith("...")


def test_um_unico_h1_nao_ganha_o_sufixo_de_quantidade(server, monkeypatch, capsys):
    """`page.h1_count > 1`: com um só, "(+0 more)" seria ruído."""
    base, routes = server
    routes["/"] = (200, HTML, "<html><body><h1>Um</h1><p>oi</p></body></html>")

    monkeypatch.setattr("sys.argv", ["crawl_site.py", "--delay", "0", "-v", base + "/"])
    crawl_site.main()
    saida = capsys.readouterr().out

    assert "h1: Um" in saida
    assert "more)" not in saida


def test_depth_negativo_e_recusado(monkeypatch, capsys):
    """`_queue_links` para em `page.depth >= max_depth`, e a página 0 satisfaz
    isso para todo valor negativo, então `--depth -1` se comportava exatamente
    como `--depth 0`: o número que o operador digitou era trocado em silêncio em
    vez de recusado. Saída 2 diz isso."""
    monkeypatch.setattr("sys.argv", ["crawl_site.py", "--depth", "-1", INALCANCAVEL])
    with pytest.raises(SystemExit) as saida:
        crawl_site.main()

    assert saida.value.code == 2
    assert "--depth must be 0 or more" in capsys.readouterr().err


def test_depth_zero_e_aceito_e_busca_so_a_raiz(server, monkeypatch, capsys):
    """Zero é o piso, não um: "busque a semente e não siga nada" é um pedido
    real, e uma guarda escrita `< 1` — a forma que `--nav-limit` e `--min-words`
    usam — o recusaria. Os links existem e continuam sem ser buscados."""
    base, routes = server
    routes["/"] = (200, HTML, pagina("Casa", corpo='<a href="/a">a</a>'))
    routes["/a"] = (200, HTML, pagina("A"))

    monkeypatch.setattr(
        "sys.argv", ["crawl_site.py", "--depth", "0", "--delay", "0", base + "/"]
    )
    crawl_site.main()

    # A semente primeiro (é ela que fixa a origem), depois o robots.txt dessa
    # origem. `/a` está linkado e nunca é pedido, que é o ponto inteiro.
    assert _caminhos(routes) == ["/", "/robots.txt"]
    assert "1 pages fetched" in capsys.readouterr().out


def test_max_pages_abaixo_de_um_e_recusado(monkeypatch, capsys):
    """A semente é buscada antes de o orçamento ser consultado, então
    `--max-pages 0` reportava "1 pages fetched" ao lado de "stopped at
    max_pages=0" — um orçamento que o crawl já havia estourado na mesma frase em
    que afirmava respeitá-lo. Uma página é o menor crawl que existe."""
    for valor in ("0", "-3"):
        monkeypatch.setattr(
            "sys.argv", ["crawl_site.py", "--max-pages", valor, INALCANCAVEL]
        )
        with pytest.raises(SystemExit) as saida:
            crawl_site.main()

        assert saida.value.code == 2, valor
        assert "--max-pages must be at least 1" in capsys.readouterr().err, valor


def test_max_pages_um_e_aceito_e_para_na_semente(server, monkeypatch, capsys):
    """Um é o piso, e o piso tem de ser alcançável.

    O complemento de `test_max_pages_abaixo_de_um_e_recusado`: sem ele a guarda
    podia virar `< 2` sem que a suíte notasse, e "uma página é o menor crawl que
    existe" — o argumento pelo qual o piso é 1 e não 2 — ficava sem prova. O
    link para `/a` existe e não é seguido, e o relatório conta o que fez em vez
    de anunciar um orçamento que não cumpriu.
    """
    base, routes = server
    routes["/"] = (200, HTML, pagina("Casa", corpo='<a href="/a">a</a>'))
    routes["/a"] = (200, HTML, pagina("A"))

    monkeypatch.setattr(
        "sys.argv", ["crawl_site.py", "--max-pages", "1", "--delay", "0", base + "/"]
    )
    codigo = crawl_site.main()
    saida = capsys.readouterr().out

    assert _caminhos(routes) == ["/", "/robots.txt"]
    assert "1 pages fetched, 1 readable HTML" in saida
    assert "stopped at max_pages=1" in saida
    # Contagem e parada batem entre si, ao contrário do "1 pages fetched ...
    # stopped at max_pages=0" que o piso de 1 existe para impedir.
    assert "stopped at max_pages=0" not in saida
    # Uma página só não observa o que o ADS-CRAWL-05 pede, então o veredito
    # final não é passe e o processo não sai com 0.
    assert "[MISS] ADS-CRAWL-05" in saida
    assert "Verdict: nothing failed, but something could not be observed" in saida
    assert codigo == 1


def test_delay_fora_da_faixa_e_recusado_antes_da_primeira_requisicao(
    server, monkeypatch, capsys
):
    """Este é o argumento que alcança fora, então é aqui que um valor ruim custa
    a outra pessoa — e a guarda só vale se recusar antes de a rede ser tocada.

    Abaixo de zero `crawl` pula o sleep (a guarda dele é `delay > 0`), então
    `--delay -1`, um caractere de distância de `--delay 1`, crawleava a toda
    velocidade contra o host de um terceiro fingindo pedir o contrário. `nan`
    fazia o mesmo pela mesma razão. `inf` era pior: `crawl` busca a semente e o
    robots.txt antes do primeiro sleep, então o OverflowError de `time.sleep`
    caía depois de duas requisições já terem saído. O `received` do servidor é o
    que prova isso — o relatório não, porque nesse caso não há relatório.
    """
    base, routes = server
    routes["/"] = (200, HTML, pagina("Casa", corpo='<a href="/a">a</a>'))
    routes["/a"] = (200, HTML, pagina("A"))

    for valor in ("inf", "nan", "-1", "9223372036.854776"):
        routes.received.clear()
        monkeypatch.setattr(
            "sys.argv", ["crawl_site.py", "--delay", valor, "--depth", "1", base + "/"]
        )
        with pytest.raises(SystemExit) as saida:
            crawl_site.main()

        assert saida.value.code == 2, valor
        assert "--delay must be 0 or more and less than" in capsys.readouterr().err, valor
        assert routes.received == [], valor


def test_o_maior_delay_que_o_sleep_aceita_nao_e_recusado(server, monkeypatch, capsys):
    """O teto é o do mecanismo e nem um float abaixo dele.

    9223372036.854774 é o último valor que `time.sleep` segura; o seguinte,
    9223372036.854776, é 2**63 nanossegundos e estoura. Um teto redondo qualquer
    recusaria entrada que a máquina honraria. `--max-pages 1` faz o laço parar
    antes do primeiro sleep, e o `time.sleep` trocado abaixo garante que a suíte
    falhe em voz alta em vez de travar 292 anos caso essa ordem mude.
    """
    base, routes = server
    routes["/"] = (200, HTML, pagina("Casa", corpo='<a href="/a">a</a>'))
    routes["/a"] = (200, HTML, pagina("A"))

    def nao_deveria_dormir(_segundos):
        raise AssertionError("o laço do crawl parou depois do sleep, não antes")

    monkeypatch.setattr(time, "sleep", nao_deveria_dormir)
    monkeypatch.setattr(
        "sys.argv",
        ["crawl_site.py", "--delay", "9223372036.854774", "--max-pages", "1", base + "/"],
    )
    codigo = crawl_site.main()

    # Passou da guarda: o crawl rodou e devolveu um código de saída em vez de o
    # argparse levantar SystemExit(2).
    assert codigo == 1
    assert "--delay" not in capsys.readouterr().err
    assert _caminhos(routes) == ["/", "/robots.txt"]


def test_timeout_fora_da_faixa_do_socket_e_recusado_na_linha_de_comando(monkeypatch, capsys):
    """As duas pontas terminavam em traceback cru vindo da camada de socket.

    `--timeout 0` chegava à urllib3 e voltava como ValueError. `--timeout inf` —
    a grafia plausível de "sem timeout", e no que `Infinity` e `1e400` também se
    transformam sob `type=float` — passava pela guarda `not ... > 0` e voltava
    como OverflowError de `socket.settimeout`. `fetch` repassa as duas de
    propósito, então é a CLI que tem de recusá-las primeiro.
    """
    for valor in ("inf", "Infinity", "1e400", "nan", "0", "-5", "9223372036.854776"):
        monkeypatch.setattr(
            "sys.argv", ["crawl_site.py", "--timeout", valor, INALCANCAVEL]
        )
        with pytest.raises(SystemExit) as saida:
            crawl_site.main()

        assert saida.value.code == 2, valor
        erro = capsys.readouterr().err
        assert "--timeout must be greater than 0 and less than" in erro, valor


def test_menos_infinito_separado_por_espaco_morre_no_argparse(server, monkeypatch, capsys):
    """`-inf` com espaço nunca chega às guardas, e tudo bem.

    O `_negative_number_matcher` do argparse é `^-\\d+$|^-\\d*\\.\\d+$`, então
    `-inf` não parece um número negativo e sim outra flag: o erro é "expected one
    argument" em vez da mensagem da guarda. Só a mensagem muda — as duas
    propriedades que importam continuam de pé, saída 2 e rede intocada —, e
    consertá-la exigiria sobrescrever um atributo privado do argparse que governa
    a tokenização de *todos* os argumentos. Fica pinado como está, para que
    ninguém o "conserte" em algo que aceita o valor.
    """
    base, routes = server
    routes["/"] = (200, HTML, pagina("Casa"))

    for flag in ("--timeout", "--delay"):
        routes.received.clear()
        monkeypatch.setattr("sys.argv", ["crawl_site.py", flag, "-inf", base + "/"])
        with pytest.raises(SystemExit) as saida:
            crawl_site.main()

        assert saida.value.code == 2, flag
        assert f"argument {flag}: expected one argument" in capsys.readouterr().err, flag
        assert routes.received == [], flag

    # Colado com `=` o token chega inteiro e é a guarda que responde.
    routes.received.clear()
    monkeypatch.setattr("sys.argv", ["crawl_site.py", "--timeout=-inf", base + "/"])
    with pytest.raises(SystemExit) as saida:
        crawl_site.main()

    assert saida.value.code == 2
    assert "--timeout must be greater than 0 and less than" in capsys.readouterr().err
    assert routes.received == []
