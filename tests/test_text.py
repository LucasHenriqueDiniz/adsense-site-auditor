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


# Palavras por seção da página de ferramenta abaixo: ~600 no total, desiguais de
# propósito, para que exista "a maior" e dê para ver quando só ela é contada.
SECOES_DA_FERRAMENTA = (180, 150, 130, 90, 50)


def _secoes(secoes=SECOES_DA_FERRAMENTA) -> str:
    return "".join(
        f'<section><h2>Secao {i}</h2><div class="body"><p>{_palavras(n)}</p></div></section>'
        for i, n in enumerate(secoes)
    )


def _pagina_de_ferramenta(secoes=SECOES_DA_FERRAMENTA, extra_na_regiao: str = "") -> str:
    """O ToolLayout do smallwebapps.com, reduzido: nenhum <main>, nenhum <article>.

    O título fica num hero e cada seção leva um <h2> e um <div> com os
    parágrafos, como o `div.deep-body` da página real. Cada <p> credita o <div>
    dele inteiro e a <section> pela metade; o <div> que reúne as seções não
    recebe nada, então a pontuação elege o bloco da maior seção e para nele.
    Chrome é `ruido`, conteúdo é `palavra`.
    """
    menu = "".join(f'<li><a href="/c{i}">{_ruido(2)}</a></li>' for i in range(40))
    corpo = _secoes(secoes)
    return (
        "<html><body>"
        f'<header><a href="/">{_ruido(2)}</a><nav><ul>{menu}</ul></nav></header>'
        '<div class="tool-page">'
        f'<section class="hero"><h1>Girar paginas de PDF</h1><p>{_palavras(40)}</p></section>'
        f'<div class="lower">{corpo}{extra_na_regiao}</div>'
        "</div>"
        f"<footer><p>{_ruido(20)}</p><nav>{_ruido(10)}</nav></footer>"
        "</body></html>"
    )


def _palavras_da_ferramenta(secoes=SECOES_DA_FERRAMENTA) -> int:
    """O que um leitor conta do <h1> ao fim da última seção: título, intro, e
    cada seção com seu <h2> ("Secao N", duas palavras)."""
    return word_count("Girar paginas de PDF") + 40 + sum(2 + n for n in secoes)


def test_sem_main_conta_todas_as_secoes_e_nao_so_a_maior():
    """O defeito medido em 2026-10-18 em smallwebapps.com/apps/pdf-rotate/: sem
    <main>, a pontuação elegia um único `div.deep-body` de 114 palavras numa
    página de 589, e a mediana das páginas de ferramenta saía abaixo do limiar
    — o Blocker do item 4 do pre-flight disparado em falso.

    O conteúdo principal é o menor ancestral que guarda o <h1> e as seções; o
    menu e o rodapé ficam de fora."""
    html = _pagina_de_ferramenta()
    d = measure_depth(html)
    assert d.words == _palavras_da_ferramenta()
    assert d.words > max(SECOES_DA_FERRAMENTA) + 2  # não é a maior seção sozinha
    assert "ruido" not in d.text
    assert d.text.startswith("Girar paginas de PDF")
    assert d.status is Status.OK, d.reason
    assert main_content_text(html) == d.text


def test_aside_dentro_da_regiao_do_titulo_fica_de_fora():
    """Subir do bloco até o <h1> passa por cima do que estiver entre eles — na
    página real, um `<aside class="tl-rail">` dentro de `div.tl-body`. A
    mobília que a subida atravessa não entra na contagem."""
    d = measure_depth(_pagina_de_ferramenta(extra_na_regiao=f"<aside>{_ruido(30)}</aside>"))
    assert d.words == _palavras_da_ferramenta()
    assert "ruido" not in d.text


@pytest.mark.parametrize(
    "antes",
    [
        # O <h1> de logo no cabeçalho do site nomeia o site, não a página.
        "<header><h1>ruido ruido</h1></header>",
        # Um <h1> sem palavra nenhuma não é título de nada.
        '<div class="logo"><h1><img src="/logo.png" alt=""></h1></div>',
    ],
)
def test_h1_que_nao_e_o_titulo_da_pagina_nao_ancora_a_regiao(antes):
    """O primeiro <h1> do documento não é o título quando está na mobília ou
    não tem texto. Ancorar nele sobe a região até o wrapper da página e traz
    junto o que não é mobília marcada — aqui, uma faixa promocional."""
    html = (
        _pagina_de_ferramenta()
        .replace(
            "<header>",
            f'<div id="page">{antes}<div class="promo"><p>{_ruido(30)}</p></div><header>',
        )
        .replace("</footer>", "</footer></div>")
    )
    d = measure_depth(html)
    assert "ruido" not in d.text
    assert d.words == _palavras_da_ferramenta()


def test_titulo_e_bloco_que_so_se_encontram_no_body_nao_contam_o_body():
    """Com o <h1> pendurado direto no <body>, o menor ancestral comum é a
    página. Contar o body — mesmo sem a mobília — é a contagem que
    `measure_depth` recusa em todo outro caminho, então o bloco volta sozinho.

    O teste fixa essa recusa, não o número que ela dá. Até 2026-10-18 era o
    bloco sozinho, uma subcontagem; desde 2026-10-19 `_prose_region` sobe do
    bloco até o `div.lower` e recupera as seções irmãs, sem contar o body —
    e foi para deixar essa correção passar que o teste não fixa o número. O
    que denunciaria o body é o banner de cookies: um <div> de texto puro,
    fora de qualquer mobília, que só entra na conta se o body entrar. Sem ele,
    o body menos a mobília soma exatamente o conteúdo, e contar a página
    inteira não deixaria rastro nenhum."""
    html = (
        "<html><body>"
        f"<header><nav>{_ruido(20)}</nav></header>"
        "<h1>Girar paginas de PDF</h1>"
        f'<div class="lower">{_secoes()}</div>'
        f"<footer><p>{_ruido(20)}</p></footer>"
        f'<div class="cookie-banner">{_ruido(20)}</div>'
        "</body></html>"
    )
    conteudo = word_count("Girar paginas de PDF") + sum(2 + n for n in SECOES_DA_FERRAMENTA)
    d = measure_depth(html)
    assert "ruido" not in d.text
    assert max(SECOES_DA_FERRAMENTA) <= d.words <= conteudo


# --------------------------------------------------------------------------
# O chrome que não usa as quatro tags de mobília. Os dois falsos PASS da
# revisão do PR #2, medidos em 2026-10-18 contra `d5b0784`: 489 e 521 palavras,
# OK, em volta de um post de 120 — que a base do PR media 120, WARNING.
# --------------------------------------------------------------------------


def _tema_de_divs(logo: str) -> str:
    """Um tema no estilo Twenty Ten: nem <header>, nem <nav>, nem <footer>.

    Cabeçalho, menu, lateral e rodapé são <div>, e o post não tem <article>.
    O menu e a lateral são o que menu e lateral costumam ser: listas de links.
    """
    menu = "".join(f'<li><a href="/c{i}">{_ruido(4)}</a></li>' for i in range(60))
    lateral = "".join(f'<li><a href="/p{i}">{_ruido(4)}</a></li>' for i in range(30))
    return (
        '<html><body><div id="page">'
        f'<div id="header">{logo}<div id="access"><ul>{menu}</ul></div></div>'
        '<div id="main"><div id="content"><div class="post"><h2>Titulo do post</h2>'
        f'<div class="entry-content"><p>{_palavras(120)}</p></div></div></div>'
        f'<div id="sidebar"><ul>{lateral}</ul></div></div>'
        '<div id="footer">Todos os direitos reservados</div>'
        "</div></body></html>"
    )


@pytest.mark.parametrize(
    "logo",
    [
        '<h1 id="site-title"><a href="/">Meu Blog</a></h1>',
        # O link por fora do <h1>: a palavra continua sendo texto de link.
        '<a href="/"><h1 id="site-title">Meu Blog</h1></a>',
        # A raiz escrita com o domínio: um host sem caminho também é a home.
        '<h1 id="site-title"><a href="https://exemplo.com">Meu Blog</a></h1>',
    ],
)
def test_logo_h1_que_e_so_link_nao_ancora_a_regiao(logo):
    """Num <div id="header">, nenhuma tag diz que o <h1> é o logo. O que diz é
    que ele é um link para a home, e o título de uma página não é. Ancorar nele
    subia a região até o `div#page` e contava o menu e a lateral."""
    d = measure_depth(_tema_de_divs(logo))
    assert d.words == 120
    assert "ruido" not in d.text
    assert "Meu Blog" not in d.text
    assert d.status is Status.WARNING, d.reason


