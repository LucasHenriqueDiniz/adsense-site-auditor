"""Testes do CLI de completeness.

Os três crashes e o veredito subestimado deste script atravessaram a suíte
inteira porque nenhum dos cinco CLIs tinha teste. O servidor real da fixture é
o que permite rodar `main()` de ponta a ponta.
"""

import check_completeness
import pytest

from adsense_checks.completeness import MAX_PROBED_DIRECTORIES
from adsense_checks.completeness import check_completeness as checar
from adsense_checks.status import Status

PROSA = "Escrevo sobre marcenaria desde 2015 e mantenho este site sozinho. " * 6
HTML = {"Content-Type": "text/html; charset=utf-8"}
# Conexão recusada, na hora. Um argumento recusado não pode chegar à rede, e
# este endereço prova isso sem fazer a suíte esperar.
INALCANCAVEL = "http://127.0.0.1:1/"


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


def test_menu_de_soft_404_sai_do_exit_0_sem_acusar_no_CLI(server, monkeypatch, capsys):
    """O buraco visto de onde o usuário lê. O host serve o template de erro com
    200 para tudo, e a linha de navegação imprimia `[PASS] ... none broken` sobre
    um menu inteiro que não leva a lugar nenhum, com exit 0.

    Sai do exit 0, e como MISS e não FAIL: os mesmos três links seriam
    produzidos por um WordPress com uma categoria vazia, uma tag vazia e uma
    página 7 de 6 — todas rotas que funcionam. O relatório imprime os endereços
    e diz que ninguém os verificou, que é o que foi observado."""
    base, routes = server
    menu = '<nav><a href="/a">a</a><a href="/b">b</a><a href="/c">c</a></nav>'
    routes["/"] = (200, HTML, _home().replace("</body>", f"{menu}</body>"))
    routes.default = (200, HTML, _home().replace("Bancada", "Página não encontrada"))

    monkeypatch.setattr("sys.argv", ["check_completeness.py", base + "/"])
    codigo = check_completeness.main()
    saida = capsys.readouterr().out

    assert "[MISS] ADS-COMPLETE-01 navigation" in saida
    assert "[FAIL] ADS-COMPLETE-01 navigation" not in saida
    assert "unverified" in saida
    assert "none broken" not in saida
    assert codigo == 1


def test_o_CLI_nao_afirma_que_nenhum_link_serviu_a_pagina_de_erro(server, monkeypatch, capsys):
    """A linha de aprovação prometia uma prova que o código não dá.

    Um host com DOIS templates de "não encontrado" — o CMS montado em /blog/ tem
    o seu — casa com a impressão digital de um e com a do outro nunca. Sobre três
    posts inexistentes sob /blog/ o CLI imprimia `[PASS] ... none broken; this
    host answers 200 for URLs it does not have, and none of these links served
    the page it answers with` e saía 0. Nenhuma dessas três URLs foi observada
    levando a lugar nenhum, e nenhuma foi observada levando a algum.

    Antes do commit a mesma linha dizia só "none broken" — mais fraca e honesta.
    Agora não há linha nenhuma a calibrar: num host que responde 200 para uma URL
    que não tem, todo 200 é registrado como não verificado, então este ramo não é
    alcançável ali."""
    base, routes = server
    menu = (
        '<nav><a href="/blog/a">a</a><a href="/blog/b">b</a><a href="/blog/c">c</a></nav>'
    )
    routes["/"] = (200, HTML, _home().replace("</body>", f"{menu}</body>"))

    def dois_templates(metodo, caminho):
        # O CMS em /blog/ responde com o erro DELE; o resto do site, com o do
        # servidor. Os dois com 200, os dois estáveis.
        de_quem = "blog" if caminho.startswith("/blog/") else "site"
        return (200, HTML, _home().replace("Bancada", f"Nada encontrado ({de_quem})"))

    routes.default = dois_templates

    monkeypatch.setattr("sys.argv", ["check_completeness.py", base + "/"])
    codigo = check_completeness.main()
    saida = capsys.readouterr().out

    assert "none of these links served the page it answers with" not in saida
    assert "none broken" not in saida
    assert "[MISS] ADS-COMPLETE-01 navigation" in saida
    assert codigo == 1


def test_o_CLI_diz_por_que_o_menu_limpo_e_limpo(server, monkeypatch, capsys):
    """Uma aprovação tem que nomear o que a sustenta. "None broken" sobre um host
    que responde 200 para toda URL que não tem é o próprio buraco do soft 404, e
    a linha não distinguia um host do outro."""
    base, routes = server
    routes["/"] = (200, HTML, _home().replace("</body>", '<nav><a href="/a">a</a></nav></body>'))
    routes["/a"] = (200, HTML, _home().replace("Bancada", "Sobre"))

    monkeypatch.setattr("sys.argv", ["check_completeness.py", base + "/"])
    check_completeness.main()
    saida = capsys.readouterr().out

    assert "all 1 navigation links followed, none broken" in saida
    # Nomeia os dois códigos, não a faixa: `>= 400` engolia 401, 403, 429 e 5xx,
    # que recusam a requisição em vez de roteá-la, e a frase afirmava sobre
    # roteamento uma coisa medida numa recusa.
    assert "answers 404 or 410 for a URL that does not exist" in saida


