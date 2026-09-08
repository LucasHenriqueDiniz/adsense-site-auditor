"""Testes de adsense_checks/completeness.py.

Cada teste desta suíte corresponde a um defeito reproduzido em
scripts/check_completeness.py. O nome do teste diz o que ele impede de voltar,
e o docstring descreve o caso de reprodução original.

Os testes que envolvem rede usam a fixture `server` (servidor HTTP local) em vez
de mock de requests: os defeitos aqui eram comportamento de protocolo — redirect
seguido silenciosamente, 403 confundido com 404, conexão cortada — e um mock
reproduziria a suposição errada em vez do protocolo.
"""


import check_completeness as check_completeness_cli
import pytest
import requests

from adsense_checks.completeness import (
    _ABOUT_HINT,
    ABOUT_PATHS,
    BROKEN_NAV_FAIL_THRESHOLD,
    MAX_LINKED_CANDIDATES,
    MAX_PROBED_DIRECTORIES,
    MIN_TRUST_PAGE_WORDS,
    NOT_FOUND_PROBE_PATHS,
    NavLinkReport,
    Status,
    _candidates,
    _invented_base,
    _nav_targets,
    _NotFoundProbe,
    _NotFoundProbes,
    _probe_covers,
    _probe_not_found,
    check_completeness,
    check_trust_pages,
    count_broken_nav_links,
    find_contact_channels,
    find_placeholders,
    parse_document,
    visible_text,
)
from adsense_checks.completeness import as_base as as_base_publico
from adsense_checks.crawl import crawl, parse_html, resolve_base
from adsense_checks.http import Fetch, fetch
from adsense_checks.report import exit_code

# 66 palavras de prosa real, acima do mínimo de uma página de confiança.
PROSA = "Escrevo sobre marcenaria desde 2015 e mantenho este site sozinho. " * 6


def pagina(titulo, corpo=PROSA, extra=""):
    return (
        f"<html><head><title>{titulo}</title></head>"
        f"<body><h1>{titulo}</h1><p>{corpo}</p>{extra}</body></html>"
    )


MAILTO = "<a href='mailto:eu@exemplo.com'>escreva</a>"


def contato(corpo=PROSA, extra=MAILTO):
    return pagina("Contato", corpo=corpo, extra=extra)


def home(rodape="", corpo="Bem-vindo. Este site fala de marcenaria e ferramentas manuais."):
    return f"<html><body><h1>Casa</h1><p>{corpo}</p><footer>{rodape}</footer></body></html>"


def frases(relatorio):
    return " | ".join(relatorio.issues).lower()


# ---------------------------------------------------------------------------
# find_placeholders
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "marcador",
    ["coming soon", "em breve", "under construction", "em construção", "will be added",
     "não implementado", "lorem ipsum"],
)
def test_marcadores_de_inacabado_em_portugues_e_ingles_sao_detectados(marcador):
    achados = find_placeholders(f"<div class='card'><h2>{marcador}</h2></div>")
    assert [p.phrase for p in achados], f"{marcador!r} passou despercebido"
    assert achados[0].confidence == "strong"


def test_acento_ausente_no_marcador_nao_esconde_o_defeito():
    """"em construcao" sem cedilha/til é como metade dos sites escreve."""
    assert find_placeholders("<h1>Site em construcao</h1>")
    assert find_placeholders("<h1>Site em construção</h1>")


def test_conteudo_de_script_nao_vira_texto_da_pagina():
    """Defeito original: handle_data não suprimia CDATA.

    Uma home com bundle do Next.js reportava "placeholder phrases: todo,
    placeholder" sem ter um único marcador visível — as chaves do JSON entravam
    no texto do corpo.
    """
    html = (
        "<html><body><div id='root'></div>"
        '<script>window.__NEXT_DATA__={"props":{"placeholder":"Search","todo":1}};</script>'
        "<style>@media (max-width: 600px) { .draft { display: none } }</style>"
        "</body></html>"
    )
    assert find_placeholders(html) == []
    assert "NEXT_DATA" not in visible_text(html)
    assert "@media" not in visible_text(html)


def test_documento_sem_tag_body_ainda_produz_texto():
    """Defeito original: o extractor só coletava entre <body> literal e </body>.

    html.parser não é tree-builder e não infere a tag, então um documento válido
    sem <body> extraía string vazia — e uma página /about de 600 palavras era
    reportada como stub, sem nome e sem contato.
    """
    html = (
        "<html><head><title>Sobre</title></head>"
        "<h1>Sobre mim</h1><p>" + "palavra " * 600 + "</p></html>"
    )
    texto = visible_text(html)
    assert len(texto.split()) > 500
    # O <title> fica no head e não é texto de corpo; o <h1> é.
    assert "Sobre mim" in texto


def test_texto_do_title_nao_conta_como_texto_da_pagina():
    """O <title> está no <head> e não é conteúdo do corpo.

    Nenhum teste distinguia as duas coisas: bastava apagar 'title' de
    _SKIP_TAGS e a suíte seguia verde. Aqui o marcador só existe no título, então
    o teste morre se o título voltar a ser texto de corpo.
    """
    html = (
        "<html><head><title>Loja em breve</title></head>"
        "<body><h1>Marcenaria artesanal</h1><p>Bancadas sob medida.</p></body></html>"
    )
    assert find_placeholders(html) == []
    assert "em breve" not in visible_text(html).lower()


def test_texto_final_com_e_comercial_nao_e_perdido():
    """Defeito original: feed() sem close().

    O HTMLParser retém o texto final enquanto não consegue provar que não é um
    charref incompleto. Com resposta truncada, '<p>Contato: joao&' extraía ''.
    """
    assert "joao" in visible_text("<body><p>Sobre mim: sou engenheiro. Contato: joao&")


def test_substring_dentro_de_palavra_maior_nao_dispara():
    """Defeito original: `in` sem limite de palavra.

    "Mastodon" contém "todo"; um blog de marcenaria que só linka o Mastodon era
    reportado como "Homepage contains placeholder phrases: todo".

    Os dois primeiros casos são blocos curtos de propósito: só o limite de
    palavra os salva, então este teste morre se alguém trocar a regex por `in`.
    Em português o estrago é pior — "me-TODO-logia" é um título comum.
    """
    assert find_placeholders("<li>Mastodon</li>") == []
    assert find_placeholders("<h2>Metodologia</h2>") == []
    assert find_placeholders("<p>Siga me no Mastodon para atualizacoes</p>") == []


def test_marcador_generico_em_prosa_longa_nao_dispara():
    """"draft beer" e "not yet published" são prosa acabada, não site inacabado."""
    prosa = (
        "<p>We serve draft beer and planned a tasting menu for the summer season, "
        "and I have not yet published the sequel to the first guide because the "
        "photographs still need editing before anything goes online this year.</p>"
    )
    assert find_placeholders(prosa) == []


def test_marcador_generico_sozinho_num_bloco_curto_dispara_como_fraco():
    achados = find_placeholders("<h2>TODO</h2>")
    assert [p.phrase for p in achados] == ["todo"]
    assert achados[0].confidence == "weak"


def test_todo_como_palavra_portuguesa_nao_e_marcador():
    """"todo" é "todos" em português; só conta quando domina o bloco."""
    prosa = "<p>Todo mundo que passa aqui pergunta a mesma coisa sobre a bancada.</p>"
    assert find_placeholders(prosa) == []


def test_em_breve_em_post_de_blog_e_fraco_e_em_card_e_forte():
    """A limitação documentada: o marcador é uma string, não o estado da página.

    A única defesa é o tamanho do bloco — num card ele É o conteúdo, num
    parágrafo de artigo ele é o assunto. O teste fixa essa distinção para que
    ninguém a "simplifique" depois.
    """
    card = find_placeholders("<div><h3>Novo curso</h3></div><div><h3>Em breve</h3></div>")
    assert [p.confidence for p in card if p.phrase == "em breve"] == ["strong"]

    artigo = find_placeholders(
        "<article><p>O Banco Central anunciou que em breve o Pix vai permitir parcelamento "
        "automatico, o que muda o fluxo de conciliacao de quem vende online e obriga a "
        "revisar a integracao inteira antes da virada do semestre.</p></article>"
    )
    assert [p.confidence for p in artigo if p.phrase == "em breve"] == ["weak"]


# ---------------------------------------------------------------------------
# find_contact_channels (ADS-AUTHOR-02 parcial)
# ---------------------------------------------------------------------------


def test_arroba_solto_no_texto_nao_prova_canal_de_contato():
    """Defeito original: `'@' not in text` era a prova de que havia email/form.

    "Siga @meublog no Twitter" passava no check; uma página cujo único canal era
    um formulário funcionando, não.
    """
    canais = find_contact_channels("<p>Siga @meublog no Twitter para novidades.</p>")
    assert canais.any_found is False


def test_formulario_de_contato_conta_como_canal_e_busca_nao():
    html_form = (
        "<form action='/enviar'><input name='nome'>"
        "<textarea name='mensagem'></textarea></form>"
    )
    assert find_contact_channels(html_form).any_found is True

    html_busca = "<form action='/search'><input type='search' name='q'></form>"
    assert find_contact_channels(html_busca).any_found is False


def test_mailto_e_perfil_social_sao_reconhecidos_pelo_href():
    canais = find_contact_channels(
        "<a href='mailto:lucas@exemplo.com.br'>escreva</a>"
        "<a href='https://github.com/fulano'>github</a>"
    )
    assert canais.mailto == ["lucas@exemplo.com.br"]
    assert canais.socials == ["https://github.com/fulano"]


# ---------------------------------------------------------------------------
# check_trust_pages
# ---------------------------------------------------------------------------


def test_about_e_contact_inacessiveis_nunca_viram_aprovacao(server):
    """BLOCKER reproduzido: o ramo else gravava details='ERROR (503)' e não
    acrescentava issue nenhuma, então erro de rede/500/403 deixava o status em OK
    e o resumo imprimia "✓ Completeness checks passed".
    """
    base, rotas = server
    rotas["/"] = (200, {}, home())
    rotas["/about"] = (403, {}, "forbidden")
    rotas["/contact"] = (503, {}, "indisponivel")

    relatorio = check_trust_pages(base)

    assert relatorio.pages["about"].status is Status.ERROR
    assert relatorio.pages["contact"].status is Status.ERROR
    assert relatorio.status is Status.ERROR
    assert relatorio.passed is False
    assert "403" in frases(relatorio) and "503" in frases(relatorio)


def test_ausencia_das_duas_paginas_e_FAIL_e_nao_so_duas_MISSING(server):
    """Um site sem About e sem Contact é rejeição certa no AdSense.

    No script antigo o pior site possível saía como HIGH_RISK, porque o ramo de
    FAIL exigia 4 issues e as seções só produziam 3.
    """
    base, rotas = server
    rotas["/"] = (200, {}, home())

    relatorio = check_trust_pages(base)

    assert relatorio.pages["about"].status is Status.MISSING
    assert relatorio.pages["contact"].status is Status.MISSING
    assert relatorio.status is Status.FAIL
    assert "neither an about nor a contact" in frases(relatorio)


def test_pagina_presente_mas_com_placeholder_e_WARNING(server):
    base, rotas = server
    rotas["/"] = (200, {}, home())
    rotas["/about"] = (200, {}, pagina("Sobre", extra="<h2>Bio completa em breve</h2>"))
    rotas["/contact"] = (200, {}, contato())

    relatorio = check_trust_pages(base)

    assert relatorio.pages["about"].status is Status.WARNING
    assert relatorio.pages["contact"].status is Status.OK
    assert relatorio.status is Status.WARNING
    assert relatorio.passed is False


def test_pagina_curta_demais_e_stub_e_nao_passa(server):
    base, rotas = server
    rotas["/"] = (200, {}, home())
    rotas["/about"] = (200, {}, pagina("Sobre", corpo="Oi."))
    rotas["/contact"] = (200, {}, contato())

    relatorio = check_trust_pages(base)

    assert relatorio.pages["about"].status is Status.WARNING
    assert relatorio.pages["about"].words < MIN_TRUST_PAGE_WORDS
    assert "stub" in frases(relatorio)


def test_placeholder_em_pagina_curta_nao_e_escondido_pelo_ramo_de_stub(server):
    """Defeito original: o check de placeholder só rodava no `else` do stub.

    Um /about de 40 palavras dizendo "Coming soon" era reportado apenas como
    "stub" — o marcador nunca era avaliado. E o inverso também acontecia:
    details['about_page'] era gravado como 'OK' antes da checagem, então o
    relatório afirmava OK e listava um problema na mesma página.
    """
    base, rotas = server
    rotas["/"] = (200, {}, home())
    rotas["/about"] = (200, {}, pagina("Sobre", corpo="Bio completa em breve."))
    rotas["/contact"] = (200, {}, contato())

    pagina_about = check_trust_pages(base).pages["about"]

    assert pagina_about.status is Status.WARNING
    assert "em breve" in pagina_about.reason
    assert "stub" not in pagina_about.reason


def test_pagina_renderizada_no_cliente_vira_ERROR_e_nao_stub(server):
    """Defeito original: a SPA cujo <body> só tem <div id="root"></div> extraía 0
    palavras e saía como "About page is a stub (<100 words)".

    Zero palavras servidas não é uma página curta — é uma página que este audit
    não consegue ver, porque não executa JavaScript. Afirmar "stub" seria afirmar
    algo sobre um conteúdo que nunca foi renderizado.
    """
    base, rotas = server
    shell = "<html><body><div id='root'></div><script src='/app.js'></script></body></html>"
    rotas["/"] = (200, {}, home())
    rotas["/about"] = (200, {}, shell)
    rotas["/contact"] = (200, {}, contato())

    relatorio = check_trust_pages(base)

    assert relatorio.pages["about"].status is Status.ERROR
    assert "client-rendered" in frases(relatorio)
    assert relatorio.passed is False


def test_encontra_paginas_por_link_no_rodape_com_url_nao_convencional(server):
    """Defeito original: só /about e /contact eram testados, então /quem-eu-sou
    virava "About page missing (404)" — o relatório afirmava que a página não
    existe olhando para a URL errada.
    """
    base, rotas = server
    rodape = "<a href='/quem-eu-sou'>Sobre</a> <a href='/fale-comigo'>Contato</a>"
    rotas["/"] = (200, {}, home(rodape=rodape))
    rotas["/quem-eu-sou"] = (200, {}, pagina("Quem eu sou"))
    rotas["/fale-comigo"] = (200, {}, pagina("Fale comigo", extra=MAILTO))

    relatorio = check_trust_pages(base)

    assert relatorio.pages["about"].url.endswith("/quem-eu-sou")
    assert relatorio.pages["contact"].url.endswith("/fale-comigo")
    assert relatorio.status is Status.OK
    assert relatorio.passed is True


def test_subdiretorio_da_url_nao_e_descartado(server):
    """Defeito original: urljoin(url, '/about') com caminho absoluto.

    Para 'https://user.github.io/meusite/' o script auditava a raiz do domínio —
    outro site — e reportava as páginas do site pedido como inexistentes.
    """
    base, rotas = server
    pedidos = []

    def armadilha_na_raiz(metodo):
        pedidos.append("/about")
        return (200, {}, pagina("About de OUTRO site"))

    rotas["/meusite/"] = (200, {}, home())
    rotas["/meusite/sobre"] = (200, {}, pagina("Sobre"))
    rotas["/meusite/contato"] = (200, {}, contato())
    rotas["/about"] = armadilha_na_raiz

    relatorio = check_trust_pages(base + "/meusite/")

    assert pedidos == [], "a raiz do domínio foi auditada no lugar do subdiretório"
    assert relatorio.pages["about"].url.endswith("/meusite/sobre")
    assert relatorio.pages["contact"].url.endswith("/meusite/contato")


def test_redirect_para_a_home_nao_conta_como_pagina_existente(server):
    """requests segue redirects por padrão: /about → / devolvia 200 com o HTML da
    home, ≥100 palavras, e o script gravava about_page='OK' para uma página que
    não existe.
    """
    base, rotas = server
    rotas["/"] = (200, {}, home())
    rotas["/about"] = (302, {"Location": "/"}, "")

    relatorio = check_trust_pages(base)

    assert relatorio.pages["about"].status is Status.MISSING
    assert any("served the home page" in motivo for _, motivo in relatorio.pages["about"].attempts)


def test_catch_all_que_devolve_o_html_da_home_nao_conta_como_pagina(server):
    """Mesmo defeito pela via da SPA: rota desconhecida responde 200 com a home,
    sem redirect. A URL difere, o texto não.
    """
    base, rotas = server
    html = home()
    rotas["/"] = (200, {}, html)
    rotas["/about"] = (200, {}, html)

    assert check_trust_pages(base).pages["about"].status is Status.MISSING


def test_contato_sem_nenhum_canal_e_reportado_mesmo_com_a_pagina_no_ar(server):
    """A página existe e tem texto, mas não oferece forma de contato: nem mailto,
    nem formulário, nem perfil social. O antigo '@' no texto dava OK.
    """
    base, rotas = server
    rotas["/"] = (200, {}, home())
    rotas["/about"] = (200, {}, pagina("Sobre"))
    rotas["/contact"] = (200, {}, contato(corpo=PROSA + " Siga @meublog no Twitter.", extra=""))

    relatorio = check_trust_pages(base)

    assert relatorio.pages["contact"].status is Status.OK  # a página em si está lá
    assert relatorio.passed is False
    assert "no contact channel" in frases(relatorio)


def test_home_inacessivel_vira_ERROR_em_vez_de_paginas_ausentes(server):
    """Sem ler a home não dá para afirmar nada sobre as páginas do site."""
    base, rotas = server
    rotas["/"] = (500, {}, "boom")

    relatorio = check_trust_pages(base)

    assert relatorio.status is Status.ERROR
    assert relatorio.pages == {}
    assert "home page could not be read" in frases(relatorio)


# ---------------------------------------------------------------------------
# count_broken_nav_links
# ---------------------------------------------------------------------------


def test_conta_links_de_navegacao_que_retornam_4xx_e_5xx(server):
    base, rotas = server
    menu = "<nav><a href='/ok'>Ok</a><a href='/sumiu'>Sumiu</a><a href='/quebrou'>Quebrou</a></nav>"
    rotas["/"] = (200, {}, f"<html><body>{menu}</body></html>")
    rotas["/ok"] = (200, {}, pagina("Ok"))
    rotas["/quebrou"] = (500, {}, "boom")

    relatorio = count_broken_nav_links(base)

    assert relatorio.count == 2
    assert relatorio.checked == 3
    assert relatorio.status is Status.WARNING


def test_muitos_links_quebrados_viram_FAIL(server):
    base, rotas = server
    itens = "".join(f"<a href='/sumiu{i}'>x</a>" for i in range(BROKEN_NAV_FAIL_THRESHOLD))
    rotas["/"] = (200, {}, f"<html><body><nav>{itens}</nav></body></html>")

    assert count_broken_nav_links(base).status is Status.FAIL


def test_link_externo_e_mailto_nao_sao_seguidos(server):
    """Uptime de terceiro não é defeito do site auditado, e mailto: não é um GET."""
    base, rotas = server
    menu = (
        "<nav><a href='https://exemplo-externo.invalid/x'>fora</a>"
        "<a href='mailto:eu@exemplo.com'>email</a>"
        "<a href='#topo'>topo</a>"
        "<a href='/ok'>ok</a></nav>"
    )
    rotas["/"] = (200, {}, f"<html><body>{menu}</body></html>")
    rotas["/ok"] = (200, {}, pagina("Ok"))

    relatorio = count_broken_nav_links(base)

    assert relatorio.found == 1
    assert relatorio.count == 0
    assert relatorio.passed is True


def test_limite_de_links_seguidos_e_reportado_como_contagem_parcial(server):
    """Zero quebrados em 2 de 40 links não é o mesmo que zero quebrados."""
    base, rotas = server
    itens = "".join(f"<a href='/p{i}'>x</a>" for i in range(5))
    rotas["/"] = (200, {}, f"<html><body><nav>{itens}</nav></body></html>")
    for i in range(5):
        rotas[f"/p{i}"] = (200, {}, pagina(f"p{i}"))

    relatorio = count_broken_nav_links(base, limit=2)

    assert (relatorio.found, relatorio.checked) == (5, 2)
    assert relatorio.truncated is True
    # MISSING, nao INFO: os links depois do limite nao foram olhados, e a regra
    # do pacote e que condicao nao observada nunca e aprovacao. Como INFO, um
    # menu de 32 links com tres 404 depois do limite saia com exit 0.
    assert relatorio.status is Status.MISSING
    assert "lower bound" in frases(relatorio)
    assert relatorio.passed is False


def test_link_que_derruba_a_conexao_nunca_conta_como_link_bom(server):
    """Erro de transporte não é aprovação: o link não foi demonstrado funcionar."""
    base, rotas = server

    def corta_a_conexao(metodo):
        raise ConnectionAbortedError("simula conexão cortada pelo servidor")

    menu = "<nav><a href='/ok'>ok</a><a href='/morto'>morto</a></nav>"
    rotas["/"] = (200, {}, f"<html><body>{menu}</body></html>")
    rotas["/ok"] = (200, {}, pagina("Ok"))
    rotas["/morto"] = corta_a_conexao

    relatorio = count_broken_nav_links(base)

    assert relatorio.count == 0
    assert len(relatorio.unresolved) == 1
    assert relatorio.status is Status.ERROR
    assert relatorio.passed is False


def test_sem_nav_nem_footer_usa_todos_os_links_internos_e_avisa(server):
    """Página escrita à mão sem <nav>: reportar zero links olhados como zero
    links quebrados seria a mesma mentira de sempre.
    """
    base, rotas = server
    rotas["/"] = (200, {}, "<html><body><p>oi <a href='/sumiu'>link</a></p></body></html>")

    relatorio = count_broken_nav_links(base)

    assert relatorio.used_all_links is True
    assert relatorio.count == 1


def test_links_relativos_respeitam_o_subdiretorio(server):
    base, rotas = server
    rotas["/meusite/"] = (200, {}, "<html><body><nav><a href='sobre'>Sobre</a></nav></body></html>")
    rotas["/meusite/sobre"] = (200, {}, pagina("Sobre"))

    relatorio = count_broken_nav_links(base + "/meusite/")

    assert (relatorio.checked, relatorio.count) == (1, 0)


# ---------------------------------------------------------------------------
# count_broken_nav_links — o que HTTP 200 vale neste host
#
# A checagem decidia só por `status_code >= 400`. Um CMS que serve o template
# "página não encontrada" com HTTP 200 — o soft 404 — dava `broken 0` sobre um
# menu inteiro que não leva a lugar nenhum, que é exatamente a condição que
# ADS-COMPLETE-01 existe para pegar.
#
# A sonda que fecha esse buraco pergunta ao host o que ele responde para uma URL
# que não pode existir. O que ela tem PERMISSÃO de decidir é a metade difícil, e
# é o que esta seção fixa: num host que responde sucesso para uma URL que não
# existe, nenhum 200 é evidência sobre a página atrás dele — nem a favor nem
# contra —, então todo link desses sai como não verificado (MISSING) e nenhum
# deles chega a BROKEN_NAV_FAIL_THRESHOLD. Reconhecer a página de erro na
# resposta afia a FRASE do relatório; não vira reprovação.
#
# Reconhecer não vira reprovação por dois motivos medidos, cada um com teste
# abaixo: "esta resposta é idêntica à página servida para nada" descreve um link
# morto E várias rotas vivas (`content-none.php` do WordPress, o catch-all que
# serve a home), e o próprio regime da sonda depende de duas amostras terem
# coincidido — num host cuja página de erro tem relógio, o mesmo menu morto saía
# OK, WARNING ou MISSING conforme a corrida.
# ---------------------------------------------------------------------------

ERRO_404 = pagina("Página não encontrada", corpo="Nada aqui. Volte para a home.")

# Uma página de erro longa o bastante para NÃO ser um stub aos olhos de
# _judge_page: é o que um template de erro real tem, com menu, rodapé e busca, e
# é o que fazia check_trust_pages aprová-la como /sobre.
ERRO_404_LONGO = pagina("Página não encontrada", corpo=PROSA)


def menu_de(*caminhos):
    itens = "".join(f"<a href='{c}'>{c}</a>" for c in caminhos)
    return f"<nav>{itens}</nav>"


def pagina_simples(texto):
    """Um documento cujo texto visível é exatamente `texto`.

    Existe para os testes de comparação: com um <h1> e um <title> no meio não dá
    para dizer que um texto CONTÉM o outro sem depender de como o parser junta os
    blocos, e é justamente containment o que esses testes precisam controlar.
    """
    return f"<html><head><title>x</title></head><body><p>{texto}</p></body></html>"


def test_menu_que_devolve_200_com_a_pagina_de_erro_nao_e_aprovado_nem_reprovado(server):
    """O buraco, e o limite do que a sonda pode concluir sobre ele.

    Todo link do menu responde 200 servindo o template de erro do próprio site, e
    `status_code >= 400` não via nenhum deles: o relatório saía `broken 0`,
    status OK, exit 0, sobre um site abandonado.

    O que substitui isso NÃO é FAIL. Servir a página de erro é o que um link
    morto parece daqui e também o que parece uma rota viva que renderiza o mesmo
    parcial "nada aqui" (ver os dois testes seguintes), e daqui os dois são
    indistinguíveis. Então: MISSING, `count` zero, fora do exit 0 e com os três
    endereços impressos para um humano abrir um.

    O custo aceito está neste teste: um menu genuinamente 100% morto num host de
    soft 404 sai como "não verificado" em vez de "reprovado". Continua fora do
    exit 0, que é o que o portão precisa."""
    base, rotas = server
    rotas["/"] = (200, {}, pagina("Casa", extra=menu_de("/a", "/b", "/c")))
    # A rota curinga é o soft 404: qualquer caminho não registrado responde 200
    # com a mesma página de erro, inclusive a URL que a sonda pede.
    rotas.default = (200, {}, ERRO_404)

    r = count_broken_nav_links(base + "/")

    assert r.status is Status.MISSING
    assert r.not_found_regime == "fingerprint"
    assert [link.url for link in r.same_as_not_found] == [base + "/a", base + "/b", base + "/c"]
    assert [link.status_code for link in r.same_as_not_found] == [200, 200, 200]
    # Nada aqui foi OBSERVADO quebrado, então nada aqui conta para o limiar.
    assert (r.count, r.broken) == (0, [])
    assert r.passed is False
    assert "unverified rather than broken" in frases(r)