@pytest.mark.parametrize(
    "titulo",
    [
        '<h1><a href="/post">Titulo</a></h1>',
        '<h1><a href="#topo">Titulo</a></h1>',
        '<h1><a href="https://exemplo.com/2026/titulo">Titulo</a></h1>',
        # O link por fora: a pergunta é para onde ele leva, não de que lado está.
        '<a href="/post"><h1>Titulo</h1></a>',
    ],
)
def test_titulo_linkado_ao_proprio_post_e_titulo_e_nao_logo(titulo):
    """Alguns temas imprimem `<h1><a href="/post">` no próprio post. É texto de
    link, como um logo, mas não leva à home — e era rejeitado como logo, e a
    página ficava com a subcontagem que este PR existe para corrigir: 100
    palavras, WARNING, onde um leitor conta 562."""
    html = (
        '<html><body><div id="page"><div class="post">'
        f"{titulo}"
        f'<div class="intro"><p>{_palavras(60)}</p></div>'
        f"<section><h2>Parte</h2><ul><li>{_palavras(400)}</li></ul></section>"
        f'<div class="entry"><p>{_palavras(100)}</p></div>'
        "</div></div></body></html>"
    )
    d = measure_depth(html)
    assert d.words == 562
    assert d.status is Status.OK, d.reason


def test_bloco_dentro_da_mobilia_nao_desconta_o_que_nao_contou():
    """Conteúdo só em listas; o único <p> da página está num <footer> dentro do
    wrapper, e ganha a pontuação. O rodapé inteiro fica fora da conta — e o
    texto solto dele era descontado mesmo assim, como dúvida: 402 contadas,
    202 "decididas", ERROR. O que não entrou na conta não sai dela."""
    html = (
        '<html><body><div id="page"><h1>Titulo</h1>'
        f"<section><h2>Passos</h2><ul><li>{_palavras(400)}</li></ul></section>"
        f'<footer><div class="brand"><p>{_ruido(30)}</p></div>{_ruido(200)}</footer>'
        "</div></body></html>"
    )
    d = measure_depth(html)
    assert d.words == 402
    assert "ruido" not in d.text
    assert d.status is Status.INFO, d.reason


@pytest.mark.parametrize(
    "logo,url",
    [
        # A home localizada: sem a URL, `/pt-br/` não se distingue de um título.
        ('<h1 id="site-title"><a href="/pt-br/">Meu Blog</a></h1>', "https://exemplo.com/pt-br/um/"),
        # Na raiz do site, o logo aponta para a própria página e continua logo.
        ('<h1 id="site-title"><a href="/">Meu Blog</a></h1>', "https://exemplo.com/"),
        # A raiz servida pelo documento de índice ainda é a raiz.
        ('<h1 id="site-title"><a href="/">Meu Blog</a></h1>', "https://exemplo.com/index.php"),
    ],
)
def test_com_a_url_da_pagina_logo_e_o_link_que_leva_a_outro_lugar(logo, url):
    """Com a URL, um <h1> só de link é título quando leva à própria página, e
    logo em qualquer outro caso — a home em `/`, em `/pt-br/`, em `/blog/`."""
    d = measure_depth(_tema_de_divs(logo), url=url)
    assert d.words == 120
    assert "Meu Blog" not in d.text
    assert d.status is Status.WARNING, d.reason


def test_main_content_text_recebe_a_url_e_devolve_o_texto_medido():
    """A URL decide qual <h1> ancora a região, e `main_content_text` não a
    recebia: na mesma página, `measure_depth(url=...)` media 120 palavras e o
    texto devolvido tinha 425. Com a mesma URL, os dois falam da mesma coisa."""
    html = _tema_de_divs('<h1 id="site-title"><a href="/pt-br/">Meu Blog</a></h1>')
    url = "https://exemplo.com/pt-br/um/"
    texto = main_content_text(html, url=url)
    assert texto == measure_depth(html, url=url).text
    assert word_count(texto) == 120


@pytest.mark.parametrize(
    "href,url",
    [
        ("/post", "https://exemplo.com/post/"),
        ("/post", "https://exemplo.com/Post/"),
        ("/post", "https://exemplo.com/post/index.html"),
        ("/ca%C3%A7a", "https://exemplo.com/caça/"),
    ],
)
def test_com_a_url_da_pagina_titulo_linkado_a_ela_mesma_e_titulo(href, url):
    """O outro lado da regra: o link e a página são a mesma página quando só a
    grafia do caminho difere — barra final, maiúsculas, o documento de índice,
    o percent-encoding. Comparados ao pé da letra, `/post` e `/Post/` davam
    logo, e a página ficava com 100 palavras onde um leitor conta 562."""
    html = (
        '<html><body><div id="page"><div class="post">'
        f'<h1><a href="{href}">Titulo</a></h1>'
        f'<div class="intro"><p>{_palavras(60)}</p></div>'
        f"<section><h2>Parte</h2><ul><li>{_palavras(400)}</li></ul></section>"
        f'<div class="entry"><p>{_palavras(100)}</p></div>'
        "</div></div></body></html>"
    )
    d = measure_depth(html, url=url)
    assert d.words == 562
    assert d.status is Status.OK, d.reason


def test_menu_e_lateral_de_links_em_div_ficam_de_fora_da_regiao():
    """Um <h1> de verdade, pendurado direto no wrapper: a região é o wrapper, e
    o menu antes do título e a lateral depois do bloco vêm junto. Toda palavra
    deles é texto de link — navegação, com ou sem <nav> em volta."""
    links = "".join(f'<li><a href="/x{i}">{_ruido(4)}</a></li>' for i in range(60))
    html = (
        '<html><body><div id="page">'
        f'<div class="menu"><ul>{links}</ul></div>'
        "<h1>Titulo</h1>"
        f'<div class="content"><div class="entry"><p>{_palavras(120)}</p></div></div>'
        f'<div class="sidebar"><ul>{links}</ul></div>'
        "</div></body></html>"
    )
    d = measure_depth(html)
    assert d.words == 121
    assert "ruido" not in d.text
    assert d.status is Status.WARNING, d.reason


@pytest.mark.parametrize(
    "lista",
    [
        "<ul><li>{}</li></ul>",
        # Um <p> dentro do <li> passa pela lista como o próprio <li> passaria.
        "<ul><li><p>{}</p></li></ul>",
    ],
)
def test_texto_sem_titulo_depois_do_bloco_que_decide_a_faixa_e_ERROR(lista):
    """O caso que estrutura nenhuma separa: um <div> de texto puro depois do
    bloco, sem heading. É uma lateral ou uma lista de casos de uso, e as duas
    se escrevem igual. Quando a faixa depende disso, a faixa não é medição —
    a mesma regra do controle de mobília.

    Itens de lista pontuam desde que estejam numa seção com heading. Esta não
    tem: se pontuassem, as 400 palavras venceriam o post de 120 e seriam
    contadas inteiras como o bloco principal, OK, sem dúvida nenhuma."""
    html = (
        '<html><body><div id="page"><h1>Titulo</h1>'
        f'<div class="content"><div class="entry"><p>{_palavras(120)}</p></div></div>'
        f'<div class="widgets">{lista.format(_ruido(400))}</div>'
        "</div></body></html>"
    )
    d = measure_depth(html)
    assert d.status is Status.ERROR
    assert "not separated from the page around it" in d.reason
    assert "below the configured threshold" in d.reason  # sem o <div>: 121 palavras


def test_texto_sem_titulo_entre_o_titulo_e_o_bloco_conta():
    """O trecho entre o <h1> e o bloco é o que a subida foi buscar: uma
    introdução em lista, sem heading próprio, é conteúdo e não vira dúvida."""
    html = (
        '<html><body><div id="page"><h1>Titulo</h1>'
        f'<ul class="intro"><li>{_palavras(300)}</li></ul>'
        f'<div class="content"><div class="entry"><p>{_palavras(200)}</p></div></div>'
        "</div></body></html>"
    )
    d = measure_depth(html)
    assert d.words == 501
    assert d.status is Status.OK, d.reason


@pytest.mark.parametrize("por_card", [10, 100])
def test_grade_de_cards_linkados_fica_de_fora_e_nao_vence_a_pontuacao(por_card):
    """A grade de ferramentas relacionadas do smallwebapps: cada card é um
    `<a>` com <h3> e <p>. Com descrições curtas ela é só um galho da região, e
    sai por ser só link. Com descrições longas, os <p> dela somavam mais que o
    conteúdo — em /apps/csv-to-json/ de 2026-08-30, a grade ganhava a
    pontuação e era contada inteira como o bloco principal."""
    cards = "".join(
        f'<a href="/apps/t{i}" class="related-card"><h3>{_ruido(2)}</h3>'
        f"<p>{_ruido(por_card)}</p></a>"
        for i in range(6)
    )
    grade = (
        f'<section class="related"><h2>Mais ferramentas</h2><div class="grid">{cards}</div>'
        "</section>"
    )
    d = measure_depth(_pagina_de_ferramenta(extra_na_regiao=grade))
    assert "ruido" not in d.text
    assert d.words == _palavras_da_ferramenta() + 2  # + "Mais ferramentas"
    assert d.status is Status.OK, d.reason


