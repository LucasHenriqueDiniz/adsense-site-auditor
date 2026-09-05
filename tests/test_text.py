"""Testes da extração de texto e da medição de profundidade.

Cada teste abaixo corresponde a um defeito reproduzido nos extratores antigos
(scripts/check_duplicates.py e scripts/analyze_text_depth.py). O nome diz o que
o teste impede de voltar.
"""

import pytest

from adsense_checks.status import Status
from adsense_checks.text import (
    MIN_ARTICLE_WORDS,
    borderline_ceiling,
    classify_depth,
    describe_depth,
    extract_text,
    looks_javascript_rendered,
    main_content_text,
    measure_depth,
    measure_url,
    word_count,
)

# HTML do relatório de defeitos, palavra por palavra. É a página mais banal
# possível — e o extrator antigo devolvia '' para ela.
QUIZ = (
    '<html><head><meta charset="utf-8"><link rel="stylesheet" href="/s.css">'
    "<title>Quiz</title></head><body><h1>Which Dog Breed Are You?</h1>"
    "<p>Answer twenty questions...</p></body></html>"
)


def _palavras(n: int) -> str:
    """n palavras idênticas: a contagem esperada é exatamente n."""
    return " ".join(["palavra"] * n)


def _pagina_com_chrome(corpo: str) -> str:
    """Página cujo corpo editorial é minúsculo e o chrome é enorme.

    Reproduz o defeito da skip list curta: <title>, <header> e <aside> entravam
    no word_count como se fossem conteúdo.
    """
    menu = " ".join(f'<a href="/{i}">Item {i}</a>' for i in range(200))
    lateral = " ".join(f"<li>Link {i}</li>" for i in range(150))
    return (
        "<html><head><title>Nove palavras de titulo que nao sao conteudo</title></head>"
        f"<body><header><nav>{menu}</nav></header>"
        f"<aside><ul>{lateral}</ul></aside>"
        f"<main><p>{corpo}</p></main>"
        "<footer>Todos os direitos reservados</footer></body></html>"
    )


def _artigo(n: int) -> str:
    return (
        "<html><head><title>Artigo</title></head><body>"
        "<nav>Home Sobre Contato</nav>"
        f"<main><h1>Titulo</h1><p>{_palavras(n)}</p></main>"
        "<footer>Rodape</footer></body></html>"
    )


# --- o blocker: elementos void engoliam o documento inteiro -----------------


def test_meta_e_link_no_head_nao_engolem_o_documento():
    """O BLOCKER. `meta` e `link` estavam na skip list e não têm tag de
    fechamento, então o contador de nível nunca voltava a zero e get_text()
    devolvia '' para qualquer página real."""
    texto = extract_text(QUIZ)
    assert "Which Dog Breed Are You?" in texto
    assert "Answer twenty questions" in texto
    assert word_count(texto) > 0


def test_paginas_iguais_produzem_texto_igual_e_nao_vazio():
    """A consequência do blocker: o detector de duplicatas comparava '' com ''
    e aprovava um site 100% duplicado."""
    outra = QUIZ.replace('href="/s.css"', 'href="/t.css"')
    assert extract_text(outra) == extract_text(QUIZ) != ""


def test_elementos_void_no_corpo_nao_desligam_a_extracao():
    html = "<body><p>um<br>dois<img src='x.png'>tres</p><hr><p>quatro</p><input value='y'></body>"
    assert extract_text(html) == "um dois tres quatro"


def test_void_auto_fechado_nao_e_empilhado():
    """<meta />, <br/> e <link/> com barra passavam por handle_startendtag."""
    html = (
        '<html><head><meta charset="utf-8" /><link rel="icon" href="/i.png"/></head>'
        "<body><p>corpo</p></body></html>"
    )
    assert extract_text(html) == "corpo"


# --- o que não é texto visível ---------------------------------------------


def test_head_script_e_style_ficam_fora_do_texto():
    html = (
        "<html><head><title>Titulo do head</title>"
        "<style>body{color:red}</style></head>"
        "<body><p>visivel</p><script>var x = 1;</script></body></html>"
    )
    texto = extract_text(html)
    assert texto == "visivel"
    assert "Titulo" not in texto
    assert "color" not in texto


def test_noscript_svg_e_template_sao_descartados():
    html = (
        "<body><noscript>ative o javascript</noscript>"
        "<svg><text>rotulo do grafico</text></svg>"
        "<template><p>modelo</p></template>"
        "<p>conteudo real</p></body>"
    )
    assert extract_text(html) == "conteudo real"