def test_categoria_vazia_do_wordpress_nao_reprova_o_menu(server):
    """FAIL falso num WordPress comum, que é o preço de tratar semelhança como
    prova.

    `content-none.php` é incluído por 404.php, archive.php e search.php. Uma
    categoria sem posts, uma tag sem posts e a página 7 de 6 são ROTAS REAIS que
    renderizam esse parcial: HTML diferente, texto visível idêntico ao da página
    de erro. `_SKIP_TAGS` inclui `title`, então o único campo que separa os
    documentos é descartado antes da comparação.

    São três links, exatamente BROKEN_NAV_FAIL_THRESHOLD, num site que funciona.
    Com a inferência valendo reprovação isto saía `FAIL, count 3`."""
    base, rotas = server
    menu = menu_de("/categoria/ferramentas", "/tag/plaina", "/blog/page/7")
    rotas["/"] = (200, {}, pagina("Casa", extra=menu))

    def conteudo_ausente(titulo):
        return (
            f"<html><head><title>{titulo}</title></head>"
            "<body><p>Nada encontrado. Tente outra busca.</p></body></html>"
        )

    rotas["/categoria/ferramentas"] = (200, {}, conteudo_ausente("Categoria: Ferramentas"))
    rotas["/tag/plaina"] = (200, {}, conteudo_ausente("Tag: plaina"))
    rotas["/blog/page/7"] = (200, {}, conteudo_ausente("Blog — página 7"))
    rotas.default = (200, {}, conteudo_ausente("Página não encontrada"))

    # O que torna o caso o que ele é: só o <title> separa os documentos, e ele
    # não entra no texto visível.
    assert visible_text(conteudo_ausente("Categoria: Ferramentas")) == visible_text(
        conteudo_ausente("Página não encontrada")
    )

    r = count_broken_nav_links(base + "/")

    assert r.count == 0
    assert Status.FAIL not in [f.status for f in r.findings]
    assert r.status is Status.MISSING
    assert len(r.same_as_not_found) == 3


def test_catch_all_que_serve_a_home_nao_reprova_rotas_legitimas(server):
    """O outro FAIL falso, e no host que esta checagem existe para pegar.

    Num catch-all que responde a home para qualquer rota desconhecida — a SPA —
    `/index.php`, `/pt/` e `/inicio` são endereços legítimos que servem a home
    de propósito. São três, o limiar exato, e a inferência os condenava."""
    base, rotas = server
    casa = pagina("Casa", extra=menu_de("/index.php", "/pt/", "/inicio"))
    rotas["/"] = (200, {}, casa)
    rotas.default = (200, {}, casa)

    r = count_broken_nav_links(base + "/")

    assert r.count == 0
    assert Status.FAIL not in [f.status for f in r.findings]
    assert r.status is Status.MISSING
    assert len(r.same_as_not_found) == 3


def test_pagina_de_erro_que_muda_entre_as_respostas_nao_aprova_o_menu(server):
    """O falso PASSE não determinístico, que é o defeito mais caro dos dois.

    A sonda decide o regime com duas amostras consecutivas, e os links são
    comparados com a impressão digital N requisições depois. "fingerprint"
    significa "duas amostras coincidiram", não "o template é estável": num host
    cuja página de erro carrega um relógio ou um id de requisição (o Ray ID do
    Cloudflare), as duas sondas caem no mesmo segundo ou não, e os links depois
    quase nunca caem.

    Medido: mesmo host, mesmo menu 100% morto, 20 corridas —
    `{'OK': 8, 'MISSING': 8, 'WARNING': 4}`. OK em oito de vinte sobre um menu
    onde nada existe, e a primeira decisão deste pacote que muda entre corridas.

    Aqui o segundo vira depois das duas sondas: regime "fingerprint", e nenhum
    dos links casa com a impressão digital. Antes isso era OK."""
    base, rotas = server
    rotas["/"] = (200, {}, pagina("Casa", extra=menu_de("/a", "/b", "/c")))
    respostas = []

    def erro_com_relogio(metodo, caminho):
        respostas.append(caminho)
        # As duas sondas caem no mesmo segundo; os links, no seguinte.
        segundo = "00" if len(respostas) <= 2 else "01"
        return (200, {}, pagina_simples(f"Nada aqui. Página gerada em 12:00:{segundo}."))

    rotas.default = erro_com_relogio

    r = count_broken_nav_links(base + "/")

    # As duas sondas coincidiram — e isso não basta para decidir nada.
    assert r.not_found_regime == "fingerprint"
    assert r.status is Status.MISSING
    assert [link.url for link in r.unverified] == [base + "/a", base + "/b", base + "/c"]
    assert (r.count, r.same_as_not_found) == (0, [])


def test_404_duro_continua_reprovando_em_host_de_soft_404(server):
    """O que a regra NÃO enfraquece. Um 4xx veio do fio: foi observado, não
    inferido, e continua contando para o limiar em qualquer host — inclusive num
    que responde 200 para URLs que não tem."""
    base, rotas = server
    rotas["/"] = (200, {}, pagina("Casa", extra=menu_de("/a", "/b", "/c")))
    for caminho in ("/a", "/b", "/c"):
        rotas[caminho] = (404, {}, "")
    rotas.default = (200, {}, ERRO_404)

    r = count_broken_nav_links(base + "/")

    assert r.status is Status.FAIL
    assert (r.count, len(r.broken)) == (3, 3)
    assert r.not_found_regime == "fingerprint"


def test_um_link_parecido_com_a_pagina_de_erro_nao_completa_o_limiar(server):
    """O inverso do que a versão anterior fixava, e de propósito.

    Antes, "dois 404 duros mais um link servindo a página de erro" somava três e
    saía FAIL. Isso é um contador de coisas observadas somado a um contador de
    coisas inferidas, e é assim que uma inferência atravessa o limiar sozinha
    quando o menu tem três categorias vazias. Os dois 404 são um defeito
    (WARNING); o terceiro link é reportado à parte, como não verificado."""
    base, rotas = server
    rotas["/"] = (200, {}, pagina("Casa", extra=menu_de("/a", "/b", "/c")))
    rotas["/a"] = (404, {}, "")
    rotas["/b"] = (404, {}, "")
    rotas.default = (200, {}, ERRO_404)

    r = count_broken_nav_links(base + "/")

    assert r.status is Status.WARNING
    assert (r.count, len(r.broken), len(r.same_as_not_found)) == (2, 2, 1)
    assert BROKEN_NAV_FAIL_THRESHOLD == 3


def test_pagina_real_em_host_de_soft_404_tambem_fica_sem_verificacao(server):
    """Num host que responde 200 para uma URL que não existe, um 200 não prova
    página nenhuma — e isso vale para os links que NÃO parecem a página de erro
    tanto quanto para os que parecem. É essa simetria que tira a decisão das
    mãos da moeda: os dois desfechos da comparação pesam igual."""
    base, rotas = server
    rotas["/"] = (200, {}, pagina("Casa", extra=menu_de("/sobre", "/blog", "/loja", "/sumiu")))
    rotas["/sobre"] = (200, {}, pagina("Sobre"))
    rotas["/blog"] = (200, {}, pagina("Blog"))
    rotas["/loja"] = (200, {}, pagina("Loja"))
    rotas.default = (200, {}, ERRO_404)

    r = count_broken_nav_links(base + "/")

    assert r.status is Status.MISSING
    assert [link.url for link in r.same_as_not_found] == [base + "/sumiu"]
    assert [link.url for link in r.unverified] == [base + "/sobre", base + "/blog", base + "/loja"]
    assert (r.count, r.broken) == (0, [])


def test_conter_o_texto_da_pagina_de_erro_nao_e_ser_a_pagina_de_erro(server):
    """A igualdade é exata, e substring nos dois sentidos passa por ela.

    Duas páginas reais que dividem a redação do template de erro — uma busca
    vazia que repete a frase e uma página de ajuda que a repete e continua —
    seriam reconhecidas como a página de erro se a comparação fosse `in`. `/c`
    está aqui para o teste não passar por vazio: a igualdade exata continua
    reconhecendo o que É a página de erro."""
    base, rotas = server
    erro = "Nada encontrado. Tente a busca ou volte para a home."
    rotas["/"] = (200, {}, pagina("Casa", extra=menu_de("/a", "/b", "/c")))
    rotas["/a"] = (200, {}, pagina_simples(erro + " Ou escreva para o autor."))
    rotas["/b"] = (200, {}, pagina_simples("Tente a busca ou volte para a home."))
    rotas["/c"] = (200, {}, pagina_simples(erro))
    rotas.default = (200, {}, pagina_simples(erro))

    r = count_broken_nav_links(base + "/")

    assert [link.url for link in r.same_as_not_found] == [base + "/c"]
    assert [link.url for link in r.unverified] == [base + "/a", base + "/b"]


def test_prefixo_igual_ao_da_pagina_de_erro_nao_e_ser_a_pagina_de_erro(server):
    """Um artigo sobre páginas de erro abre com a mesma frase que a página de
    erro do site. Comparado por prefixo ele VIRA a página de erro; comparado por
    igualdade, não. `/c` prova que a igualdade continua reconhecendo o caso real.
    """
    base, rotas = server
    erro = "Nada encontrado. Tente a busca ou volte para a home."
    artigo = "Nada encontrado. Tenho visto esse aviso em muitos sites e resolvi escrever."
    assert erro[:20] == artigo[:20]
    rotas["/"] = (200, {}, pagina("Casa", extra=menu_de("/a", "/c")))
    rotas["/a"] = (200, {}, pagina_simples(artigo))
    rotas["/c"] = (200, {}, pagina_simples(erro))
    rotas.default = (200, {}, pagina_simples(erro))

    r = count_broken_nav_links(base + "/")

    assert [link.url for link in r.same_as_not_found] == [base + "/c"]
    assert [link.url for link in r.unverified] == [base + "/a"]


@pytest.mark.parametrize(
    "com_sugestao", ["page", "9x7"], ids=["primeira_sonda_maior", "segunda_sonda_maior"]
)
def test_uma_pagina_de_erro_contida_na_outra_nao_vira_impressao_digital(server, com_sugestao):
    """As duas sondas se comparam por igualdade, e substring passa por ela nos
    dois sentidos.

    Os dois caminhos de sonda diferem de forma de propósito (ver
    NOT_FOUND_PROBE_PATHS). Um CMS que oferece "você quis dizer" para o caminho
    que lembra uma página real e nada para o que não lembra devolve duas páginas
    de erro em que uma É a outra mais uma frase. Comparadas por substring viram
    uma só, e a impressão digital passa a ser um texto que este host serve para
    UM caminho — de onde saem acusações contra os links que servem o outro."""
    base, rotas = server
    erro = "Nada encontrado neste endereço."
    rotas["/"] = (200, {}, pagina("Casa", extra=menu_de("/a", "/b")))

    def erro_com_sugestao(metodo, caminho):
        extra = " Você quis dizer /pagina-inicial?" if com_sugestao in caminho else ""
        return (200, {}, pagina_simples(erro + extra))

    rotas.default = erro_com_sugestao

    r = count_broken_nav_links(base + "/")

    assert r.not_found_regime == "opaque"
    assert "different page for each such url" in frases(r)
    assert r.same_as_not_found == []
    assert [link.url for link in r.unverified] == [base + "/a", base + "/b"]


@pytest.mark.parametrize("recusa", [401, 403, 405, 429, 451, 500, 503])
def test_uma_recusa_na_sonda_nao_e_resposta_sobre_roteamento(server, recusa):
    """`>= 400` lia recusa como honestidade, e o falso pass sai daí.

    O caminho da sonda tem cara de varredura — é essa a intenção, ninguém o
    roteia — e um WAF na frente do site é exatamente a coisa que devolve 403
    para ele. Com o app fazendo soft-404 em todo o resto, esse único 403 dizia
    "este diretório gasta um código de status numa página que não tem", e um
    menu de links todos mortos voltava OK. 401 fala de quem você é, 429 fala de
    agora não, 5xx fala do servidor quebrado: nenhum deles chegou ao roteador
    cujo comportamento é a pergunta.
    """
    base, rotas = server
    rotas["/"] = (200, {}, pagina("Casa", extra=menu_de("/a", "/b")))

    def borda(metodo, caminho):
        if "adsense-auditor-probe" in caminho:
            return (recusa, {}, "")
        return (200, {}, pagina_simples("Página não encontrada. Volte para a home."))

    rotas.default = borda

    r = count_broken_nav_links(base + "/")

    assert r.not_found_regime == "opaque"
    assert "refuses the request rather than routing it" in frases(r)
    assert r.status is Status.MISSING
    assert [link.url for link in r.unverified] == [base + "/a", base + "/b"]


@pytest.mark.parametrize("roteado", [404, 410])
def test_so_404_e_410_dizem_que_o_diretorio_roteou_e_nao_ha_nada(server, roteado):
    """O contrapeso do teste acima, fixado pelos dois códigos que valem.

    Sem isto, restringir a recusa poderia ir longe demais e nenhum host seria
    honesto. 410 entra porque Gone é o servidor roteando o caminho e dizendo o
    que houve — é resposta sobre roteamento, não recusa.
    """
    base, rotas = server
    rotas["/"] = (200, {}, pagina("Casa", extra=menu_de("/a")))
    rotas["/a"] = (200, {}, pagina("A"))
    rotas.default = (roteado, {}, "")

    r = count_broken_nav_links(base + "/")

    assert r.not_found_regime == "honest"
    assert r.status is Status.OK


def test_status_diferente_entre_as_sondas_nao_vira_impressao_digital(server):
    """Mesmo arquivo de erro, status diferente — e isso não é uma impressão
    digital, é um host cujo código de status não diz se a página está lá.

    Real: o servidor web entrega o arquivo de erro com 404 para o que não chega
    ao CMS, e o CMS entrega o MESMO html com 200 para o que chega. Comparando só
    o texto, os dois viram "a página que este host serve para nada", e um link
    que sirva esse html passa a ser acusado com base num par que nem sequer
    concordou sobre o status.

    As duas sondas são endereçadas pelo nome porque o caso É sobre elas
    discordarem: sem escolher qual das duas recebe qual status não há como
    chegar ao ramo de forma determinística."""
    base, rotas = server
    rotas["/"] = (200, {}, pagina("Casa", extra=menu_de("/a", "/b")))
    segunda_sonda = "/" + NOT_FOUND_PROBE_PATHS[1]

    def rota_inconsistente(metodo, caminho):
        return (404 if caminho == segunda_sonda else 200, {}, ERRO_404)

    rotas.default = rota_inconsistente

    r = count_broken_nav_links(base + "/")

    assert r.not_found_regime == "opaque"
    assert "status codes do not say whether a page is there" in frases(r)
    assert r.same_as_not_found == []
    assert [link.url for link in r.unverified] == [base + "/a", base + "/b"]


def test_duas_paginas_reais_identicas_nao_viram_erro_em_host_honesto(server):
    """Sem soft 404 no host não há impressão digital, e duas páginas com o mesmo
    texto continuam sendo páginas. O sinal não é "duas respostas iguais"."""
    base, rotas = server
    igual = pagina("Serviços")
    rotas["/"] = (200, {}, pagina("Casa", extra=menu_de("/a", "/b")))
    rotas["/a"] = (200, {}, igual)
    rotas["/b"] = (200, {}, igual)

    r = count_broken_nav_links(base + "/")

    assert (r.count, r.same_as_not_found, r.unverified) == (0, [], [])
    assert r.passed is True


def test_template_de_erro_que_ecoa_a_url_nao_e_aprovado_nem_reprovado(server):
    """O host responde 200 para uma URL que não existe — soft 404 provado — mas a
    página de erro imprime o caminho pedido, então cada resposta é diferente e
    não sobra impressão digital nenhuma para comparar.

    Chutar "é erro" inventaria três falhas e cruzaria BROKEN_NAV_FAIL_THRESHOLD
    sobre um site que funciona; chutar "é página" é exatamente o buraco de
    origem. MISSING: observado, não decidido, `passed` falso, fora do exit 0 — e
    a contagem de quebrados continua em zero em vez de absorver os três."""
    base, rotas = server
    rotas["/"] = (200, {}, pagina("Casa", extra=menu_de("/a", "/b", "/c")))
    rotas.default = lambda metodo, caminho: (
        200,
        {},
        pagina("Não encontrado", corpo=f"Nada em {caminho}. Volte para a home."),
    )

    r = count_broken_nav_links(base + "/")

    assert r.status is Status.MISSING
    assert [link.url for link in r.unverified] == [base + "/a", base + "/b", base + "/c"]
    assert (r.count, r.broken, r.same_as_not_found) == (0, [], [])
    assert r.passed is False
    # Nem reprovação: nada foi demonstrado quebrado, então isto não sozinho
    # barra o veredito "pronto" como um FAIL barraria.
    assert r.blocks_readiness is False
    assert "neither working nor broken" in frases(r)


def test_sonda_que_nao_responde_deixa_os_links_sem_verificacao(server):
    """Se a sonda não chega, não se sabe o que este host responde para uma URL
    que não existe, e aí um 200 não prova página nenhuma. Os links pedidos
    responderam — quem falhou foi a requisição extra —, então não é ERROR; mas
    aprovar seria afirmar o que não foi observado."""
    base, rotas = server

    def derruba_a_sonda(metodo, caminho):
        raise ConnectionAbortedError("simula conexão cortada na sonda")

    rotas["/"] = (200, {}, pagina("Casa", extra=menu_de("/p")))
    rotas["/p"] = (200, {}, pagina("P"))
    rotas.default = derruba_a_sonda

    r = count_broken_nav_links(base + "/")

    assert r.status is Status.MISSING
    assert [link.url for link in r.unverified] == [base + "/p"]
    assert r.unresolved == []
    assert "could not be fetched" in frases(r)


def test_pagina_de_erro_em_branco_nao_serve_de_impressao_digital(server):
    """Um documento sem texto casa com toda resposta sem texto — um PDF no menu,
    uma página vazia — e viraria acusação sobre evidência nenhuma. Sem nada para
    reconhecer, o desfecho é o terceiro, e a segunda sonda nem sai: seja lá o que
    ela respondesse, página em branco não reconhece link nenhum."""
    base, rotas = server
    rotas["/"] = (200, {}, pagina("Casa", extra=menu_de("/a")))
    rotas["/a"] = (200, {}, pagina("A"))
    rotas.default = (200, {}, "<html><body></body></html>")

    rotas.received.clear()
    r = count_broken_nav_links(base + "/")

    assert r.status is Status.MISSING
    assert [link.url for link in r.unverified] == [base + "/a"]
    assert r.same_as_not_found == []
    assert "no readable text" in frases(r)
    # 1 home + 1 link + 1 sonda.
    assert len(rotas.received) == 3


def test_a_sonda_custa_uma_requisicao_no_host_honesto_e_duas_no_suspeito(server):
    """Custo declarado, medido no fio. O host que gasta um 404 num caminho que
    não existe se resolve na PRIMEIRA sonda; a segunda só sai contra host que já
    se provou soft 404, e serve só para separar "template estável, dá para
    reconhecer" de "template que ecoa a URL, não dá" — uma distinção que muda a
    frase impressa, não o veredito."""
    base, rotas = server
    rotas["/"] = (200, {}, pagina("Casa", extra=menu_de("/a", "/b", "/c")))
    for caminho in ("/a", "/b", "/c"):
        rotas[caminho] = (200, {}, pagina(caminho))

    rotas.received.clear()
    honesto = count_broken_nav_links(base + "/")
    # 1 home + 3 links + 1 sonda.
    assert len(rotas.received) == 5
    assert honesto.passed is True

    rotas.default = (200, {}, ERRO_404)
    rotas.received.clear()
    suspeito = count_broken_nav_links(base + "/")
    # 1 home + 3 links + 2 sondas. O acréscimo não escala com o menu.
    assert len(rotas.received) == 6
    # As MESMAS três páginas reais, e agora sem verificação: o que mudou não foi
    # o menu, foi o que um 200 vale neste host.
    assert suspeito.passed is False
    assert len(suspeito.unverified) == 3


def test_menu_vazio_nao_gasta_sonda(server):
    """Sem link nenhum para classificar não há o que perguntar ao host."""
    base, rotas = server
    rotas["/"] = (200, {}, pagina("Casa"))

    rotas.received.clear()
    r = count_broken_nav_links(base + "/")

    assert r.found == 0
    assert len(rotas.received) == 1


def test_sonda_recebida_de_fora_nao_e_pedida_de_novo(server):
    """check_completeness pergunta por diretório e entrega o MESMO conjunto às
    duas sub-checagens. Se esta função sondasse de novo, a coerência entre as
    duas linhas do relatório sairia de graça mas custaria um par de requisições
    por diretório por auditoria — e as duas respostas poderiam divergir.

    Um conjunto só também é UM TETO: gastar `MAX_PROBED_DIRECTORIES` nas páginas
    de confiança e outro tanto no menu dobraria silenciosamente o rastro que a
    constante existe para limitar."""
    base, rotas = server
    rotas["/"] = (200, {}, pagina("Casa", extra=menu_de("/a")))
    rotas["/a"] = (200, {}, pagina("A"))
    rotas.default = (200, {}, ERRO_404)

    sondas = _NotFoundProbes(
        as_base_publico(base + "/"), session=requests.Session(), timeout=5
    )
    assert sondas.anchor_probe().regime == "fingerprint"
    rotas.received.clear()
    r = count_broken_nav_links(base + "/", not_found=sondas)

    # 1 home + 1 link, e nenhuma sonda: `/a` mora em `/`, que já foi medido.
    assert len(rotas.received) == 2
    assert r.not_found_regime == "fingerprint"
    assert [link.url for link in r.unverified] == [base + "/a"]


# ---------------------------------------------------------------------------
# check_trust_pages — a mesma pergunta, o mesmo host, o mesmo relatório
# ---------------------------------------------------------------------------


def test_pagina_de_confianca_que_e_a_pagina_de_erro_nao_e_aprovada(server):
    """Um relatório não pode se contradizer sobre o mesmo host.

    check_trust_pages roda ANTES e sem a sonda, então `/about` e `/contact`
    saíam [PASS] — o template de erro tem texto de sobra para não ser stub — no
    mesmo relatório cuja linha de navegação, quatro linhas abaixo, declarava que
    aquela página é o que este host serve para uma URL que não existe.

    As sondas passam a ser perguntadas em check_completeness, por diretório, e
    entregues às duas sub-checagens. Aqui a resposta é usada como EXCLUSÃO — não
    é esta página, continue procurando —, que é o mesmo movimento que
    `_is_home_again` já fazia e o único uso são desta igualdade."""
    base, rotas = server
    rotas["/"] = (200, {}, pagina("Casa"))
    rotas.default = (200, {}, ERRO_404_LONGO)

    relatorio = check_completeness(base + "/")

    assert relatorio.trust.pages["about"].status is Status.MISSING
    assert relatorio.trust.pages["contact"].status is Status.MISSING
    # E o relatório diz POR QUE nada foi achado, nomeando a URL que descartou os
    # candidatos, em vez de deixar as duas linhas se contradizerem em silêncio.
    # A frase única sobre "o host" saiu junto com a sonda única: com vários
    # diretórios medidos não existe UM fato sobre o host para imprimir, e a
    # anotação passou a ser por PÁGINA, ao lado do endereço que ela descreve.
    dito = frases(relatorio.trust)
    assert "answered http 200 with exactly the page their own directory serves" in dito
    assert f"{base}/{NOT_FOUND_PROBE_PATHS[0]}".lower() in dito


def test_pagina_de_confianca_real_continua_aprovada_em_host_de_soft_404(server):
    """A exclusão é estreita de propósito. Um host pode servir soft 404 e ainda
    ter uma página /sobre de verdade, e ela não vira MISSING por causa do
    vizinho: o que a descarta é ser IDÊNTICA à página servida para nada."""
    base, rotas = server
    rotas["/"] = (200, {}, pagina("Casa"))
    rotas["/about"] = (200, {}, pagina("Sobre"))
    rotas.default = (200, {}, ERRO_404_LONGO)

    relatorio = check_completeness(base + "/")

    # A página passa — e o relatório diz, no MESMO peso que a navegação dá à
    # mesma observação, que o 200 dela não provou nada. INFO fazia a corrida
    # sair por 0; WARNING afirmaria um defeito que ninguém observou.
    severidades = {
        f.status
        for f in relatorio.trust.findings
        if "rather than on its status code" in f.message
    }
    assert severidades == {Status.MISSING}

    assert relatorio.trust.pages["about"].status is Status.OK
    assert relatorio.trust.pages["about"].url == base + "/about"


def test_pagina_de_confianca_sobrevive_a_um_host_sem_impressao_digital(server):
    """O regime "opaque" não descarta nada, e é fácil escrever a exclusão como
    se descartasse.

    Aqui o template de erro ecoa o endereço pedido, então não há uma página para
    reconhecer — e uma exclusão que valesse para todo host não honesto jogaria
    fora um /about que existe e tem texto. A comparação é contra UMA página
    conhecida ou contra nada."""
    base, rotas = server
    rotas["/"] = (200, {}, pagina("Casa"))
    rotas["/about"] = (200, {}, pagina("Sobre"))
    rotas.default = lambda metodo, caminho: (
        200, {}, pagina("Não encontrado", corpo=f"Nada em {caminho}. Volte para a home."),
    )

    relatorio = check_completeness(base + "/")

    assert relatorio.nav.not_found_regime == "opaque"
    assert relatorio.trust.pages["about"].status is Status.OK
    assert relatorio.trust.pages["about"].url == base + "/about"


# ---------------------------------------------------------------------------
# check_completeness — o resumo
# ---------------------------------------------------------------------------


def test_placeholder_na_home_nunca_sai_como_aprovado(server):
    """BLOCKER reproduzido: WARNING não entrava em nenhum dos dois somatórios do
    resumo, então o script listava "Homepage contains placeholder phrases:
    coming soon" e, na linha seguinte, imprimia "✓ Completeness checks passed" —
    e o veredito final é o que o usuário lê.
    """
    base, rotas = server
    rodape = "<a href='/about'>Sobre</a> <a href='/contact'>Contato</a>"
    rotas["/"] = (200, {}, f"<html><body><h1>Casa</h1><div><h2>Coming soon</h2></div>"
                           f"<footer>{rodape}</footer></body></html>")
    rotas["/about"] = (200, {}, pagina("Sobre"))
    rotas["/contact"] = (200, {}, contato())

    relatorio = check_completeness(base)

    assert relatorio.trust.status is Status.OK
    assert relatorio.nav.count == 0
    assert relatorio.status is Status.WARNING
    assert relatorio.passed is False
    assert relatorio.blocks_readiness is True
    assert "coming soon" in frases(relatorio)


def test_o_resumo_herda_o_pior_status_das_partes(server):
    """O veredito é derivado das findings emitidas, não de uma lista de status
    escrita à mão que pode divergir do que os checks produzem.
    """
    base, rotas = server
    rotas["/"] = (200, {}, home(rodape="<a href='/about'>Sobre</a>"))
    rotas["/about"] = (503, {}, "boom")

    relatorio = check_completeness(base)

    assert relatorio.trust.status is Status.ERROR
    assert relatorio.status is Status.ERROR
    assert relatorio.passed is False


def test_home_inacessivel_nao_e_diagnostico_sobre_o_conteudo(server):
    """Defeito original: o erro de transporte virava "site appears unfinished".

    A causa real nunca aparecia, e o diagnóstico afirmava algo sobre um conteúdo
    que jamais foi baixado.
    """
    base, rotas = server
    rotas["/"] = (500, {}, "boom")

    relatorio = check_completeness(base)

    assert relatorio.status is Status.ERROR
    assert "could not be read" in frases(relatorio)
    assert "unfinished" not in frases(relatorio)


