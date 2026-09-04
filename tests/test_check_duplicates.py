"""Testes do CLI de conteúdo duplicado.

Como os outros dois CLIs sem teste, este sobrevivia a qualquer mutação: trocar
`result.status` por `Status.OK`, devolver `0` em vez do código de saída, largar
o `--threshold` e iterar uma tupla vazia no lugar dos grupos passavam a suíte
inteira. O servidor real do conftest é o que permite rodar `main()` de ponta a
ponta — a comparação é entre páginas buscadas, e mock nenhum prova isso.
"""

import check_duplicates

HTML = {"Content-Type": "text/html; charset=utf-8"}

# Três blocos de prosa distintos. Cada página é a soma de dois deles, então a
# similaridade entre duas páginas que compartilham um bloco é conhecida (~0.34)
# e cai dos dois lados do limiar padrão de 0.6 conforme o `--threshold` passado.
AFIACAO = (
    "Afio o formão numa pedra d'água de mil grãos antes de cada encaixe, "
    "porque um gume cego arranca a fibra em vez de cortá-la. "
) * 3
SECAGEM = (
    "Guardo as tábuas em pé por três semanas para o teor de umidade encontrar "
    "o da oficina antes de qualquer corte. "
) * 3
ACABAMENTO = (
    "Passo verniz marítimo em quatro demãos finas e lixo entre elas com lixa "
    "de trezentos e vinte para a superfície ficar lisa. "
) * 3


def pagina(texto):
    return (
        "<html><head><title>Oficina</title></head>"
        f"<body><main><p>{texto}</p></main></body></html>"
    )


def test_paginas_quase_identicas_reprovam_e_o_grupo_e_listado(server, monkeypatch, capsys):
    """O grupo tem de sair impresso, com o limiar padrão de 0.6 nomeado.

    Impede duas voltas: um relatório que reprova sem dizer quais páginas são o
    problema (o laço sobre `result.groups` some e ninguém nota), e uma troca do
    padrão, que o texto do cabeçalho do grupo estampa.
    """
    base, routes = server
    routes["/a"] = (200, HTML, pagina(AFIACAO + SECAGEM))
    routes["/b"] = (200, HTML, pagina(AFIACAO + SECAGEM + " Fim do texto."))

    monkeypatch.setattr("sys.argv", ["check_duplicates.py", f"{base}/a", f"{base}/b"])
    codigo = check_duplicates.main()
    saida = capsys.readouterr().out

    assert "[FAIL] ADS-CONTENT-02 (part) 2 URLs" in saida
    assert "2 pages at similarity >= 0.60:" in saida
    assert f"    {base}/a" in saida
    assert f"    {base}/b" in saida
    assert "no near-duplicate groups" not in saida
    assert codigo == 1


def test_threshold_da_linha_de_comando_decide_o_agrupamento(server, monkeypatch, capsys):
    """`--threshold` tem de chegar ao comparador, não só ao texto do relatório.

    As duas páginas compartilham ~34% do conteúdo: com o argumento largado no
    caminho vale o padrão de 0.6, nenhum grupo é formado e um site que o auditor
    mandou olhar a 0.3 volta com "no near-duplicate groups".
    """
    base, routes = server
    routes["/a"] = (200, HTML, pagina(AFIACAO + SECAGEM))
    routes["/b"] = (200, HTML, pagina(AFIACAO + ACABAMENTO))

    monkeypatch.setattr(
        "sys.argv", ["check_duplicates.py", "--threshold", "0.3", f"{base}/a", f"{base}/b"]
    )
    codigo = check_duplicates.main()
    saida = capsys.readouterr().out

    assert "[FAIL] ADS-CONTENT-02 (part)" in saida
    assert "2 pages at similarity >= 0.30:" in saida
    assert "2 of 2 analyzed pages (100%) are involved in duplication" in saida
    assert codigo == 1


def test_sem_duplicata_o_overlap_continua_MISSING_e_a_saida_nao_e_zero(
    server, monkeypatch, capsys
):
    """A linha permanentemente MISSING é o ponto do script.

    ADS-CONTENT-OVERLAP pede comparação contra os 5 primeiros resultados de
    busca e este script não busca nada; dizer isso em voz alta é o que impede a
    referência de citá-lo como se buscasse. Consequência: nem no site mais limpo
    o processo sai com 0, porque metade do requisito não foi observada.
    """
    base, routes = server
    routes["/a"] = (200, HTML, pagina(AFIACAO))
    routes["/b"] = (200, HTML, pagina(SECAGEM))
    routes["/c"] = (200, HTML, pagina(ACABAMENTO))

    monkeypatch.setattr(
        "sys.argv", ["check_duplicates.py", f"{base}/a", f"{base}/b", f"{base}/c"]
    )
    codigo = check_duplicates.main()
    saida = capsys.readouterr().out

    assert "[PASS] ADS-CONTENT-02 (part) 3 URLs" in saida
    assert "no near-duplicate groups" in saida
    assert "[MISS] ADS-CONTENT-OVERLAP compared against the web" in saida
    assert "this script performs no search" in saida
    assert "Verdict: nothing failed, but something could not be observed." in saida
    assert codigo == 1


def test_pagina_ilegivel_impede_a_aprovacao(server, monkeypatch, capsys):
    """A página que não foi lida pode ser justamente a duplicada.

    O script original engolia falha de fetch num `return None` e anunciava "No
    significant duplicates found" sobre um crawl em que nada tinha sido baixado.
    """
    base, routes = server
    routes["/a"] = (200, HTML, pagina(AFIACAO + SECAGEM))

    monkeypatch.setattr(
        "sys.argv", ["check_duplicates.py", "-v", f"{base}/a", f"{base}/sumiu"]
    )
    codigo = check_duplicates.main()
    saida = capsys.readouterr().out

    assert "[ERR ] ADS-CONTENT-02 (part) 2 URLs" in saida
    assert f"{base}/sumiu (HTTP 404)" in saida
    assert "duplication requires at least 2 analyzable pages, got 1" in saida
    assert "no near-duplicate groups" not in saida
    # `-v` mostra de quantas páginas o veredito fala, que é o que separa
    # "nenhuma duplicata" de "nada foi lido".
    assert "analyzed: 1" in saida
    assert "unanalyzable: 1" in saida
    assert codigo == 1
