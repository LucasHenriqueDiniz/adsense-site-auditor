"""Testes do CLI de conteúdo duplicado.

Como os outros dois CLIs sem teste, este sobrevivia a qualquer mutação: trocar
`result.status` por `Status.OK`, devolver `0` em vez do código de saída, largar
o `--threshold` e iterar uma tupla vazia no lugar dos grupos passavam a suíte
inteira. O servidor real do conftest é o que permite rodar `main()` de ponta a
ponta — a comparação é entre páginas buscadas, e mock nenhum prova isso.
"""

import check_duplicates
import pytest

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
    assert "2 pages at similarity >= 0.6:" in saida
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
    assert "2 pages at similarity >= 0.3:" in saida
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


def test_threshold_fora_da_faixa_da_similaridade_e_recusado(monkeypatch, capsys):
    """Jaccard vive em [0, 1], então um limiar fora dessa faixa transforma
    `score >= threshold` numa constante e o veredito numa invenção.

    As duas pontas eram alcançáveis pela linha de comando. `--threshold 5` não
    pode ser satisfeito por par nenhum, então o relatório imprimia "no
    near-duplicate groups" sobre qualquer entrada — um PASS que nada poderia
    quebrar. `--threshold 0` e abaixo são satisfeitos por todo par, então duas
    páginas sem uma palavra em comum voltavam agrupadas e reprovavam a rodada.
    Nenhum dos dois mediu coisa alguma. `inf` e `nan` caem na mesma comparação
    encadeada: nenhum satisfaz os dois lados dela.
    """
    for valor in ("inf", "nan", "5", "1.5", "0", "-1"):
        monkeypatch.setattr(
            "sys.argv",
            ["check_duplicates.py", "--threshold", valor, "http://127.0.0.1:1/a"],
        )
        with pytest.raises(SystemExit) as saida:
            check_duplicates.main()

        assert saida.value.code == 2, valor
        erro = capsys.readouterr().err
        assert "--threshold must be greater than 0 and at most 1" in erro, valor


def test_threshold_1_e_aceito_e_agrupa_so_o_que_e_identico(server, monkeypatch, capsys):
    """O teto é inclusivo de propósito: `>= 1.0` continua sendo um teste de
    verdade — conjuntos de shingles idênticos passam e diferentes não —, então
    "agrupe só páginas cuja extração é a mesma" é uma pergunta que a ferramenta
    pode legitimamente receber. Uma guarda escrita `< 1` a recusaria."""
    base, routes = server
    routes["/a"] = (200, HTML, pagina(AFIACAO + SECAGEM))
    routes["/b"] = (200, HTML, pagina(AFIACAO + SECAGEM))
    routes["/c"] = (200, HTML, pagina(ACABAMENTO))

    monkeypatch.setattr(
        "sys.argv",
        ["check_duplicates.py", "--threshold", "1", f"{base}/a", f"{base}/b", f"{base}/c"],
    )
    codigo = check_duplicates.main()
    saida = capsys.readouterr().out

    assert "2 pages at similarity >= 1.0:" in saida
    assert f"    {base}/a" in saida
    assert f"    {base}/b" in saida
    assert f"    {base}/c" not in saida
    assert codigo == 1


def test_limiar_minusculo_sai_impresso_inteiro_e_nao_arredondado_a_zero(
    server, monkeypatch, capsys
):
    """A linha de evidência tem de imprimir o limiar aplicado, não zero.

    Um limiar entre 0 e 0.005 é legal — a guarda só exclui o próprio 0 — mas o
    `:.2f` de antes o imprimia como "similarity >= 0.00", que é exatamente a
    string que a guarda cita como sintoma do limiar que agrupa tudo. O veredito
    embaixo era real e a evidência em cima era indistinguível do defeito
    fechado. Aqui as duas páginas compartilham ~34% do texto, então o grupo é
    honesto; o que se pede é que o número impresso seja o número aplicado.

    O segundo valor cobra a razão de o formato ser o `str` do float e não `:g`:
    `str` é o texto mais curto que relê como o mesmo float, então não existe
    limiar que ele imprima diferente do que decidiu o agrupamento. `:g` para em
    6 dígitos significativos e transformaria 0.1234567 em 0.123457.
    """
    base, routes = server
    routes["/a"] = (200, HTML, pagina(AFIACAO + SECAGEM))
    routes["/b"] = (200, HTML, pagina(AFIACAO + ACABAMENTO))

    for valor in ("0.004", "0.1234567"):
        monkeypatch.setattr(
            "sys.argv",
            ["check_duplicates.py", "--threshold", valor, f"{base}/a", f"{base}/b"],
        )
        codigo = check_duplicates.main()
        saida = capsys.readouterr().out

        assert f"2 pages at similarity >= {valor}:" in saida, valor
        assert codigo == 1, valor
