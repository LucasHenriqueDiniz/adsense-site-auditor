"""Testes do CLI de completeness.

Os três crashes e o veredito subestimado deste script atravessaram a suíte
inteira porque nenhum dos cinco CLIs tinha teste. O servidor real da fixture é
o que permite rodar `main()` de ponta a ponta.
"""

import check_completeness

from adsense_checks.completeness import check_completeness as checar
from adsense_checks.status import Status

PROSA = "Escrevo sobre marcenaria desde 2015 e mantenho este site sozinho. " * 6
HTML = {"Content-Type": "text/html; charset=utf-8"}


def _home(corpo=PROSA):
    return (
        "<html><head><title>Bancada</title></head>"
        f"<body><h1>Bancada</h1><p>{corpo}</p></body></html>"
    )


def test_o_fail_agregado_das_paginas_de_confianca_chega_ao_veredito(server, monkeypatch, capsys):
    """Sem About e sem Contact, check_trust_pages grava FAIL.

    O CLI iterava só `report.findings`, que não inclui os achados dos
    sub-relatórios, então essa frase nunca era impressa e o veredito saía como
    MISSING — "nada falhou" sobre um site que o módulo já tinha reprovado.
    """
    base, routes = server
    routes["/"] = (200, HTML, _home())

    monkeypatch.setattr("sys.argv", ["check_completeness.py", base + "/"])
    codigo = check_completeness.main()
    saida = capsys.readouterr().out

    assert checar(base + "/").status is Status.FAIL
    assert "Neither an About nor a Contact page was found" in saida
    assert "Verdict: worst status is FAIL" in saida
    assert codigo == 1


def test_placar_conta_checagens_e_nao_repete_os_achados(server, monkeypatch, capsys):
    """Cada achado virava uma Line, e `render` conta Lines como checagens: quatro
    checagens (home, about, contact, navegação) saíam no placar como cinco, ou
    como sete quando faltavam as duas páginas de confiança."""
    base, routes = server
    routes["/"] = (200, HTML, _home())

    monkeypatch.setattr("sys.argv", ["check_completeness.py", base + "/"])
    check_completeness.main()
    saida = capsys.readouterr().out

    # 5 nomeadas (home, about, contact, canal de contato, navegação) + 1 linha
    # agrupando os achados.
    assert "6 checks:" in saida
    assert saida.count("[MISS] ADS-UX-05") == 2
    # ADS-AUTHOR-02 era prometido no docstring e não era estampado em lugar
    # nenhum, então o Completeness Gate lia o requisito como não checado.
    assert "ADS-AUTHOR-02 contact channel" in saida
    # E nenhuma mensagem se perdeu no agrupamento.
    assert "Neither an About nor a Contact page was found" in saida
    assert "Verdict: worst status is FAIL" in saida


def test_navegacao_truncada_diz_que_o_numero_e_piso(server, monkeypatch, capsys):
    """"25 links followed, none broken" sobre um menu de 32 com três 404 depois
    do limite: mesmo site, mesmos defeitos, veredito decidido por um default."""
    base, routes = server
    menu = "".join(f'<a href="/p{i}">p{i}</a>' for i in range(30))
    routes["/"] = (200, HTML, _home() .replace("</body>", f"<nav>{menu}</nav></body>"))
    for i in range(30):
        routes[f"/p{i}"] = (200, HTML, _home())

    # 7, nao 5: "5 of 30" e substring de "25 of 30", o default do modulo, entao
    # largar `nav_link_limit=args.nav_limit` da chamada passava o teste.
    monkeypatch.setattr("sys.argv", ["check_completeness.py", "--nav-limit", "7", base + "/"])
    check_completeness.main()
    saida = capsys.readouterr().out

    assert "7 of 30 links followed" in saida
    assert "lower bound" in saida
    assert "none broken" not in saida


def test_marcador_forte_na_home_reprova_e_o_codigo_de_saida_diz(server, monkeypatch, capsys):
    """Forçar o status da linha da home para OK fazia uma home com "Coming soon"
    sair `[PASS] ADS-COMPLETE-01 home page is finished`, e nenhum teste percebia
    porque todos usavam uma home sem marcador."""
    base, routes = server
    routes["/"] = (200, HTML, _home().replace("<h1>Bancada</h1>", "<h1>Coming soon</h1>"))

    monkeypatch.setattr("sys.argv", ["check_completeness.py", base + "/"])
    codigo = check_completeness.main()
    saida = capsys.readouterr().out

    assert "[WARN] ADS-COMPLETE-01 home page is finished" in saida
    assert "coming soon" in saida
    assert codigo == 1


def test_pagina_de_contato_sem_canal_nenhum_nao_passa(server, monkeypatch, capsys):
    """Nenhum teste tinha página de contato EXISTENTE e sem canal, então forçar
    a linha ADS-AUTHOR-02 para OK sobrevivia — e é justamente a linha que o
    EXAMPLES.md mostra como `[MISS]`."""
    base, routes = server
    routes["/"] = (200, HTML, _home())
    routes["/contato"] = (200, HTML, _home().replace("Bancada", "Contato"))

    monkeypatch.setattr("sys.argv", ["check_completeness.py", base + "/"])
    codigo = check_completeness.main()
    saida = capsys.readouterr().out

    assert "[MISS] ADS-AUTHOR-02 contact channel" in saida
    assert "no mailto:, address, form or profile link" in saida
    assert codigo == 1


def test_link_de_nav_quebrado_reprova_a_linha_de_navegacao(server, monkeypatch, capsys):
    """Forçar `nav.status` para OK imprimia `[PASS] navigation` logo acima de
    `-> HTTP 404`: os testes só cobriam "none broken" e o caso truncado."""
    base, routes = server
    routes["/"] = (200, HTML, _home().replace(
        "</body>", '<nav><a href="/sumiu">x</a></nav></body>'))

    monkeypatch.setattr("sys.argv", ["check_completeness.py", base + "/"])
    codigo = check_completeness.main()
    saida = capsys.readouterr().out

    assert "[WARN] ADS-COMPLETE-01 navigation" in saida
    assert "/sumiu -> HTTP 404" in saida
    assert codigo == 1


def test_cli_com_home_ilegivel_reporta_em_vez_de_estourar(server, monkeypatch, capsys):
    """`home_unreadable = report.trust is None` forçado para False dava
    AttributeError e traceback em vez das linhas `[ERR ]`: todo o ramo
    "home ilegível" do CLI do gate era intocado por teste — os que existiam
    exercitavam o módulo, não o script."""
    base, routes = server
    routes["/"] = (503, HTML, "")

    monkeypatch.setattr("sys.argv", ["check_completeness.py", base + "/"])
    codigo = check_completeness.main()
    saida = capsys.readouterr().out

    assert codigo == 1
    assert saida.count("not checked: the home page could not be read") >= 4
    assert "[ERR ] ADS-COMPLETE-01 home page is finished" in saida
    assert "[ERR ] ADS-AUTHOR-02 contact channel" in saida