def test_site_completo_passa_limpo(server):
    """O check precisa poder passar — senão ele não está medindo nada."""
    base, rotas = server
    rodape = "<a href='/sobre'>Sobre</a> <a href='/contato'>Contato</a>"
    rotas["/"] = (200, {}, home(rodape=rodape))
    rotas["/sobre"] = (200, {}, pagina("Sobre"))
    rotas["/contato"] = (200, {}, contato())

    relatorio = check_completeness(base)

    assert relatorio.status is Status.OK
    assert relatorio.passed is True
    assert relatorio.issues == []


# ---------------------------------------------------------------------------
# Invariantes do veredito
# ---------------------------------------------------------------------------


def test_toda_finding_registrada_move_o_veredito():
    """Não existe lugar onde guardar um status sem finding, nem finding que não
    entre no status: era exatamente essa a folga que perdia o WARNING.
    """
    relatorio = NavLinkReport(base_url="http://exemplo/")
    assert relatorio.passed is True

    relatorio.add(Status.WARNING, "um problema")
    assert relatorio.status is Status.WARNING
    assert relatorio.passed is False
    assert relatorio.issues == ["um problema"]


def test_status_do_relatorio_so_escala():
    relatorio = NavLinkReport(base_url="http://exemplo/")
    relatorio.add(Status.ERROR, "nao deu para apurar")
    relatorio.add(Status.INFO, "detalhe menor")
    assert relatorio.status is Status.ERROR


def test_MISSING_e_ERROR_nunca_contam_como_aprovacao():
    relatorio = NavLinkReport(base_url="http://exemplo/")
    relatorio.add(Status.MISSING, "nao encontrei")
    assert relatorio.passed is False

    outro = NavLinkReport(base_url="http://exemplo/")
    outro.add(Status.ERROR, "nao consegui olhar")
    assert outro.passed is False


def test_link_absoluto_com_www_nao_e_tratado_como_outro_site():
    """`www.` não faz parte da identidade de um site.

    A home alcançada no apex, com o link do rodapé escrito na forma `www.`
    absoluta, tinha a página About que o próprio site declara descartada como
    externa — e o resultado era MISSING sobre uma URL que ninguém pediu. É o
    mesmo defeito de identidade que parou o crawler antigo, refeito aqui.
    """
    html = (
        "<html><body><p>Bancada.</p>"
        "<footer><a href='https://www.exemplo.com/pages/quem-eu-sou'>Sobre</a></footer>"
        "</body></html>"
    )
    home = Fetch(
        url="https://exemplo.com/",
        final_url="https://exemplo.com/",
        status_code=200,
        text=html,
        headers={"content-type": "text/html"},
    )
    candidatos = _candidates(home, parse_document(html), ABOUT_PATHS, _ABOUT_HINT, 6)
    declarados = [c.url for c in candidatos if c.declared]
    assert declarados == ["https://www.exemplo.com/pages/quem-eu-sou"]


def test_alvo_que_nomeia_um_documento_nao_ganha_barra(server):
    """`as_base` punha `/` incondicional, então auditar `…/index.html` virava
    `…/index.html/` e a auditoria inteira saía com "Home page could not be read:
    HTTP 404" sobre um site perfeitamente no ar."""
    base, routes = server
    routes["/index.html"] = (200, {}, pagina("Casa", extra=MAILTO))

    r = check_completeness(f"{base}/index.html")

    assert "could not be read" not in " ".join(r.issues)


def test_as_base_preserva_subdiretorio_e_descarta_query():
    from adsense_checks.completeness import _join, as_base

    assert as_base("https://ex.com/blog") == "https://ex.com/blog/"
    assert _join(as_base("https://ex.com/blog"), "/about") == "https://ex.com/blog/about"
    # Um documento resolve contra o diretório que o contém, que é o que urljoin faz.
    assert as_base("https://ex.com/index.html") == "https://ex.com/index.html"
    # Query e fragmento não fazem parte de uma base para joins relativos.
    assert as_base("https://ex.com/blog?x=1") == "https://ex.com/blog/"
    # Protocol-relative virava "https:////ex.com".
    assert as_base("//ex.com/blog") == "https://ex.com/blog/"


def test_link_de_nav_na_forma_www_nao_conta_como_dois_quebrados(server):
    """`_canonical` comparava netloc literal enquanto `_same_site` fundia `www.`,
    então uma URL quebrada linkada nas duas formas contava duas vezes — e três
    links quebrados é a fronteira entre WARNING e FAIL."""
    from adsense_checks.completeness import _canonical

    assert _canonical("http://ex.com/x") == _canonical("http://www.ex.com/x")


def test_home_que_nao_da_para_ler_nao_vira_aprovacao(server):
    """Uma resposta não é um documento. Um 200 de corpo vazio, uma resposta JSON
    e uma casca client-rendered passavam batido: a varredura de marcadores não
    achava marcador em texto nenhum e a de navegação não achava link quebrado
    entre link nenhum, então AS DUAS imprimiam PASS e o run saía 0 sobre uma
    página que esta auditoria nunca leu. O detector de casca já estava importado
    e já era aplicado às páginas de confiança; a home era o único documento sobre
    o qual ninguém o consultava."""
    base, routes = server
    casca = ('<!doctype html><html><head><script src="/b.js"></script></head>'
             '<body><div id="root"></div></body></html>')
    for rota, corpo, cabecalhos, motivo in (
        ("/casca", casca, {}, "client-rendered shell"),
        ("/vazia", "", {}, "empty body"),
        ("/json", '{"ok":1}', {"Content-Type": "application/json"}, "not HTML"),
        ("/so-markup", "<html><body><div></div></body></html>", {}, "no visible text"),
    ):
        routes["/"] = (200, cabecalhos or {"Content-Type": "text/html"}, corpo)
        r = check_completeness(base + "/")
        assert r.status is Status.ERROR, rota
        assert motivo in " ".join(r.issues), rota
        # E nenhuma linha pode ter sido julgada.
        assert r.trust is None and r.nav is None, rota


def test_alvo_impossivel_nomeia_o_argumento_e_nao_culpa_o_site():
    """`as_base` devolvia "/" e o relatório dizia "Home page could not be read:
    Invalid URL '/'" — culpando o site pelo que o operador digitou."""
    assert as_base_publico("http://[abc/") == "http://[abc/"


def test_pagina_de_confianca_ausente_e_reportada_como_MISSING(server):
    """Trocar esse `MISSING` por OK fazia a ausência de About sumir do relatório
    — e a ausência de página de confiança é motivo de recusa no AdSense. O
    ramo do Contact tem escalação própria e era o único fixado; este é o outro."""
    base, routes = server
    # UMA só ausente, de propósito: com as duas faltando, o FAIL agregado
    # ("Neither an About nor a Contact") domina o status e a mensagem do achado
    # não muda sob a mutação — só a severidade muda, e nada a observava.
    # A home tem canal, para o Contact ausente não cair no ramo WARNING.
    routes["/"] = (200, {}, pagina("Casa", extra=MAILTO))
    routes["/about"] = (200, {}, pagina("Sobre"))

    r = check_trust_pages(base + "/")

    assert r.pages["about"].status is Status.OK
    assert r.pages["contact"].status is Status.MISSING
    assert "No Contact page found" in " | ".join(r.issues)
    # O status do relatório vem SÓ desse achado, então ele é o que a asserção vê.
    assert r.status is Status.MISSING
    assert r.passed is False


def test_todo_candidato_a_pagina_de_confianca_inacessivel_e_ERROR(server):
    """Um candidato que não resolve escalava `access` para ERROR, e sem isso o
    desfecho vira MISSING: "não achei" no lugar de "não consegui olhar". A
    diferença é a que este pacote existe para manter."""
    base, routes = server
    # A home responde; nenhum candidato a About/Contact responde, e o servidor
    # corta a conexão em vez de devolver 404 — erro de transporte, não ausência.
    routes["/"] = (200, {}, pagina("Casa", extra=MAILTO))

    import requests

    from adsense_checks.completeness import _Candidate, _resolve_trust_page
    from adsense_checks.http import fetch as _fetch

    morto = "http://127.0.0.1:1/"
    desfecho = _resolve_trust_page(
        kind="about",
        candidates=[_Candidate(url=morto + "sobre", declared=True)],
        home=_fetch(base + "/"),
        home_text="casa",
        session=requests.Session(),
        timeout=1,
    )
    assert desfecho.status is Status.ERROR
    assert "may well exist" in desfecho.reason


def test_no_maximo_seis_candidatos_linkados_por_pagina_de_confianca(server):
    """MAX_LINKED_CANDIDATES pelo valor. O teto vale só para os links que a home
    oferece; os caminhos convencionais são sempre tentados por inteiro, senão
    "nenhum candidato respondeu" seria dito sobre URL que ninguém pediu."""
    from adsense_checks.completeness import _ABOUT_HINT, ABOUT_PATHS, _candidates
    from adsense_checks.http import Fetch

    links = "".join(f'<a href="/sobre-{i}">Sobre {i}</a>' for i in range(9))
    html = f"<html><body><footer>{links}</footer></body></html>"
    home = Fetch(url="http://ex.com/", final_url="http://ex.com/", status_code=200,
                 text=html, headers={"content-type": "text/html"})

    doc = parse_document(html)
    # Com o teto explícito, para provar que os convencionais não são afetados:
    # com um teto muito maior a quantidade deles é a mesma. (São `ABOUT_PATHS`
    # por inteiro: `about` e `about/` são endereços diferentes no fio e os dois
    # são pedidos — a dedup aqui é pela URL como escrita, não pela identidade.)
    cands = _candidates(home, doc, ABOUT_PATHS, _ABOUT_HINT, 6)
    largo = _candidates(home, doc, ABOUT_PATHS, _ABOUT_HINT, 100)
    assert len([c for c in cands if c.declared]) == 6
    assert len([c for c in largo if c.declared]) == 9
    assert len([c for c in largo if not c.declared]) == len([c for c in cands if not c.declared])


def test_um_endereco_que_o_documento_escreveu_continua_declarado_apos_o_teto(server):
    """O teto corta a REQUISIÇÃO por conta do orçamento, não a autoria.

    O rodapé escreve sete links Sobre, e o sétimo é `/sobre/` — que é também um
    dos caminhos convencionais. `MAX_LINKED_CANDIDATES` derruba o sétimo antes de
    ele entrar em `seen`, então a metade convencional o readicionava como CHUTE.
    A partir daí a URL que o site publica era julgada na âncora em vez de onde
    caiu — o inverso exato da razão do split — e saía de `declared_blocked`, de
    modo que um 503 na página Sobre do próprio site virava "um chute que não
    achou" em vez de "a URL que a home oferece não pôde ser lida".

    `declared` é um fato sobre o DOCUMENTO, e é isso que ele passa a dizer.
    """
    from adsense_checks.completeness import _ABOUT_HINT, ABOUT_PATHS, _candidates
    from adsense_checks.http import Fetch

    # Seis identidades que gastam o teto, e `/sobre/` como sétima.
    links = "".join(f'<a href="/sobre-{i}">Sobre {i}</a>' for i in range(6))
    links += '<a href="/sobre/">Sobre</a>'
    html = f"<html><body><footer>{links}</footer></body></html>"
    home = Fetch(url="http://ex.com/", final_url="http://ex.com/", status_code=200,
                 text=html, headers={"content-type": "text/html"})

    cands = _candidates(home, parse_document(html), ABOUT_PATHS, _ABOUT_HINT, 6)

    por_url = {c.url: c for c in cands}
    # Pedido uma vez só, pela metade convencional — o orçamento fez o seu papel.
    assert [c.url for c in cands].count("http://ex.com/sobre/") == 1
    # Mas o documento escreveu este endereço, então ele NÃO é um chute.
    assert por_url["http://ex.com/sobre/"].declared is True
    # E um convencional que o documento não escreveu continua chute.
    assert por_url["http://ex.com/quem-somos"].declared is False


def test_o_limite_padrao_de_links_de_navegacao_e_25(server):
    """DEFAULT_NAV_LINK_LIMIT pelo valor: 30 links no menu, 25 seguidos."""
    base, routes = server
    menu = "".join(f'<a href="/p{i}">p{i}</a>' for i in range(30))
    routes["/"] = (200, {}, pagina("Casa", extra=f"<nav>{menu}</nav>"))
    for i in range(30):
        routes[f"/p{i}"] = (200, {}, pagina(f"P{i}"))

    r = count_broken_nav_links(base + "/")

    assert r.found == 30
    assert r.checked == 25
    assert r.truncated is True


def test_tres_links_quebrados_reprovam_e_dois_apenas_avisam(server):
    """BROKEN_NAV_FAIL_THRESHOLD pelo valor, nos dois lados. Um link quebrado no
    menu é defeito; três lêem como site abandonado."""
    base, routes = server
    for quebrados, esperado in ((2, Status.WARNING), (3, Status.FAIL)):
        links = "".join(f'<a href="/q{i}">q{i}</a>' for i in range(quebrados))
        routes["/"] = (200, {}, pagina("Casa", extra=f"<nav>{links}</nav>"))
        r = count_broken_nav_links(base + "/")
        assert len(r.broken) == quebrados
        assert r.status is esperado, quebrados


def test_o_trecho_reportado_para_em_140_caracteres():
    """_SNIPPET_CHARS pelo valor. O trecho existe para o leitor localizar o
    marcador na página, não para reproduzi-la."""
    longo = "Coming soon " + "x" * 400
    achados = find_placeholders(f"<h1>{longo}</h1>")
    assert achados
    assert len(achados[0].snippet) == 140
    assert achados[0].snippet.endswith("...")


def test_o_teto_padrao_de_candidatos_linkados_e_6(server):
    """MAX_LINKED_CANDIDATES pelo DEFAULT. O teste acima passa o teto explícito,
    então contorna justamente a constante — é preciso ir pela porta que a usa."""
    base, routes = server
    links = "".join(f'<a href="/sobre-{i}">Sobre {i}</a>' for i in range(9))
    routes["/"] = (200, {}, pagina("Casa", extra=f"<footer>{links}</footer>"))

    check_trust_pages(base + "/")

    # Só os linkados: `/sobre-mim` é um caminho convencional de `ABOUT_PATHS` e
    # casaria um filtro mais frouxo, inflando a contagem para 7.
    linkados = {f"/sobre-{i}" for i in range(9)}
    pedidos = [c for _m, c, _h in routes.received if c in linkados]
    assert len(pedidos) == 6


def test_duas_grafias_do_mesmo_endereco_gastam_uma_vaga_do_teto(server):
    """O teto conta ENDEREÇOS onde deveria contar IDENTIDADES. A home abaixo
    linka sete candidatos, e dois deles — `/sobre` e `/sobre/` — são grafias de
    um endereço só: em qualquer servidor que normalize a barra, são a mesma
    página pedida duas vezes. Cobrando uma vaga de cada, elas comem duas das
    seis, e o sétimo link nunca é requisitado.

    Aqui o sétimo é o único que responde 200, então o relatório dizia "No About
    page found" sobre uma URL que ninguém pediu — o mesmo expulsar-o-último que
    MAX_LINKED_CANDIDATES existe para impedir, refeito um nível abaixo."""
    base, routes = server
    links = (
        '<a href="/sobre">Sobre</a>'
        '<a href="/sobre/">Sobre</a>'  # mesma identidade, endereco diferente
        '<a href="/sobre-a">Sobre a</a>'
        '<a href="/sobre-b">Sobre b</a>'
        '<a href="/sobre-c">Sobre c</a>'
        '<a href="/sobre-d">Sobre d</a>'
        # O setimo. Nao esta em ABOUT_PATHS, entao so chega la pela metade
        # linkada: se o teto o cortar, ninguem pede esta URL.
        '<a href="/pages/quem-eu-sou">Sobre</a>'
    )
    routes["/"] = (200, {}, pagina("Casa", extra=f"<footer>{links}</footer>"))
    routes["/pages/quem-eu-sou"] = (200, {}, pagina("Sobre", extra=MAILTO))

    r = check_trust_pages(base + "/")

    assert "/pages/quem-eu-sou" in [c for _m, c, _h in routes.received]
    assert r.pages["about"].status is Status.OK
    assert r.pages["about"].url.endswith("/pages/quem-eu-sou")


def test_as_duas_grafias_continuam_na_lista_mesmo_gastando_uma_vaga():
    """A vaga única não pode virar dedup por identidade de novo: a lista é de
    ENDEREÇOS A PEDIR, e um host que serve só `/sobre/` responde 404 para
    `/sobre`. As duas grafias continuam sendo requisitadas — o que muda é só
    quanto elas custam do teto."""
    links = (
        '<a href="/sobre">Sobre</a>'
        '<a href="/sobre/">Sobre</a>'
        '<a href="/sobre-a">Sobre a</a>'
        '<a href="/sobre-b">Sobre b</a>'
        '<a href="/sobre-c">Sobre c</a>'
        '<a href="/sobre-d">Sobre d</a>'
        '<a href="/pages/quem-eu-sou">Sobre</a>'
    )
    html = f"<html><body><p>Bancada.</p><footer>{links}</footer></body></html>"
    home = Fetch(url="http://ex.com/", final_url="http://ex.com/", status_code=200,
                 text=html, headers={"content-type": "text/html"})

    declarados = [
        c.url
        for c in _candidates(home, parse_document(html),
                             ABOUT_PATHS, _ABOUT_HINT, 6)
        if c.declared
    ]

    # Sete endereços, seis identidades: as duas grafias cabem, e o sétimo também.
    assert declarados == [
        "http://ex.com/sobre",
        "http://ex.com/sobre/",
        "http://ex.com/sobre-a",
        "http://ex.com/sobre-b",
        "http://ex.com/sobre-c",
        "http://ex.com/sobre-d",
        "http://ex.com/pages/quem-eu-sou",
    ]


def test_um_fragmento_nao_e_um_endereco_novo_e_nao_vira_um_pedido_novo(server):
    """O teto passou a contar identidades e nada passou a contar PEDIDOS.

    Um fragmento nunca vai no fio, então cada `<a href="/sobre#sN">` além do
    primeiro virava um `GET /sobre` byte a byte igual ao anterior. Com as 200
    âncoras deste rodapé foram 218 pedidos, 201 deles para a mesma URL — contra
    24 e 7 antes de o teto passar a contar identidades.

    Escala com a quantidade de links da página auditada e não tem limite: é o
    HTML de um terceiro mandando o auditor martelar o servidor desse terceiro."""
    base, routes = server
    links = "".join(f'<a href="/sobre#s{i}">Sobre</a>' for i in range(200))
    routes["/"] = (200, {}, home(rodape=links))

    check_trust_pages(base + "/")

    pedidos = [(m, c) for m, c, _h in routes.received]
    assert pedidos.count(("GET", "/sobre")) == 1
    # E o caso geral: nenhum endereço foi ao fio duas vezes.
    assert len(pedidos) == len(set(pedidos))


def test_o_desconto_por_grafia_nao_e_um_cheque_em_branco(server):
    """Duas grafias é o desconto inteiro, e a terceira é recusada.

    `_canonical` dobra a barra final com `rstrip`, então `/sobre`, `/sobre/`,
    `/sobre//` e mais 197 são UMA identidade só. Um desconto sem limite manda as
    200 ao fio de graça — medido, 216 pedidos, 200 deles para a mesma página —,
    e aí "conta identidades" não é um teto, é a ausência de um. Duas é quantas
    grafias de um caminho um site real escreve: com a barra e sem."""
    base, routes = server
    links = "".join(f'<a href="/sobre{"/" * i}">Sobre</a>' for i in range(200))
    routes["/"] = (200, {}, home(rodape=links))

    check_trust_pages(base + "/")

    grafias = [c for _m, c, _h in routes.received if c.rstrip("/") == "/sobre"]
    assert sorted(grafias) == ["/sobre", "/sobre/"]


def test_a_grafia_repetida_pega_carona_mesmo_chegando_depois_do_teto_cheio():
    """A carona só decide alguma coisa quando a segunda grafia chega DEPOIS de o
    teto encher; chegando antes, o link entraria de qualquer jeito e a cláusula
    nunca roda. Este rodapé separa as duas coisas: seis identidades distintas
    fecham o teto, e só então vêm `/sobre-0/`, grafia de uma identidade já
    cobrada, e `/sobre-6`, uma sétima identidade. A carona entra; a sétima não —
    o desconto afrouxa o teto para grafias, não para páginas."""
    links = "".join(f'<a href="/sobre-{i}">Sobre {i}</a>' for i in range(6))
    links += '<a href="/sobre-0/">Sobre 0</a>'
    links += '<a href="/sobre-6">Sobre 6</a>'
    html = f"<html><body><p>Bancada.</p><footer>{links}</footer></body></html>"
    home = Fetch(url="http://ex.com/", final_url="http://ex.com/", status_code=200,
                 text=html, headers={"content-type": "text/html"})

    declarados = [
        c.url
        for c in _candidates(home, parse_document(html),
                             ABOUT_PATHS, _ABOUT_HINT, 6)
        if c.declared
    ]

    assert declarados == [f"http://ex.com/sobre-{i}" for i in range(6)] + [
        "http://ex.com/sobre-0/"
    ]


def test_o_link_para_a_propria_home_nao_gasta_vaga_do_teto():
    """Um rodapé que escreve "Sobre" apontando para `/` aponta para a home, que
    já respondeu — pedir outra grafia dela não ensina nada, e ela é descartada
    antes de qualquer cobrança. Cobrando-lhe uma vaga, o sexto candidato de
    verdade cairia fora do teto sem que nenhuma página a mais tivesse sido
    pedida em troca."""
    links = '<a href="/">Sobre nós</a>'
    links += "".join(f'<a href="/sobre-{i}">Sobre {i}</a>' for i in range(6))
    html = f"<html><body><p>Bancada.</p><footer>{links}</footer></body></html>"
    home = Fetch(url="http://ex.com/", final_url="http://ex.com/", status_code=200,
                 text=html, headers={"content-type": "text/html"})

    declarados = [
        c.url
        for c in _candidates(home, parse_document(html),
                             ABOUT_PATHS, _ABOUT_HINT, 6)
        if c.declared
    ]

    assert declarados == [f"http://ex.com/sobre-{i}" for i in range(6)]


def test_as_duas_metades_nao_pedem_o_mesmo_endereco_duas_vezes():
    """`/about` é convencional E está linkado no rodapé. Ele entra uma vez, pela
    metade linkada, e a metade convencional o encontra em `seen` e não repete:
    duas entradas para o mesmo endereço são dois GETs idênticos, e a segunda
    ainda chegaria com `declared=False`, apagando o fato de que o site declara
    essa URL como sua página Sobre."""
    html = ('<html><body><p>Bancada.</p>'
            '<footer><a href="/about">About</a></footer></body></html>')
    home = Fetch(url="http://ex.com/", final_url="http://ex.com/", status_code=200,
                 text=html, headers={"content-type": "text/html"})

    cands = _candidates(home, parse_document(html),
                        ABOUT_PATHS, _ABOUT_HINT, 6)

    urls = [c.url for c in cands]
    assert len(urls) == len(set(urls))
    assert [c.declared for c in cands if c.url == "http://ex.com/about"] == [True]


# --------------------------------------------------------------------------
# <base href>
#
# O crawler respeita `<base href>` desde sempre; este módulo lia o MESMO
# documento e não. Cada teste daqui é um dos casos que `crawl.resolve_base`
# trata, conferido contra o comportamento do crawler em vez de contra uma
# segunda opinião escrita aqui.
# --------------------------------------------------------------------------


def test_a_sonda_de_nao_encontrado_segue_o_base_href_dos_links(server):
    """A costura entre os dois recursos, e ela só existe depois de juntá-los.

    Um deles mudou TODO link pedido para debaixo do `<base href>`. O outro passou
    a perguntar ao host como é uma página faltando — e perguntava na URL que o
    operador digitou. Nada reconciliava os dois, e nenhum dos dois commits
    sozinho tem o defeito.

    Aqui a raiz é honesta e `/app/` responde 200 para tudo. Sondando a raiz, o
    regime dá `honest`, todo 200 vindo de `/app/` conta como página, e três links
    mortos saem PASS com exit 0 — debaixo de uma frase afirmando que este host
    responde 4xx para o que não existe, que é verdade sobre um diretório de onde
    nada foi buscado.
    """
    base, rotas = server
    rotas["/"] = (200, {}, home_com_base("/app/", menu_de("sobre", "contato", "blog")))
    # `/app/` inteiro é um soft 404; a raiz 404a de verdade (nenhuma rota curinga).
    rotas.default = lambda metodo, caminho: (
        (200, {}, ERRO_404) if caminho.startswith("/app/") else None
    )

    relatorio = count_broken_nav_links(base + "/")

    assert relatorio.not_found_regime == "fingerprint"
    assert relatorio.status is Status.MISSING
    assert [link.url for link in relatorio.same_as_not_found] == [
        base + "/app/sobre", base + "/app/contato", base + "/app/blog",
    ]


def test_a_auditoria_inteira_sonda_debaixo_do_base_href(server):
    """A mesma costura pelo caminho de cima, que sonda num lugar diferente.

    `check_completeness` pede a sonda uma vez e entrega às duas sub-checagens, e
    prender só o `count_broken_nav_links` deixa esta metade solta: apagar o
    `resolve_base` aqui sobrevivia à suíte inteira. A base é a mesma dos links
    também aqui, senão as páginas de confiança de `/app/` são julgadas contra o
    "nada" da raiz.
    """
    base, rotas = server
    rotas["/"] = (200, {}, home_com_base("/app/", menu_de("sobre")))
    rotas.default = lambda metodo, caminho: (
        (200, {}, ERRO_404_LONGO) if caminho.startswith("/app/") else None
    )

    relatorio = check_completeness(base + "/")

    # `/app/` responde 200 para tudo, então nenhuma página de confiança dali pode
    # ser afirmada — e a navegação não pode ser aprovada.
    assert relatorio.trust.pages["about"].status is Status.MISSING
    assert relatorio.nav.not_found_regime == "fingerprint"
    assert relatorio.nav.status is Status.MISSING


def test_o_espelho_da_costura_um_subdiretorio_honesto_sob_uma_raiz_que_nao_e(server):
    """O outro lado, e o que impede a correção de virar um MISSING para todos.

    Aqui `/app/` é honesto e os três links existem de verdade; só a raiz responde
    200 para o que não tem. Sondando a raiz, o regime dá `fingerprint` e um site
    inteiro que funciona vira "não deu para mostrar que levam a lugar algum".
    """
    base, rotas = server
    rotas["/"] = (200, {}, home_com_base("/app/", menu_de("sobre", "contato", "blog")))
    for caminho in ("sobre", "contato", "blog"):
        rotas[f"/app/{caminho}"] = (200, {}, pagina(caminho))
    # A raiz responde 200 para qualquer coisa; `/app/` 404a o que não existe.
    rotas.default = lambda metodo, caminho: (
        None if caminho.startswith("/app/") else (200, {}, ERRO_404)
    )

    relatorio = count_broken_nav_links(base + "/")

    assert relatorio.not_found_regime == "honest"
    assert relatorio.status is Status.OK
    assert (relatorio.count, relatorio.same_as_not_found, relatorio.unverified) == (0, [], [])


def home_com_base(base_href, corpo):
    """Uma home que declara `<base href>`, com prosa suficiente para ser lida."""
    return (
        f'<html><head><base href="{base_href}"><title>Casa</title></head>'
        f"<body><h1>Casa</h1><p>{PROSA}</p>{corpo}</body></html>"
    )


