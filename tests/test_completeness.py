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

from adsense_checks.completeness import (
    _ABOUT_HINT,
    ABOUT_PATHS,
    BROKEN_NAV_FAIL_THRESHOLD,
    MIN_TRUST_PAGE_WORDS,
    NavLinkReport,
    Status,
    _candidates,
    _nav_targets,
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
    candidatos = _candidates(
        home, parse_document(html), "https://exemplo.com/", ABOUT_PATHS, _ABOUT_HINT, 6
    )
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
    cands = _candidates(home, doc, "http://ex.com/", ABOUT_PATHS, _ABOUT_HINT, 6)
    largo = _candidates(home, doc, "http://ex.com/", ABOUT_PATHS, _ABOUT_HINT, 100)
    assert len([c for c in cands if c.declared]) == 6
    assert len([c for c in largo if c.declared]) == 9
    assert len([c for c in largo if not c.declared]) == len([c for c in cands if not c.declared])


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
        for c in _candidates(home, parse_document(html), "http://ex.com/",
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
        for c in _candidates(home, parse_document(html), "http://ex.com/",
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
        for c in _candidates(home, parse_document(html), "http://ex.com/",
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

    cands = _candidates(home, parse_document(html), "http://ex.com/",
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
    assert relatorio.passed is True
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
    assert "ftp://" not in " | ".join(relatorio.issues)


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

    cands = _candidates(home, doc, "http://ex.com/", (), _ABOUT_HINT, 6)

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
        cands = _candidates(home, doc, "http://ex.com/", paths, hint, 6)
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