def test_lista_em_secao_com_titulo_tira_a_vitoria_do_hero():
    """smallwebapps.com/apps/compress-pdf-to-1mb/, 2026-08-30: o único <p> da
    página era a descrição no hero, com o <h1> dentro dele. O resto — passos,
    casos de uso, FAQ — estava em <ol>, <ul> e <details>, que não pontuavam. O
    hero ganhava, o título estava dentro do vencedor, nada alargava: 182
    palavras onde a referência tem 541. Com itens de lista pontuando nas suas
    seções, uma seção vence, e a região a partir do <h1> pega a página."""
    passos = "".join(f"<li>{_palavras(40)}</li>" for _ in range(3))
    casos = "".join(f"<li>{_palavras(30)}</li>" for _ in range(3))
    perguntas = "".join(
        f"<details><summary>Pergunta um</summary><p>{_palavras(25)}</p></details>"
        for _ in range(2)
    )
    html = (
        f"<html><body><header><nav>{_ruido(20)}</nav></header>"
        '<div class="tool-page">'
        f'<section class="hero"><h1>Comprimir PDF</h1><p>{_palavras(60)}</p></section>'
        '<div class="lower">'
        f"<section><h2>Passos</h2><ol>{passos}</ol></section>"
        f"<section><h2>Casos</h2><ul>{casos}</ul></section>"
        f'<section class="faq"><h2>Perguntas</h2><div class="list">{perguntas}</div></section>'
        "</div></div>"
        f"<footer><p>{_ruido(10)}</p></footer></body></html>"
    )
    esperado = 2 + 60 + (1 + 120) + (1 + 90) + (1 + 2 * (2 + 25))
    d = measure_depth(html)
    assert d.words == esperado
    assert "ruido" not in d.text


def test_resposta_do_faq_credita_o_faq_e_nao_o_vazio():
    """Um <p> de resposta dentro de <details> creditava o <details>, que não é
    container, e o FAQ ganhava só a metade. Um <div> com um parágrafo maior
    que cada resposta, e menor que todas juntas, levava a página. Sem <h1>,
    sem alargamento: o que se mede é só a pontuação."""
    perguntas = "".join(
        f"<details><summary>Pergunta dois</summary><p>{_palavras(30)}</p></details>"
        for _ in range(4)
    )
    html = (
        "<html><body>"
        f'<section class="faq"><h2>Perguntas</h2><div class="list">{perguntas}</div></section>'
        # 539 caracteres: mais que a metade das quatro respostas (478), que era
        # tudo o que o FAQ levava; menos que elas inteiras (956).
        f'<div class="outro"><p>{_ruido(90)}</p></div>'
        "</body></html>"
    )
    d = measure_depth(html)
    assert "ruido" not in d.text
    assert d.words == 4 * (2 + 30)


def test_lista_na_mobilia_nao_pontua_mesmo_com_titulo():
    """Um rodapé com colunas de endereço em <ul>, e um <h4> em cima: é uma
    seção com heading, mas é a mobília da página. Itens de lista só pontuam
    fora dela — um rodapé gordo não pode vencer o artigo."""
    html = (
        "<html><body>"
        f'<div class="entry"><p>{_palavras(120)}</p></div>'
        f'<footer><div class="cols"><h4>Endereco</h4><ul><li>{_ruido(300)}</li></ul></div></footer>'
        "</body></html>"
    )
    d = measure_depth(html)
    assert d.words == 120
    assert "ruido" not in d.text


def test_menu_de_links_com_titulo_nao_pontua():
    """Um menu em <div> com um <h3> em cima tem seção e heading; o que ele não
    tem é texto que não seja link. Item só de link é navegação e não pontua."""
    itens = "".join(f'<li><a href="/c{i}">{_ruido(2)}</a></li>' for i in range(100))
    html = (
        "<html><body>"
        f'<div class="menu"><h3>Categorias</h3><ul>{itens}</ul></div>'
        f'<div class="entry"><p>{_palavras(120)}</p></div>'
        "</body></html>"
    )
    d = measure_depth(html)
    assert d.words == 120
    assert "ruido" not in d.text


def test_o_body_nao_e_secao_de_ninguem():
    """Um <h2> em qualquer lugar da página põe um heading dentro do <body>. Se
    isso bastasse, toda lista solta direto no body teria "seção com heading":
    a lateral pontua, vence, e o teto do body a devolve sozinha como o
    conteúdo — 400 palavras de chrome, OK."""
    html = (
        "<html><body><h1>Titulo</h1>"
        f'<div class="content"><h2>Parte</h2><div class="entry"><p>{_palavras(120)}</p></div></div>'
        f'<div class="widgets"><ul><li>{_ruido(400)}</li></ul></div>'
        "</body></html>"
    )
    d = measure_depth(html)
    assert "ruido" not in d.text


def test_collect_nunca_pula_o_proprio_no():
    """`_collect(no, skip)` com o próprio nó no conjunto devolve o texto dele:
    é como a pontuação lê só as palavras de cada bloco, passando o conjunto de
    todos os blocos da página de uma vez."""
    from adsense_checks.text import _collect, _iter_elements, _parse

    doc = _parse("<ul><li>rotulo<ul><li>filho</li></ul></li></ul>")
    externo = next(n for n in _iter_elements(doc.root) if n.tag == "li")
    blocos = frozenset(n for n in _iter_elements(doc.root) if n.tag == "li")
    assert _collect(externo, skip=blocos).split() == ["rotulo"]


# --------------------------------------------------------------------------
# Sem título que alargue: a região sobe enquanto a prosa compensa
# (`_prose_region`). Medido em 2026-10-19 nas 28 páginas de ferramenta do
# smallwebapps.com, com a busca do <h1> desligada: mediana de 156,5 palavras
# antes, 729,5 depois, contra 722,5 da contagem de leitor.
# --------------------------------------------------------------------------


def test_sem_titulo_a_regiao_sobe_ate_onde_a_prosa_compensa():
    """Seções irmãs, sem <h1> nenhum. A pontuação credita dois níveis acima e
    elege uma seção; a subida pesa prosa contra o resto e chega ao container
    que guarda todas. O menu e o rodapé ficam fora: a subida nunca passa do
    <body>, e a mobília não entra na contagem."""
    html = (
        f"<html><body><header><nav>{_ruido(20)}</nav></header>"
        f'<div class="lower">{_secoes()}</div>'
        f"<footer><p>{_ruido(20)}</p></footer></body></html>"
    )
    d = measure_depth(html)
    assert d.words == sum(2 + n for n in SECOES_DA_FERRAMENTA)
    assert "ruido" not in d.text
    assert d.status is Status.OK, d.reason


def test_sem_titulo_texto_que_nao_e_prosa_segura_a_subida():
    """Um <div> de texto solto ao lado do post pesa contra o container que
    guarda os dois: mais prosa adiante (`div.mais`) não compensa 400 palavras
    que não são prosa, e a região fica no post."""
    html = (
        '<html><body><div id="page">'
        f'<div class="content"><div class="entry"><p>{_palavras(120)}</p></div></div>'
        f'<div class="widgets">{_ruido(400)}</div>'
        f'<div class="mais"><p>{_palavras(50)}</p></div>'
        "</div></body></html>"
    )
    d = measure_depth(html)
    assert d.words == 120
    assert "ruido" not in d.text


@pytest.mark.parametrize(
    "entre",
    [
        # Link não é prosa nem resto: sai da conta, então não pesa contra.
        "".join(f'<a href="/t{i}"><p>{" ".join(["ruido"] * 10)}</p></a>' for i in range(30)),
        # A mobília também sai da conta, e também não pesa.
        f"<aside><p>{' '.join(['ruido'] * 300)}</p></aside>",
    ],
    ids=["grade-de-links", "aside"],
)
def test_sem_titulo_link_e_mobilia_nao_pesam_contra_a_subida(entre):
    """Na página de ferramenta de 2026-08-30, a grade de relacionados (~130
    palavras só de link) fica entre o hero e o conteúdo. Pesada como resto,
    ela segurava 29 das 142 páginas no FAQ."""
    html = (
        '<html><body><div class="page-body">'
        f'<section><h2>Intro</h2><div class="b"><p>{_palavras(150)}</p></div></section>'
        f'<div class="related">{entre}</div>'
        f'<section><h2>Mais</h2><div class="b"><p>{_palavras(200)}</p></div></section>'
        "</div></body></html>"
    )
    d = measure_depth(html)
    assert d.words == 352
    assert "ruido" not in d.text


def test_sem_titulo_o_empate_fica_no_elemento_mais_fundo():
    """Um banner que é só heading não pesa nada: subir até ele empata, e o
    empate fica embaixo — a região não cresce para pegar o que não acrescenta
    prosa."""
    html = (
        '<html><body><div id="page">'
        f'<div class="content"><div class="b"><p>{_palavras(200)}</p></div></div>'
        '<div class="banner"><h2>Promocao</h2></div>'
        "</div></body></html>"
    )
    d = measure_depth(html)
    assert d.words == 200
    assert "Promocao" not in d.text