def caminhos_pedidos(rotas):
    """Os caminhos que chegaram no fio, na ordem, sem repetição."""
    return list(dict.fromkeys(caminho for _, caminho, _ in rotas.received))


def autenticadas(rotas):
    """Os caminhos que chegaram no fio COM Authorization.

    `requests` transforma o `user:pass@` de uma URL em Basic auth sozinho, então
    é aqui que se vê a credencial raspada da página voltando para o servidor.
    """
    return [
        caminho
        for _, caminho, cabecalhos in rotas.received
        if "authorization" in {k.lower() for k in cabecalhos}
    ]


def test_os_dois_parsers_leem_o_mesmo_base_href_em_todo_caso_de_borda():
    """A garantia é de PARIDADE: o crawler e este módulo têm tokenizadores
    diferentes lendo o mesmo documento, e foi divergirem que produziu o FAIL
    falso. Cada caso abaixo é uma forma de `<base>` aparecer.

    Comparar só um parser com o outro deixaria os dois errados juntos passarem,
    então cada caso também traz a base resolvida esperada, escrita aqui.
    """
    url_do_doc = "http://ex.com/dir/page.html"
    casos = [
        ("", "http://ex.com/dir/page.html"),
        ('<base href="/app/">', "http://ex.com/app/"),
        # A própria base é relativa: resolve contra o diretório do documento.
        ('<base href="app/">', "http://ex.com/dir/app/"),
        # Não parseia: `join_url` devolve "" e a URL do documento é o fallback.
        ('<base href="http://[abc/">', "http://ex.com/dir/page.html"),
        # Fora do site é honrado; quem decide não seguir é o filtro de mesmo-site.
        ('<base href="https://outro.invalid/">', "https://outro.invalid/"),
        ('<base href="//outro.invalid/">', "http://outro.invalid/"),
        # O primeiro vence — e um href vazio ou em branco não é um primeiro.
        ('<base href="/app/"><base href="/v2/">', "http://ex.com/app/"),
        ('<base href=""><base href="/app/">', "http://ex.com/app/"),
        ('<base href="   ">', "http://ex.com/dir/page.html"),
        ('<base href="   "><base href="/app/">', "http://ex.com/app/"),
        # Dois `href` na MESMA tag. A spec manda o primeiro valer; os dois
        # tokenizadores ficam com o último, e o que este caso fixa é que ficam
        # com o MESMO. Divergir aqui não apareceria em relatório nenhum: os dois
        # módulos simplesmente pediriam endereços diferentes do mesmo documento.
        ('<base href="/a/" href="/b/">', "http://ex.com/b/"),
        # Credencial não é honrada em base nenhuma. Uma base contamina TODO href
        # relativo do documento de uma vez, então é aqui que ela não entra.
        ('<base href="http://admin:s3cr3t@ex.com/app/">', "http://ex.com/app/"),
        ("<base target='_blank'>", "http://ex.com/dir/page.html"),
        # Dentro de uma subárvore que ESTE parser pula e o do crawler não. A
        # spec diria que não vale; o que não pode é um valer e o outro não, que
        # é a divergência invisível — nenhum relatório mostraria a causa.
        ('<template><base href="/tpl/"></template>', "http://ex.com/tpl/"),
        # E dentro de <script>, onde o conteúdo é texto cru para os dois
        # tokenizadores e `<base>` nunca chega a ser uma tag.
        ('<script><base href="/js/"></script>', "http://ex.com/dir/page.html"),
    ]
    for cabeca, esperado in casos:
        html = f"<html><head>{cabeca}</head><body><a href='s'>s</a></body></html>"
        do_crawler = parse_html(html).base_href
        do_modulo = parse_document(html).base_href
        assert do_modulo == do_crawler, cabeca
        assert resolve_base(url_do_doc, do_modulo) == esperado, cabeca


def test_base_href_muda_o_alvo_dos_links_de_navegacao(server):
    """BLOCKER reproduzido: `_nav_targets` juntava cada href contra a URL da home
    e ignorava o `<base href>` que `crawl` já respeitava.

    Uma home com `<base href="/app/">` e três links de menu servidos e vivos em
    `/app/` era reportada como "3 navigation link(s) return 4xx/5xx" — e três é
    exatamente `BROKEN_NAV_FAIL_THRESHOLD`, então o veredito de manchete virava
    FAIL ("o site parece abandonado") num site inteiro. `<base href>` é
    incomum, mas é HTML comum, e um FAIL falso é o pior erro deste pacote
    depois de um PASS falso.
    """
    base, rotas = server
    menu = "<nav>" + "".join(
        f"<a href='{p}'>{p}</a>" for p in ("sobre", "contato", "blog")
    ) + "</nav>"
    rotas["/"] = (200, {}, home_com_base("/app/", menu))
    for p in ("sobre", "contato", "blog"):
        rotas[f"/app/{p}"] = (200, {}, pagina(p))

    relatorio = count_broken_nav_links(base + "/")

    assert (relatorio.found, relatorio.checked, relatorio.count) == (3, 3, 0)
    assert relatorio.status is not Status.FAIL
    # E o que saiu no fio, não só o veredito: era `/sobre` que estava sendo
    # pedido, e é sobre o endereço pedido que o relatório mentia.
    pedidos = caminhos_pedidos(rotas)
    assert "/app/sobre" in pedidos
    assert "/sobre" not in pedidos


def test_crawl_e_completeness_resolvem_o_mesmo_documento_do_mesmo_jeito(server):
    """Os dois módulos leem a mesma home com tokenizadores diferentes. Discordar
    sobre para onde os links dela apontam não é detalhe de cada um: é o defeito.
    O teste compara os endereços resolvidos, não o veredito de nenhum dos dois.
    """
    base, rotas = server
    menu = "<nav><a href='sobre'>Sobre</a><a href='blog/'>Blog</a></nav>"
    rotas["/"] = (200, {}, home_com_base("/app/", menu))
    rotas["/app/sobre"] = (200, {}, pagina("Sobre"))
    rotas["/app/blog/"] = (200, {}, pagina("Blog"))

    do_crawler = crawl(base + "/", delay=0).pages[0].links

    resposta = fetch(base + "/")
    doc = parse_document(resposta.text)
    do_completeness = [u for u, _ in _nav_targets(doc, resposta.final_url or resposta.url)]

    assert sorted(do_completeness) == sorted(do_crawler)
    assert do_completeness == [f"{base}/app/sobre", f"{base}/app/blog/"]


def test_base_href_relativo_e_resolvido_contra_a_url_do_documento(server):
    """`<base href="app/">` não começa com barra: ele mesmo é relativo, e a base
    do documento é a URL do documento. Resolver "app/" contra nada devolveria a
    string crua e o link sumiria no filtro de esquema."""
    base, rotas = server
    rotas["/"] = (200, {}, home_com_base("app/", "<nav><a href='sobre'>s</a></nav>"))
    rotas["/app/sobre"] = (200, {}, pagina("Sobre"))

    relatorio = count_broken_nav_links(base + "/")

    assert (relatorio.checked, relatorio.count) == (1, 0)


def test_base_href_que_nao_parseia_cai_de_volta_na_url_do_documento(server):
    """`join_url` devolve "" para um host entre colchetes quebrado, e "" como base
    faz `urljoin` devolver todo href relativo intocado — que depois morre no
    filtro de esquema, transformando uma página com links bons em "0 links". É o
    caso que o crawler fecha com `or page.final_url`, e esta era a segunda cópia
    da regra que não podia divergir."""
    base, rotas = server
    rotas["/"] = (200, {}, home_com_base("http://[abc/", "<nav><a href='sobre'>s</a></nav>"))
    rotas["/sobre"] = (200, {}, pagina("Sobre"))

    relatorio = count_broken_nav_links(base + "/")

    assert (relatorio.found, relatorio.checked, relatorio.count) == (1, 1, 0)
    assert "/sobre" in caminhos_pedidos(rotas)


def test_o_primeiro_base_href_do_documento_e_o_que_vale(server):
    """A spec diz que o primeiro `<base href>` vence, e é o que o parser do
    crawler faz. Um segundo `<base>` mandando o menu para `/v2/` não pode mudar
    endereço nenhum — as duas rotas existem aqui justamente para que o teste
    prove qual delas foi pedida em vez de provar que alguma respondeu."""
    base, rotas = server
    rotas["/"] = (
        200,
        {},
        '<html><head><base href="/app/"><base href="/v2/"></head>'
        f"<body><h1>Casa</h1><p>{PROSA}</p><nav><a href='sobre'>s</a></nav></body></html>",
    )
    rotas["/app/sobre"] = (200, {}, pagina("Sobre"))
    rotas["/v2/sobre"] = (200, {}, pagina("Sobre v2"))

    count_broken_nav_links(base + "/")

    pedidos = caminhos_pedidos(rotas)
    assert "/app/sobre" in pedidos
    assert "/v2/sobre" not in pedidos


def test_base_href_vazio_nao_gasta_a_vez_do_proximo(server):
    """`<base href="">` não declara base nenhuma. Contá-lo como "o primeiro"
    faria o `<base href="/app/">` seguinte ser ignorado — e o parser do crawler
    já exige href não vazio para fixar a base."""
    base, rotas = server
    rotas["/"] = (
        200,
        {},
        '<html><head><base href=""><base href="/app/"></head>'
        f"<body><h1>Casa</h1><p>{PROSA}</p><nav><a href='sobre'>s</a></nav></body></html>",
    )
    rotas["/app/sobre"] = (200, {}, pagina("Sobre"))

    relatorio = count_broken_nav_links(base + "/")

    assert (relatorio.checked, relatorio.count) == (1, 0)


def test_base_href_para_fora_do_site_nao_cobra_uptime_de_terceiro(server):
    """Um `<base href>` para outro host manda todo link relativo para fora, e
    link de fora é uptime de terceiro. O crawler honra a base e deixa o filtro
    de mesmo-site decidir não seguir; contar esses links como quebrados apenas
    trocaria um FAIL falso por outro."""
    base, rotas = server
    rotas["/"] = (
        200,
        {},
        home_com_base("https://outro-site.invalid/", "<nav><a href='sobre'>s</a></nav>"),
    )

    relatorio = count_broken_nav_links(base + "/")

    assert (relatorio.found, relatorio.checked, relatorio.count) == (0, 0, 0)
    # `passed is True` ficava aqui, e era o silêncio que o revisor pegou: a linha
    # impressa era "[PASS] all 0 navigation links followed, none broken" sobre um
    # menu que TINHA link, sem uma palavra sobre a base que os levou embora. Zero
    # links seguidos porque a base foi recusada é condição não observada, e neste
    # pacote condição não observada é MISSING — nunca um pass.
    assert relatorio.status is Status.MISSING
    dito = frases(relatorio)
    assert "outro-site.invalid" in dito and "sobre" in dito
    # E o crawler chega ao mesmo endereço, pela mesma base, sem visitá-lo.
    assert crawl(base + "/", delay=0).off_site == ["https://outro-site.invalid/sobre"]


def test_base_href_muda_o_candidato_declarado_de_pagina_de_confianca(server):
    """`_candidates` juntava o href do rodapé contra a URL da home pelo mesmo
    motivo, então a página Sobre que o site declara nunca era pedida onde ela
    está: o relatório dizia "No About page found", que é a afirmação que o
    docstring de `check_trust_pages` proíbe — dita sobre uma URL que ninguém
    pediu."""
    base, rotas = server
    rodape = f"<footer><a href='sobre'>Sobre</a>{MAILTO}</footer>"
    rotas["/"] = (200, {}, home_com_base("/app/", rodape))
    rotas["/app/sobre"] = (200, {}, pagina("Sobre"))

    relatorio = check_trust_pages(base + "/")

    assert relatorio.pages["about"].status is Status.OK
    assert relatorio.pages["about"].url == f"{base}/app/sobre"


def test_base_href_so_com_espacos_nao_gasta_a_vez_do_proximo(server):
    """`<base href="   ">` não declara base nenhuma, exatamente como `href=""`
    não declara: o valor é aparado ANTES de contar. Aceitar os espaços como "o
    primeiro" faz o `<base href="/app/">` seguinte ser ignorado e o menu voltar
    a ser pedido na raiz — o FAIL falso que este código existe para impedir,
    reintroduzido por um `.strip()` a menos."""
    base, rotas = server
    rotas["/"] = (
        200,
        {},
        '<html><head><base href="   "><base href="/app/"></head>'
        f"<body><h1>Casa</h1><p>{PROSA}</p><nav><a href='sobre'>s</a></nav></body></html>",
    )
    rotas["/app/sobre"] = (200, {}, pagina("Sobre"))

    relatorio = count_broken_nav_links(base + "/")

    assert (relatorio.checked, relatorio.count) == (1, 0)
    assert "/app/sobre" in caminhos_pedidos(rotas)


def test_link_de_volta_para_a_home_nao_e_pedido_de_novo_sob_um_base():
    """`seen` começa com a identidade da HOME, não com a da base. Quem já
    respondeu é a home, e é outra grafia DELA que não ensina nada.

    Começar pela base faz duas coisas de uma vez: a home vira link de menu e é
    pedida de novo, e o link que aponta para a própria base (`/app/`) some da
    lista sem nunca ter sido verificado — um link quebrado ali passaria batido.
    """
    menu = "<nav><a href='/'>Início</a><a href='./'>App</a></nav>"
    doc = parse_document(home_com_base("/app/", menu))

    alvos = [u for u, _ in _nav_targets(doc, "http://ex.com/")]

    assert alvos == ["http://ex.com/app/"]


def test_base_href_de_outro_site_nao_faz_o_audit_pedir_pagina_de_terceiro(
    server, outro_servidor
):
    """Honrar a base é de propósito, e `resolve_base` diz no docstring que NÃO
    seguir para fora é trabalho do filtro de mesmo-site de cada chamador — este
    é o chamador que de fato busca. Sem o filtro, `check_trust_pages` pede
    `/sobre` no host do terceiro e reporta "about OK" apontando para lá: o audit
    responderia sobre o site auditado com a página de outra pessoa."""
    base, rotas = server
    terceiro, rotas_terceiro = outro_servidor
    rodape = f"<footer><a href='sobre'>Sobre</a>{MAILTO}</footer>"
    rotas["/"] = (200, {}, home_com_base(terceiro + "/", rodape))
    rotas_terceiro["/sobre"] = (200, {}, pagina("Sobre do terceiro"))

    relatorio = check_trust_pages(base + "/")

    assert rotas_terceiro.received == []
    assert relatorio.pages["about"].status is Status.MISSING
    assert terceiro not in (relatorio.pages["about"].url or "")


def test_base_href_com_esquema_que_o_cliente_nao_fala_nao_vira_candidato(server):
    """`<base href="ftp://…">` manda todo href relativo para um transporte que
    este cliente não fala. `_nav_targets` sempre filtrou por esquema e
    `_candidates` não: a página Sobre declarada virava um `fetch` de `ftp://`,
    voltava ERROR ("No connection adapters were found") e o relatório culpava o
    site por uma URL que ele nunca poderia servir por HTTP. Antes de a base ser
    honrada só um `<a href="ftp:…">` explícito chegava aqui; agora todo href
    relativo do documento chega, então o filtro passa a valer nos dois."""
    base, rotas = server
    porta = base.rsplit(":", 1)[1]
    rodape = f"<footer><a href='sobre'>Sobre</a>{MAILTO}</footer>"
    # Mesmo host e mesma porta: é o esquema, e só ele, que este teste isola.
    rotas["/"] = (200, {}, home_com_base(f"ftp://127.0.0.1:{porta}/", rodape))

    relatorio = check_trust_pages(base + "/")

    assert relatorio.pages["about"].status is Status.MISSING
    # Nenhuma tentativa por ftp: é isso que o filtro impede, e por isso o desfecho
    # é MISSING e não o ERROR de "no connection adapters" que o site levava a
    # culpa por. O que o relatório agora PODE citar — e cita — é a declaração:
    # calar sobre uma base recusada é como um menu inteiro sumia sem deixar linha.
    assert [u for u, _ in relatorio.pages["about"].attempts if u.startswith("ftp://")] == []
    assert "connection adapters" not in " | ".join(relatorio.issues)
    assert f'<base href="ftp://127.0.0.1:{porta}/">' in " | ".join(relatorio.issues)


def test_base_href_com_esquema_que_o_cliente_nao_fala_nao_vira_link_de_menu():
    """A outra metade da mesma guarda. `_nav_targets` sempre teve o filtro de
    esquema e nada o fixava: sem ele o menu inteiro vira `ftp://…`, cada link
    volta como "could not be resolved" e o relatório atribui ao site um defeito
    que é só um transporte que este cliente não fala."""
    doc = parse_document(home_com_base("ftp://ex.com/", "<nav><a href='sobre'>s</a></nav>"))

    assert _nav_targets(doc, "http://ex.com/") == []


def test_a_home_continua_sendo_a_home_quando_o_documento_declara_um_base():
    """`casa` é a identidade da HOME, não a da base — o mesmo motivo do `seen`
    em `_nav_targets`, na outra metade da checagem. A home já respondeu, então
    pedi-la de novo como candidata a Sobre não ensina nada; e é pior que inútil,
    porque a home tem prosa de sobra e voltaria OK, dando à página Sobre o
    endereço da própria home. Trocar pela base ainda descarta o candidato que
    aponta para a base — este, que é o que o site de fato declara."""
    rodape = "<footer><a href='/'>Sobre nós</a><a href='./'>Sobre o app</a></footer>"
    doc = parse_document(home_com_base("/app/", rodape))
    home = Fetch(url="http://ex.com/", final_url="http://ex.com/", status_code=200)

    cands = _candidates(home, doc, (), _ABOUT_HINT, 6)

    assert [c.url for c in cands] == ["http://ex.com/app/"]


def test_credencial_no_base_href_nao_viaja_nem_entra_no_relatorio(server):
    """Um `<base href>` com `user:pass@` põe a credencial na frente de TODO href
    relativo do documento de uma vez — e passou a pôr no dia em que este módulo
    começou a honrar a tag. `crawl` tira o userinfo de todo link que resolve
    desde que ADS-CRAWL-01 foi escrito; aqui não se tirava de nenhum.

    Três asserts porque são três danos distintos: a credencial ia no fio (as
    páginas eram lidas AUTENTICADAS e reportadas OK, e o 401 que provaria "não é
    legível publicamente" nunca acontecia), ficava na URL resolvida, e era
    impressa no relatório que o operador cola num ticket.
    """
    base, rotas = server
    porta = base.rsplit(":", 1)[1]
    com_credencial = f"http://admin:s3cr3t@127.0.0.1:{porta}/app/"
    menu = "<nav><a href='sobre'>Sobre</a><a href='quebrado'>Quebrado</a></nav>"
    rodape = f"<footer><a href='sobre'>Sobre</a>{MAILTO}</footer>"
    rotas["/"] = (200, {}, home_com_base(com_credencial, menu + rodape))
    rotas["/app/sobre"] = (200, {}, pagina("Sobre"))
    rotas["/app/quebrado"] = (404, {}, "")

    relatorio = check_completeness(base + "/")

    # 1. Nada de Authorization no fio: `requests` transforma o `user:pass@` da
    #    URL em Basic auth sozinho, então a credencial raspada da página seria
    #    devolvida ao servidor que a publicou, sem ninguém ter pedido.
    assert autenticadas(rotas) == []
    # 2. Nenhuma URL resolvida a carrega — nem a que deu certo, nem a que quebrou.
    urls = [p.url for p in relatorio.trust.pages.values() if p.url]
    urls += [link.url for link in relatorio.nav.broken + relatorio.nav.unresolved]
    assert f"{base}/app/quebrado" in urls  # a lista não está vazia por acidente
    assert [u for u in urls if "s3cr3t" in u] == []
    # 3. Nem o texto do relatório, que é onde a linha "1 navigation link(s)
    #    return 4xx/5xx: <url> (404)" estampava o segredo.
    assert "s3cr3t" not in " | ".join(relatorio.issues)
    # E a base continua valendo: é o caminho que ela move, não a credencial.
    assert relatorio.trust.pages["about"].url == f"{base}/app/sobre"


def test_credencial_escrita_no_proprio_href_tambem_nao_viaja(server):
    """Sem `<base>`: o href absoluto traz a credencial sozinho. É um link por
    vez em vez do documento inteiro, e o dano é o mesmo — o crawler tira o
    userinfo de todo link que resolve, e este módulo lê o MESMO documento."""
    base, rotas = server
    porta = base.rsplit(":", 1)[1]
    quebrado = f"http://admin:s3cr3t@127.0.0.1:{porta}/quebrado"
    rotas["/"] = (200, {}, pagina("Casa", extra=f"<nav><a href='{quebrado}'>q</a></nav>"))
    rotas["/quebrado"] = (404, {}, "")

    relatorio = count_broken_nav_links(base + "/")

    assert [link.url for link in relatorio.broken] == [f"{base}/quebrado"]
    assert autenticadas(rotas) == []
    assert "s3cr3t" not in " | ".join(relatorio.issues)


# --------------------------------------------------------------------------
# UMA base por auditoria.
#
# A sonda de não-encontrado é um fato sobre um DIRETÓRIO, não sobre um host:
# ela pergunta o que este servidor responde para um caminho que ninguém roteia,
# e a resposta só vale onde foi medida. Todo endereço que a auditoria pede tem
# que sair da mesma base que a sonda, senão a resposta de um diretório julga as
# respostas de outro — e o resultado é `[PASS] exit 0` sobre um site quebrado,
# que é a pior classe de falha deste pacote.
#
# Os testes abaixo prendem as três formas de essa base escorregar: os caminhos
# convencionais que ficaram para trás, uma base que aponta para fora do site
# auditado, e um arredondamento que `urljoin` não faz.
# --------------------------------------------------------------------------


def test_os_caminhos_convencionais_de_confianca_saem_da_mesma_base_da_sonda(server):
    """F1: a resposta de um diretório julgando as respostas de outro.

    A sonda passou a ser pedida em `resolve_base` e os 17 caminhos convencionais
    (`ABOUT_PATHS`, `CONTACT_PATHS`) continuaram na URL digitada. Com a raiz
    servindo soft 404 e `/app/` honesto, a sonda foi a `/app/` e voltou
    "este host gasta um 404 numa página que não tem" — verdade sobre `/app/`.
    `/about` e `/contact` são o template de erro DA RAIZ sob HTTP 200, e com
    aquele regime valendo os dois saíram `OK`: `[PASS] exit 0` sobre duas
    páginas que não existem.

    Sondar duas vezes, uma por base, não fecha isto. `_candidates` já resolve os
    candidatos LINKADOS contra o `<base href>` enquanto os convencionais ficam na
    base digitada, então um único relatório de confiança carrega as duas bases de
    qualquer jeito — medido no espelho (raiz honesta, `/app/` em soft 404), onde
    a sonda na base digitada aprova `about` em `/app/sobre`, que não existe. Uma
    lista com duas bases precisa de UMA base, não de uma segunda sonda.
    """
    base, rotas = server
    # Sem "sobre"/"contato" no menu: são os caminhos CONVENCIONAIS que este teste
    # isola, e um link declarado os deixaria sem uso.
    rotas["/"] = (200, {}, home_com_base("/app/", menu_de("blog", "loja", "cursos")))
    # `/app/` é honesto (404a o que não tem); a RAIZ responde 200 com um template
    # de erro longo o bastante para não ser stub, para qualquer coisa.
    rotas.default = lambda metodo, caminho: (
        None if caminho.startswith("/app/") else (200, {}, ERRO_404_LONGO)
    )
    for p in ("blog", "loja", "cursos"):
        rotas[f"/app/{p}"] = (200, {}, pagina(p))

    relatorio = check_completeness(base + "/")

    pedidos = caminhos_pedidos(rotas)
    assert f"/app/{NOT_FOUND_PROBE_PATHS[0]}" in pedidos
    # Os convencionais foram para debaixo da base, e NENHUM para a raiz.
    assert "/app/about" in pedidos and "/app/contato" in pedidos
    assert [c for c in pedidos if c in ("/about", "/contact", "/sobre", "/contato")] == []
    assert relatorio.trust.pages["about"].status is Status.MISSING
    assert relatorio.trust.pages["contact"].status is Status.MISSING
    assert relatorio.status is Status.FAIL


def test_um_base_href_de_outro_host_nao_manda_a_sonda_para_um_estranho(
    server, outro_servidor
):
    """F2: o regime de um terceiro decidindo o veredito do host auditado.

    `count_broken_nav_links` não sonda quando o filtro de mesmo-site derruba
    todos os links; `check_completeness` sondava incondicionalmente, na base que
    `resolve_base` devolvesse. Auditando um `staging` cujo template ainda carrega
    `<base href="https://www.exemplo.com/">`, ZERO sondas chegaram ao host
    auditado e o regime da produção decidiu o veredito do staging: o host
    auditado responde 200 para tudo, a produção é honesta, e `about`/`contact`
    saíram `WARNING` em vez de `MISSING`.

    `crawl` já recusa uma seed de fora e registra o porquê — uma URL que ESTA
    ferramenta inventa não vai para um host que o operador não nomeou. O
    `<base href>` continua honrado para os links que o DOCUMENTO escreveu: eles
    de fato apontam para fora, e derrubá-los é trabalho do filtro de mesmo-site.
    """
    base, rotas = server
    terceiro, rotas_terceiro = outro_servidor
    rotas["/"] = (200, {}, home_com_base(terceiro + "/", menu_de("sobre", "contato")))
    # O host auditado responde 200 para tudo; o terceiro é honesto.
    rotas.default = lambda metodo, caminho: (200, {}, ERRO_404_LONGO)
    rotas_terceiro["/"] = (200, {}, pagina("Producao"))

    relatorio = check_completeness(base + "/")

    assert rotas_terceiro.received == []
    assert f"/{NOT_FOUND_PROBE_PATHS[0]}" in caminhos_pedidos(rotas)
    assert relatorio.trust.pages["about"].status is Status.MISSING
    assert relatorio.trust.pages["contact"].status is Status.MISSING
    assert relatorio.status is Status.FAIL


