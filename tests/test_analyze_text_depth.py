"""Testes do CLI de profundidade de texto.

O script não tinha teste e nada o importava, então TODA mutação nele sobrevivia
à suíte: trocar o veredito de cada página por `Status.OK`, devolver `0` em vez
do código de saída, largar o `--min-words` no caminho e trocar o ID do requisito
por um inventado passavam os 335 testes sem uma falha. O servidor real do
conftest é o que permite rodar `main()` de ponta a ponta.
"""

import analyze_text_depth

HTML = {"Content-Type": "text/html; charset=utf-8"}
# Dez palavras exatas, para a contagem impressa ser conferível em vez de aceita.
FRASE = "Escrevo sobre marcenaria desde 2015 e mantenho este site sozinho. "
# Dezoito palavras de chrome: 1 no h1, 8 no menu, 9 no rodapé.
CABECALHO = (
    "<header><h1>Bancada</h1>"
    "<nav>Início Sobre Contato Guias Ferramentas Oficina Cursos Loja</nav></header>"
)
RODAPE = "<footer>Todos os direitos reservados a esta oficina de marcenaria</footer>"
PALAVRAS_DE_CHROME = 18
# Porta 1 não escuta: um erro de transporte, que é o caso em que o script não
# tem contagem nenhuma para dar.
INALCANCAVEL = "http://127.0.0.1:1/"


def pagina(palavras):
    """Página com `palavras` palavras em <main> e 18 de chrome fora dele."""
    return (
        "<html><head><title>Bancada</title></head><body>"
        f"{CABECALHO}<main><p>{FRASE * (palavras // 10)}</p></main>{RODAPE}"
        "</body></html>"
    )


def test_pagina_ilegivel_e_ERROR_e_nunca_uma_contagem_de_palavras(monkeypatch, capsys):
    """Uma pergunta sem resposta não é um requisito satisfeito.

    Impede o retorno do balde OK do script original, onde fetch falho entrava
    junto das páginas medidas — e do `return 0` que anunciaria sucesso sobre uma
    URL que ninguém conseguiu ler.
    """
    monkeypatch.setattr("sys.argv", ["analyze_text_depth.py", INALCANCAVEL])
    codigo = analyze_text_depth.main()
    saida = capsys.readouterr().out

    assert f"[ERR ] ADS-CONTENT-03 {INALCANCAVEL}" in saida
    assert "words in main content" not in saida
    assert "Verdict: worst status is ERROR" in saida
    assert codigo == 1


def test_pagina_profunda_aprova_e_o_codigo_de_saida_e_zero(server, monkeypatch, capsys):
    """O outro lado do código de saída: sem ele, `return 0` não seria mutação
    nenhuma e a asserção acima passaria por acidente."""
    base, routes = server
    routes["/guia"] = (200, HTML, pagina(600))

    monkeypatch.setattr("sys.argv", ["analyze_text_depth.py", f"{base}/guia"])
    codigo = analyze_text_depth.main()
    saida = capsys.readouterr().out

    assert f"[PASS] ADS-CONTENT-03 {base}/guia" in saida
    assert "600 words: at or above the configured bar (>= 450 words)" in saida
    assert "Verdict: every check observed its condition and passed." in saida
    assert codigo == 0


def test_uma_linha_por_url_carregando_ADS_CONTENT_03(server, monkeypatch, capsys):
    """Uma linha por URL, cada uma com o ID que o Completeness Gate procura.

    Um ID trocado (ou ausente) é lido pelo gate como um requisito que ninguém
    checou. E ADS-COMPLETE-02 não pode aparecer: o script mede uma página por
    vez contra um limiar de revisão e não conta artigo nenhum.
    """
    base, routes = server
    routes["/guia"] = (200, HTML, pagina(600))
    routes["/nota"] = (200, HTML, pagina(400))

    monkeypatch.setattr(
        "sys.argv",
        ["analyze_text_depth.py", f"{base}/guia", f"{base}/nota", f"{base}/sumiu"],
    )
    codigo = analyze_text_depth.main()
    saida = capsys.readouterr().out

    assert saida.count("ADS-CONTENT-03") == 3
    assert "ADS-COMPLETE-02" not in saida
    assert "3 checks:" in saida
    # 404 é ausência observada, não erro de leitura — e mesmo assim não é passe.
    assert f"[MISS] ADS-CONTENT-03 {base}/sumiu" in saida
    assert "HTTP 404" in saida
    assert codigo == 1


def test_min_words_da_linha_de_comando_decide_a_faixa(server, monkeypatch, capsys):
    """`--min-words` tem de chegar ao medidor, não só ao título do relatório.

    Com o argumento largado no caminho, a mesma página de 400 palavras sai como
    `borderline` contra o padrão de 300 — um "não falhou nada" impresso sobre a
    barra de 1200 palavras que o auditor pediu.
    """
    base, routes = server
    routes["/guia"] = (200, HTML, pagina(400))

    monkeypatch.setattr(
        "sys.argv", ["analyze_text_depth.py", "--min-words", "1200", f"{base}/guia"]
    )
    codigo = analyze_text_depth.main()
    saida = capsys.readouterr().out

    assert "Content depth (min 1200 words)" in saida
    assert f"[WARN] ADS-CONTENT-03 {base}/guia" in saida
    assert "400 words: below the configured threshold (1200 words)" in saida
    assert "borderline" not in saida
    assert codigo == 1


def test_verbose_mostra_o_conteudo_principal_separado_do_chrome(server, monkeypatch, capsys):
    """A contagem reportada é a do conteúdo principal, com o chrome de fora.

    As três medidas são impressas juntas de propósito: `words` sozinho não deixa
    ninguém ver que o menu e o rodapé ficaram fora, e é exatamente essa a
    pergunta do ADS-CONTENT-03.
    """
    base, routes = server
    routes["/guia"] = (200, HTML, pagina(600))

    monkeypatch.setattr("sys.argv", ["analyze_text_depth.py", "-v", f"{base}/guia"])
    analyze_text_depth.main()
    saida = capsys.readouterr().out

    assert "words: 600" in saida
    assert f"total_words: {600 + PALAVRAS_DE_CHROME}" in saida
    assert "main_ratio: 0.971" in saida


def test_min_words_1_e_aceito(server, monkeypatch, capsys):
    """`args.min_words < 1` rejeita o 0; com `<=` recusaria o 1, que é válido."""
    base, rotas = server
    rotas["/"] = (200, HTML, "<html><body><main><p>uma duas tres</p></main></body></html>")

    monkeypatch.setattr("sys.argv", ["analyze_text_depth.py", "--min-words", "1", base + "/"])
    analyze_text_depth.main()
    assert "min 1 words" in capsys.readouterr().out