def test_sem_titulo_prosa_sem_heading_nao_vira_duvida():
    """Sem título não há "entre o título e o bloco"; o que a subida pegou por
    ter prosa é conteúdo pela mesma razão que a fez subir. Sem essa ressalva,
    o segundo bloco viraria dúvida e a página, ERROR."""
    html = (
        '<html><body><div class="lower">'
        f'<div class="a"><p>{_palavras(200)}</p></div>'
        f'<div class="b"><p>{_palavras(150)}</p></div>'
        "</div></body></html>"
    )
    d = measure_depth(html)
    assert d.words == 350
    assert d.status is Status.INFO, d.reason


def test_sem_titulo_a_subida_para_antes_do_body():
    """Dois blocos pendurados direto no <body> só se juntam no <body>, que é a
    página. A subida para antes dele: conta o maior bloco, a mesma recusa que
    o teto do título já fazia."""
    html = (
        "<html><body>"
        f'<div class="a"><p>{_palavras(200)}</p></div>'
        f'<div class="b"><p>{_palavras(150)}</p></div>'
        "</body></html>"
    )
    assert measure_depth(html).words == 200


def _linkado(partes: int, no_link: int, fora: int) -> str:
    """Um parágrafo de texto corrido com um link no meio de cada frase."""
    return " ".join(
        f'Leia <a href="/g{i}">{_palavras(no_link)}</a> e {_palavras(fora)}' for i in range(partes)
    )


def test_link_no_meio_da_frase_conta_com_a_frase():
    """"Leia <a>este guia</a> e volte" é uma frase com um link, e as palavras
    do link são da frase. A regra do só-link achava cada <a> dentro do <p> e o
    tirava da conta: numa seção vizinha com 100 palavras em links, um leitor
    conta 475 e a medição dava 375 — desde o caminho do título, no PR #2."""
    html = (
        '<html><body><div class="post"><h1>Titulo</h1>'
        f'<section><h2>Parte um</h2><div class="b"><p>{_palavras(250)}</p></div></section>'
        f"<section><h2>Parte dois</h2><p>{_linkado(20, 5, 4)}</p></section>"
        "</div></body></html>"
    )
    assert measure_depth(html).words == 1 + 2 + 250 + 2 + 20 * 11


def test_links_em_linha_nao_carregam_a_subida_sem_titulo():
    """Pesadas como prosa e também como sem peso, as palavras de link de um
    parágrafo vizinho valiam em dobro: 40 delas levaram a região por cima de
    80 palavras de chrome, contadas em silêncio. Sem os links, a mesma página
    não sobe — e com eles também não deve."""
    html = (
        '<html><body><div id="page">'
        f'<div class="content"><div class="a"><p>{_palavras(150)}</p></div></div>'
        f'<div class="more"><p>{_linkado(10, 4, 0)}</p></div>'
        f'<div class="chrome">{_ruido(80)}</div>'
        "</div></body></html>"
    )
    d = measure_depth(html)
    assert d.words == 150
    assert "ruido" not in d.text


def test_bloco_dentro_da_mobilia_nao_sobe():
    """O único <p> da página está num <header>, ao lado de um <nav> de texto.
    A subida parava no próprio <header> — sem peso, então valendo mais que o
    bloco — e contava o nav como conteúdo. Bloco na mobília fica como está."""
    html = (
        '<html><body><div id="page"><header><div class="intro">'
        f"<p>{_palavras(40)}</p></div><nav>{_ruido(20)}</nav></header>"
        f'<div class="x">{_ruido(30)}</div></div></body></html>'
    )
    d = measure_depth(html)
    assert d.words == 40
    assert "ruido" not in d.text


@pytest.mark.parametrize(
    "html,so_link",
    [
        ('<div><p>Leia <a href="/g">este guia</a> agora</p></div>', set()),
        # O link no começo da frase: a palavra ao lado está à direita.
        ('<div><p><a href="/g">Baixe</a> o arquivo agora</p></div>', set()),
        # A palavra fora do link pode estar num irmão inline, não no texto direto.
        ('<div><p><em>Leia</em> <a href="/g">isto</a></p></div>', set()),
        # Um link numa linha própria, acima de um parágrafo, é navegação: o fluxo
        # do <div> não tem palavra fora de link, só o <p> aninhado tem.
        ('<div><a href="/">Inicio</a><p>Texto do post</p></div>', {"Inicio"}),
        # Só elemento inline fica dentro de uma frase. Um <div> de link no meio
        # do texto é um bloco à parte — um card "leia mais" — e sai.
        ('<div>Texto do post <div><a href="/x">Leia mais</a></div></div>', {"Leia mais"}),
        # Um rótulo não faz de uma lista de links uma frase: ele segura o link
        # que encosta nele, e os outros, só com vírgula e link ao lado, saem.
        (
            '<div>Tags: <a href="/t1">pdf</a>, <a href="/t2">ferramentas</a>, '
            '<a href="/t3">guias</a></div>',
            {"ferramentas", "guias"},
        ),
        # Pontuação num <span> próprio é pontuação, como escrita direto no texto.
        ('<div><p><a href="/x">Foo</a><span>:</span> uma biblioteca</p></div>', set()),
        # Dois links abrindo a frase: a frase continua depois deles.
        ('<div><p><a href="/x">Foo</a> <a href="/y">Bar</a> e uma biblioteca</p></div>', set()),
        # Separados só por espaço, mas sem frase depois: é a lista do rótulo.
        (
            '<div>Tags: <a href="/t1">pdf</a> <a href="/t2">ferramentas</a> '
            '<a href="/t3">guias</a></div>',
            {"ferramentas", "guias"},
        ),
        # O ponto encerra a lista; o que vem depois não a transforma em frase.
        # O último link encosta na frase seguinte e fica; o do meio sai.
        (
            '<div>Tags: <a href="/t1">pdf</a> <a href="/t2">ferramentas</a> '
            '<a href="/t3">guias</a>. Mais texto aqui</div>',
            {"ferramentas"},
        ),
    ],
    ids=[
        "frase",
        "inicio-da-frase",
        "irmao-inline",
        "linha-propria",
        "bloco-de-link",
        "rotulo",
        "pontuacao-em-span",
        "dois-links-abrem-a-frase",
        "rotulo-com-espacos",
        "rotulo-e-ponto",
    ],
)
def test_link_em_texto_corrido_nao_e_so_link(html, so_link):
    from adsense_checks.text import _collect, _iter_elements, _link_only, _normalize, _parse

    doc = _parse(html)
    div = next(n for n in _iter_elements(doc.root) if n.tag == "div")
    assert {_normalize(_collect(n)) for n in _link_only([div])} == so_link


def test_heading_que_e_ancora_de_si_mesmo_continua_heading():
    """`<h2><a href="#parte">Parte dois</a></h2>`, o heading que linka a própria
    âncora, é comum em temas de blog e de documentação. Lido como navegação, ele
    saía da conta e a seção ficava sem heading: virava dúvida, e a página dava
    353 palavras, ERROR, onde a mesma com <h2> sem link dá 357, INFO."""
    html = (
        '<html><body><div class="post"><h1>Titulo</h1>'
        f'<section><h2>Parte um</h2><div class="b"><p>{_palavras(200)}</p></div></section>'
        f'<section><h2><a href="#dois">Parte dois</a></h2><p>{_palavras(90)}</p></section>'
        f'<section><h2><a href="#tres">Parte tres</a></h2><p>{_palavras(60)}</p></section>'
        "</div></body></html>"
    )
    d = measure_depth(html)
    assert d.words == 1 + 2 + 200 + 2 + 90 + 2 + 60
    assert d.status is Status.INFO, d.reason


def test_lista_de_tags_com_rotulo_nao_vira_texto():
    """Uma linha "Tags: <a>…</a>, <a>…</a>, …" depois do post: a palavra de
    rótulo fazia de todo link "texto corrido", e as 160 palavras dos 80 links
    entravam na conta — 362 palavras, ERROR, numa página de 202."""
    tags = "Tags: " + ", ".join(f'<a href="/t{i}">{_ruido(2)}</a>' for i in range(80))
    html = (
        '<html><body><div class="post"><h1>Titulo</h1>'
        f'<div class="entry"><p>{_palavras(200)}</p></div><div class="tags">{tags}</div>'
        "</div></body></html>"
    )
    d = measure_depth(html)
    assert d.text.count("ruido") == 2  # o link que encosta no rótulo, e só ele
    assert d.words == 1 + 200 + 1 + 2
    assert d.status is Status.WARNING, d.reason


def test_texto_solto_depois_do_bloco_que_decide_a_faixa_e_ERROR():
    """Texto fora de qualquer elemento, direto no wrapper, depois do bloco. Só
    os elementos eram classificados, então ele entrava na conta sem passar por
    controle nenhum: 521 palavras, OK, em volta de um post de 120. É o mesmo
    caso do <div> de texto puro, só que sem o <div>."""
    html = (
        '<html><body><div id="page"><h1>Titulo</h1>'
        f'<div class="content"><div class="entry"><p>{_palavras(120)}</p></div></div>'
        f"{_ruido(400)}"
        "</div></body></html>"
    )
    d = measure_depth(html)
    assert d.status is Status.ERROR
    assert "not separated from the page around it: 400 words" in d.reason
    assert "below the configured threshold" in d.reason  # sem o texto solto: 121