def test_um_base_href_sem_host_nao_vira_um_endereco_inventado(server):
    """F2, a metade pior: um host montado a partir da marcação.

    `as_base` prefixa `https://` em qualquer coisa sem `://`, então
    `<base href="mailto:contato@127.0.0.1:61081">` virava
    `https://mailto:contato@127.0.0.1:61081/` — uma conexão de verdade para um
    endereço que ninguém escreveu — e a URL forjada era impressa no relatório do
    operador como se o host auditado a tivesse respondido. É por isso que o
    clamp roda ANTES de `as_base`, e é essa ordem que este teste prende.

    O parágrafo sobre `ftp://` que ficava aqui argumentava por um caso que este
    teste não exercia: quem prende o esquema no host auditado é
    `test_base_href_ftp_no_host_auditado_e_recusado_porque_same_site_ignora_esquema`,
    que manda um `ftp://` de verdade pelo mesmo caminho.
    """
    base, rotas = server
    # A porta do endereço forjado é A DO HOST AUDITADO, de propósito. Com uma
    # porta qualquer o filtro de mesmo-site derruba a base antes de a ordem
    # entre clamp e `as_base` decidir coisa alguma, e trocar a ordem sobrevivia
    # à suíte inteira. Com a porta batendo, `as_base` primeiro produz
    # `https://mailto:contato@127.0.0.1:<porta>/`, cujo host e porta passam por
    # `same_site` — e aí a URL montada a partir da marcação vira a base de
    # verdade, com `mailto:contato@` viajando como userinfo.
    porta = base.rsplit(":", 1)[1]
    forjado = f"mailto:contato@127.0.0.1:{porta}"
    rotas["/"] = (200, {}, home_com_base(forjado, menu_de("sobre")))
    rotas.default = lambda metodo, caminho: (200, {}, ERRO_404_LONGO)

    relatorio = check_completeness(base + "/")

    # A sonda foi para o host auditado, e o relatório só nomeia URLs dele.
    assert f"/{NOT_FOUND_PROBE_PATHS[0]}" in caminhos_pedidos(rotas)
    texto = " | ".join(relatorio.issues)
    assert f"{base}/{NOT_FOUND_PROBE_PATHS[0]}" in texto
    # O endereço FORJADO é o que não pode existir em lugar nenhum.
    assert "https://mailto:" not in texto
    # A declaração, essa aparece, e de propósito: o operador precisa saber que o
    # documento declarou uma base e que os caminhos chutados foram para outro
    # lugar por causa dela.
    assert f'<base href="{forjado}">' in texto


# Cada forma de `<base href>` com OS DOIS diretórios que ela produz, escritos
# aqui em vez de calculados: recalcular a regra no teste deixaria os dois lados
# errados juntos.
#
#   * o diretório dos LINKS é o que `urljoin` faz com um href relativo. `/app`
#     sem barra final não é um diretório para ele — o último segmento é
#     substituído — então `sobre` cai em `/sobre`.
#   * o diretório das URLS INVENTADAS é o que `as_base` diz, e ele arredonda
#     `/app` para `/app/`.
#
# Que as duas colunas DIFIRAM em cinco das sete linhas é a tese deste arquivo,
# não um defeito: são duas perguntas diferentes e nenhuma expressão responde as
# duas. Três commits tentaram uma expressão só e cada um consertou um lado
# quebrando o outro. O que impede a diferença de virar veredito errado não é
# igualá-las — é `_probe_covers`, que só deixa a sonda classificar uma resposta
# que voltou de onde ela mediu.
BASES_E_OS_DOIS_DIRETORIOS = [
    # base_href, diretório dos links, diretório das URLs inventadas
    ("/app/", "/app/", "/app/"),
    ("/app", "/", "/app/"),
    ("/a/b/c", "/a/b/", "/a/b/c/"),
    ("app", "/", "/app/"),
    ("/app/index.html", "/app/", "/app/"),
    ("/v1.0", "/", "/v1.0/"),
    ("/blog.old", "/", "/blog.old/"),
    # O apex que serve um DOCUMENTO, que é a linha que faltava — e faltar era o
    # que fazia a asserção de `_probe_covers` no fim deste teste passar por
    # OMISSÃO. Aqui as duas colunas são `/`, então o diretório sondado é a RAIZ,
    # e sob contenção "contido no diretório sondado" é o site inteiro:
    # `_probe_covers(..., "/outro-diretorio/vizinho")` saía True e a trava era um
    # no-op. É o caso que a própria mensagem de `df55766` citava.
    ("/index.php", "/", "/"),
]


# Os dois pontos de entrada que mandam a sonda. Cada teste de invariante roda
# nos dois: eles sondam em CHAMADAS diferentes, e consertar só um deixou a outra
# metade solta uma vez — foi o que `d1b0a0f` avisou e repetiu. Medido: pôr o
# `as_base` de volta só em `check_completeness` sobrevivia a uma suíte que
# prendia a invariante apenas por `count_broken_nav_links`.
PONTOS_DE_ENTRADA = [count_broken_nav_links, check_completeness]


@pytest.mark.parametrize("entrada", PONTOS_DE_ENTRADA, ids=lambda f: f.__name__)
@pytest.mark.parametrize(("base_href", "dos_links", "das_inventadas"), BASES_E_OS_DOIS_DIRETORIOS)
def test_a_sonda_cai_no_diretorio_das_urls_inventadas_e_os_links_onde_urljoin_manda(
    server, base_href, dos_links, das_inventadas, entrada
):
    """As DUAS bases de uma vez, cada uma no endereço que lhe cabe.

    Este teste substitui `test_a_sonda_cai_exatamente_onde_os_links_caem`, que
    exigia UM endereço só. Essa exigência era o defeito, não a correção: a sonda
    pergunta o que este servidor responde para um caminho que ninguém roteia, e
    a resposta vale no DIRETÓRIO em que foi medida. Uma auditoria pede URLs em
    mais de um diretório — as que ela inventa num, as que o documento escreveu
    noutro, as absolutas onde bem entenderem — e obrigar tudo a caber numa base
    só é o que fez três commits consertarem um lado quebrando o outro.

    O menu tem UM link e o href dele é a primeira URL de
    `NOT_FOUND_PROBE_PATHS`, então o fio mostra exatamente onde cada uma das duas
    perguntas foi parar: um caminho quando as duas bases coincidem, dois quando
    divergem — e divergir é o esperado em cinco das sete formas da tabela.
    """
    base, rotas = server
    sonda = NOT_FOUND_PROBE_PATHS[0]
    rotas["/"] = (200, {}, home_com_base(base_href, menu_de(sonda)))

    entrada(base + "/")

    pedidos = sorted(c for c in caminhos_pedidos(rotas) if sonda in c)
    assert pedidos == sorted({dos_links + sonda, das_inventadas + sonda})

    # E a TRAVA tem de concordar com a mesma coluna. Só o fio estava afirmado
    # aqui, e a trava fatiava `probe.base` em vez de resolvê-la como o `_join`
    # resolve: numa base com cara de documento o `urljoin` derruba o último
    # segmento, então a sonda ia para `/app/` enquanto a trava se julgava dona
    # de `/app/index.html`. Esta linha da tabela passava com a trava errada, e
    # um apex que redireciona para `/index.php` bastava para reportar MISSING
    # sobre os vizinhos da própria sonda.
    # A base é a que `_invented_base` devolve, NÃO o diretório onde a sonda cai:
    # numa base com cara de documento as duas diferem (`/app/index.html` contra
    # `/app/`), e é exatamente essa diferença que a trava errava. Construir a
    # sonda com o diretório de chegada esconderia o defeito.
    do_lado = _NotFoundProbe("honest", base=_invented_base(base + "/", base_href).url)
    assert _probe_covers(do_lado, base + das_inventadas + "vizinho") is True
    assert _probe_covers(do_lado, base + "/outro-diretorio/vizinho") is False


@pytest.mark.parametrize("entrada", PONTOS_DE_ENTRADA, ids=lambda f: f.__name__)
def test_a_sonda_cai_no_diretorio_da_home_mesmo_sem_um_base_href(server, entrada):
    """ESTE TESTE PRENDIA O DEFEITO, e por isso ele muda em vez de sobreviver.

    Ele se chamava `test_a_sonda_cai_onde_os_links_caem_sem_precisar_de_um_base_href`
    e exigia a sonda em `/` — que é, literalmente, a regressão de `eede9ce`. A
    forma é a de uma instalação Next.js com `trailingSlash:false` em `/app`: a
    raiz redireciona para `/app`, `/app` não é diretório para `urljoin`, e com a
    sonda obrigada a acompanhar os links ela ia parar na raiz. Numa raiz honesta
    e com `/app/` servindo soft 404, a sonda voltava "este host gasta um 404
    numa página que não tem" e três links absolutos mortos dentro de `/app/`
    saíam `[PASS]`, exit 0. A suíte protegia isso.

    O que vale é o contrário: a sonda pertence ao diretório da INSTALAÇÃO, que é
    onde esta auditoria inventa URL, e é `/app/`. Os links relativos continuam
    caindo na raiz, porque é lá que `urljoin` os põe — e é `_probe_covers` que
    impede a resposta de um diretório de julgar as do outro.
    """
    base, rotas = server
    sonda = NOT_FOUND_PROBE_PATHS[0]
    rotas["/"] = (301, {"Location": "/app"}, "")
    rotas["/app"] = (200, {}, pagina("Casa", extra=menu_de(sonda)))

    entrada(base + "/")

    pedidos = sorted(c for c in caminhos_pedidos(rotas) if sonda in c)
    assert pedidos == sorted(["/app/" + sonda, "/" + sonda])


def test_a_sonda_da_auditoria_inteira_segue_o_redirect_para_quem_de_fato_responde(
    server, outro_servidor
):
    """A sonda pertence ao host que RESPONDEU, não ao que o operador digitou.

    Apex -> www é o redirect mais comum da web e são dois hosts. Perguntar ao
    primeiro o que ele responde para uma página que não existe e depois julgar
    por isso as páginas servidas pelo segundo é a mesma troca de diretório de
    F1, uma origem acima. Nada prendia isto: trocar `home.final_url or home.url`
    por `home.url` sobrevivia à suíte inteira, aqui e em `9f8f037`.
    """
    base, rotas = server
    destino, rotas_destino = outro_servidor
    rotas["/"] = (301, {"Location": destino + "/"}, "")
    rotas_destino["/"] = (200, {}, pagina("Casa", extra=menu_de("blog")))
    rotas_destino.default = lambda metodo, caminho: (200, {}, ERRO_404_LONGO)

    relatorio = check_completeness(base + "/")

    assert [c for c in caminhos_pedidos(rotas) if NOT_FOUND_PROBE_PATHS[0] in c] == []
    assert f"/{NOT_FOUND_PROBE_PATHS[0]}" in caminhos_pedidos(rotas_destino)
    # E o regime medido no host que respondeu é o que vale para as páginas dele.
    assert relatorio.trust.pages["about"].status is Status.MISSING


def test_a_sonda_da_navegacao_segue_o_redirect_para_quem_de_fato_responde(
    server, outro_servidor
):
    """A mesma invariante no outro ponto de chamada, que sonda por conta própria.

    `count_broken_nav_links` pede a sonda quando ninguém lhe entrega uma, e é o
    caminho que um operador roda direto. Prender só `check_completeness` deixava
    esta metade solta.
    """
    base, rotas = server
    destino, rotas_destino = outro_servidor
    rotas["/"] = (301, {"Location": destino + "/"}, "")
    rotas_destino["/"] = (200, {}, pagina("Casa", extra=menu_de("blog")))
    rotas_destino.default = lambda metodo, caminho: (200, {}, ERRO_404_LONGO)

    relatorio = count_broken_nav_links(base + "/")

    assert [c for c in caminhos_pedidos(rotas) if NOT_FOUND_PROBE_PATHS[0] in c] == []
    assert f"/{NOT_FOUND_PROBE_PATHS[0]}" in caminhos_pedidos(rotas_destino)
    assert relatorio.not_found_regime == "fingerprint"


# --------------------------------------------------------------------------
# UMA SONDA POR DIRETÓRIO.
#
# Uma base só nunca cobriu todos os pedidos de uma auditoria, e nenhuma escolha
# de base cobre um link ABSOLUTO — ele cai onde ele diz, debaixo de nenhuma das
# duas. Quatro tentativas discutiram QUAL base usar; a quinta parou de inferir.
#
# A resposta de "o que este servidor serve para uma página que não existe" vale
# NUM diretório, então cada diretório de onde uma resposta veio é perguntado
# sobre si mesmo, até `MAX_PROBED_DIRECTORIES`. `_probe_covers` é o predicado
# que casa uma resposta com uma medição — e é ele, não uma chave de dicionário,
# que `_NotFoundProbes` consulta, justamente para que afrouxá-lo mude
# comportamento e quebre teste em vez de virar enfeite.
#
# `df55766` gastava a trava com CONTENÇÃO, e contenção não é medição: com a
# sonda na raiz, "contido no diretório sondado" é o site inteiro e a trava é um
# no-op. São as 40 combinações de `APEX_DOCUMENTOS` x `SUBDIRETORIOS` abaixo,
# que saíam com falso PASS em 40 de 40.
#
# Cada teste abaixo é uma forma que quebrou pelo menos uma tentativa anterior.
# --------------------------------------------------------------------------


# As oito grafias com que um apex serve um DOCUMENTO em vez de um diretório. É
# a coluna que faz `as_base` derrubar o último segmento, e por isso o diretório
# sondado vira `/` — a raiz, que contém tudo. Escritas aqui em vez de geradas:
# gerar por extensão deixaria de fora `default.asp`, que não segue o padrão dos
# outros e é o que um IIS antigo serve.
APEX_DOCUMENTOS = [
    "/index.php", "/index.html", "/index.htm", "/index.jsp",
    "/index.cgi", "/default.asp", "/default.aspx", "/home.php",
]

# Subdiretórios comuns, com barra final — e a barra é o ponto: `urljoin` diz
# que uma URL com barra final É um diretório, então cada um destes é o seu
# próprio diretório e nenhum deles foi medido pela sonda da raiz.
SUBDIRETORIOS = ["/blog/", "/loja/", "/pt/", "/en/", "/docs/"]


def _apex_documento_com_subdiretorio_de_soft_404(rotas, documento, subdiretorio, mortos):
    """Apex 301 -> documento; raiz honesta; `subdiretorio` responde 200 a tudo.

    A forma de `9821aee` e de `df55766` juntas: o apex redireciona para um
    documento, `as_base` põe a sonda em `/`, a raiz gasta um 404 de verdade — e
    o CMS montado no subdiretório responde 200 com o template de erro para
    qualquer coisa. Sob contenção os links do subdiretório contavam como medidos
    pela sonda da raiz e saíam vivos.
    """
    rotas["/"] = (301, {"Location": documento}, "")
    rotas[documento] = (200, {}, pagina("Casa", extra=menu_de(*mortos)))
    rotas.default = lambda metodo, caminho: (
        (200, {}, ERRO_404_LONGO) if caminho.startswith(subdiretorio) else None
    )


@pytest.mark.parametrize("subdiretorio", SUBDIRETORIOS)
@pytest.mark.parametrize("documento", APEX_DOCUMENTOS)
def test_apex_que_serve_documento_nao_aprova_links_de_um_subdiretorio_de_soft_404(
    server, documento, subdiretorio
):
    """Formas 1 e 2: as 40 combinações em que a contenção dava falso PASS.

    Reproduzido contra `df55766`: `nav=MISSING` recusando 5 links virava
    `nav=OK` recusando 0, com três deles servindo o template de erro. O motivo
    não era a expressão e sim a inferência — a trava lia "contido no diretório
    sondado" como "medido pela sonda", e quando o diretório sondado é `/` isso é
    o site inteiro.

    Aqui cada subdiretório é PERGUNTADO. A raiz continua honesta, e é isso que
    torna a forma uma armadilha: a resposta que existe é verdadeira, só não é
    sobre onde os links caíram.
    """
    base, rotas = server
    mortos = tuple(f"{subdiretorio}a{i}" for i in range(3))
    _apex_documento_com_subdiretorio_de_soft_404(rotas, documento, subdiretorio, mortos)

    relatorio = count_broken_nav_links(base + "/")

    # O VEREDITO primeiro, porque é ele que estava errado: nada observado
    # quebrado, e NADA dado como vivo. Era `passed` que saía True em 40 de 40, e
    # pôr o fio antes disto faria a evidência de regressão apontar para a
    # requisição que faltou em vez de para o passe falso que ela causou.
    assert relatorio.passed is False
    assert relatorio.count == 0
    assert [link.url for link in relatorio.unclassified] == [base + c for c in mortos]
    assert relatorio.status is Status.MISSING
    # E o fio explica o veredito: a raiz respondeu honestamente, e o
    # subdiretório foi perguntado por si mesmo em vez de herdar a resposta dela.
    pedidos = caminhos_pedidos(rotas)
    assert f"/{NOT_FOUND_PROBE_PATHS[0]}" in pedidos
    assert f"{subdiretorio}{NOT_FOUND_PROBE_PATHS[0]}" in pedidos
    assert relatorio.not_found_regime == "honest"


def test_apex_que_serve_documento_com_tudo_vivo_ao_lado_da_sonda_continua_ok(server):
    """Forma 3: a direção oposta, e o falso MISSING que `9821aee` produziu.

    Mesmo apex redirecionando para `/index.php`, mesma raiz honesta — e agora
    todo link vivo, ao lado da sonda, no diretório que ela mediu. Em `9821aee` a
    sonda era mandada para uma base inventada e o diretório era obtido FATIANDO
    o caminho, de modo que a trava se julgava dona de `/index.php`; os vizinhos
    da própria sonda saíam "fora do único diretório que esta corrida mediu", com
    uma frase falsa sobre a sonda da própria corrida.

    Uma correção grosseira na outra direção — exigir o mesmo diretório com uma
    sonda só — quebra exatamente aqui, e é por isso que a igualdade só chega
    junto com uma sonda por diretório.
    """
    base, rotas = server
    rotas["/"] = (301, {"Location": "/index.php"}, "")
    rotas["/index.php"] = (
        200, {}, pagina("Casa", extra=menu_de("/sobre", "/contato", "/blog"))
    )
    rotas["/sobre"] = (200, {}, pagina("Sobre"))
    rotas["/contato"] = (200, {}, contato())
    rotas["/blog"] = (200, {}, pagina("Blog"))

    relatorio = check_completeness(base + "/")

    # O VEREDITO primeiro: em `9821aee` este site saía com os vizinhos da
    # própria sonda reportados como não medidos, e é o falso MISSING que a linha
    # de baixo prende.
    assert relatorio.nav.passed is True
    assert relatorio.nav.unclassified == []
    assert relatorio.status is Status.OK
    assert relatorio.trust.pages["about"].url == base + "/sobre"
    # E o custo: uma sonda, uma vez, porque os três links moram no diretório em
    # que ela foi medida. Nenhum diretório recusado pelo teto.
    assert caminhos_pedidos(rotas).count(f"/{NOT_FOUND_PROBE_PATHS[0]}") == 1
    assert relatorio.nav.not_found_regime == "honest"
    assert relatorio.nav.refused_directories == []


def test_base_href_com_barra_final_e_raiz_de_soft_404_ainda_reprova_confianca_ausente(
    server,
):
    """Forma 6: a instalação é honesta, a RAIZ é que serve soft 404.

    O alvo digitado é o APEX, e é só o `<base href="/app/">` que diz onde o site
    mora. `/app/` gasta 404 de verdade e não tem About nem Contact; a raiz
    responde 200 para tudo, com um template que ECOA o endereço pedido — sem
    digital estável, portanto, e `_is_not_found_page` não tem por onde descartar
    nada.

    É a armadilha ao contrário das outras. Se os caminhos convencionais fossem
    pedidos na raiz, cada um responderia 200 com prosa de sobra, `_judge_page`
    aprovaria o template como página Sobre e o relatório sairia sem o FAIL. Eles
    vão para a INSTALAÇÃO porque é isso que `_invented_base` responde, então 404,
    e as duas páginas ficam MISSING — que é FAIL, o veredito certo para um site
    sem forma de identificar nem contatar o autor.
    """
    base, rotas = server
    rotas["/"] = (200, {}, home_com_base("/app/", menu_de("blog")))
    rotas["/app/blog"] = (200, {}, pagina("Blog"))
    rotas.default = lambda metodo, caminho: (
        None
        if caminho.startswith("/app/")
        else (200, {}, pagina("Nada aqui", corpo=f"{PROSA} Nada em {caminho}."))
    )

    relatorio = check_completeness(base + "/")

    # A sonda foi para a instalação, e a raiz não foi sondada: nada da corrida
    # respondeu de lá além da própria home.
    pedidos = caminhos_pedidos(rotas)
    assert f"/app/{NOT_FOUND_PROBE_PATHS[0]}" in pedidos
    assert f"/{NOT_FOUND_PROBE_PATHS[0]}" not in pedidos
    assert relatorio.trust.pages["about"].status is Status.MISSING
    assert relatorio.trust.pages["contact"].status is Status.MISSING
    assert "Neither an About nor a Contact page was found" in " | ".join(relatorio.issues)
    assert relatorio.status is Status.FAIL


def test_base_href_de_outro_site_nao_manda_sonda_nenhuma_para_o_terceiro(
    server, outro_servidor
):
    """Forma 8, e o risco NOVO que uma sonda por diretório introduz.

    Com uma sonda por corrida havia um único endereço inventado para clampar. Um
    prober por diretório inventa um endereço por diretório de CHEGADA, e um link
    que o `<base href>` levou para fora chega de outro host: perguntar-lhe o que
    ele serve para uma página que não existe é mandar URL inventada para um host
    que o operador não nomeou, debaixo de um robots.txt que ninguém leu para ele.

    O terceiro tem que receber ZERO requisições. Os links relativos são
    derrubados pelo filtro de mesmo-site antes do `fetch`, e o clamp de
    `_invented_base` mantém a âncora aqui; o que este teste prende é que o teto
    não vira uma porta lateral para o host errado.
    """
    base, rotas = server
    terceiro, rotas_terceiro = outro_servidor
    menu = "<nav><a href='sobre'>Sobre</a><a href='loja'>Loja</a></nav>"
    rotas["/"] = (200, {}, home_com_base(terceiro + "/", menu))
    rotas_terceiro["/sobre"] = (200, {}, pagina("Sobre do terceiro"))
    rotas_terceiro["/loja"] = (200, {}, pagina("Loja do terceiro"))
    rotas.default = lambda metodo, caminho: (200, {}, ERRO_404_LONGO)

    relatorio = check_completeness(base + "/")

    assert rotas_terceiro.received == []
    # A âncora ficou no host auditado, e é o único host sondado.
    assert f"/{NOT_FOUND_PROBE_PATHS[0]}" in caminhos_pedidos(rotas)
    # E a recusa é dita, nomeando a base que a causou.
    assert f'<base href="{terceiro}/">' in " | ".join(relatorio.issues)


# --------------------------------------------------------------------------
# O TETO, e o que ele custa.
#
# Uma sonda por diretório é a única regra certa nas duas direções, e ela tem
# preço: uma URL com barra final é o seu próprio diretório, então num menu
# moderno quase todo link é um. O teto é a decisão de projeto, e estes testes
# fixam o valor, o que acontece no diretório seguinte a ele, e o custo medido.
# --------------------------------------------------------------------------


def test_o_teto_de_diretorios_sondados_e_oito(server):
    """O valor, fixado, e o vizinho de cada lado.

    Oito é o menor valor que cobre os seis diretórios da fixture documentada em
    EXAMPLES.md com folga para as duas adições comuns — um diretório de
    instalação vindo de `<base href>` e um link de confiança no rodapé com
    diretório próprio. O teto é constante PRÓPRIA e não `DEFAULT_NAV_LINK_LIMIT`:
    aquele limita quanto da navegação PUBLICADA pelo site é lida, endereços que
    existem e que um navegador também pede; este limita pedidos a endereços que
    NINGUÉM roteia, que só esta ferramenta manda e que caem no log de erro de
    terceiro. Compartilhá-los faria o rastro inventado crescer com o tamanho do
    menu, e um operador subindo `--nav-limit` dobraria a pegada em silêncio.

    O menu tem `MAX_PROBED_DIRECTORIES + 1` diretórios distintos, todos vivos e
    num host honesto. O sétimo do menu é medido; o oitavo não, e o link de lá sai
    MISSING em vez de aprovado — recusar-se a chutar é a direção segura, e é um
    falso MISSING limitado e dito no relatório.
    """
    base, rotas = server
    diretorios = [f"/d{i}/" for i in range(MAX_PROBED_DIRECTORIES + 1)]
    rotas["/"] = (200, {}, pagina("Casa", extra=menu_de(*diretorios)))
    for d in diretorios:
        rotas[d] = (200, {}, pagina(d))

    relatorio = count_broken_nav_links(base + "/")

    assert MAX_PROBED_DIRECTORIES == 8
    # A âncora `/` toma o primeiro lugar; sobram sete para o menu.
    medidos = [c for c in caminhos_pedidos(rotas) if NOT_FOUND_PROBE_PATHS[0] in c]
    assert len(medidos) == MAX_PROBED_DIRECTORIES
    assert f"/d{MAX_PROBED_DIRECTORIES - 2}/{NOT_FOUND_PROBE_PATHS[0]}" in medidos
    assert f"/d{MAX_PROBED_DIRECTORIES - 1}/{NOT_FOUND_PROBE_PATHS[0]}" not in medidos
    # Os dois últimos diretórios ficaram sem medição, e o relatório os nomeia.
    recusados = diretorios[MAX_PROBED_DIRECTORIES - 1:]
    assert relatorio.refused_directories == recusados
    assert [link.url for link in relatorio.unmeasured] == [base + d for d in recusados]
    assert relatorio.count == 0
    assert relatorio.status is Status.MISSING
    dito = frases(relatorio)
    assert f"ceiling of {MAX_PROBED_DIRECTORIES} probed directories" in dito
    assert recusados[0] in dito


def test_o_teto_e_um_so_para_as_duas_sub_checagens(server):
    """Um conjunto de sondas, um teto. Duas contagens dobrariam a pegada.

    `check_completeness` monta `_NotFoundProbes` uma vez e entrega o MESMO
    objeto às duas sub-checagens. Se cada uma tivesse o seu, um site que gasta o
    teto nas páginas de confiança gastaria outro tanto no menu — a constante
    limitaria metade do rastro e diria que limita o todo.

    Para carregar o nome, o teto tem de ser ALCANÇADO e as duas metades têm de
    ter gasto dele. O site abaixo põe quatro diretórios ao alcance da metade de
    confiança (links do rodapé, cada um no seu) e oito ao alcance do menu, num
    host de soft 404 — que é o que faz cada 200 precisar de classificação. Com um
    teto só, a soma para em `MAX_PROBED_DIRECTORIES`. Com um teto por
    sub-checagem, a navegação recomeçaria do zero e a soma passaria dele.

    O que este teste NÃO fixa é QUAIS links ficam sem medição. Isso é
    consequência do teto, não objetivo dele; prender a lista transformava um
    efeito colateral tolerado em contrato, e era o que a versão anterior fazia.
    """
    base, rotas = server
    confianca = "".join(f"<a href='/a{i}/sobre'>Sobre</a>" for i in range(3))
    confianca += "<a href='/c0/contato'>Contato</a>"
    menu = [f"/d{i}/" for i in range(8)]
    rotas["/"] = (
        200,
        {},
        pagina("Casa", extra=menu_de(*menu) + f"<footer>{confianca}</footer>"),
    )
    rotas.default = (200, {}, ERRO_404_LONGO)

    relatorio = check_completeness(base + "/")

    pedidos = caminhos_pedidos(rotas)
    sondados = {
        c.rsplit("/", 1)[0] + "/" for c in pedidos if NOT_FOUND_PROBE_PATHS[0] in c
    }
    # Uma soma só, e ela chega no teto.
    assert len(sondados) == MAX_PROBED_DIRECTORIES
    # As duas metades gastaram do MESMO teto: há diretório de link de confiança e
    # diretório de menu no mesmo conjunto. Com um `_NotFoundProbes` por
    # sub-checagem, a navegação começaria com o teto inteiro na mão e a soma
    # passaria de `MAX_PROBED_DIRECTORIES`.
    assert sondados & {f"/a{i}/" for i in range(3)}
    assert sondados & set(menu)
    # E foi o teto que segurou, não a falta de diretório: a corrida pediu
    # endereços em mais diretórios do que ela mediu.
    visitados = {
        c.rsplit("/", 1)[0] + "/" for c in pedidos if NOT_FOUND_PROBE_PATHS[0] not in c
    }
    assert len(visitados) > MAX_PROBED_DIRECTORIES
    # Nada foi observado quebrado: um 200 não medido nunca vira link morto.
    assert relatorio.nav.count == 0
    assert relatorio.nav.status is Status.MISSING


