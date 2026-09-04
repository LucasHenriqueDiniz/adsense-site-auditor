"""Testes de adsense_checks/completeness.py.

Cada teste desta suíte corresponde a um defeito reproduzido em
scripts/check_completeness.py. O nome do teste diz o que ele impede de voltar,
e o docstring descreve o caso de reprodução original.

Os testes que envolvem rede usam a fixture `server` (servidor HTTP local) em vez
de mock de requests: os defeitos aqui eram comportamento de protocolo — redirect
seguido silenciosamente, 403 confundido com 404, conexão cortada — e um mock
reproduziria a suposição errada em vez do protocolo.
"""

import pytest

from adsense_checks.completeness import (
    _ABOUT_HINT,
    ABOUT_PATHS,
    BROKEN_NAV_FAIL_THRESHOLD,
    MIN_TRUST_PAGE_WORDS,
    NavLinkReport,
    Status,
    _candidates,
    check_completeness,
    check_trust_pages,
    count_broken_nav_links,
    find_contact_channels,
    find_placeholders,
    parse_document,
    visible_text,
)
from adsense_checks.completeness import as_base as as_base_publico
from adsense_checks.http import Fetch

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