def test_texto_solto_entre_o_titulo_e_o_bloco_conta():
    """A mesma regra dos elementos: o trecho entre o <h1> e o bloco é o que a
    subida foi buscar, solto ou não."""
    html = (
        '<html><body><div id="page"><h1>Titulo</h1>'
        f"{_palavras(300)}"
        f'<div class="content"><div class="entry"><p>{_palavras(200)}</p></div></div>'
        "</div></body></html>"
    )
    d = measure_depth(html)
    assert d.words == 501
    assert d.status is Status.OK, d.reason


def test_ancora_sem_href_nao_e_link():
    """`<a name="faq">` marca um lugar, não leva a lugar nenhum. Tratada como
    link, ela tirava o <h2> da conta e o status de heading da seção, e uma
    seção de conteúdo depois do bloco virava dúvida: 351 palavras, ERROR."""
    html = (
        '<html><body><div id="page"><h1>Titulo</h1>'
        f'<div class="content"><div class="entry"><p>{_palavras(200)}</p></div></div>'
        '<div class="more"><h2><a name="faq">Perguntas frequentes</a></h2>'
        f"<ul><li>{_palavras(150)}</li></ul></div>"
        "</div></body></html>"
    )
    d = measure_depth(html)
    assert d.words == 353
    assert "Perguntas frequentes" in d.text
    assert d.status is Status.INFO, d.reason


@pytest.mark.parametrize("rodape", ["", "<footer><p>Copyright 2026 Exemplo</p></footer>"])
def test_diretorio_so_de_links_e_medido_como_lista(rodape):
    """Uma página que é só uma lista de `<p><a>`: a lista é o conteúdo dela.
    Parágrafos de link só deixam de pontuar quando há parágrafo de texto
    competindo; sem nenhum, a página caía no <body> e um diretório ralo virava
    "main content not isolated" — sem número, onde a base dizia 180, WARNING.

    O `<p>` de copyright do rodapé não compete: só parágrafo de texto fora da
    mobília tira a vez dos links. Sem essa ressalva, o conserto valia apenas
    para diretório sem rodapé, quase nenhum."""
    itens = "".join(f'<p><a href="/i{i}">item numero {i}</a></p>' for i in range(60))
    d = measure_depth(f'<html><body><div class="list">{itens}</div>{rodape}</body></html>')
    assert d.words == 180
    assert d.status is Status.WARNING, d.reason
    assert "not isolated" not in d.reason


def test_so_link_e_toda_palavra_dentro_de_link_nem_uma_a_menos():
    """`_link_only` é tudo ou nada. Uma palavra fora do link basta para o
    trecho ser texto; um separador sem palavra ("»") não basta."""
    from adsense_checks.text import _iter_elements, _link_only, _parse

    def so_link(html: str) -> bool:
        doc = _parse(html)
        div = next(n for n in _iter_elements(doc.root) if n.tag == "div")
        return div in _link_only([div])

    assert so_link('<div><a href="/">Inicio</a> » <a href="/f">Ferramentas</a></div>')
    assert not so_link('<div>Hyper<a href="/t">texto</a></div>')
    assert not so_link('<div><a href="/n">Noticias</a> (15)</div>')
    assert not so_link("<div>   </div>")  # sem palavra nenhuma, não é link
    assert not so_link('<div><a name="faq">Perguntas</a></div>')  # âncora, não link


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


def _pagina_com_wrapper(corpo: str, itens: int = 80) -> str:
    """Um tema de CMS: a página inteira dentro de um só <div id="wrapper">.

    `div` é container de conteúdo, então o wrapper ganha a pontuação, e
    `WHOLE_PAGE_ELEMENTS` (só #document/body/html) o deixa passar como se a
    heurística tivesse isolado alguma coisa.
    """
    menu = "".join(
        f'<li><a href="/c{i}">categoria numero {i} do nosso catalogo</a></li>'
        for i in range(itens)
    )
    return (
        "<html><body><div id=\"wrapper\">"
        f"<nav><ul>{menu}</ul></nav>"
        f"<p>{corpo}</p>"
        "<footer>Todos os direitos reservados</footer>"
        "</div></body></html>"
    )


def test_wrapper_da_pagina_inteira_nao_e_medido_como_conteudo_principal():
    """O defeito que isto trava: um corpo editorial de seis palavras reportado
    como aprovação de 490 palavras, porque o menu e o rodapé foram contados
    junto.

    `WHOLE_PAGE_ELEMENTS` só pega o wrapper quando o wrapper é o <body>. Um
    <div> em volta da mesma página não está nesse conjunto, então ele ganhou a
    pontuação e a contagem voltou OK — exatamente a aprovação falsa sobre
    ADS-CONTENT-03 que o docstring do módulo diz que se recusa a imprimir."""
    html = _pagina_com_wrapper("Este e o unico paragrafo editorial.")
    d = measure_depth(html)
    # O menu inteiro entrou na contagem, e sozinho já limpa a barra de OK.
    assert d.words >= borderline_ceiling(300)
    assert d.main_ratio == 1.0  # nada foi separado de nada
    assert d.status is Status.ERROR
    assert "not separated from page furniture" in d.reason


def test_controle_de_mobilia_nao_depende_de_quem_preencheu_outside(monkeypatch):
    """O controle de mobília era pulado sempre que `outside` não vinha vazio —
    certo só porque hoje o único caminho que o preenche também põe a mobília
    nele. Um caminho que um dia deixe algo de fora sem a mobília não pode
    desligar o controle e devolver o wrapper de menu como aprovação."""
    import adsense_checks.text as texto

    original = texto._main_container

    def com_algo_de_fora(*args, **kwargs):
        achado = original(*args, **kwargs)
        paragrafo = next(n for n in texto._iter_elements(achado.node) if n.tag == "p")
        return achado._replace(outside=achado.outside | {paragrafo})

    monkeypatch.setattr(texto, "_main_container", com_algo_de_fora)
    d = measure_depth(_pagina_com_wrapper("Este e o unico paragrafo editorial."))
    assert d.status is Status.ERROR
    assert "not separated from page furniture" in d.reason


def test_pagina_que_e_toda_conteudo_continua_passando():
    """Ratio 1.0 sozinho não pode ser a regra. Uma página que legitimamente *é*
    toda conteúdo principal também mede 1.0, e foi lida certo — não havia nada
    a separar, em vez de uma heurística que não separou nada."""
    for html in (
        f'<html><body><div id="content"><p>{_palavras(500)}</p></div></body></html>',
        f"<html><body><main><h1>Titulo</h1><p>{_palavras(500)}</p></main></body></html>",
    ):
        d = measure_depth(html)
        assert d.main_ratio == 1.0, html[:40]
        assert d.status is Status.OK, d.reason


def test_rodape_curto_dentro_do_container_nao_invalida_a_medicao():
    """O discriminante é a banda, não a presença de mobília. Quase toda página
    real tem uma linha de copyright dentro do wrapper de conteúdo; dar erro
    nelas deixaria a checagem inútil, e a contagem não está em dúvida quando
    descartá-las deixa a página na mesma banda."""
    html = (
        f'<html><body><div id="wrapper"><p>{_palavras(2000)}</p>'
        "<footer>Todos os direitos reservados</footer></div></body></html>"
    )
    d = measure_depth(html)
    assert d.main_ratio == 1.0
    assert d.status is Status.OK, d.reason


# --------------------------------------------------------------------------
# De quem é a mobília: a tag diz que a região não é prosa, não de quem ela é.
# --------------------------------------------------------------------------


def _post_do_wordpress(corpo: str) -> str:
    """O tema padrão de post do WordPress, e a forma canônica de <article>.

    `entry-header` e `entry-footer` ficam DENTRO do <article>: o <h1>, a
    assinatura e a linha de categorias são do artigo, não da página. O header e
    o footer do site ficam fora do <main>, então a heurística isola direito — o
    ratio nem chega a 1.0.
    """
    return (
        "<html><body>"
        "<header class='site-header'><nav><a href='/'>Home</a>"
        "<a href='/blog'>Blog</a><a href='/sobre'>Sobre</a></nav></header>"
        "<main><article class='post'>"
        "<header class='entry-header'>"
        "<h1 class='entry-title'>Como escolher a racao certa para o seu cachorro</h1>"
        "<p class='entry-meta'>Publicado por Lucas Ostroski em 4 de marco de 2026</p>"
        "</header>"
        f"<div class='entry-content'><p>{corpo}</p></div>"
        "<footer class='entry-footer'>"
        "<span class='cat-links'>Categorias: Animais, Saude, Alimentacao</span>"
        "</footer>"
        "</article></main>"
        "<footer class='site-footer'><p>Todos os direitos reservados</p></footer>"
        "</body></html>"
    )