def test_fechamento_orfao_nao_reativa_texto_descartado():
    """O contador antigo era decrementado por qualquer </tag> da skip list, então
    um fechamento órfão desligava o skip no meio do documento."""
    html = "<body><p>antes</p></section><script>var x = 1</script><p>depois</p></body>"
    texto = extract_text(html)
    assert texto == "antes depois"
    assert "var" not in texto


def test_tag_descartada_sem_fechamento_no_head_nao_zera_o_corpo():
    """Uma tag de skip aberta e nunca fechada deixava o contador preso e apagava
    todo o resto do documento. Aqui o <template> fica pendente e a abertura de
    <body> abandona o que sobrou acima dela.

    (<title> não serve de exemplo: é RCDATA, então um <title> sem fechamento
    engole o documento também num navegador. Não é defeito nosso corrigir.)
    """
    html = "<html><head><template><p>oculto<body><p>corpo visivel</p></body></html>"
    assert extract_text(html) == "corpo visivel"


def test_nav_sem_fechamento_nao_apaga_um_artigo_de_800_palavras():
    """Reprodução literal do defeito: o </nav> faltando fazia um artigo de 800
    palavras ser medido como 0 e reportado como THIN."""
    html = (
        "<html><body><nav>Home About Contact<main><p>"
        + _palavras(800)
        + "</p></main></body></html>"
    )
    assert word_count(main_content_text(html)) == 800
    assert "Home" not in main_content_text(html)
    assert measure_depth(html).status is Status.OK


# --- fronteiras de palavra --------------------------------------------------


def test_tag_inline_no_meio_da_palavra_nao_cria_palavra_nova():
    """O extrator antigo juntava os nós com ' ' e contava 6 palavras onde o
    leitor vê 3 — erro sistemático e sempre para cima."""
    html = "<p>Hyper<em>text</em> mark<b>up</b> lan<i>guage</i></p>"
    assert extract_text(html) == "Hypertext markup language"
    assert word_count(extract_text(html)) == 3


def test_fronteira_de_bloco_separa_palavras():
    """O oposto do teste acima: sem separador em bloco, 'um' e 'dois' virariam
    'umdois', uma palavra que não existe na página."""
    assert extract_text("<p>um</p><p>dois</p>") == "um dois"
    assert extract_text("<li>um</li><li>dois</li>") == "um dois"


def test_texto_curto_nao_e_descartado():
    """O filtro `len(text) > 2` apagava preços, notas e resultados de quiz, e o
    join com '\\n' partia palavras quebradas por markup inline."""
    assert extract_text("<p>O preço é <b>R$</b> 5 <i>ou</i> 10</p>") == "O preço é R$ 5 ou 10"
    assert extract_text("<p>dupli<b>cate</b></p>") == "duplicate"


def test_texto_final_com_e_comercial_nao_fica_no_buffer():
    """feed() sem close(): o parser segurava o último bloco de texto quando ele
    tinha um '&' solto nos 34 caracteres finais, e ele nunca chegava em
    handle_data."""
    texto = extract_text("<p>Our lab does serious research in chemistry physics and R&D")
    assert "R&D" in texto
    assert word_count(texto) >= 10


def test_entidades_e_espaco_inquebravel_viram_texto_normal():
    assert extract_text("<p>Caf&eacute; &amp; leite</p>") == "Café & leite"
    assert extract_text("<p>um&nbsp;dois</p>") == "um dois"


# --- robustez ---------------------------------------------------------------


def test_html_malformado_nao_trava_nem_perde_texto():
    html = "<div><p>um<p>dois<div>tres</span></b></div><em>quatro"
    texto = extract_text(html)
    for palavra in ("um", "dois", "tres", "quatro"):
        assert palavra in texto


def test_aninhamento_profundo_sem_fechamento_nao_estoura_a_pilha():
    """2000 <div> abertos e nenhum fechado: uma varredura recursiva estouraria o
    limite de recursão do CPython."""
    assert extract_text("<div>" * 2000 + "palavra") == "palavra"


def test_entrada_vazia_ou_sem_texto_devolve_string_vazia():
    assert extract_text("") == ""
    assert extract_text("<html><head><title>x</title></head><body></body></html>") == ""


def test_bytes_em_vez_de_str_viram_ERROR_e_nunca_uma_contagem():
    """`except Exception: pass` devolvia o texto parcial como se fosse a página
    inteira. Passar resp.content no lugar de resp.text é o acidente típico."""
    d = measure_depth(b"<html><body><p>corpo</p></body></html>")
    assert d.status is Status.ERROR
    assert "parse did not complete" in d.reason


# --- conteúdo principal -----------------------------------------------------