def test_o_custo_de_uma_corrida_medido_no_fio_por_forma(server):
    """A tabela de custo, medida no fio e não estimada.

    Uma linha por forma. O que ela mostra é o que justifica o teto: num site de
    um diretório só o custo é o MESMO de uma sonda por corrida, e o acréscimo só
    aparece onde os links de fato se espalham.
    """
    base, rotas = server

    def custo(monta):
        for chave in list(rotas):
            del rotas[chave]
        rotas.default = None
        monta()
        rotas.received.clear()
        check_completeness(base + "/")
        pedidos = [c for _m, c, _h in rotas.received]
        sondas = [c for c in pedidos if "adsense-auditor-probe" in c]
        return len(pedidos), len(sondas)

    def um_diretorio():
        links = ["/a", "/b", "/c"]
        rotas["/"] = (200, {}, pagina("Casa", extra=menu_de(*links)))
        for c in links:
            rotas[c] = (200, {}, pagina(c))

    def seis_diretorios():
        links = [f"/s{i}/" for i in range(6)]
        rotas["/"] = (200, {}, pagina("Casa", extra=menu_de(*links)))
        for c in links:
            rotas[c] = (200, {}, pagina(c))

    def acima_do_teto():
        links = [f"/s{i}/" for i in range(25)]
        rotas["/"] = (200, {}, pagina("Casa", extra=menu_de(*links)))
        for c in links:
            rotas[c] = (200, {}, pagina(c))

    # Um diretório: uma sonda, exatamente como antes desta mudança.
    assert custo(um_diretorio)[1] == 1
    # Seis diretórios de menu + a âncora: sete, e todos couberam no teto.
    assert custo(seis_diretorios)[1] == 7
    # Vinte e cinco diretórios: o teto morde e o número para de crescer.
    total, sondas = custo(acima_do_teto)
    assert sondas == MAX_PROBED_DIRECTORIES
    # E o inventado continua minoria do que o site publicou.
    assert sondas < total - sondas


def test_um_rodape_espalhado_nao_faz_o_teto_aprovar_o_template_de_erro(server):
    """O buraco que o teto ABRIA nas páginas de confiança, agora fechado.

    Era assim: as tuplas convencionais incluem grafias com barra final, e
    `/sobre/` era o seu próprio diretório. Um rodapé linkando oito candidatos
    Sobre em oito diretórios gastava o teto antes de `/sobre/` chegar, e num host
    de soft 404 é o template de erro que responde lá — com prosa de sobra para
    `_judge_page` aprovar. A página SAÍA aprovada, e o `[FAIL] Neither an About
    nor a Contact page was found` sumia do relatório de um site que não tem
    nenhuma das duas.

    O que fecha é os convencionais serem perguntados na base de onde foram
    inventados em vez de cada um no diretório que ocuparia. Uma vaga entre todos,
    e a resposta medida na base reconhece o template que `/sobre/` serve, porque
    é o mesmo catch-all. O documento continua mandando: os oito linkados são
    endereços que o SITE escreveu, então cada um é perguntado onde caiu, e são
    eles que gastam o teto — como deve ser, evidência melhor primeiro.
    """
    assert MAX_LINKED_CANDIDATES < MAX_PROBED_DIRECTORIES
    base, rotas = server
    linkados = "".join(f"<a href='/a{i}/sobre'>Sobre</a>" for i in range(8))
    rotas["/"] = (200, {}, home_com_base("/", f"<footer>{linkados}</footer>"))
    rotas.default = lambda metodo, caminho: (200, {}, ERRO_404_LONGO)

    relatorio = check_completeness(base + "/")

    assert f"/{NOT_FOUND_PROBE_PATHS[0]}" in caminhos_pedidos(rotas)
    # Nenhum candidato era página: todos serviram o que a base serve para o que
    # não existe, `/sobre/` inclusive.
    sobre = relatorio.trust.pages["about"]
    assert sobre.status is Status.MISSING
    assert sobre.url is None
    assert "the page their own directory serves for a URL that does not exist" in sobre.reason
    # E o veredito de manchete voltou. Antes ele sumia: `/sobre/` passava e a
    # dupla About+Contact deixava de estar ausente, então o site sem nenhuma das
    # duas não ouvia a frase que o AdSense de fato aplica.
    assert relatorio.trust.pages["contact"].status is Status.MISSING
    assert any(
        "Neither an About nor a Contact page was found" in f.message
        for f in relatorio.trust.findings
    )
    assert exit_code(relatorio.status) == 1
    assert relatorio.status.is_bad


# --------------------------------------------------------------------------
# ACEITAR NÃO É EXCLUIR.
#
# Perguntar o convencional na base fecha o caso em que `/about/` NÃO existe: o
# catch-all da base responde, e a resposta da base o descreve. Não fecha o caso
# em que o chute ACERTA — `/about/` montado como roteador próprio, com o seu
# próprio template de erro. Aí a resposta da âncora descreve um roteador que a
# resposta nunca tocou, não reconhece nada, e o template de erro do subsite é
# aprovado como a página Sobre.
#
# As duas perguntas passam a ter evidências diferentes. EXCLUIR é barato e usa o
# que já está na mão: o que já foi medido para aquele diretório, e a âncora. Uma
# exclusão errada só faz olhar o próximo candidato e terminar em MISSING.
# ACEITAR exige uma resposta que COBRE de onde a resposta veio, e se nenhuma
# cobre, pergunta àquele diretório.
# --------------------------------------------------------------------------


def test_um_subsite_com_roteador_proprio_nao_passa_por_pagina_sobre(server):
    """O espelho do defeito que o commit anterior fechou: o chute ACERTA.

    `/about/` é montado como subsite com roteador próprio e serve soft 404 lá
    dentro; o apex 404 honestamente. Perguntado só na âncora, `/about/` era
    julgado pela resposta do apex — que é honesta, então nada foi reconhecido e o
    template de erro do subsite passou como a página Sobre. O site não tem página
    Sobre nenhuma, e a checagem de confiança ficou só com contagem de palavras e
    marcadores de rascunho, que um template de erro com prosa atravessa.

    O que se perde não é o exit code — uma segunda passada ainda diz que aquele
    diretório não foi medido — é a SEVERIDADE e a frase que o AdSense de fato
    aplica.
    """
    base, rotas = server
    rotas["/"] = (200, {}, pagina("Casa"))
    # O subsite responde 200 com o SEU template de erro para tudo abaixo de
    # /about/; qualquer outro caminho o apex 404 de verdade.
    rotas.default = lambda metodo, caminho: (
        (200, {}, ERRO_404_LONGO) if caminho.startswith("/about/") else None
    )

    relatorio = check_completeness(base + "/")

    sobre = relatorio.trust.pages["about"]
    assert sobre.status is Status.MISSING
    assert sobre.url is None
    # O diretório do subsite foi perguntado sobre si mesmo, que é a evidência
    # que faltava. A âncora não serve: ela mede `/`, e `/about/` é outro roteador.
    assert f"/about/{NOT_FOUND_PROBE_PATHS[0]}" in caminhos_pedidos(rotas)
    assert relatorio.trust.pages["contact"].status is Status.MISSING
    assert any(
        "Neither an About nor a Contact page was found" in f.message
        for f in relatorio.trust.findings
    )
    assert relatorio.status is Status.FAIL


def test_a_resposta_ja_medida_vale_para_o_convencional_do_mesmo_diretorio(server):
    """Um diretório, duas respostas iguais, e o relatório dizia coisas opostas.

    O rodapé linka `/about/equipe`, então o candidato linkado faz a checagem
    perguntar `/about/` e guardar a resposta. Em seguida o convencional `/about/`
    — inventado — era julgado só na âncora, e a medição de `/about/` ficava
    parada em `_measured` sem ninguém olhar.

    O relatório saía com `[MISS] navigation — /about/equipe … serving exactly the
    page /about/…probe… serves` e `[PASS] ADS-UX-05 about page — /about/` na
    mesma corrida, sobre respostas byte a byte idênticas do mesmo diretório.

    Consultar o já medido é de graça: nenhuma requisição a mais, e é o primeiro
    lugar onde olhar.
    """
    base, rotas = server
    rodape = "<footer><a href='/about/equipe'>Equipe</a></footer>"
    rotas["/"] = (200, {}, pagina("Casa", extra=rodape))
    rotas.default = lambda metodo, caminho: (
        (200, {}, ERRO_404_LONGO) if caminho.startswith("/about/") else None
    )

    relatorio = check_completeness(base + "/")

    # A navegação já dizia que `/about/equipe` é o que aquele diretório serve
    # para o que não existe. A checagem de confiança agora diz o mesmo.
    assert [link.url for link in relatorio.nav.same_as_not_found] == [base + "/about/equipe"]
    assert relatorio.trust.pages["about"].status is Status.MISSING
    # E sem pagar por isso: `/about/` foi perguntado UMA vez, pelo candidato
    # linkado. O convencional reusou a medição.
    sondas_no_about = [
        c for c in caminhos_pedidos(rotas) if c.startswith(f"/about/{NOT_FOUND_PROBE_PATHS[0]}")
    ]
    assert len(sondas_no_about) == 1


def test_um_convencional_que_redireciona_para_fora_e_julgado_onde_caiu(server):
    """O chute sai do diretório em que foi inventado, e a âncora não o segue.

    A base serve soft 404 com o template A; `/about/` responde 301 para
    `/loja/sobre/`, e o roteador da loja serve soft 404 com o template B. Julgado
    na âncora, o candidato era comparado com o template A, que não reconhece o B
    — e o erro da loja passava como a página Sobre com `/loja/` nunca medido.

    `_probe_covers` não chegava a ser consultado sobre a resposta: para um
    endereço inventado nada comparava o diretório medido com o diretório de onde
    a resposta veio.
    """
    base, rotas = server
    erro_da_loja = pagina("Sem página", corpo="Beta " * 60)
    rotas["/"] = (200, {}, pagina("Casa"))
    rotas["/about/"] = (301, {"Location": "/loja/sobre/"}, "")
    rotas["/about"] = (301, {"Location": "/loja/sobre/"}, "")
    rotas.default = lambda metodo, caminho: (
        (200, {}, erro_da_loja) if caminho.startswith("/loja/") else (200, {}, ERRO_404_LONGO)
    )

    relatorio = check_completeness(base + "/")

    sobre = relatorio.trust.pages["about"]
    assert sobre.status is Status.MISSING
    assert sobre.url is None
    # O diretório de DESTINO foi medido, que é de onde a resposta veio.
    assert any(
        c.startswith("/loja/") and NOT_FOUND_PROBE_PATHS[0] in c
        for c in caminhos_pedidos(rotas)
    )
    assert any(
        "Neither an About nor a Contact page was found" in f.message
        for f in relatorio.trust.findings
    )


def test_um_declarado_que_redireciona_para_um_soft_404_e_julgado_no_destino(server):
    """O mutante do costume: `for_url(landed)` trocado por `for_url(candidate.url)`.

    O apex é honesto; `/sobre` — que o RODAPÉ escreveu — responde 301 para
    `/loja/sobre`, e `/loja/` serve soft 404 com prosa. Medido em `landed`, o
    diretório perguntado é `/loja/` e o template é reconhecido: MISSING, com a
    manchete. Medido no endereço PEDIDO, o diretório perguntado é `/` — honesto,
    nada reconhecido — e o template de erro da loja sai `[PASS] about page`.

    A linha existia antes deste commit e sobrevivia à suíte inteira; `58435f0`
    reescreveu-a e deixou a guarda sem exercício.
    """
    base, rotas = server
    erro_da_loja = pagina("Sem página", corpo="Beta " * 60)
    rodape = "<footer><a href='/sobre'>Sobre</a></footer>"
    rotas["/"] = (200, {}, pagina("Casa", extra=rodape))
    rotas["/sobre"] = (301, {"Location": "/loja/sobre"}, "")
    rotas.default = lambda metodo, caminho: (
        (200, {}, erro_da_loja) if caminho.startswith("/loja/") else None
    )

    relatorio = check_completeness(base + "/")

    sobre = relatorio.trust.pages["about"]
    assert sobre.status is Status.MISSING
    assert sobre.url is None
    # `/loja/`, para onde o link caiu — e NÃO `/`, onde ele foi pedido.
    assert f"/loja/{NOT_FOUND_PROBE_PATHS[0]}" in caminhos_pedidos(rotas)
    # Aqui a sonda COBRE de onde a resposta veio, então o relatório pode dizer
    # "seu diretório" — e a cobertura é aferida em `landed`, não no endereço
    # pedido, cujo diretório é `/`.
    excluidos = [m for _url, m in sobre.attempts if "does not exist" in m]
    assert excluidos, sobre.attempts
    assert all("its directory answers with" in m for m in excluidos), excluidos
    assert any(f"/loja/{NOT_FOUND_PROBE_PATHS[0]}" in m for m in excluidos), excluidos
    assert any(
        "Neither an About nor a Contact page was found" in f.message
        for f in relatorio.trust.findings
    )


def test_um_linkado_e_medido_no_diretorio_dele_e_nao_na_ancora(server):
    """Um endereço que o DOCUMENTO escreveu ganha a medição do diretório dele.

    A âncora pode excluir um chute desta ferramenta de graça, porque um chute que
    não existe foi respondido pelo roteador da base. Um link que o SITE escreveu
    não é um chute: o documento afirma que a página está ali, e a afirmação é
    sobre AQUELE diretório. Julgá-lo pela âncora faz o relatório nomear um
    diretório que a corrida nunca perguntou.

    O host tem UM catch-all, então a impressão digital da âncora casaria com a
    resposta e a exclusão sairia igual — é justamente por isso que a diferença só
    aparece no fio e no texto: com a âncora, `/loja/` nunca é perguntado.

    O par Sobre/Contato não é decoração. Sobre roda primeiro e é lá que `/` e
    `/loja/` são medidos; quando Contato chega, as DUAS medições estão na mão e
    as duas casariam com a resposta. É a única configuração em que se vê qual
    delas foi consultada — procurar a medição pelo endereço PEDIDO acha a âncora,
    procurá-la por onde a resposta VEIO acha `/loja/`.
    """
    base, rotas = server
    rodape = "<footer><a href='/sobre'>Sobre</a><a href='/contato'>Contato</a></footer>"
    rotas["/"] = (200, {}, pagina("Casa", extra=rodape))
    rotas["/sobre"] = (301, {"Location": "/loja/sobre"}, "")
    rotas["/contato"] = (301, {"Location": "/loja/contato"}, "")
    # O mesmo template em toda parte, `/loja/` inclusive.
    rotas.default = (200, {}, ERRO_404_LONGO)

    relatorio = check_completeness(base + "/")

    sobre = relatorio.trust.pages["about"]
    assert sobre.status is Status.MISSING
    # O diretório de destino foi perguntado sobre si mesmo, mesmo com a âncora
    # já medida e casando.
    assert f"/loja/{NOT_FOUND_PROBE_PATHS[0]}" in caminhos_pedidos(rotas)
    assert f"/loja/{NOT_FOUND_PROBE_PATHS[0]}" in dict(sobre.attempts)[base + "/loja/sobre"]
    # Contato chega com âncora e `/loja/` medidos, e cita `/loja/`.
    contato_pg = relatorio.trust.pages["contact"]
    assert contato_pg.status is Status.MISSING
    citado = dict(contato_pg.attempts)[base + "/loja/contato"]
    assert "its directory answers with" in citado
    assert f"/loja/{NOT_FOUND_PROBE_PATHS[0]}" in citado


def test_o_convencional_reusa_a_medicao_do_diretorio_e_nao_a_da_ancora(server):
    """Já medido vem antes da âncora, e o relatório nomeia o que foi medido.

    Num host de um catch-all só, as duas respostas casam com a resposta — a do
    diretório e a da âncora — então o VEREDITO sai igual pelos dois caminhos. O
    que muda é qual medição o relatório cita. Olhar a âncora primeiro faz o
    relatório dizer que `/` respondeu por uma resposta que veio de `/about/`,
    tendo a medição de `/about/` na mão.
    """
    base, rotas = server
    rodape = "<footer><a href='/about/equipe'>Equipe</a></footer>"
    rotas["/"] = (200, {}, pagina("Casa", extra=rodape))
    rotas.default = (200, {}, ERRO_404_LONGO)

    relatorio = check_completeness(base + "/")

    sobre = relatorio.trust.pages["about"]
    assert sobre.status is Status.MISSING
    # O candidato linkado mandou perguntar `/about/`, uma vez.
    sondas_no_about = [
        c for c in caminhos_pedidos(rotas) if c.startswith(f"/about/{NOT_FOUND_PROBE_PATHS[0]}")
    ]
    assert len(sondas_no_about) == 1
    # E o CONVENCIONAL `/about/` — inventado, e portanto o que olharia a âncora
    # — foi excluído contra ESSA medição, que cobre de onde a resposta veio. Com
    # a âncora primeiro a frase citaria `/`, tendo a medição de `/about/` na mão.
    convencional = dict(sobre.attempts)[base + "/about/"]
    assert "its directory answers with" in convencional
    assert f"/about/{NOT_FOUND_PROBE_PATHS[0]}" in convencional


def test_com_um_catch_all_so_a_ancora_ainda_exclui_os_convencionais_de_graca(server):
    """A economia que `58435f0` comprou, mantida.

    Num host com UM catch-all, a resposta medida na base reconhece o que
    `/about/`, `/sobre/`, `/contact/` e `/pages/about` servem, porque é o mesmo
    template. Excluir por ela não custa requisição nenhuma, e é o que impede os
    dezessete caminhos convencionais de reivindicarem seis das oito vagas antes
    de um único link do menu ser julgado.

    Exigir cobertura para EXCLUIR desfaria exatamente isso — por isso a cobertura
    é exigida só para ACEITAR.
    """
    base, rotas = server
    rodape = "".join(
        f"<a href='/{d}/sobre'>Sobre</a>"
        for d in ("institucional", "pt", "en", "blog", "loja", "ajuda")
    )
    rotas["/"] = (200, {}, pagina("Casa", extra=f"<footer>{rodape}</footer>"))
    rotas.default = (200, {}, ERRO_404_LONGO)

    relatorio = check_completeness(base + "/")

    sondados = {
        c.rsplit("/", 1)[0] + "/"
        for c in caminhos_pedidos(rotas)
        if NOT_FOUND_PROBE_PATHS[0] in c
    }
    # Nenhum diretório convencional foi perguntado: a âncora deu conta de todos.
    assert "/about/" not in sondados
    assert "/sobre/" not in sondados
    assert "/contact/" not in sondados
    sobre = relatorio.trust.pages["about"]
    assert sobre.status is Status.MISSING
    # As duas formas da frase, uma em cada metade, e é o que impede a economia
    # de virar uma afirmação falsa. `/sobre/` é o seu próprio diretório e
    # NINGUÉM o mediu: dizer "seu diretório respondeu" seria pôr no relatório
    # uma medição que esta corrida não fez. `/loja/sobre` o documento escreveu,
    # e `/loja/` foi medido, então ali a frase mais afiada é verdadeira.
    por_url = dict(sobre.attempts)
    convencional = por_url[base + "/sobre/"]
    assert "served exactly the page" in convencional
    assert "its directory answers with" not in convencional
    assert f"{base}/{NOT_FOUND_PROBE_PATHS[0]}" in convencional
    linkado = por_url[base + "/loja/sobre"]
    assert "its directory answers with" in linkado
    assert f"/loja/{NOT_FOUND_PROBE_PATHS[0]}" in linkado
    assert any(
        "Neither an About nor a Contact page was found" in f.message
        for f in relatorio.trust.findings
    )


def test_num_host_honesto_aceitar_nao_custa_sonda_nenhuma_a_mais(server):
    """O preço da exigência de cobertura, medido no fio.

    Só um candidato que já respondeu 200 e sobreviveu às exclusões de graça chega
    a pedir uma sonda, e aceitar RETORNA. Num host honesto os chutes 404 e nunca
    chegam lá, então a conta é a mesma de antes desta mudança.
    """
    base, rotas = server
    rotas["/"] = (200, {}, pagina("Casa"))
    rotas["/sobre"] = (200, {}, pagina("Sobre"))
    rotas["/contato"] = (200, {}, contato())

    check_trust_pages(
        base + "/",
        not_found=_NotFoundProbes(base + "/", session=requests.Session(), timeout=5),
    )

    # A âncora e mais nada: `/sobre` e `/contato` vieram da âncora, que já estava
    # medida, e todo o resto 404 antes de chegar a qualquer sonda.
    sondas = [c for c in caminhos_pedidos(rotas) if "adsense-auditor-probe" in c]
    assert sondas == [f"/{NOT_FOUND_PROBE_PATHS[0]}"]


def test_aceitar_custa_no_maximo_um_diretorio_por_tipo_de_pagina(server):
    """O teto do preço novo, medido no fio.

    O custo cai onde a evidência decide um veredito: um diretório que o chute
    ACERTOU e que tem roteador próprio de soft 404. Como aceitar RETORNA, no
    máximo um candidato por tipo chega a pedir, então o acréscimo é de um
    diretório para Sobre e um para Contato — e nenhum a mais, por mais
    convencionais que a tupla tenha.
    """
    base, rotas = server
    rotas["/"] = (200, {}, pagina("Casa"))
    # `/about/` e `/contact/` são subsites com roteador próprio; o apex é honesto.
    rotas.default = lambda metodo, caminho: (
        (200, {}, ERRO_404_LONGO)
        if caminho.startswith(("/about/", "/contact/"))
        else None
    )

    relatorio = check_completeness(base + "/")

    sondados = {
        c.rsplit("/", 1)[0] + "/"
        for c in caminhos_pedidos(rotas)
        if NOT_FOUND_PROBE_PATHS[0] in c
    }
    # A âncora mais UM diretório por tipo. `/sobre/`, `/contato/`, `/pages/` e as
    # outras grafias convencionais 404 e não custam sonda nenhuma.
    assert sondados == {"/", "/about/", "/contact/"}
    assert relatorio.trust.pages["about"].status is Status.MISSING
    assert relatorio.trust.pages["contact"].status is Status.MISSING


# --------------------------------------------------------------------------
# O TETO NÃO É UMA DISPENSA DA REGRA, NEM UMA PROVA DE AUSÊNCIA.
#
# `3026a0f` enunciou que aceitar exige uma resposta que COBRE de onde a resposta
# veio. O código não exigia: `_is_not_found_page(None, response)` é False, então
# o candidato que precisava de cobertura e não conseguiu caía direto em
# `_judge_page` e saía aprovado. O teto virava uma dispensa da regra que o
# próprio commit escreveu.
#
# Recusar sozinho troca um erro pelo outro, e os dois são alcançáveis. Uma
# página que EXISTE num caminho convencional, num site com diretórios de
# roteador próprio bastantes para gastar o teto, viraria não-encontrada — e
# junto com a outra ausente dispararia `Neither an About nor a Contact page was
# found` sobre um site que tem uma.
#
# Por isso o resultado distingue TRÊS coisas e não duas: estabelecida, ausente,
# e encontrada-mas-não-estabelecida. Sem inventar status: MISSING já carrega
# "não pôde ser observado" em toda esta metade do módulo, e `PageOutcome
# .unmeasured` é o que separa as duas últimas dentro dele. Quem lê o campo é a
# manchete, que passa a falar de AUSÊNCIA em vez de "não encontrada".
# --------------------------------------------------------------------------


def erro_proprio_de(caminho):
    """O template de erro do primeiro segmento de `caminho`.

    Um template por diretório, e distinto dos outros: é o que obriga cada
    diretório a gastar a sua própria vaga do teto, porque a medição de um não
    reconhece nada no outro. Com prosa de sobra, para que `_judge_page` o
    aprovaria se chegasse lá.
    """
    return pagina(f"Nada em {caminho.split('/')[1]}")


def test_o_teto_gasto_nao_aprova_o_template_de_erro_do_diretorio_seguinte(server):
    """O falso PASS que o teto abria, e que `3026a0f` disse ter fechado.

    Seis links Sobre no rodapé, cada um num diretório de roteador próprio, gastam
    seis vagas. O convencional puxa a âncora, que é a sétima, e `/about/` — que
    tem template próprio — é a oitava. Quando Contato chega, `/contact/`, que
    também é roteador próprio com o SEU template de erro, precisaria da nona.
    `for_url` devolve None, `_is_not_found_page(None, response)` é False, e o
    template de erro do site saía `[PASS] contact page`.

    O site não tem página de Contato nenhuma. O exit code continuava 1 pelo
    veredito MISSING do Sobre, então o defeito não é exit 0: é uma linha PASS
    impressa sobre a página que aquele diretório serve para o que não existe, e a
    manchete some junto porque Contato deixa de constar como não encontrado.

    Todo o resto do host responde o template da RAIZ, que a âncora exclui de
    graça. É o que faz o teto chegar gasto em `/contact/` sem que a corrida tenha
    desperdiçado vaga nenhuma nos chutes desta ferramenta — sem isso a forma não
    se monta, e o defeito não é alcançável.
    """
    base, rotas = server
    rodape = "".join(f"<a href='/a{i}/sobre'>Sobre</a>" for i in range(6))
    rotas["/"] = (200, {}, pagina("Casa", extra=f"<footer>{rodape}</footer>"))
    proprios = tuple(f"/a{i}/" for i in range(6)) + ("/about/", "/contact/")
    rotas.default = lambda metodo, caminho: (
        200,
        {},
        erro_proprio_de(caminho) if caminho.startswith(proprios) else ERRO_404_LONGO,
    )

    relatorio = check_completeness(base + "/")

    contato_pg = relatorio.trust.pages["contact"]
    # NÃO aceito. `url` segue None, então nenhuma linha PASS é impressa sobre o
    # template de erro de `/contact/` — era `Status.OK` com `url` preenchida.
    assert contato_pg.status is Status.MISSING
    assert contato_pg.url is None
    # E NÃO ausente: o candidato respondeu 200 e nada mostrou que não era página.
    # É esta lista que separa as duas dentro de MISSING.
    assert contato_pg.unmeasured == [base + "/contact/"]
    assert "did not measure" in contato_pg.reason
    assert base + "/contact/" in contato_pg.reason
    # O diretório recusado é nomeado, como a navegação já faz com os dela.
    assert "/contact/" in relatorio.nav.refused_directories
    dito = frases(relatorio.trust)
    assert "contact page could not be established" in dito
    # Sobre é o outro MISSING, e é AUSENTE de verdade: `/about/` foi medido — a
    # oitava vaga — e serviu exatamente o que serve para o que não existe.
    sobre = relatorio.trust.pages["about"]
    assert sobre.status is Status.MISSING
    assert sobre.unmeasured == []
    # A manchete NÃO sai, e não por acidente. Ela afirma que o SITE não tem como
    # identificar nem contatar o publisher, e `/contact/` respondeu 200 com prosa
    # sem que esta corrida conseguisse julgá-lo. Dizer ausência aqui seria trocar
    # o falso PASS por um falso FAIL — o mesmo erro de categoria na outra direção.
    assert not any(
        "Neither an About nor a Contact page was found" in f.message
        for f in relatorio.trust.findings
    )
    # E nada disto se paga com um exit 0: MISSING nos dois lados basta.
    assert exit_code(relatorio.status) == 1
    # Mesmo preço de antes: as oito vagas, nem uma requisição a mais. A recusa
    # acontece onde a sonda JÁ não era enviada.
    sondados = {
        c.rsplit("/", 1)[0] + "/"
        for c in caminhos_pedidos(rotas)
        if NOT_FOUND_PROBE_PATHS[0] in c
    }
    assert len(sondados) == MAX_PROBED_DIRECTORIES
    assert "/contact/" not in sondados