def test_header_e_footer_dentro_do_article_nao_sao_mobilia_da_pagina():
    """O falso positivo: a forma mais comum da web reportada como ERROR.

    Pular <header>/<footer> por nome de tag, em qualquer profundidade, apaga o
    título, a assinatura e a linha de categorias de um post — e como as duas
    contagens caem em bandas diferentes, a medição virava "main content not
    separated from page furniture". A frase é falsa: o conteúdo principal FOI
    separado da mobília da página, que ficou fora do <main>. E ERROR, no
    contrato deste pacote (status.py), quer dizer que a checagem não conseguiu
    rodar — ela rodou.

    Com 430 palavras de corpo as duas contagens cruzam a fronteira de banda de
    propósito: é onde o guard disparava.
    """
    corpo = 430
    html = _post_do_wordpress(_palavras(corpo))
    d = measure_depth(html)
    # As duas contagens estão em bandas diferentes: sem o título e a assinatura
    # a página é borderline, com eles passa da barra. É a condição do guard.
    assert classify_depth(corpo, min_words=300) is Status.INFO
    assert d.words >= borderline_ceiling(300)
    assert d.status is Status.OK, d.reason
    assert "not separated from page furniture" not in d.reason


def test_o_header_do_proprio_article_e_editorial_mesmo_sem_main():
    """O exemplo de <article> do próprio HTML Living Standard: header com o
    título e a data, footer com o link dos comentários, e nada em volta.

    Aqui o <article> É o container vencedor, então "o ancestral article mais
    próximo é o próprio container" não pode significar mobília — o header
    continua sendo do artigo. Sem isso, a página mais simples que existe com um
    artigo dentro volta ERROR.
    """
    html = (
        "<html><body><article>"
        "<header><h2>The Very First Rule of Life</h2><p>3 days ago</p></header>"
        f"<p>{_palavras(440)}</p>"
        "<footer><a href='?comments=1'>Show comments</a></footer>"
        "</article></body></html>"
    )
    d = measure_depth(html)
    assert classify_depth(440, min_words=300) is Status.INFO
    assert d.words >= borderline_ceiling(300)  # as bandas se cruzam
    assert d.status is Status.OK, d.reason
    assert "not separated from page furniture" not in d.reason


def test_main_em_volta_do_menu_do_site_continua_sendo_pego():
    """O outro lado: o <article> perdoa a mobília dentro dele, o <main> não.

    Um <main> em volta do menu inteiro é o mesmo defeito do <div id="wrapper">
    com outra tag, e o guard tem de continuar pegando — senão a correção do
    falso positivo vira "confie em qualquer main/article" e a checagem perde os
    dentes.
    """
    menu = "".join(
        f'<li><a href="/c{i}">categoria numero {i} do nosso catalogo</a></li>'
        for i in range(80)
    )
    html = (
        f"<html><body><main><nav><ul>{menu}</ul></nav>"
        "<p>Este e o unico paragrafo editorial.</p></main></body></html>"
    )
    d = measure_depth(html)
    assert d.words >= borderline_ceiling(300)
    assert d.status is Status.ERROR
    assert "not separated from page furniture" in d.reason


def test_o_corte_da_mobilia_nao_solda_as_palavras_vizinhas():
    """A subárvore vai embora, a fronteira que ela desenhava fica.

    Descartar o <nav> sem escrever o separador cola "ultima" em "palavra" e
    devolve "ultimapalavra": uma palavra que não está em página nenhuma, e uma
    a menos que as duas que estão. Isso só empurra `editorial_words` para
    baixo, então faz o ERROR disparar — e aqui a solda SOZINHA produz o ERROR,
    porque 299 é WARNING e 300 é borderline, a mesma banda das 303 medidas.

    É a classe de defeito que o docstring do módulo diz existir para impedir.
    """
    html = (
        f'<html><body><div id="wrapper"><p>{_palavras(298)}</p>'
        "ultima<nav>Menu Home Sobre</nav>palavra</div></body></html>"
    )
    d = measure_depth(html)
    assert d.words == 303
    assert d.status is Status.INFO, d.reason
    assert "not separated from page furniture" not in d.reason


def test_o_separador_sobrevive_a_subarvore_descartada():
    """O mesmo defeito no nível em que ele mora, sem a aritmética de bandas."""
    from adsense_checks.text import _collect, _furniture, _parse

    doc = _parse("<div>abc<nav>x</nav>def</div>")
    div = doc.root.content[0]
    assert div.tag == "div"
    assert word_count(_collect(div)) == 3  # abc x def
    assert word_count(_collect(div, skip=_furniture(div))) == 2  # abc def, não abcdef


# --------------------------------------------------------------------------
# Cada membro de BOILERPLATE_ELEMENTS, escrito literalmente. O teste que estava
# aqui iterava sobre o próprio conjunto sob teste, então tomava os casos de
# quem deveria vigiar: apagar uma tag do código E da lista literal — o que um
# autor removendo uma tag faz numa edição só — passava com a suíte verde para
# `footer`, `header` e `aside`. Só `nav` estava preso por comportamento.
# --------------------------------------------------------------------------


def test_o_conjunto_de_mobilia_e_exatamente_estas_quatro_tags():
    """A lista literal pega a remoção; os quatro testes abaixo pegam a tag que
    está na lista e não funciona."""
    from adsense_checks.text import BOILERPLATE_ELEMENTS

    assert sorted(BOILERPLATE_ELEMENTS) == ["aside", "footer", "header", "nav"]


def test_nav_no_wrapper_e_mobilia():
    """600 palavras de menu em volta de seis de conteúdo: sem reconhecer o
    <nav>, o wrapper volta OK sobre o menu."""
    html = (
        '<html><body><div id="wrapper">'
        f"<nav><p>{_palavras(600)}</p></nav>"
        f"<p>{_palavras(6)}</p></div></body></html>"
    )
    d = measure_depth(html)
    assert d.status is Status.ERROR, d.reason
    assert "not separated from page furniture" in d.reason
    # `words - editorial_words`, nessa ordem: 606 medidas, 6 editoriais.
    assert "still holds 600 words" in d.reason


def test_header_no_wrapper_e_mobilia():
    """Um <header> que não é de nenhum <article> é o header do site."""
    html = (
        '<html><body><div id="wrapper">'
        f"<header><p>{_palavras(600)}</p></header>"
        f"<p>{_palavras(6)}</p></div></body></html>"
    )
    d = measure_depth(html)
    assert d.status is Status.ERROR, d.reason
    assert "not separated from page furniture" in d.reason


def test_footer_no_wrapper_e_mobilia():
    """Um <footer> que não é de nenhum <article> é o rodapé do site."""
    html = (
        '<html><body><div id="wrapper">'
        f"<footer><p>{_palavras(600)}</p></footer>"
        f"<p>{_palavras(6)}</p></div></body></html>"
    )
    d = measure_depth(html)
    assert d.status is Status.ERROR, d.reason
    assert "not separated from page furniture" in d.reason