def test_o_CLI_nao_conta_link_inclassificavel_como_bom_nem_como_quebrado(
    server, monkeypatch, capsys
):
    """O terceiro desfecho impresso. O host é soft 404 provado e o template de
    erro ecoa a URL pedida, então não há como reconhecer os links — e o
    relatório tem que dizer isso em vez de escolher um lado."""
    base, routes = server
    menu = '<nav><a href="/a">a</a><a href="/b">b</a></nav>'
    routes["/"] = (200, HTML, _home().replace("</body>", f"{menu}</body>"))
    routes.default = lambda metodo, caminho: (
        200, HTML, _home().replace("Bancada", f"Nada em {caminho}"),
    )

    monkeypatch.setattr("sys.argv", ["check_completeness.py", base + "/"])
    codigo = check_completeness.main()
    saida = capsys.readouterr().out

    assert "[MISS] ADS-COMPLETE-01 navigation" in saida
    assert "neither working nor broken" in saida
    assert "none broken" not in saida
    # MISSING sai do exit 0 sem afirmar defeito nenhum: "nada falhou, mas algo
    # não pôde ser observado".
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


def test_timeout_fora_da_faixa_do_socket_e_recusado_na_linha_de_comando(monkeypatch, capsys):
    """Este script já recusava `--nav-limit 0` e ainda quebrava no `--timeout`.

    Em zero, com o ValueError que a urllib3 levanta; em `inf` — a grafia
    plausível de "sem timeout", e no que `Infinity` e `1e400` também se
    transformam sob `type=float` — com o OverflowError de `socket.settimeout`,
    porque `not ... > 0` é falso para o infinito. `fetch` repassa as duas de
    propósito, então a checagem de fronteira mora aqui, ao lado da outra: saída
    2 e a razão no stderr, sem nenhuma requisição no meio.
    """
    for valor in ("inf", "Infinity", "1e400", "nan", "0", "-5", "9223372036.854776"):
        monkeypatch.setattr(
            "sys.argv", ["check_completeness.py", "--timeout", valor, INALCANCAVEL]
        )
        with pytest.raises(SystemExit) as saida:
            check_completeness.main()

        assert saida.value.code == 2, valor
        erro = capsys.readouterr().err
        assert "--timeout must be greater than 0 and less than" in erro, valor


def test_o_maior_timeout_que_o_socket_aceita_nao_e_recusado(server, monkeypatch, capsys):
    """O teto é o do mecanismo e nem um float abaixo dele.

    9223372036.854774 é o último valor que `socket.settimeout` segura; o
    seguinte, 9223372036.854776, é 2**63 nanossegundos e estoura. Cada cópia da
    constante precisa da sua própria prova: sem esta, baixar o teto deste
    arquivo pela metade não quebrava nada, e a guarda passaria a recusar entrada
    que a máquina honraria.
    """
    base, routes = server
    routes["/"] = (200, HTML, _home())

    monkeypatch.setattr(
        "sys.argv", ["check_completeness.py", "--timeout", "9223372036.854774", base + "/"]
    )
    check_completeness.main()

    assert "/" in [caminho for _metodo, caminho, _headers in routes.received]


def test_linha_de_navegacao_nomeia_os_links_do_diretorio_que_o_teto_recusou(
    server, monkeypatch, capsys
):
    """A lista `unmeasured` tem que chegar ao papel, não só ao veredito.

    Um link que respondeu 200 de um diretório que ninguém mediu é MISSING, e o
    achado agregado já dizia isso — mas a linha `navigation` montava sua
    evidência a partir de `broken`, `same_as_not_found`, `unresolved` e
    `unverified`, e cairia no ramo "all N navigation links followed, none
    broken" com a lista nova invisível. `render` conta a Line, e uma Line que
    esconde metade do que decidiu é como este pacote imprimia PASS sob a própria
    lista de problemas.

    O menu tem um diretório a mais do que `MAX_PROBED_DIRECTORIES` permite, que
    é a única forma de o teto morder num site de um host só: a âncora `/` toma o
    primeiro lugar e os `MAX_PROBED_DIRECTORIES - 1` seguintes tomam o resto.
    Todos os links respondem 200 e o servidor 404 as sondas, então nada aqui
    está quebrado — o que o relatório precisa dizer é QUANTO ficou sem medir e
    ONDE, para o operador poder apontar uma segunda corrida ao subdiretório.
    """
    base, routes = server
    diretorios = [f"/d{i}/" for i in range(MAX_PROBED_DIRECTORIES + 2)]
    itens = "".join(f"<a href='{d}'>{d}</a>" for d in diretorios)
    routes["/"] = (
        200, HTML,
        "<html><head><title>Casa</title></head>"
        f"<body><h1>Casa</h1><p>{PROSA}</p><nav>{itens}</nav></body></html>",
    )
    for d in diretorios:
        routes[d] = (200, HTML, _home(corpo=f"Pagina {d}. {PROSA}"))

    monkeypatch.setattr("sys.argv", ["check_completeness.py", base + "/", "-v"])
    codigo = check_completeness.main()
    saida = capsys.readouterr().out

    # Os três últimos diretórios ficaram fora do teto: 1 âncora + 7 medidos.
    recusados = diretorios[MAX_PROBED_DIRECTORIES - 1:]
    for d in recusados:
        assert f"{base}{d}" in saida
    assert "none broken" not in saida
    assert "unmeasured: 3" in saida
    assert f"refused_directories: {recusados}" in saida
    assert codigo == 1