def test_chrome_da_pagina_nao_conta_como_conteudo_editorial():
    """Reprodução: corpo editorial de 2 palavras, mais título, menu e sidebar.
    O extrator antigo somava 360 palavras e classificava BORDERLINE."""
    html = _pagina_com_chrome("Buy now.")
    assert word_count(main_content_text(html)) == 2
    # O que o extrator antigo media na mesma página:
    assert word_count(extract_text(html)) > 300
    assert measure_depth(html).status is Status.WARNING


def test_artigo_mais_longo_vence_entre_varios():
    """Numa página índice, concatenar todos os <article> faria a soma das
    chamadas parecer um artigo substancial."""
    html = (
        "<body>"
        f"<article><p>{_palavras(40)}</p></article>"
        f"<article><p>{_palavras(300)}</p></article>"
        f"<article><p>{_palavras(25)}</p></article>"
        "</body>"
    )
    assert word_count(main_content_text(html)) == 300


def test_sem_main_nem_article_vence_o_bloco_com_paragrafos():
    html = (
        "<body>"
        '<div id="menu"><a>Home</a> <a>Sobre</a> <a>Contato</a> <a>Blog</a></div>'
        f'<div id="post"><p>{_palavras(120)}</p><p>{_palavras(80)}</p></div>'
        "</body>"
    )
    principal = main_content_text(html)
    assert word_count(principal) == 200
    assert "Home" not in principal


def test_sem_estrutura_nenhuma_cai_no_corpo_inteiro():
    """Fallback documentado: sem <main>, <article> nem <p>, devolve o corpo. O
    que não pode acontecer é devolver '' e a página parecer vazia."""
    html = "<html><body>texto solto sem nenhuma estrutura</body></html>"
    assert main_content_text(html) == "texto solto sem nenhuma estrutura"


def test_razao_de_conteudo_principal_separa_corpo_de_chrome():
    d = measure_depth(_pagina_com_chrome(_palavras(400)))
    assert d.words == 400
    assert d.total_words > 1000
    assert 0.0 < d.main_ratio < 0.5


# --- contagem de palavras ---------------------------------------------------


@pytest.mark.parametrize(
    "texto,esperado",
    [
        ("coração intuição não é ação", 5),
        ("guarda-chuva", 1),
        ("d'água", 1),
        ("São Paulo tem 12 milhões", 5),
        ("Olá, mundo! Tudo bem?", 4),
        ("R$ 5,00", 2),
        ("", 0),
        ("   \n  ", 0),
    ],
)
def test_contagem_de_palavras_em_portugues_e_ingles(texto, esperado):
    """Uma classe [a-zA-Z]+ cortaria 'coração' em 'cora' e 'o'."""
    assert word_count(texto) == esperado


def test_hifen_invisivel_nao_parte_a_palavra():
    assert word_count(extract_text("<p>super&shy;calif&shy;ragil</p>")) == 1


# --- classificação ----------------------------------------------------------


def test_pagina_sem_conteudo_e_FAIL_e_nunca_OK():
    """ADS-CONTENT-04 trata 'zero palavras de conteúdo principal' como achado."""
    assert classify_depth(0) is Status.FAIL
    assert measure_depth("<html><body></body></html>").status is Status.FAIL


@pytest.mark.parametrize(
    "palavras,esperado",
    [
        (1, Status.WARNING),
        (299, Status.WARNING),
        (300, Status.INFO),
        (449, Status.INFO),
        (450, Status.OK),
        (5000, Status.OK),
    ],
)
def test_faixas_de_profundidade_contra_o_limiar(palavras, esperado):
    assert classify_depth(palavras, min_words=300) is esperado


def test_rotulos_das_faixas_derivam_dos_mesmos_limiares():
    """O resumo antigo anunciava 'BORDERLINE (300-450)' e 'OK (>450)' enquanto o
    código classificava 450 como OK: uma página de 450 palavras caía numa faixa
    que o próprio relatório declarava impossível."""
    assert borderline_ceiling(300) == 450
    assert classify_depth(449, min_words=300) is Status.INFO
    assert "300-449" in describe_depth(449, min_words=300)
    assert classify_depth(450, min_words=300) is Status.OK
    assert ">= 450" in describe_depth(450, min_words=300)


def test_mensagem_nao_afirma_violacao_de_politica():
    """O limiar de palavras é escolha do auditor, não linha da política: a saída
    antiga cravava 'may trigger ADS-CONTENT-03 or ADS-CONTENT-04'."""
    texto = describe_depth(100, min_words=300)
    assert "configured threshold" in texto
    assert "ADS-" not in texto