def test_aside_no_wrapper_e_mobilia():
    """Um <aside> que não é de nenhum <article> é a barra lateral da página."""
    html = (
        '<html><body><div id="wrapper">'
        f"<aside><p>{_palavras(600)}</p></aside>"
        f"<p>{_palavras(6)}</p></div></body></html>"
    )
    d = measure_depth(html)
    assert d.status is Status.ERROR, d.reason
    assert "not separated from page furniture" in d.reason


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
    "charset=utf-8" no corpo sem declarar nada.

    Os offsets anteriores eram 900 e 1100, uma folga de 100 de cada lado: medido,
    `_DECLARATION_WINDOW 1024 -> 1099` sobrevivia. Aqui a declaração é posicionada
    no byte, e o par 1023/1024 prende a janela em qualquer direção.
    """
    from adsense_checks.text import _declared_charset

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


def test_exatamente_25_palavras_ja_nao_e_casca():
    """`>= JS_SHELL_MAX_WORDS`: no valor exato a página tem conteúdo servido."""
    def casca(n):
        return ('<html><head><script src="/b.js"></script></head><body>'
                f'<p>{" ".join(["palavra"] * n)}</p><div id="root"></div></body></html>')

    assert looks_javascript_rendered(casca(24)) is True
    assert looks_javascript_rendered(casca(25)) is False


def test_um_svg_entre_duas_palavras_nao_as_solda_numa_so():
    """O acoplamento entre o corte de subárvore e a guarda de casca de SPA.

    `_collect` passou a emitir o separador de bloco também para as subárvores
    `dropped`, então `Entrar<svg/>Sair` — o ícone inline de todo cabeçalho —
    deixou de virar a palavra única "EntrarSair". Isso é mais correto, e mexe em
    `extract_text`, que `looks_javascript_rendered` consulta: uma casca de 24
    palavras com um ícone assim passou a contar 25 e a cruzar
    `JS_SHELL_MAX_WORDS`, devolvendo `False` para uma página sem conteúdo
    nenhum. A contagem é o mais fraco dos três sinais da guarda e o único
    arbitrário; este teste existe para que ela não volte a se mover em silêncio
    junto com a extração.
    """
    assert extract_text("<p>Entrar<svg><path/></svg>Sair</p>").split() == ["Entrar", "Sair"]

    corpo = " ".join(["palavra"] * 23)
    casca = ('<html><head><script src="/b.js"></script></head><body>'
             f'<nav>{corpo}</nav>Entrar<svg><path/></svg>Sair'
             '<div id="root"></div></body></html>')

    assert word_count(extract_text(casca)) == 25
    assert looks_javascript_rendered(casca) is False


def test_exatamente_o_minimo_de_palavras_nao_fica_abaixo_dele():
    """`words < min_words`: no valor exato a página não está abaixo da barra."""
    assert classify_depth(299, min_words=300) is Status.WARNING
    assert classify_depth(300, min_words=300) is not Status.WARNING


def test_empate_de_palavras_mantem_o_primeiro_container():
    """`words > best_words` em `_richest`: no empate, o primeiro ganha. Com
    `>=` o último venceria, e qual `<article>` é "o principal" mudaria com a
    ordem no documento."""
    html = ("<html><body>"
            "<article><p>um dois tres quatro cinco</p></article>"
            "<article><p>seis sete oito nove dez</p></article>"
            "</body></html>")
    assert "um dois" in main_content_text(html)


def test_o_rotulo_e_a_classificacao_concordam_na_fronteira():
    """Há dois `words < min_words`: um em `classify_depth`, que decide o status,
    e outro em `describe_depth`, que escreve o rótulo. O docstring do módulo
    existe por causa de uma divergência entre os dois — o resumo dizia
    "BORDERLINE (300-450)" enquanto o código classificava 450 como OK — então
    a fronteira tem de ser a mesma nos dois."""
    assert classify_depth(300, min_words=300) is Status.INFO
    assert "borderline" in describe_depth(300, min_words=300)
    assert classify_depth(299, min_words=300) is Status.WARNING
    assert "below the configured threshold" in describe_depth(299, min_words=300)


# --------------------------------------------------------------------------
# Conteúdo das coleções.
#
# Cada teste abaixo leva TRÊS partes, e nenhuma delas basta sozinha:
#
#   1. a contagem literal, que pega a remoção de uma entrada;
#   2. a iteração sobre uma lista LITERAL, escrita aqui, que pega a troca de
#      uma entrada por outra;
#   3. a lista inversa — entradas que têm de NÃO estar no conjunto — que pega
#      o outro lado da troca, e a inclusão de uma entrada a mais.
#
# Iterar o próprio conjunto seria `x in S for x in S`: a função consumida é
# definida como pertinência no conjunto iterado, então trocar uma entrada leva
# o caso de teste junto e a suíte inteira continua verde. Medido: trocar `wbr`
# por `marquee` em `VOID_ELEMENTS`, `a` por `div` em `INLINE_ELEMENTS` e
# `.css` por `.html` em `_ASSET_SUFFIXES` — cada troca rodada sozinha contra a
# suíte toda — não derrubava um único teste.
# --------------------------------------------------------------------------

# Os catorze elementos void do HTML5, escritos à mão.
ELEMENTOS_VOID = (
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
)

# Os oito elementos cujo conteúdo não é texto da página.
ELEMENTOS_DESCARTADOS = (
    "head", "iframe", "noscript", "script", "style", "svg", "template", "title",
)

# Os trinta e quatro elementos que não separam palavras.
ELEMENTOS_INLINE = (
    "a", "abbr", "b", "bdi", "bdo", "big", "cite", "code", "data", "del",
    "dfn", "em", "font", "i", "ins", "kbd", "label", "mark", "q", "rp",
    "rt", "ruby", "s", "samp", "small", "span", "strong", "sub", "sup",
    "time", "tt", "u", "var", "wbr",
)

# Os seis containers que podem abrigar o corpo de um artigo.
CONTAINERS_DE_CONTEUDO = ("article", "body", "div", "main", "section", "td")

# Os três elementos que são a página inteira, e não uma parte dela.
ELEMENTOS_DE_PAGINA_INTEIRA = ("#document", "body", "html")

# Os seis ids de ponto de montagem que os frameworks deixam no HTML servido.
IDS_DE_MOUNT = ("__next", "__nuxt", "___gatsby", "app", "main-app", "root")


def _ruido(n: int) -> str:
    """n palavras de chrome, distinguíveis das de `_palavras` na saída."""
    return " ".join(["ruido"] * n)


def _duas_divs(meio: str) -> str:
    """Duas divs disputando a pontuação, com `meio` na frente do <p> da maior.

    Um elemento void não empilha: o `<p>` de 200 palavras continua filho do
    `div#a` e credita tudo a ele. Um elemento que empilha vira o pai do `<p>`,
    não é container de conteúdo, e o `div#a` fica só com a metade que o
    crédito de avô paga — perde para o `div#b`, que é menor.
    """
    return (
        f'<html><body><div id="a"><{meio}><p>{_palavras(200)}</p></div>'
        f'<div id="b"><p>{_ruido(150)}</p></div></body></html>'
    )


def _com_e_sem_container(tag: str) -> str:
    """Um `<p>` longo dentro de `tag`, dentro de um `<div>` que também pontua.

    Se `tag` é container de conteúdo, ela leva o crédito inteiro do parágrafo
    e vence; se não é, o crédito de avô cai no `div#fora`, que já tem ruído
    próprio, e é o `div#fora` que vence — trazendo o ruído junto.

    O ruído é um parágrafo só de link. Já foi um parágrafo de texto, e então
    `_prose_region` subia do `<{tag}>` para o `div#fora` com razão: dois
    parágrafos de texto no mesmo container são o mesmo conteúdo para quem lê.
    Link não é prosa, então o ruído pesa contra a subida em vez de a favor, e o
    teste continua perguntando só o que pergunta: quem ganha a pontuação.
    """
    return (
        f'<html><body><div id="fora"><p><a href="/r">{_ruido(20)}</a></p>'
        f'<{tag} id="dentro"><p>{_palavras(200)}</p></{tag}></div></body></html>'
    )


def test_todo_elemento_void_nao_engole_o_resto_do_documento():
    """Sem end tag, um elemento void empilhado nunca desempilha. O parser em
    árvore tolera isso melhor que o contador que ele substituiu, e o texto
    extraído sai igual — o comentário do módulo diz isso e está certo. O que
    muda é a estrutura: o irmão seguinte vira filho dele, e a pontuação de
    conteúdo principal credita o pai errado. É essa a consequência observável,
    e é a terceira asserção de cada volta."""
    from adsense_checks.text import VOID_ELEMENTS, _parse

    assert len(VOID_ELEMENTS) == 14
    for tag in ELEMENTOS_VOID:
        html = f"<html><body><p>antes</p><{tag} x=1><p>depois</p></body></html>"
        assert extract_text(html) == "antes depois", tag
        # E nada fica aberto na pilha por conta dele.
        assert [n.tag for n in _parse(html).open] == ["#document"], tag
        # E o irmão seguinte continua sendo irmão, não filho.
        assert main_content_text(_duas_divs(tag)).startswith("palavra"), tag

    # O inverso: um elemento que NÃO é void empilha, e aí o div menor vence.
    # É o que uma entrada a mais nesta lista quebraria.
    for tag in ("blockquote", "b", "figure", "marquee", "span"):
        assert main_content_text(_duas_divs(tag)).startswith("ruido"), tag


def test_todo_elemento_descartado_some_da_extracao():
    from adsense_checks.text import DROPPED_ELEMENTS

    assert len(DROPPED_ELEMENTS) == 8
    for tag in ELEMENTOS_DESCARTADOS:
        html = f"<html><body><p>visivel</p><{tag}>escondido</{tag}></body></html>"
        assert "escondido" not in extract_text(html), tag
        assert "visivel" in extract_text(html), tag

    # E nada além destes é descartado: marcar uma tag de conteúdo aqui apaga a
    # prosa da página do relatório e reporta o autor como se não tivesse escrito.
    for tag in ("article", "aside", "div", "footer", "header", "main",
                "nav", "p", "section", "td"):
        html = f"<html><body><p>visivel</p><{tag}>presente</{tag}></body></html>"
        assert "presente" in extract_text(html), tag


def test_todo_elemento_inline_nao_parte_uma_palavra():
    """"Hyper<em>text</em>" é uma palavra, não duas: um separador aqui inventa
    uma palavra que não está na página."""
    from adsense_checks.text import INLINE_ELEMENTS

    assert len(INLINE_ELEMENTS) == 34
    for tag in ELEMENTOS_INLINE:
        html = f"<p>hyper<{tag}>texto</{tag}></p>"
        assert extract_text(html) == "hypertexto", tag
        assert word_count(extract_text(html)) == 1, tag

    # E o inverso, que é o outro lado de toda troca: uma tag de bloco tem de
    # separar. `br`, `hr`, `img` e `input` estão fora da lista de propósito —
    # o comentário do módulo os nomeia —, então soldá-los aqui é o defeito.
    for tag in ("br", "div", "h1", "hr", "img", "input", "li", "p", "section", "td"):
        html = f"<p>hyper<{tag}>texto</{tag}></p>"
        assert extract_text(html) == "hyper texto", tag
        assert word_count(extract_text(html)) == 2, tag


def test_todo_container_de_conteudo_pode_vencer_a_pontuacao():
    """`article` e `main` não passam por aqui: o atalho de `_main_container`
    devolve os dois antes de a pontuação rodar, e a participação deles NESTE
    conjunto não tem saída pública que a distinga — ver
    `test_article_e_main_no_conjunto_de_containers_nao_mudam_saida_nenhuma`.
    `body` tem forma própria, porque vencer com ele significa não ter isolado
    nada — `test_body_vence_a_pontuacao_e_isso_e_nada_isolado`."""
    from adsense_checks.text import CONTENT_CONTAINERS

    assert len(CONTENT_CONTAINERS) == 6
    for tag in CONTAINERS_DE_CONTEUDO:
        if tag in ("article", "body", "main"):
            continue
        assert "ruido" not in main_content_text(_com_e_sem_container(tag)), tag
        assert main_content_text(_com_e_sem_container(tag)).count("palavra") == 200, tag

    # E o inverso: mobília nunca pode vencer a pontuação. Um `<nav>` aqui é o
    # menu virando conteúdo principal — o defeito que a pontuação existe para
    # não cometer.
    for tag in ("aside", "footer", "form", "header", "li", "nav", "span", "ul"):
        assert "ruido" in main_content_text(_com_e_sem_container(tag)), tag


def test_body_vence_a_pontuacao_e_isso_e_nada_isolado():
    """`body` é container de conteúdo porque uma página pode pendurar os
    parágrafos direto nele — e um único `<p>` no rodapé basta, porque o crédito
    de avô cai no body sempre que o pai (`<footer>`, `<nav>`) não é container.

    Sem `body` no conjunto, o `<div>` menor venceria e a medição sairia como se
    o rodapé tivesse sido separado do artigo. Com ele, `_main_container` diz
    que não separou nada e `measure_depth` recusa a contagem."""
    html = (f"<html><body><footer><p>{_ruido(150)}</p></footer>"
            f'<div id="d"><p>{_palavras(100)}</p></div></body></html>')
    d = measure_depth(html)
    assert d.status is Status.ERROR
    assert "main content not isolated" in d.reason


def test_article_e_main_pertencem_ao_conjunto_de_containers_por_comportamento():
    """Os dois membros que quase ficaram presos só por `assert <= CONTENT_CONTAINERS`.

    `_main_container` devolve o `<main>` ou o `<article>` mais rico ANTES de
    pontuar, então a pontuação só os alcança quando nenhum dos dois tem uma
    única palavra. Isso parece torná-los inobserváveis pela pontuação — e é
    falso, por uma assimetria da fonte: `_richest` mede em PALAVRAS e o peso em
    `credit` mede em CARACTERES. Um `<p>` só de pontuação tem zero palavras, e
    o atalho o recusa, mas tem peso alto e credita. Com um concorrente ao lado,
    tirar `article` ou `main` do conjunto vira ERROR ("main content not
    isolated", 3 palavras) onde deveria ser FAIL.

    O atalho, que é o que as duas tags decidem no caso comum, segue pinado
    embaixo: um `<main>` ou um `<article>` vence um `<div>` muito maior."""
    from adsense_checks.text import CONTENT_CONTAINERS

    assert {"article", "main"} <= CONTENT_CONTAINERS

    # Um <p> só de pontuação: zero palavras para o atalho, peso alto para a
    # pontuação. É o único documento em que ser membro do conjunto decide algo.
    for tag in ("main", "article"):
        html = (f"<html><body><{tag}><p>{'!' * 400}</p></{tag}>"
                '<div id="d"><p>palavra palavra palavra</p></div></body></html>')
        r = measure_depth(html)
        assert r.status is Status.FAIL, tag
        assert r.words == 0, tag
        assert "no main content found" in r.reason, tag

    for tag in ("main", "article"):
        html = (f"<html><body><{tag}><p>{_palavras(200)}</p></{tag}>"
                f'<div id="maior"><p>{_ruido(400)}</p></div></body></html>')
        assert "ruido" not in main_content_text(html), tag
        assert main_content_text(html).count("palavra") == 200, tag

    # A ordem do atalho: `<main>` antes de `<article>`.
    html = (f"<html><body><main><p>{_palavras(50)}</p></main>"
            f"<article><p>{_ruido(400)}</p></article></body></html>")
    assert "ruido" not in main_content_text(html)

    # E o documento em que o conjunto chega a ser consultado sobre eles: sem
    # palavra nenhuma, a resposta é a mesma nos dois lados da troca.
    for tag in ("main", "article"):
        d = measure_depth(f"<html><body><{tag}><p>!!! ???</p></{tag}></body></html>")
        assert d.words == 0, tag
        assert "no main content found" in d.reason, tag


def test_todo_elemento_de_pagina_inteira_significa_nada_isolado():
    """Vencer a pontuação com um destes é o heurístico dizendo que não separou
    nada — e `measure_depth` reporta isso como ERROR em vez de medir o menu.

    Como vencedor, só `body` é alcançável, e a interseção abaixo é a prova: o
    vencedor da pontuação sai de `credit`, que só aceita tag em
    `CONTENT_CONTAINERS`, e o outro caminho de saída de `_main_container`
    devolve `False` fixo sem consultar este conjunto. `html` e `#document`
    nunca vencem; o conjunto só é perguntado sobre eles como teto de
    `_titled_region`, e é lá que o teste seguinte os fixa por comportamento."""
    from adsense_checks.text import CONTENT_CONTAINERS, WHOLE_PAGE_ELEMENTS

    assert sorted(WHOLE_PAGE_ELEMENTS) == sorted(ELEMENTOS_DE_PAGINA_INTEIRA)
    alcancaveis = CONTENT_CONTAINERS & WHOLE_PAGE_ELEMENTS
    assert alcancaveis == {"body"}

    html = "<html><body><p>" + _palavras(400) + "</p></body></html>"
    d = measure_depth(html)
    assert d.status is Status.ERROR
    assert "main content not isolated" in d.reason


@pytest.mark.parametrize(
    "abre,fecha,teto",
    [
        ("<html><body>", "</body></html>", "body"),
        ("<html>", "</html>", "html"),
        ("", "", "#document"),
    ],
)
def test_todo_elemento_de_pagina_inteira_e_o_teto_da_regiao_do_titulo(abre, fecha, teto):
    """Um <h1> e o bloco vencedor que só se encontram num destes dividem a
    página, não uma região: o bloco volta sozinho. Sem `<body>` no documento o
    ancestral comum é `html`; sem `<html>`, é o `#document` — os três têm de
    estar no conjunto, ou a página inteira vira "região do título"."""
    html = (
        f"{abre}<h1>Titulo</h1>"
        f'<div id="a"><p>{_palavras(200)}</p></div>'
        f'<div id="b"><p>{_ruido(100)}</p></div>{fecha}'
    )
    from adsense_checks.text import _main_container, _parse

    # A premissa: o <h1> e o vencedor são irmãos, então o ancestral comum é o pai.
    vencedor = _main_container(_parse(html)).node
    assert vencedor.attrs.get("id") == "a", teto
    assert vencedor.parent.tag == teto
    assert main_content_text(html) == _palavras(200), teto


def test_todo_id_de_mount_conhecido_identifica_uma_casca():
    from adsense_checks.text import _MOUNT_IDS

    def casca(ident: str) -> str:
        return ('<html><head><script src="/b.js"></script></head>'
                f'<body><div id="{ident}"></div></body></html>')

    assert len(_MOUNT_IDS) == 6
    for ident in IDS_DE_MOUNT:
        assert looks_javascript_rendered(casca(ident)) is True, ident

    # E o inverso: um id desconhecido não é casca só por estar vazio. Um
    # wrapper de tema vazio é uma página fina, não uma página renderizada no
    # cliente, e as duas coisas viram recomendações diferentes.
    for ident in ("conteudo-principal", "container", "page", "site", "wrapper", ""):
        assert looks_javascript_rendered(casca(ident)) is False, ident


def test_nenhum_elemento_void_esta_entre_os_descartados():
    """A parte de `VOID_ELEMENTS` que de fato carrega o resultado, e a única que
    o docstring do módulo chama de fatal: um elemento sem end tag marcado como
    descartado empilharia um nó que nunca desempilha e levaria o resto do
    documento junto. Era o defeito original — `meta` e `link` na lista de
    descarte zeravam a extração de qualquer página real."""
    from adsense_checks.text import DROPPED_ELEMENTS, VOID_ELEMENTS, extract_text

    assert not (VOID_ELEMENTS & DROPPED_ELEMENTS)

    # E a consequência, para o caso de a interseção deixar de ser vazia um dia.
    for tag in ("meta", "link", "img"):
        html = f"<html><body><p>antes</p><{tag} x=1><p>depois</p></body></html>"
        assert extract_text(html) == "antes depois", tag