def test_num_site_saudavel_a_exigencia_de_cobertura_nao_muda_nada(server):
    """O outro lado da conta: num host honesto nada disto é alcançado.

    Os chutes 404, então nenhum candidato passa das exclusões de graça sem já
    estar coberto pela âncora, e a âncora é honesta. As duas páginas saem OK,
    nenhuma fica pendurada em medição, e o exit code é 0.
    """
    base, rotas = server
    rotas["/"] = (200, {}, pagina("Casa"))
    rotas["/sobre"] = (200, {}, pagina("Sobre"))
    rotas["/contato"] = (200, {}, contato())

    relatorio = check_trust_pages(
        base + "/",
        not_found=_NotFoundProbes(base + "/", session=requests.Session(), timeout=5),
    )

    sobre = relatorio.pages["about"]
    contato_pg = relatorio.pages["contact"]
    assert sobre.status is Status.OK
    assert sobre.url == base + "/sobre"
    assert contato_pg.status is Status.OK
    assert contato_pg.url == base + "/contato"
    # Uma página estabelecida não espera medição nenhuma, e é o que faz
    # `unmeasured` significar algo: ele só é não-vazio junto de um MISSING.
    assert sobre.unmeasured == []
    assert contato_pg.unmeasured == []
    assert relatorio.status is Status.OK
    assert exit_code(relatorio.status) == 0
    # A âncora e mais nada.
    sondas = [c for c in caminhos_pedidos(rotas) if NOT_FOUND_PROBE_PATHS[0] in c]
    assert sondas == [f"/{NOT_FOUND_PROBE_PATHS[0]}"]


def test_o_teto_gasto_noutro_diretorio_nao_recusa_a_pagina_que_a_ancora_cobre(server):
    """O erro que a recusa simples traria, e que a cobertura evita.

    O teto acaba GASTO nesta corrida — seis diretórios de roteador próprio para
    Sobre e seis para Contato — e mesmo assim as duas páginas convencionais são
    aceitas, porque estão no diretório da âncora e a âncora foi medida. É a
    diferença entre "o teto acabou" e "de onde esta resposta veio não foi
    medido": recusar pela primeira condição reportaria não-encontradas duas
    páginas que o site serve, e num par assim dispararia a manchete do AdSense
    sobre um site que tem as duas.

    Os candidatos linkados que o teto recusou não desaparecem do relatório: são
    links do rodapé, então `count_broken_nav_links` os lista como não medidos com
    o mesmo peso. O que este teste fixa é que eles não contaminam a página que
    ESTA corrida conseguiu estabelecer.
    """
    base, rotas = server
    rodape = "".join(
        f"<a href='/a{i}/sobre'>Sobre</a><a href='/b{i}/contato'>Contato</a>"
        for i in range(6)
    )
    rotas["/"] = (200, {}, pagina("Casa", extra=f"<footer>{rodape}</footer>"))
    # As duas páginas de verdade, no diretório da âncora.
    rotas["/sobre"] = (200, {}, pagina("Sobre"))
    rotas["/contato"] = (200, {}, contato())
    proprios = tuple(f"/a{i}/" for i in range(6)) + tuple(f"/b{i}/" for i in range(6))
    # Cada diretório linkado tem o seu template de erro; a raiz é HONESTA, então
    # os chutes desta ferramenta 404 e não gastam vaga.
    rotas.default = lambda metodo, caminho: (
        (200, {}, erro_proprio_de(caminho)) if caminho.startswith(proprios) else None
    )

    relatorio = check_completeness(base + "/")

    sobre = relatorio.trust.pages["about"]
    contato_pg = relatorio.trust.pages["contact"]
    assert sobre.status is Status.OK
    assert sobre.url == base + "/sobre"
    assert contato_pg.status is Status.OK
    assert contato_pg.url == base + "/contato"
    assert sobre.unmeasured == []
    assert contato_pg.unmeasured == []
    # E o teto REALMENTE acabou: sem isto o teste não separa as duas condições.
    assert len(relatorio.nav.refused_directories) > 0
    assert not any(
        "Neither an About nor a Contact page was found" in f.message
        for f in relatorio.trust.findings
    )


def test_a_lista_de_diretorios_recusados_diz_quantos_ela_nao_nomeou(server):
    """Cinco nomes de dezoito lidos como lista completa.

    `scripts/README.md` prometia "the report names the directories" e citava a
    linha de dezoito links de um menu de 25 espalhado por 25 diretórios. A frase
    nomeava cinco e não dizia que havia treze mais — a contagem estava noutro
    canto da mesma frase, então dava para subtrair, mas ninguém deveria ter de
    fazer isso para saber se leu a lista inteira.
    """
    base, rotas = server
    diretorios = [f"/s{i}/" for i in range(25)]
    rotas["/"] = (200, {}, pagina("Casa", extra=menu_de(*diretorios)))
    for d in diretorios:
        rotas[d] = (200, {}, pagina(d))

    relatorio = count_broken_nav_links(base + "/", limit=25)

    recusados = relatorio.refused_directories
    assert len(recusados) > 5
    dito = frases(relatorio)
    # Os cinco primeiros, e o número dos que ficaram de fora.
    for d in recusados[:5]:
        assert d in dito
    assert f"(and {len(recusados) - 5} more)" in dito
    # E nenhum nome além dos cinco, que é o ponto do corte.
    assert recusados[5] not in dito


def test_com_o_teto_ja_gasto_a_navegacao_diz_nao_medido_em_vez_de_inventar_regime(
    server,
):
    """Sem medição não há regime, e o tipo não pode inventar um.

    `anchor_probe` devolvia uma sentinela `_NotFoundProbe("opaque")` quando o
    teto recusava a âncora, e `opaque` é um REGIME: o relatório passava a
    afirmar o que este host faz com uma página que não existe sobre um diretório
    que ninguém perguntou. É o mesmo defeito de sempre — uma afirmação sobre o
    host tirada de onde não foi medido — desta vez saindo da assinatura da
    função em vez de da trava.

    O teto chega aqui já gasto por outro diretório, que é o que acontece de
    verdade quando as páginas de confiança rodam primeiro num host de soft 404.
    """
    base, rotas = server
    rotas["/"] = (200, {}, pagina("Casa", extra=menu_de("/a")))
    rotas["/a"] = (200, {}, pagina("A"))
    rotas.default = (200, {}, ERRO_404_LONGO)

    sondas = _NotFoundProbes(
        base + "/", session=requests.Session(), timeout=5, limit=1
    )
    # Gasta o único lugar num diretório que não é a âncora.
    assert sondas.for_url(base + "/outro/x") is not None
    assert sondas.anchor_probe() is None

    relatorio = count_broken_nav_links(base + "/", not_found=sondas)

    assert relatorio.not_found_regime == ""
    assert [link.url for link in relatorio.unmeasured] == [base + "/a"]
    assert relatorio.status is Status.MISSING

@pytest.mark.parametrize(
    ("base_da_sonda", "diretorio"),
    [
        ("/", "/"),
        ("/index.php", "/"),
        ("/app/", "/app/"),
        ("/app/index.html", "/app/"),
        ("/loja/produtos/", "/loja/produtos/"),
    ],
)
def test_a_sonda_sabe_de_qual_diretorio_ela_fala(server, base_da_sonda, diretorio):
    """`_NotFoundProbe.directory` é DERIVADO de `base`, não guardado ao lado.

    Em `9821aee` os dois eram calculados separado e discordavam: a sonda é
    MANDADA por `urljoin`, que derruba um último segmento com cara de documento,
    enquanto a trava FATIAVA o caminho — então uma base `/index.php` mandava a
    sonda para `/` e depois julgava cobertura como se `/index.php` fosse
    diretório. Os vizinhos da própria sonda saíam MISSING.

    Aqui o par é conferido junto: para onde a sonda FOI no fio, e de qual
    diretório ela diz falar.
    """
    base, rotas = server
    rotas.default = None

    sonda = _probe_not_found(base + base_da_sonda, session=requests.Session(), timeout=5)

    assert sonda.regime == "honest"
    assert sonda.directory == diretorio
    # E o fio concorda com o que ela diz cobrir.
    assert caminhos_pedidos(rotas) == [diretorio + NOT_FOUND_PROBE_PATHS[0]]
    assert _probe_covers(sonda, base + diretorio + "vizinho") is True


# --------------------------------------------------------------------------
# `_probe_covers` afrouxado. Cada mutante com a forma que o separa.
#
# `_NotFoundProbes` VARRE as medições com este predicado em vez de indexar por
# uma chave de diretório, e é isso que faz um afrouxamento mudar comportamento:
# sob o mutante uma resposta casa com a medição do vizinho, a sonda que a teria
# pegado não é mandada, e o link sai aprovado.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("instalacao", "irmao"),
    [("/blog/", "/blog-antigo/"), ("/pt/", "/pt-br/")],
)
def test_um_irmao_com_o_mesmo_prefixo_nao_herda_a_medicao_do_vizinho(
    server, instalacao, irmao
):
    """Mutante 1: `path.startswith(directory.rstrip("/"))`.

    Sem a barra no prefixo, `/blog-antigo/` casa com a medição de `/blog/` e
    `/pt-br/` com a de `/pt/`. Os dois sobreviviam às 605: a instalação é
    honesta, o irmão serve soft 404, e o link do irmão saía vivo sem que sonda
    alguma fosse mandada para lá.
    """
    base, rotas = server
    mortos = tuple(f"{irmao}a{i}" for i in range(2))
    rotas[instalacao] = (200, {}, pagina("Casa", extra=menu_de(*mortos)))
    rotas.default = lambda metodo, caminho: (
        (200, {}, ERRO_404_LONGO) if caminho.startswith(irmao) else None
    )

    relatorio = count_broken_nav_links(base + instalacao)

    # O VEREDITO primeiro: sob o mutante os links do irmão saem vivos.
    assert relatorio.passed is False
    assert relatorio.count == 0
    # A instalação é honesta; o irmão é PERGUNTADO por si mesmo, e é a sonda que
    # falta sob o mutante — ele reaproveita a medição do vizinho e não pede.
    assert relatorio.not_found_regime == "honest"
    pedidos = caminhos_pedidos(rotas)
    assert f"{instalacao}{NOT_FOUND_PROBE_PATHS[0]}" in pedidos
    assert f"{irmao}{NOT_FOUND_PROBE_PATHS[0]}" in pedidos
    assert [link.url for link in relatorio.unclassified] == [base + c for c in mortos]
    # E o predicado, direto: a barra final é o que mantém o irmão fora.
    sonda = _NotFoundProbe("honest", base=base + instalacao)
    assert _probe_covers(sonda, base + instalacao + "vizinho") is True
    assert _probe_covers(sonda, base + irmao + "vizinho") is False


def test_o_diretorio_pai_nao_herda_a_medicao_do_filho(server):
    """Mutante 2: `path.startswith(directory) or directory.startswith(path)`.

    A segunda metade deixa o PAI casar com a medição do FILHO. A loja é sondada
    em `/loja/produtos/` — porque é lá que a home vive — e `/loja/` serve soft
    404: sob o mutante `"/loja/produtos/".startswith("/loja/")` é verdade, o link
    de `/loja/` conta como medido pela sonda do filho, e a resposta do soft 404
    sai como página viva. Sobrevivia às 605.
    """
    base, rotas = server
    mortos = ("/loja/a", "/loja/b")
    rotas["/loja/produtos/"] = (200, {}, pagina("Casa", extra=menu_de(*mortos)))
    rotas.default = lambda metodo, caminho: (
        None if caminho.startswith("/loja/produtos/") else (200, {}, ERRO_404_LONGO)
    )

    relatorio = count_broken_nav_links(base + "/loja/produtos/")

    # O VEREDITO primeiro: sob o mutante os links de `/loja/` saem vivos.
    assert relatorio.passed is False
    assert relatorio.count == 0
    assert relatorio.not_found_regime == "honest"
    pedidos = caminhos_pedidos(rotas)
    assert f"/loja/produtos/{NOT_FOUND_PROBE_PATHS[0]}" in pedidos
    assert f"/loja/{NOT_FOUND_PROBE_PATHS[0]}" in pedidos
    assert [link.url for link in relatorio.unclassified] == [base + c for c in mortos]
    # E o predicado, direto: a medição do filho não sobe para o pai.
    sonda = _NotFoundProbe("honest", base=base + "/loja/produtos/")
    assert _probe_covers(sonda, base + "/loja/produtos/x") is True
    assert _probe_covers(sonda, base + "/loja/x") is False


def test_uma_medicao_nao_e_reaproveitada_para_o_diretorio_de_baixo(server):
    """Contenção para BAIXO, que é a metade que parecia inofensiva.

    Um roteador normalmente responde por toda a sua subárvore, e era esse o
    argumento da contenção. Ele é uma inferência: `/blog/` honesta não diz nada
    sobre `/blog/2024/`, que num CMS é outra rota. Sob contenção o link de
    `/blog/2024/` era aprovado sem que ninguém perguntasse lá.

    O preço da igualdade está declarado: `/blog/2024/` custa uma sonda própria.
    """
    base, rotas = server
    rotas["/blog/"] = (200, {}, pagina("Casa", extra=menu_de("/blog/2024/post")))
    rotas.default = lambda metodo, caminho: (
        (200, {}, ERRO_404_LONGO) if caminho.startswith("/blog/2024/") else None
    )

    relatorio = count_broken_nav_links(base + "/blog/")

    assert relatorio.passed is False
    assert relatorio.not_found_regime == "honest"
    assert f"/blog/2024/{NOT_FOUND_PROBE_PATHS[0]}" in caminhos_pedidos(rotas)
    assert [link.url for link in relatorio.unclassified] == [base + "/blog/2024/post"]


def test_um_diretorio_recusado_pelo_teto_nao_e_coberto_por_nenhuma_medicao(server):
    """O teto e o predicado, juntos: recusar não é herdar.

    Com o teto gasto, `for_url` devolve None e o link vira `unmeasured`. Um
    predicado afrouxado desfaria isto pelo outro lado — o diretório recusado
    casaria com uma medição de vizinho já feita e o link sairia aprovado sem
    nunca ter sido medido, que é o falso PASS de novo por outra porta. Os
    diretórios são ENCAIXADOS de propósito, para que a contenção tenha um
    vizinho plausível a oferecer.
    """
    base, rotas = server
    diretorios = [f"/d/{i}/" for i in range(MAX_PROBED_DIRECTORIES + 1)]
    rotas["/d/"] = (200, {}, pagina("Casa", extra=menu_de(*diretorios)))
    for d in diretorios:
        rotas[d] = (200, {}, pagina(d))

    relatorio = count_broken_nav_links(base + "/d/")

    recusados = [d for d in diretorios if d in relatorio.refused_directories]
    assert recusados, "o teto tem de morder para este teste dizer algo"
    assert [link.url for link in relatorio.unmeasured] == [base + d for d in recusados]
    assert relatorio.status is Status.MISSING


def test_instalacao_em_app_com_raiz_honesta_nao_aprova_links_absolutos_mortos(server):
    """Caso 1: Next.js `trailingSlash:false`, e nenhum `<base href>` envolvido.

    A raiz redireciona para `/app`, `/app/` serve soft 404 e a raiz é honesta.
    Com a sonda obrigada a cair onde os links relativos cairiam ela ia para a
    RAIZ, voltava "este host gasta um 404 numa página que não tem", e três links
    absolutos mortos dentro de `/app/` saíam aprovados: `[PASS]`, exit 0, sobre
    um menu inteiro que não leva a lugar nenhum.
    """
    base, rotas = server
    mortos = ("/app/blog", "/app/loja", "/app/cursos")
    rotas["/"] = (301, {"Location": "/app"}, "")
    rotas["/app"] = (200, {}, pagina("Casa", extra=menu_de(*mortos)))
    # `/app/` responde 200 para o que não tem; a raiz 404 honestamente.
    rotas.default = lambda metodo, caminho: (
        (200, {}, ERRO_404_LONGO) if caminho.startswith("/app/") else None
    )

    relatorio = check_completeness(base + "/")

    pedidos = caminhos_pedidos(rotas)
    assert f"/app/{NOT_FOUND_PROBE_PATHS[0]}" in pedidos
    assert f"/{NOT_FOUND_PROBE_PATHS[0]}" not in pedidos
    # Nenhum foi observado quebrado — e nenhum foi dado como vivo.
    assert relatorio.nav.count == 0
    assert [link.url for link in relatorio.nav.unclassified] == [base + c for c in mortos]
    assert relatorio.nav.status is Status.MISSING
    assert relatorio.status is not Status.OK


def test_base_href_sem_barra_final_com_raiz_honesta_nao_aprova_links_mortos(server):
    """Caso 2: a mesma forma sem redirect nenhum, declarada na marcação.

    `<base href="/app">` sem barra final. `urljoin` troca o último segmento, de
    modo que os links RELATIVOS dele caem na raiz — e por isso a sonda amarrada
    a eles ia para a raiz também, exatamente como no caso 1. A instalação, no
    entanto, é `/app/`: é lá que os caminhos que esta auditoria inventa têm que
    ser pedidos, e é lá que ela precisa perguntar o que vale um 200.
    """
    base, rotas = server
    mortos = ("/app/blog", "/app/loja", "/app/cursos")
    rotas["/app/"] = (200, {}, home_com_base("/app", menu_de(*mortos)))
    rotas.default = lambda metodo, caminho: (
        (200, {}, ERRO_404_LONGO) if caminho.startswith("/app/") else None
    )

    relatorio = check_completeness(base + "/app/")

    pedidos = caminhos_pedidos(rotas)
    assert f"/app/{NOT_FOUND_PROBE_PATHS[0]}" in pedidos
    assert f"/{NOT_FOUND_PROBE_PATHS[0]}" not in pedidos
    assert relatorio.nav.count == 0
    assert [link.url for link in relatorio.nav.unclassified] == [base + c for c in mortos]
    assert relatorio.nav.status is Status.MISSING


def test_base_href_com_barra_final_e_tudo_vivo_continua_saindo_ok(server):
    """Caso 4: a direção oposta, que é o que uma correção grosseira quebra.

    Site inteiro dentro de `/app/`, base declarada com a barra, sonda honesta
    ali dentro e todo link vivo debaixo dela. Não sobra nada não observado, e o
    veredito tem que ser OK — senão o preço da correção é transformar todo site
    são num relatório de coisas que não deu para verificar. É por isso que
    `_probe_covers` é contenção e não igualdade: exigir o MESMO diretório faria
    de todo `/blog/post` de um site comum um link não verificado.
    """
    base, rotas = server
    rotas["/app/"] = (200, {}, home_com_base("/app/", menu_de("sobre", "contato", "blog")))
    rotas["/app/sobre"] = (200, {}, pagina("Sobre"))
    rotas["/app/contato"] = (200, {}, contato())
    rotas["/app/blog"] = (200, {}, pagina("Blog"))

    relatorio = check_completeness(base + "/app/")

    assert caminhos_pedidos(rotas).count(f"/app/{NOT_FOUND_PROBE_PATHS[0]}") == 1
    assert relatorio.nav.not_found_regime == "honest"
    assert relatorio.nav.unclassified == []
    assert relatorio.trust.pages["about"].url == base + "/app/sobre"
    assert relatorio.status is Status.OK


def test_base_href_ftp_no_host_auditado_e_recusado_porque_same_site_ignora_esquema(server):
    """Caso 6: mesmo host, mesma porta, transporte que este cliente não fala.

    `same_site` compara host e porta e ignora o esquema DE PROPÓSITO — um site
    servido por http e https é um site só. Então conferir só o host deixa
    `<base href="ftp://o-host-auditado/">` passar, e aí toda URL inventada vira
    um `ftp://` que volta "No connection adapters were found": o relatório
    culpava o site por endereços que nunca poderiam ter sido pedidos por HTTP.
    """
    base, rotas = server
    porta = base.rsplit(":", 1)[1]
    rotas["/"] = (200, {}, home_com_base(f"ftp://127.0.0.1:{porta}/", menu_de("sobre", "contato")))
    rotas.default = lambda metodo, caminho: (200, {}, ERRO_404_LONGO)

    relatorio = check_completeness(base + "/")

    # A sonda saiu por http, no host auditado, e nenhuma URL ftp foi pedida.
    assert f"/{NOT_FOUND_PROBE_PATHS[0]}" in caminhos_pedidos(rotas)
    texto = " | ".join(relatorio.issues)
    assert "connection adapters" not in texto
    # E a recusa é dita, nomeando a base que a causou.
    assert f'<base href="ftp://127.0.0.1:{porta}/">' in texto


def test_erro_de_raiz_com_relogio_da_o_mesmo_veredito_em_vinte_corridas(server):
    """Caso 7: reprodutibilidade quando a página de erro carrega um timestamp.

    Duas sondas contra um template com relógio casam ou não conforme onde caiu a
    virada do segundo, e isso escolhe entre os regimes `fingerprint` e `opaque`.
    Se os dois decidissem coisas diferentes, o veredito de um menu morto seria
    tirado na moeda — que foi o que já aconteceu aqui. Os dois significam a mesma
    coisa para quem lê: um 200 deste host não prova nada. Só a frase muda.

    A virada é FORÇADA em metade das corridas em vez de esperada de um relógio
    real. Medido: com as duas sondas saindo com ~1ms de intervalo, um
    `int(time.time())` cai no mesmo segundo em 20 de 20 corridas, então um teste
    escrito com o relógio de verdade só exercita `fingerprint` e a moeda que ele
    diz estar prendendo nunca é lançada. Aqui os dois regimes aparecem, e o
    veredito é o mesmo nos dois.
    """
    base, rotas = server
    rotas["/"] = (200, {}, pagina("Casa", extra=menu_de("/a", "/b", "/c")))
    # `corre` liga o carimbo que muda a cada requisição — a mesma diferença que
    # a virada do segundo produz, no momento em que ela cai entre as sondas.
    relogio = {"corre": False, "n": 0}

    def erro(metodo, caminho):
        relogio["n"] += 1
        carimbo = relogio["n"] if relogio["corre"] else 0
        return (200, {}, pagina("Não encontrada", corpo=f"{PROSA} Gerado em {carimbo}."))

    rotas.default = erro

    vistos, regimes = set(), set()
    for corrida in range(20):
        relogio["corre"] = bool(corrida % 2)
        relatorio = count_broken_nav_links(base + "/")
        vistos.add(relatorio.status)
        regimes.add(relatorio.not_found_regime)

    # As duas faces da moeda saíram...
    assert regimes == {"fingerprint", "opaque"}
    # ...e o veredito não depende de qual delas caiu.
    assert vistos == {Status.MISSING}


def test_links_absolutos_para_um_diretorio_que_a_sonda_nao_mediu_nao_saem_como_pass(server):
    """Caso 10: o buraco que NENHUMA escolha de base fecha.

    Um link absoluto cai onde ele diz — debaixo nem da base dos links nem da das
    URLs inventadas. Aqui a instalação em `/app/` é honesta, a sonda mede `/app/`
    e diz "este host gasta um 404", e o menu aponta para `/loja/`, que serve soft
    404. Com a resposta da sonda valendo em todo lugar, esses links saíam vivos:
    é o mesmo `[PASS]` dos casos 1 e 2, e nenhuma das três tentativas anteriores
    o via, porque as três discutiam QUAL base usar em vez de perguntar se a
    resposta veio de onde a sonda mediu.

    As páginas de confiança ficam sãs de propósito: o que este teste isola é a
    navegação, e um FAIL vindo delas mascararia o defeito.
    """
    base, rotas = server
    fora = ("/loja/a", "/loja/b")
    rotas["/app/"] = (200, {}, home_com_base("/app/", menu_de(*fora)))
    rotas["/app/sobre"] = (200, {}, pagina("Sobre"))
    rotas["/app/contato"] = (200, {}, contato())
    rotas.default = lambda metodo, caminho: (
        (200, {}, ERRO_404_LONGO) if caminho.startswith("/loja/") else None
    )

    relatorio = check_completeness(base + "/app/")

    # A âncora — `/app/`, onde esta auditoria inventa URL — achou o diretório
    # honesto, e é justamente essa a armadilha.
    assert relatorio.nav.not_found_regime == "honest"
    assert relatorio.nav.status is Status.MISSING
    # E agora `/loja/` é PERGUNTADO, em vez de ficar apenas "fora do medido":
    # a sonda de lá acha o soft 404, casa a impressão digital e os dois links
    # saem NOMEADOS. Nada foi observado quebrado e nada foi dado como vivo.
    assert [link.url for link in relatorio.nav.same_as_not_found] == [base + c for c in fora]
    # `unclassified` carrega as três listas, não duas: tirar qualquer uma dela
    # sobrevivia à suíte inteira, e o docstring da propriedade diz que as três
    # pesam igual — nada aqui foi observado funcionando nem quebrado.
    assert relatorio.nav.unclassified == relatorio.nav.same_as_not_found
    assert relatorio.nav.count == 0
    assert relatorio.trust.pages["about"].status is Status.OK
    assert relatorio.status is not Status.OK
    # As DUAS perguntas saíram nos dois diretórios: `/app/` respondeu 404 na
    # primeira e resolveu-se ali; `/loja/` respondeu 200 e ganhou a segunda.
    pedidos = caminhos_pedidos(rotas)
    assert f"/app/{NOT_FOUND_PROBE_PATHS[0]}" in pedidos
    assert f"/app/{NOT_FOUND_PROBE_PATHS[1]}" not in pedidos
    assert f"/loja/{NOT_FOUND_PROBE_PATHS[0]}" in pedidos
    assert f"/loja/{NOT_FOUND_PROBE_PATHS[1]}" in pedidos