def test_limiar_de_artigo_do_ads_complete_02():
    """ADS-COMPLETE-02 pede 1200+ palavras por artigo; o mesmo artigo de 800
    palavras passa no limiar de conteúdo raso e reprova no de artigo."""
    html = _artigo(800)
    assert MIN_ARTICLE_WORDS == 1200
    assert measure_depth(html).status is Status.OK
    assert measure_depth(html, min_words=MIN_ARTICLE_WORDS).status is Status.WARNING


# --- página renderizada no cliente ------------------------------------------

SPA = (
    "<html><head><title>App</title></head><body>"
    '<div id="root"></div><script src="/main.js"></script></body></html>'
)


def test_casca_de_spa_nao_e_classificada_como_conteudo_raso():
    """A casca de um app React não é uma página rasa: é uma página que este
    checador não consegue ler. THIN seria uma resposta inventada."""
    assert looks_javascript_rendered(SPA) is True
    d = measure_depth(SPA)
    assert d.status is Status.ERROR
    assert "client-rendered" in d.reason


def test_pagina_renderizada_no_servidor_com_mesmo_mount_nao_e_confundida():
    html = (
        "<html><body>"
        f'<div id="root"><main><p>{_palavras(500)}</p></main></div>'
        '<script src="/main.js"></script></body></html>'
    )
    assert looks_javascript_rendered(html) is False
    assert measure_depth(html).status is Status.OK


# --- medição a partir da rede -----------------------------------------------


@pytest.mark.parametrize(
    "status_http,esperado",
    [(403, Status.FAIL), (404, Status.MISSING), (500, Status.FAIL)],
)
def test_fetch_que_falha_nunca_vira_aprovacao(server, status_http, esperado):
    """O BLOCKER do analyze_text_depth: páginas com risk=ERROR caíam no balde OK
    do resumo, então uma auditoria em que nada foi lido reportava
    'OK (>450 words): 3 pages'."""
    base, routes = server
    routes["/p"] = (status_http, {"Content-Type": "text/html"}, "")
    d = measure_url(base + "/p")
    assert d.status is esperado
    assert d.status.is_bad is True
    assert d.words == 0
    assert str(status_http) in d.reason


def test_erro_de_rede_vira_ERROR_com_a_razao():
    d = measure_url("http://127.0.0.1:1/", timeout=2)
    assert d.status is Status.ERROR
    assert d.reason
    assert d.words == 0


def test_resposta_nao_html_nao_e_analisada_como_texto(server):
    """Um PDF servido com 200 era alimentado no HTMLParser e virava uma 'página'
    de comparação."""
    base, routes = server
    routes["/doc.pdf"] = (200, {"Content-Type": "application/pdf"}, "%PDF-1.4 lixo binario")
    d = measure_url(base + "/doc.pdf")
    assert d.status is Status.ERROR
    assert "application/pdf" in d.reason
    assert d.words == 0


def test_resposta_sem_content_type_nao_e_adivinhada(server):
    base, routes = server
    routes["/x"] = (200, {}, "<html><body><p>talvez html</p></body></html>")
    d = measure_url(base + "/x")
    assert d.status is Status.ERROR
    assert "no Content-Type" in d.reason


def test_pagina_boa_e_medida_pela_url_final(server):
    """O terminal antigo imprimia a URL pedida e o relatório gravava a URL final:
    duas saídas nomeando URLs diferentes para a mesma linha."""
    base, routes = server
    routes["/"] = (301, {"Location": "/artigo"}, "")
    routes["/artigo"] = (
        200,
        {"Content-Type": "text/html; charset=utf-8"},
        _artigo(500).replace("palavra palavra", "coração palavra", 1),
    )
    d = measure_url(base + "/")
    assert d.status is Status.OK
    assert d.url.endswith("/artigo")
    assert d.words == 501  # as 500 do corpo mais o H1, que está dentro do <main>
    assert "coração" in d.text  # o charset da resposta chegou inteiro até aqui
    assert "Home" not in d.text


# ---------------------------------------------------------------------------
# Encoding: o blocker que a revisão adversarial encontrou.
#
# requests decodifica um corpo text/* sem charset como ISO-8859-1 (padrão da RFC
# 2616, que o HTML5 substituiu por "leia a declaração do documento"). Uma página
# UTF-8 com acento chega como mojibake, e o contador de palavras infla: "coração"
# vira "coraÃ§Ã£o", que o regex de palavra lê como três. Uma página rasa passa a
# medir o dobro e limpa um limiar que deveria ter perdido.
# ---------------------------------------------------------------------------