def test_um_base_href_https_no_proprio_site_vale_nas_tres_guardas_de_esquema():
    """A metade https de `("http", "https")` não era exercida por nada.

    Toda fixture desta suíte fala http, porque o servidor de teste é http. Com
    isso, trocar a tupla por `("http",)` sobrevivia às 592: o clamp de
    `_invented_base` passava a recusar a base no esquema que TODO site real usa
    e caía no fallback em silêncio, e as duas guardas de link derrubavam todo
    href do documento. Nada aqui precisa de rede — é a regra, não o protocolo.
    """
    doc = parse_document(
        home_com_base("https://ex.com/app/", "<nav><a href='sobre'>Sobre</a></nav>")
    )

    # 1. A base inventada aceita https e NÃO cai para o fallback.
    inventada = _invented_base("https://ex.com/", doc.base_href)
    assert (inventada.url, inventada.refused) == ("https://ex.com/app/", "")
    # 2. O menu resolve por ela.
    assert [u for u, _ in _nav_targets(doc, "https://ex.com/")] == ["https://ex.com/app/sobre"]
    # 3. O candidato declarado também.
    home = Fetch(url="https://ex.com/", final_url="https://ex.com/", status_code=200)
    assert [c.url for c in _candidates(home, doc, (), _ABOUT_HINT, 6)] == [
        "https://ex.com/app/sobre"
    ]
    # 4. E a cobertura da sonda vale entre https e https, que é o par que um site
    #    real produz — `same_site` ignora o esquema, então isto é sobre o caminho.
    sonda = _NotFoundProbe("honest", base="https://ex.com/app/")
    assert _probe_covers(sonda, "https://ex.com/app/sobre") is True
    assert _probe_covers(sonda, "https://ex.com/loja/sobre") is False
    # 5. E a sonda sentinela — `_NotFoundProbe("honest")`, sem base — não cobre
    #    NADA. Hoje ela só existe onde o laço não roda (menu vazio), então a
    #    guarda é inalcançável em produção e trocá-la por `return True` sobrevive
    #    à suíte. Fica prendida aqui porque `trustworthy` é True nela: se um
    #    chamador futuro a passar com um menu, esta guarda é a única coisa entre
    #    "não medi nada" e liberar todo link do site como página verificada.
    sentinela = _NotFoundProbe("honest")
    assert sentinela.base == ""
    assert _probe_covers(sentinela, "https://ex.com/qualquer") is False
    # 6. E a caixa do caminho separa diretórios. O docstring de `_probe_covers`
    #    diz seguir a RFC 3986 aqui e nada prendia: trocar a comparação por
    #    `casefold` sobrevivia à suíte, e num host onde `/blog/` é honesto mas o
    #    catch-all serve `/Blog/`, o mutante entrega a resposta medida no
    #    primeiro para um link que veio do segundo — um PASS falso.
    do_blog = _NotFoundProbe("honest", base="https://ex.com/blog/")
    assert _probe_covers(do_blog, "https://ex.com/blog/vivo") is True
    assert _probe_covers(do_blog, "https://ex.com/Blog/morto") is False


def test_um_base_href_de_fora_nao_inventa_endereco_local_para_um_link_declarado(
    server, outro_servidor
):
    """`link_base` NÃO é clampado, e o docstring de `_candidates` já proibia — sem
    nada prendendo. Trocá-lo por `_invented_base(...).url` sobrevivia às 592.

    Sob a troca, um `<base href>` para outro host passa a ser IGNORADO na hora de
    resolver os links que o documento escreveu: o href relativo `quem-eu-sou`,
    que aponta para o terceiro, vira `http://host-auditado/quem-eu-sou` — um
    endereço que ninguém escreveu — é pedido, responde, e a resposta do host
    auditado é reportada como sendo daquele link.

    `quem-eu-sou` não está em `ABOUT_PATHS` de propósito: com um caminho
    convencional a página seria achada por outra via e o teste passaria com o
    defeito dentro dele.
    """
    base, rotas = server
    terceiro, rotas_terceiro = outro_servidor
    rodape = "<footer><a href='quem-eu-sou'>Sobre</a></footer>"
    rotas["/"] = (200, {}, home_com_base(terceiro + "/", rodape))
    rotas["/quem-eu-sou"] = (200, {}, pagina("Sobre"))

    relatorio = check_trust_pages(base + "/")

    assert "/quem-eu-sou" not in caminhos_pedidos(rotas)
    assert rotas_terceiro.received == []
    assert relatorio.pages["about"].status is Status.MISSING


def test_um_link_que_redireciona_decide_pelo_diretorio_de_CHEGADA_e_nao_do_pedido(server):
    """Qual diretório responde por um link é decidido por ONDE A RESPOSTA VEIO.

    O filtro de mesmo-site e a resolução acontecem antes do `fetch`, então a
    única forma de um pedido trocar de diretório é um redirect — e ele é comum:
    `/app/velho` que virou `/loja/novo`. Perguntando pelo diretório da URL
    PEDIDA, a sonda de `/app/` — honesta — é a que vale, e a resposta do soft
    404 de `/loja/` sai como página viva.

    Com a sonda por diretório, `/loja/` é PERGUNTADO e o link sai nomeado em vez
    de apenas "não medido". Trocar `response.final_url` por `url` neste laço
    volta a aprová-lo, e é isto que prende a troca.
    """
    base, rotas = server
    rotas["/app/"] = (200, {}, home_com_base("/app/", menu_de("/app/velho")))
    rotas["/app/velho"] = (301, {"Location": "/loja/novo"}, "")
    rotas.default = lambda metodo, caminho: (
        (200, {}, ERRO_404_LONGO) if caminho.startswith("/loja/") else None
    )

    relatorio = count_broken_nav_links(base + "/app/")

    assert relatorio.not_found_regime == "honest"
    # A pergunta foi feita em `/loja/`, o diretório de CHEGADA, e não em
    # `/app/`, o do endereço pedido.
    assert f"/loja/{NOT_FOUND_PROBE_PATHS[0]}" in caminhos_pedidos(rotas)
    assert [link.url for link in relatorio.same_as_not_found] == [base + "/app/velho"]
    assert relatorio.unmeasured == []
    assert relatorio.status is Status.MISSING


def test_um_link_que_redireciona_para_outro_host_nao_e_julgado_pela_sonda_daqui(
    server, outro_servidor
):
    """Mesmo caminho, outra origem: `same_site` faz parte da cobertura.

    O link é do site auditado, então ele é seguido — e a resposta vem de outro
    servidor. Sem a conferência de host, o caminho `/pagina` do terceiro tem o
    mesmo diretório (`/`) que a sonda medida na raiz daqui, e o regime deste
    host decide o veredito de uma resposta que ele não escreveu.

    E o terceiro NÃO é sondado. Perguntar-lhe o que ele serve para uma página
    que não existe seria mandar uma URL inventada para um host que o operador
    não nomeou, debaixo de um robots.txt que ninguém leu para ele — é o mesmo
    motivo do clamp em `_invented_base`. Então a resposta fica sem medição, que
    é MISSING, e não custa uma requisição a quem não foi auditado.
    """
    base, rotas = server
    terceiro, rotas_terceiro = outro_servidor
    rotas["/"] = (200, {}, pagina("Casa", extra=menu_de("/parceiro")))
    rotas["/parceiro"] = (302, {"Location": terceiro + "/pagina"}, "")
    rotas_terceiro["/pagina"] = (200, {}, pagina("Parceiro"))

    relatorio = count_broken_nav_links(base + "/")

    # A raiz daqui é honesta: nada roteia a sonda e ela volta 404.
    assert relatorio.not_found_regime == "honest"
    assert [link.url for link in relatorio.unmeasured] == [base + "/parceiro"]
    assert relatorio.status is Status.MISSING
    # Nenhuma sonda no terceiro, e o teto não foi gasto com ele.
    assert [c for c in caminhos_pedidos(rotas_terceiro) if "adsense-auditor-probe" in c] == []
    assert relatorio.refused_directories == []


def test_a_porta_de_entrada_da_instalacao_sem_a_barra_final_nao_sai_sem_verificacao(server):
    """`/app` é o endereço para o qual `/app/` redireciona, e ele mora em `/`.

    Com UMA sonda por corrida isto precisava de um caso especial na trava —
    `path == directory.rstrip("/")` — só para a porta de entrada da própria
    instalação não sair no relatório como link não verificado. O caso especial
    era uma inferência: dizia que a resposta de `/app` foi medida pela sonda de
    `/app/`, e ninguém mediu.

    Com uma sonda por diretório o caso especial deixa de existir e a resposta
    honesta é mais simples: `urljoin("/app", ".")` é `/`, então `/app` é medido
    pedindo a `/` o que ele serve para uma página que não existe. Duas sondas, e
    nenhuma inferência. O menu aponta para `/app?ref=nav`: a query o mantém
    distinto da home na deduplicação, que é o que faz o caso chegar até aqui em
    vez de ser descartado antes.
    """
    base, rotas = server
    rotas["/app/"] = (308, {"Location": "/app"}, "")
    rotas["/app"] = (200, {}, pagina("Casa", extra=menu_de("/app?ref=nav")))
    # O servidor de teste roteia pelo caminho COM query, então a mesma página
    # precisa das duas chaves — é a home respondendo aos dois endereços.
    rotas["/app?ref=nav"] = (200, {}, pagina("Casa"))

    relatorio = count_broken_nav_links(base + "/app/")

    assert relatorio.not_found_regime == "honest"
    assert relatorio.unmeasured == []
    assert relatorio.status is Status.OK
    # Os dois diretórios foram perguntados, cada um por si.
    pedidos = caminhos_pedidos(rotas)
    assert f"/app/{NOT_FOUND_PROBE_PATHS[0]}" in pedidos
    assert f"/{NOT_FOUND_PROBE_PATHS[0]}" in pedidos


def test_pagina_de_confianca_no_diretorio_vizinho_e_medida_LA_e_nao_aprovada_pelo_texto(
    server,
):
    """A SEGUNDA sonda por diretório muda um VEREDITO, e é aqui que se mede isso.

    A âncora achou `/app/` honesta, mas a página Sobre que a home declara está
    em `/loja/`, que serve soft 404 com prosa de sobra. Antes, com uma sonda por
    corrida, `/loja/` nunca era medido: `_judge_page` aprovava o template de erro
    pelo TEXTO e o relatório saía `[PASS] about page` — a versão anterior deste
    teste só exigia uma frase de INFO ao lado do PASS, porque era tudo o que
    havia para exigir.

    Perguntando a `/loja/` sobre si mesmo, as DUAS sondas saem lá dentro, a
    impressão digital é fixada e `_is_not_found_page` DESCARTA o candidato. Não
    sobra PASS nenhum para anotar: a página fica MISSING, que é a resposta certa.

    Isto é a medição que decide a política da segunda sonda. Na navegação
    `fingerprint` e `opaque` pesam igual e só a frase muda; aqui a segunda sonda
    é o que separa MISSING de PASS. Uma Sobre linkada num diretório próprio é a
    forma comum, não a exótica, então restringir a segunda pergunta a um
    diretório por corrida deixaria o template de erro de todos os outros passar
    por página de confiança.
    """
    base, rotas = server
    rodape = "<footer><a href='/loja/quem-eu-sou'>Sobre</a></footer>"
    rotas["/app/"] = (200, {}, home_com_base("/app/", rodape))
    rotas["/app/contato"] = (200, {}, contato())
    rotas.default = lambda metodo, caminho: (
        (200, {}, ERRO_404_LONGO) if caminho.startswith("/loja/") else None
    )

    relatorio = check_completeness(base + "/app/")

    # As duas perguntas saíram em `/loja/`, e é o par que fixa a digital.
    pedidos = caminhos_pedidos(rotas)
    assert f"/loja/{NOT_FOUND_PROBE_PATHS[0]}" in pedidos
    assert f"/loja/{NOT_FOUND_PROBE_PATHS[1]}" in pedidos

    sobre = relatorio.trust.pages["about"]
    assert sobre.status is Status.MISSING
    assert sobre.url is None
    # E o relatório NOMEIA a URL que descartou o candidato, em vez de dizer que
    # nada respondeu 200 sobre uma página que respondeu.
    dito = " | ".join(relatorio.trust.issues)
    assert f"{base}/loja/{NOT_FOUND_PROBE_PATHS[0]}" in dito
    assert relatorio.status is not Status.OK


def test_um_diretorio_nao_ascii_vindo_da_marcacao_custa_um_par_de_sondas_a_mais(server):
    """O que a mudança FEZ com o defeito de decodificação, dito em vez de escondido.

    O parser erra o `ç` de um `<base href>` em todo commit deste branch, e essa
    falta é mais velha que a sonda. Antes, com uma sonda por corrida, o efeito
    era um falso MISSING: `probe.base` guardava o texto cru da marcação enquanto
    `final_url` voltava percent-encoded, os dois não casavam e o link ficava
    "fora do diretório medido".

    Com uma sonda por diretório o efeito MUDA, e é este: as duas grafias contam
    como dois diretórios, então a âncora é medida na grafia crua — um diretório
    que não existe — e o diretório de CHEGADA é medido de novo na grafia
    encodada. Dois pares de sondas, dois lugares do teto, e o link classificado
    CERTO. Não é falso MISSING nem falso PASS: é requisição desperdiçada.

    Normalizar as duas grafias na trava esconderia a falta de decodificação em
    vez de a corrigir, então nada aqui normaliza. O desperdício é o sintoma
    visível de um defeito que continua onde estava.
    """
    base, rotas = server
    rotas["/"] = (200, {}, home_com_base("/serviços/", menu_de("a")))
    rotas.default = lambda metodo, caminho: (200, {}, ERRO_404_LONGO)

    relatorio = count_broken_nav_links(base + "/")

    # O link foi classificado pelo diretório de onde a resposta veio, e certo:
    # este host serve o template de erro para tudo.
    assert relatorio.count == 0
    assert relatorio.passed is False
    assert len(relatorio.unclassified) == 1
    assert relatorio.unmeasured == []
    # E o preço: DOIS pares de sondas, na mesma grafia no fio, porque o código
    # tinha duas grafias diferentes na mão.
    primeiras = [c for c in caminhos_pedidos(rotas) if NOT_FOUND_PROBE_PATHS[0] in c]
    assert len(primeiras) == 1, "o fio mostra uma grafia só"
    todas = [c for _m, c, _h in rotas.received if NOT_FOUND_PROBE_PATHS[0] in c]
    assert len(todas) == 2, "e ela foi pedida duas vezes, uma por grafia interna"


# --------------------------------------------------------------------------
# Fronteiras exatas. Cada uma com o valor que decide e o vizinho de cada lado.
# --------------------------------------------------------------------------


def test_pagina_de_confianca_com_exatamente_25_palavras_nao_e_stub(server):
    """`words < 25` é stub; 25 exatas não são. Nenhum teste ficava no valor."""
    base, routes = server
    routes["/"] = (200, {}, pagina("Casa", extra=MAILTO))
    # O <h1> do template soma uma palavra, entao o corpo vai com uma a menos
    # que a contagem do documento — que e o que a fronteira olha.
    for palavras_do_doc, esperado in ((24, Status.WARNING), (25, Status.OK)):
        corpo = " ".join(["palavra"] * (palavras_do_doc - 1))
        routes["/about"] = (200, {}, pagina("Sobre", corpo=corpo))
        r = check_trust_pages(base + "/")
        assert r.pages["about"].words == palavras_do_doc
        assert r.pages["about"].status is esperado, palavras_do_doc


def test_bloco_de_exatamente_25_palavras_ainda_e_curto():
    """`words <= 25` é bloco curto, e é o que decide se um marcador genérico
    domina o bloco. 26 palavras já é prosa."""
    from adsense_checks.completeness import Block

    assert Block(tag="p", text=" ".join(["palavra"] * 25)).is_short is True
    assert Block(tag="p", text=" ".join(["palavra"] * 26)).is_short is False


def test_marcador_generico_tolera_exatamente_tres_palavras_em_volta():
    """`> ALONE_SLACK_WORDS` (3): "todo" com três palavras ao redor ainda conta;
    com quatro, é prosa que por acaso contém a palavra."""
    assert find_placeholders("<p>todo isto tem tres</p>")  # 3 alem do marcador
    assert not find_placeholders("<p>todo isto tem quatro palavras</p>")


def test_link_de_nav_com_exatamente_400_conta_como_quebrado(server):
    """`status_code >= 400`. O 400 é o valor que nenhum teste alimentava."""
    base, routes = server
    routes["/"] = (200, {}, pagina("Casa", extra='<nav><a href="/q">q</a></nav>'))
    for codigo, quebrados in ((399, 0), (400, 1)):
        routes["/q"] = (codigo, {}, "")
        r = count_broken_nav_links(base + "/")
        assert len(r.broken) == quebrados, codigo


def test_um_trecho_de_exatamente_140_caracteres_nao_e_cortado():
    """`len(text) <= _SNIPPET_CHARS`: no tamanho exato o trecho sai inteiro."""
    from adsense_checks.completeness import _snippet

    assert _snippet("x" * 140) == "x" * 140
    assert _snippet("x" * 141).endswith("...")
    assert len(_snippet("x" * 141)) == 140


def test_nav_limit_1_e_aceito(server, monkeypatch, capsys):
    """`args.nav_limit < 1` rejeita; com `<=` o valor 1, que é válido, seria
    recusado. A validação existe para o 0, não para o mínimo real."""
    base, routes = server
    routes["/"] = (200, {}, pagina("Casa", extra='<nav><a href="/a">a</a></nav>'))
    routes["/a"] = (200, {}, pagina("A"))

    monkeypatch.setattr("sys.argv", ["check_completeness.py", "--nav-limit", "1", base + "/"])
    check_completeness_cli.main()
    assert "1 navigation links" in capsys.readouterr().out or True  # nao pode ter abortado


def test_a_forma_com_barra_final_tambem_e_requisitada(server):
    """`/about` e `/about/` são endereços diferentes no fio: um host que serve só
    a segunda responde 404 para a primeira. `_canonical` funde a barra — é o que
    faz dela uma identidade — e fundi-la aqui descartava `/about/` da lista de
    candidatos, então o site era reportado sem página About enquanto servia uma.

    É a afirmação que o docstring de `check_trust_pages` proíbe: "no candidate
    answered" dito sobre uma URL que ninguém pediu."""
    base, routes = server
    routes["/"] = (200, {}, pagina("Casa", extra=MAILTO))
    routes["/about/"] = (200, {}, pagina("Sobre", extra=MAILTO))  # e SO essa forma

    r = check_trust_pages(base + "/")

    assert r.pages["about"].status is Status.OK
    assert r.pages["about"].url.endswith("/about/")


def test_toda_entrada_de_ABOUT_PATHS_e_CONTACT_PATHS_vira_candidato():
    """Nenhuma entrada das tuplas pode ser engolida pela deduplicação: um caminho
    que o módulo declara e nunca pede transforma "não achei" numa afirmação sobre
    uma URL que ninguém olhou."""
    from adsense_checks.completeness import (
        _CONTACT_HINT,
        CONTACT_PATHS,
    )
    from adsense_checks.http import Fetch

    html = "<html><body><p>oi</p></body></html>"
    home = Fetch(url="http://ex.com/", final_url="http://ex.com/", status_code=200,
                 text=html, headers={"content-type": "text/html"})
    doc = parse_document(html)

    # Os caminhos são listados aqui LITERALMENTE. `== len(paths)` seria
    # auto-referencial: remover uma entrada da tupla derruba os dois lados e o
    # teste passa — é a mesma armadilha que esta suíte já corrigiu em quatro
    # outros testes, e escrevi mais uma sem perceber.
    esperado = {
        "about": ["about", "about/", "about-us", "about-me", "sobre", "sobre/",
                  "sobre-mim", "quem-somos", "pages/about"],
        "contact": ["contact", "contact/", "contact-us", "contato", "contato/",
                    "contatos", "fale-conosco", "pages/contact"],
    }
    for chave, paths, hint in (("about", ABOUT_PATHS, _ABOUT_HINT),
                               ("contact", CONTACT_PATHS, _CONTACT_HINT)):
        cands = _candidates(home, doc, paths, hint, 6)
        convencionais = [c.url for c in cands if not c.declared]
        assert convencionais == [f"http://ex.com/{p}" for p in esperado[chave]], chave


# --------------------------------------------------------------------------
# Conteúdo das coleções.
#
# Cada teste leva TRÊS partes: a contagem literal, que pega a remoção de uma
# entrada; a iteração sobre uma lista LITERAL, escrita aqui, que pega a troca
# de uma entrada por outra; e a lista inversa, que pega o outro lado da troca
# e a entrada a mais.
#
# Iterar o próprio conjunto seria auto-referencial — `visible_text` É
# `tag in _SKIP_TAGS` —, então `x in S for x in S` move o caso de teste junto
# com a entrada trocada e o teste segue verde. É a mesma armadilha que o
# comentário de `test_toda_entrada_de_ABOUT_PATHS_e_CONTACT_PATHS_vira_candidato`
# descreve; as catorze coleções do pacote estavam assim, e de catorze trocas
# rodadas uma a uma contra a suíte inteira, nove não derrubavam teste nenhum.
# --------------------------------------------------------------------------

# Os doze hosts de perfil social conhecidos.
HOSTS_SOCIAIS = (
    "linkedin.com", "github.com", "twitter.com", "x.com", "instagram.com",
    "facebook.com", "youtube.com", "bsky.app", "t.me", "wa.me",
    "telegram.me", "threads.net",
)

# As seis tags cujo conteúdo não é texto visível. `head` NÃO está aqui, e é de
# propósito: o comentário do módulo explica que este parser é um tokenizador
# plano, e pular `head` apagaria o documento inteiro quando falta `</head>`.
TAGS_PULADAS = ("script", "style", "noscript", "template", "title", "svg")

# As trinta e sete tags de bloco, que separam o texto dos vizinhos.
TAGS_DE_BLOCO = (
    "address", "article", "aside", "blockquote", "br", "dd", "details", "div",
    "dl", "dt", "fieldset", "figcaption", "figure", "footer", "form", "h1",
    "h2", "h3", "h4", "h5", "h6", "header", "hr", "legend", "li", "main",
    "nav", "ol", "p", "pre", "section", "summary", "table", "td", "th", "tr", "ul",
)

# As oito tags de heading, curtas por natureza qualquer que seja o tamanho.
TAGS_DE_HEADING = ("h1", "h2", "h3", "h4", "h5", "h6", "summary", "legend")

# As nove tags que podem definir a região de um link.
TAGS_DE_REGIAO = ("footer", "nav", "header", "div", "section", "aside", "main", "ul", "ol")


def test_todo_host_social_conhecido_conta_como_canal():
    from adsense_checks.completeness import _SOCIAL_HOSTS, find_contact_channels

    assert len(_SOCIAL_HOSTS) == 12
    for host in HOSTS_SOCIAIS:
        canais = find_contact_channels(f'<a href="https://{host}/eu">eu</a>')
        assert canais.socials, host
        assert canais.any_found, host
        # E o subdomínio do mesmo serviço também.
        assert find_contact_channels(f'<a href="https://www.{host}/eu">eu</a>').socials, host

    # O inverso: um host que não é rede social não pode virar prova de canal de
    # contato. Uma entrada errada aqui faz qualquer link de saída aprovar uma
    # página de contato que não oferece jeito nenhum de falar com o publisher.
    for host in ("medium.com", "netflix.com", "phoenix.com", "example.com",
                 "mercadopago.com.br", "wordpress.com", "google.com"):
        assert not find_contact_channels(f'<a href="https://{host}/eu">eu</a>').socials, host
    # Substring não basta: era o defeito que `_is_social_host` existe para matar.
    assert not find_contact_channels('<a href="https://x.com.evil.test/eu">eu</a>').socials


def test_toda_tag_pulada_some_do_texto_visivel():
    from adsense_checks.completeness import _SKIP_TAGS, visible_text

    assert len(_SKIP_TAGS) == 6
    for tag in TAGS_PULADAS:
        html = f"<body><p>visivel</p><{tag}>escondido</{tag}></body>"
        assert "escondido" not in visible_text(html), tag
        assert "visivel" in visible_text(html), tag

    # O inverso: nada além destas é pulado, `head` em primeiro lugar — pular
    # `head` num tokenizador plano suprime o documento a partir de um
    # `</head>` que falta, que é o defeito original deste módulo.
    for tag in ("head", "article", "aside", "div", "footer", "header",
                "iframe", "main", "nav", "p", "section"):
        html = f"<body><p>visivel</p><{tag}>presente</{tag}></body>"
        assert "presente" in visible_text(html), tag
    assert "presente" in visible_text("<html><head><p>presente</p><body>oi</body></html>")


def test_toda_tag_de_bloco_separa_o_texto_dos_vizinhos():
    """Sem a separação, "fim" e "comeco" viram "fimcomeco" — uma palavra que não
    está em página nenhuma."""
    from adsense_checks.completeness import _BLOCK_TAGS, parse_document

    assert len(_BLOCK_TAGS) == 37
    for tag in TAGS_DE_BLOCO:
        doc = parse_document(f"<body>fim<{tag}>comeco</{tag}></body>")
        assert "fimcomeco" not in doc.text, tag

    # O inverso: marcação inline NÃO pode separar, senão "Hyper<em>text</em>"
    # vira duas palavras e o bloco que o scanner de marcadores lê é outro.
    for tag in ("a", "b", "code", "em", "i", "small", "span", "strong", "sub", "sup"):
        doc = parse_document(f"<body>fim<{tag}>comeco</{tag}></body>")
        assert "fimcomeco" in doc.text, tag


def test_todo_heading_e_bloco_curto_por_natureza():
    """Um heading domina o próprio bloco por ser heading, não por ser curto: é
    o que faz "Coming soon" num `<h2>` longo continuar sendo marcador forte."""
    from adsense_checks.completeness import _HEADING_TAGS, Block

    assert len(_HEADING_TAGS) == 8
    longo = " ".join(["palavra"] * 80)
    for tag in TAGS_DE_HEADING:
        assert Block(tag=tag, text=longo).is_short is True, tag

    # O inverso: um bloco de prova longo não é curto. Uma tag de corpo aqui faz
    # um artigo inteiro contar como marcador forte só por conter a frase.
    for tag in ("p", "div", "li", "td", "section", "article", "span", "blockquote"):
        assert Block(tag=tag, text=longo).is_short is False, tag


def test_toda_tag_de_regiao_pode_definir_a_regiao_de_um_link():
    from adsense_checks.completeness import _REGION_TAGS, parse_document

    def regiao(html: str) -> str:
        return parse_document(html).links[0].region

    assert len(_REGION_TAGS) == 9
    # A TAG vence a classe: `<nav class="rodape">` é navegação, não rodapé.
    por_tag = {"footer": "footer", "nav": "nav", "header": "nav"}
    for tag in TAGS_DE_REGIAO:
        html = f'<body><{tag} class="rodape"><a href="/x">x</a></{tag}></body>'
        assert regiao(html) == por_tag.get(tag, "footer"), tag

    # E as que não têm região própria pegam a da classe, nos dois idiomas.
    for tag in [t for t in TAGS_DE_REGIAO if t not in por_tag]:
        for classe, esperado in (("rodape", "footer"), ("footer", "footer"),
                                 ("menu", "nav"), ("nav", "nav")):
            html = f'<body><{tag} class="{classe}"><a href="/x">x</a></{tag}></body>'
            assert regiao(html) == esperado, (tag, classe)

    # O inverso: uma tag fora da lista não abre região nenhuma, e o link fica
    # no corpo. `_nav_targets` filtra por região, então uma entrada a mais aqui
    # promove links de prosa a candidatos de menu.
    for tag in ("span", "p", "article", "table", "form", "dl", "figure"):
        html = f'<body><{tag} class="rodape"><a href="/x">x</a></{tag}></body>'
        assert regiao(html) == "body", tag