TEXTO_PT = (
    "A situação da manutenção não é simples. "
    "Precisão, coração e informação são palavras acentuadas. "
    "Avaliação, construção, três, você, também."
)


def _pagina(corpo: str, charset: str = "utf-8") -> str:
    return (
        f"<html><head><meta charset='{charset}'><title>t</title></head>"
        f"<body><main><p>{corpo}</p></main></body></html>"
    )


def test_utf8_sem_charset_no_header_nao_infla_a_contagem(server):
    """O blocker: text/html sem charset fazia acento virar mojibake."""
    base, routes = server
    html = _pagina(TEXTO_PT)
    # Sem 'charset=' no Content-Type — é onde o requests cai no ISO-8859-1.
    routes["/pt"] = (200, {"Content-Type": "text/html"}, html)

    medido = measure_url(base + "/pt")
    esperado = word_count(main_content_text(html))

    assert medido.words == esperado, (
        f"mojibake inflou a contagem: {medido.words} em vez de {esperado}"
    )
    assert "coraÃ" not in medido.text
    assert "coração" in medido.text


def test_charset_do_header_e_respeitado_quando_existe(server):
    base, routes = server
    html = _pagina(TEXTO_PT)
    routes["/pt"] = (200, {"Content-Type": "text/html; charset=utf-8"}, html)
    medido = measure_url(base + "/pt")
    assert "coração" in medido.text
    assert medido.words == word_count(main_content_text(html))


def test_charset_declarado_que_nao_decodifica_vira_motivo_registrado(server):
    """Página que nomeia uma codificação que seus próprios bytes não honram.

    O texto extraído dali é palpite, e contagem sobre palpite é a aprovação que
    ninguém observou — então o motivo tem que aparecer.
    """
    base, routes = server
    # Declara utf-16 mas o corpo é utf-8; utf-16 não decodifica bytes ímpares.
    html = _pagina(TEXTO_PT, charset="utf-16")
    routes["/ruim"] = (200, {"Content-Type": "text/html"}, html)
    medido = measure_url(base + "/ruim")
    assert medido.reason and "utf-16" in medido.reason.lower()


def test_pagina_ascii_sem_charset_nao_e_alterada(server):
    """A correção não pode mexer em quem já estava certo."""
    base, routes = server
    corpo = "The quick brown fox jumps over the lazy dog. " * 40
    html = _pagina(corpo)
    routes["/en"] = (200, {"Content-Type": "text/html"}, html)
    medido = measure_url(base + "/en")
    assert medido.words == word_count(main_content_text(html))


def test_conteudo_principal_nao_isolado_e_ERROR_na_medicao():
    """`main_content_text` devolvendo o body inteiro já era testado; o que não
    era é que `measure_depth` escala isso para ERROR. Sem a escalação, uma
    contagem sobre o menu e o rodapé volta como medição boa — e o
    `analyze_text_depth` sai com 0."""
    html = "<html><body><p>" + "palavra " * 500 + "</p></body></html>"
    d = measure_depth(html)
    assert d.words >= 500  # tem texto de sobra: não é o caso de página vazia
    assert d.status is Status.ERROR
    assert "main content not isolated" in d.reason


def test_a_casca_de_spa_e_reconhecida_ate_25_palavras():
    """JS_SHELL_MAX_WORDS, fixado pelo valor nos dois lados da fronteira. Acima
    dela a página tem conteúdo servido e é uma página rasa de verdade, não uma
    casca que este auditor não consegue ler."""
    def casca(n):
        return ('<html><head><script src="/b.js"></script></head><body>'
                # Sem dígito no token: `palavra0` conta como DUAS palavras,
                # porque o contador separa letras de dígitos.
                f'<p>{" ".join(["palavra"] * n)}</p>'
                '<div id="root"></div></body></html>')

    assert looks_javascript_rendered(casca(24)) is True
    assert looks_javascript_rendered(casca(26)) is False


def test_o_charset_declarado_e_procurado_so_no_primeiro_kib():
    """_DECLARATION_WINDOW. O HTML5 exige a declaração nos primeiros 1024 bytes,
    e procurar além disso passa a casar prosa: um artigo SOBRE codificações diz
    "charset=utf-8" no corpo sem declarar nada."""
    from adsense_checks.text import _declared_charset

    def documento(offset, valor="iso-8859-7"):
        enchimento = "<!-- " + "x" * offset + " -->"
        return f"<html><head>{enchimento}<meta charset='{valor}'>"

    # Dentro da janela: encontrado. Fora: ignorado.
    assert _declared_charset(documento(900)) == "iso-8859-7"
    assert _declared_charset(documento(1100)) == ""
