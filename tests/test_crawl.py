"""Testes do crawler — um por defeito reproduzido em scripts/crawl_site.py.

Cada teste de rede sobe o servidor da fixture `server` em vez de mockar
requests: os dois blockers são comportamento de protocolo (um redirect que troca
o host, um documento grande demais para o corte de 5KB), e um mock reproduziria
a suposição errada em vez do protocolo.
"""

import inspect
import time
from http.server import BaseHTTPRequestHandler

from adsense_checks import crawl as crawl_mod
from adsense_checks.crawl import (
    DEFAULT_DELAY,
    DEFAULT_USER_AGENT,
    CheckResult,
    CrawlResult,
    Page,
    check_pages_reachable,
    check_redirect_chain,
    check_session_urls,
    crawl,
    has_session_id,
    normalize_url,
    parse_html,
    same_site,
)
from adsense_checks.http import ADSENSE_UA
from adsense_checks.status import Status

HTML = {"Content-Type": "text/html; charset=utf-8"}
TEXTO = {"Content-Type": "text/plain"}
# Uma resposta que abre sessão: o crawl usa uma Session só, então tudo o que ele
# pedir depois desta página leva o cookie — e uma requisição feita com Session
# nova, não.
HTML_COM_SESSAO = {**HTML, "Set-Cookie": "sess=1; Path=/"}


def pagina(titulo, corpo=""):
    return f"<html><head><title>{titulo}</title></head><body><h1>{titulo}</h1>{corpo}</body></html>"


def rota_contada(contador, resposta):
    """Rota que registra cada método recebido — para provar quantos GETs saíram."""

    def rota(metodo):
        contador.append(metodo)
        return resposta

    return rota


def cookie_da_requisicao():
    """O cabeçalho Cookie da requisição HTTP em curso.

    O cabeçalho é o único lugar onde 'esta requisição carrega a sessão do crawl'
    aparece de verdade. Ramificar por contador de chamadas provaria apenas que
    saiu um segundo GET — que é justamente o que os testes de ADS-CRAWL-04/05 NÃO
    podem se contentar em provar. A fixture `server` entrega ao route só o método
    HTTP, então o handler que o chamou é procurado nos quadros acima.
    """
    frame = inspect.currentframe()
    while frame is not None:
        handler = frame.f_locals.get("self")
        if isinstance(handler, BaseHTTPRequestHandler):
            return handler.headers.get("Cookie", "")
        frame = frame.f_back
    raise AssertionError("handler HTTP não encontrado na pilha de chamadas")


def varrer(base, **kwargs):
    """crawl() sem o delay de cortesia, que é o padrão fora dos testes."""
    kwargs.setdefault("delay", 0)
    return crawl(base + "/", **kwargs)


# --------------------------------------------------------------------------- #
# Identidade de URL e de host
# --------------------------------------------------------------------------- #


def test_normalizacao_junta_fragmento_e_barra_final_na_mesma_url():
    """O crawler antigo só descartava links iniciados por '#'.

    Com isso /about, /about/, /about#team e /about#jobs eram quatro GETs e
    quatro linhas idênticas no relatório para um único documento.
    """
    alvo = "http://ex.com/about"
    assert normalize_url("http://ex.com/about") == alvo
    assert normalize_url("http://ex.com/about/") == alvo
    assert normalize_url("http://ex.com/about#team") == alvo
    assert normalize_url("http://ex.com/about#jobs") == alvo
    assert normalize_url("http://EX.com:80/about") == alvo
    # A raiz mantém a barra, e a query faz parte da identidade.
    assert normalize_url("http://ex.com") == "http://ex.com/"
    assert normalize_url("http://ex.com/busca?q=1") == "http://ex.com/busca?q=1"


def test_www_e_apex_sao_o_mesmo_site():
    """O domínio-base saía da URL digitada e os links de resp.url; num site que
    redireciona apex->www isso já bastava para zerar o crawl."""
    assert same_site("https://ex.com/a", "https://www.ex.com/b") is True
    assert same_site("https://WWW.Ex.com/", "https://ex.com/") is True
    assert same_site("https://ex.com/", "https://outro.com/") is False
    assert same_site("mailto:a@b.com", "https://ex.com/") is False


def test_identificador_de_sessao_e_detectado_sem_falso_positivo():
    """ADS-CRAWL-05. O delimitador antes do token é o que separa 'sid=' de 'asid='."""
    assert has_session_id("http://ex.com/p?PHPSESSID=ab12") is True
    assert has_session_id("http://ex.com/p;jsessionid=ab12") is True
    assert has_session_id("http://ex.com/p?a=1&sid=9") is True
    assert has_session_id("http://ex.com/p?uid=9") is True
    assert has_session_id("http://ex.com/p?asid=9") is False
    assert has_session_id("http://ex.com/considerations") is False


# --------------------------------------------------------------------------- #
# Parser — cada caso é um defeito reproduzido
# --------------------------------------------------------------------------- #


def test_head_maior_que_5kb_nao_esconde_title_meta_e_h1():
    """BLOCKER: a extração recebia resp.text[:5000] e title/meta/h1 só eram
    atribuídos no fechamento da tag, então CSS crítico inline antes do <title>
    devolvia (None, None, None) para uma página que tem os três."""
    css = "a{color:#000}" * 500  # ~6,5KB antes do title
    html = (
        f"<html><head><style>{css}</style>"
        '<title>Best Coffee Guide</title>'
        '<meta name="description" content="All about coffee">'
        "</head><body><h1>Coffee</h1></body></html>"
    )
    assert html.index("<title>") > 5000
    p = parse_html(html)
    assert p.title == "Best Coffee Guide"
    assert p.meta_description == "All about coffee"
    assert p.h1 == "Coffee"


def test_meta_description_sem_tag_head_explicita_e_lida():
    """A flag in_head nunca ligava em HTML5 válido que omite <head> — saída
    comum de minificador. HTMLParser é tokenizer e não sintetiza o head."""
    html = (
        "<!doctype html><html><meta charset='utf-8'><title>Foo</title>"
        '<meta name="description" content="Great page about cats">'
        "<body><h1>Cats</h1></body></html>"
    )
    p = parse_html(html)
    assert p.title == "Foo"
    assert p.meta_description == "Great page about cats"
    assert p.h1 == "Cats"


def test_title_e_h1_nao_concatenam_ocorrencias():
    """Os buffers nunca eram zerados: '<h1>Acme</h1>...<h1>How to bake bread</h1>'
    virava 'AcmeHow to bake bread'."""
    p = parse_html("<html><body><h1>Acme</h1><p>x</p><h1>How to bake bread</h1></body></html>")
    assert p.h1 == "Acme"
    assert p.h1s == ["Acme", "How to bake bread"]
    # A contagem de H1 é sinal clássico de auditoria e estava na mão do parser.
    assert len(p.h1s) == 2


def test_title_de_svg_nao_vira_title_da_pagina():
    """Ícone SVG no corpo produzia title == 'Home | AcmeMenu icon'."""
    p = parse_html(
        "<html><head><title>Home | Acme</title></head>"
        "<body><svg><title>Menu icon</title></svg><h1>Acme</h1></body></html>"
    )
    assert p.title == "Home | Acme"


def test_atributo_sem_valor_nao_derruba_o_parse():
    """attrs.get('content', '') devolve None quando o atributo existe sem valor;
    o .strip() estourava AttributeError e o `except: pass` engolia o resto do
    documento, deixando h1 indistinguível de uma página sem H1."""
    html = (
        '<html><head><title>Real Title</title><meta name="description" content>'
        "</head><body><h1>Real H1</h1></body></html>"
    )
    p = parse_html(html)
    assert p.title == "Real Title"
    assert p.h1 == "Real H1"
    assert p.meta_description == ""
    # E a variante com o 'name' sem valor, que estourava no .lower().
    assert parse_html('<html><meta name content="x"><h1>ok</h1></html>').h1 == "ok"


def test_links_vem_de_ancoras_e_nao_de_css_comentario_ou_script():
    """A regex href=["']([^"']+)["'] casava stylesheet, comentário e string de
    JS, e perdia href sem aspas e com espaços ao redor do '='."""
    html = (
        '<link rel="stylesheet" href="/style.css">'
        "<a href=/about>unquoted</a>"
        '<a href = "/spaced">x</a>'
        '<!-- <a href="/commented-out">old</a> -->'
        "<script>var t='<a href=\"/fake-from-js\">x</a>';</script>"
    )
    p = parse_html(html)
    assert p.links == ["/about", "/spaced"]


def test_entidade_html_no_href_e_resolvida_pelo_tokenizer():
    """'&amp;' ia literal para a requisição e o servidor via o parâmetro
    inexistente 'amp;page' — link bom reportado como quebrado."""
    p = parse_html('<a href="/search?q=1&amp;page=2">busca</a>')
    assert p.links == ["/search?q=1&page=2"]


def test_nofollow_fica_fora_dos_links_seguidos():
    p = parse_html('<a href="/a">a</a><a href="/b" rel="nofollow ugc">b</a>')
    assert p.links == ["/a"]
    assert p.nofollow_links == ["/b"]


def test_contagem_de_palavras_ignora_script_style_e_title():
    """content_length era len(resp.text) — caracteres de markup, CSS e JS —
    com um nome que sugeria conteúdo; quem lesse o JSON para julgar thin content
    invertia o veredito."""
    html = (
        "<html><head><title>Titulo Longo Aqui</title>"
        "<style>body{color:red;background:blue}</style></head>"
        "<body><script>var a = 'uma frase inteira dentro de script';</script>"
        "<p>uma duas tres quatro</p></body></html>"
    )
    p = parse_html(html)
    assert p.word_count == 4


# --------------------------------------------------------------------------- #
# BLOCKERS, contra servidor real
# --------------------------------------------------------------------------- #


def test_crawl_continua_apos_redirect_que_troca_o_host(server):
    """BLOCKER reproduzido: o domínio-base vinha da URL de ENTRADA e os links de
    resp.url (pós-redirect).

    Aqui a entrada é http://localhost:PORT/ e a resposta final vem de
    http://127.0.0.1:PORT/home — dois hosts diferentes, exatamente como
    ex.com -> www.ex.com. Com a implementação antiga o crawl emitia 1 GET e
    devolvia 1 página, e /sobre e /precos nunca eram visitados.
    """
    base, routes = server
    porta = base.rsplit(":", 1)[1]
    entrada = f"http://localhost:{porta}/"
    routes["/"] = (301, {"Location": base + "/home"}, "")
    routes["/home"] = (
        200,
        HTML,
        pagina("Home", '<a href="/sobre">Sobre</a><a href="/precos">Precos</a>'),
    )
    routes["/sobre"] = (200, HTML, pagina("Sobre"))
    routes["/precos"] = (200, HTML, pagina("Precos"))

    r = crawl(entrada, delay=0)

    assert r.base_host == "127.0.0.1"  # veio da resposta final, não de 'localhost'
    caminhos = sorted(url.rsplit("/", 1)[-1] for url in (p.final_url for p in r.pages))
    assert caminhos == ["home", "precos", "sobre"]
    assert len(r.pages) == 3


def test_head_grande_nao_reporta_pagina_sem_title_no_crawl(server):
    """BLOCKER reproduzido ponta a ponta: com o corte em 5000 caracteres a linha
    do relatório saía '200 | ... | [no title]' e o JSON gravava
    meta_description='[none]' para uma página que tem os três campos."""
    base, routes = server
    css = "a{color:#000}" * 500
    routes["/"] = (
        200,
        HTML,
        f"<html><head><style>{css}</style><title>Best Coffee Guide</title>"
        '<meta name="description" content="All about coffee"></head>'
        "<body><h1>Coffee</h1><p>uma duas tres</p></body></html>",
    )

    r = varrer(base)
    home = r.pages[0]

    assert home.title == "Best Coffee Guide"
    assert home.meta_description == "All about coffee"
    assert home.h1 == "Coffee"
    assert home.html_chars > 5000


# --------------------------------------------------------------------------- #
# Deduplicação e limites
# --------------------------------------------------------------------------- #


def test_fragmento_e_barra_final_geram_um_unico_get(server):
    """Quatro grafias do mesmo documento davam quatro GETs e quatro linhas."""
    base, routes = server
    gets = []
    routes["/"] = (
        200,
        HTML,
        pagina(
            "Home",
            '<a href="/sobre">1</a><a href="/sobre/">2</a>'
            '<a href="/sobre#equipe">3</a><a href="/sobre#vagas">4</a>',
        ),
    )
    routes["/sobre"] = rota_contada(gets, (200, HTML, pagina("Sobre")))

    r = varrer(base)

    assert gets == ["GET"]
    assert len(r.pages) == 2


def test_link_com_barra_final_e_pedido_com_a_barra(server):
    """A identidade normalizada é chave de deduplicação, não endereço.

    normalize_url tira a barra final, e o link normalizado era o que ia para a
    wire: num host que só serve a forma com barra (S3 estático, Hugo/Jekyll,
    Next com trailingSlash) o GET de '/blog' volta 404 e ADS-CRAWL-01 acusa
    '404 on a link found on the site' sobre um link que funciona.
    """
    base, routes = server
    pedidos = []
    routes["/"] = (200, HTML, pagina("Home", '<a href="/blog/">blog</a>'))
    routes["/blog/"] = rota_contada(pedidos, (200, HTML, pagina("Blog")))

    r = varrer(base)
    check = check_pages_reachable(r)

    assert pedidos == ["GET"]  # a rota com barra foi de fato pedida
    assert [p.status_code for p in r.pages] == [200, 200]
    assert r.pages[1].requested_url.endswith("/blog/")
    assert check.status is Status.OK
    assert check.findings == []


def test_url_linkada_de_duas_paginas_e_buscada_uma_vez(server):
    """A fila só testava `visited` no append: a mesma URL entrava dezenas de vezes."""
    base, routes = server
    gets = []
    routes["/"] = (200, HTML, pagina("Home", '<a href="/a">a</a><a href="/b">b</a>'))
    routes["/a"] = (200, HTML, pagina("A", '<a href="/comum">c</a>'))
    routes["/b"] = (200, HTML, pagina("B", '<a href="/comum">c</a>'))
    routes["/comum"] = rota_contada(gets, (200, HTML, pagina("Comum")))

    r = varrer(base, max_depth=2)

    assert gets == ["GET"]
    assert len(r.pages) == 4


def test_href_com_entidade_chega_decodificado_ao_servidor(server):
    """A rota só existe no caminho decodificado: se '&amp;' fosse para a wire,
    o servidor devolveria 404 e o relatório acusaria link quebrado."""
    base, routes = server
    routes["/"] = (200, HTML, pagina("Home", '<a href="/search?q=1&amp;page=2">busca</a>'))
    routes["/search?q=1&page=2"] = (200, HTML, pagina("Busca"))

    r = varrer(base)

    assert [p.status_code for p in r.pages] == [200, 200]
    assert r.pages[1].title == "Busca"


def test_profundidade_limita_os_niveis_visitados(server):
    base, routes = server
    routes["/"] = (200, HTML, pagina("Home", '<a href="/n1">n1</a>'))
    routes["/n1"] = (200, HTML, pagina("N1", '<a href="/n2">n2</a>'))
    routes["/n2"] = (200, HTML, pagina("N2"))

    assert len(varrer(base, max_depth=0).pages) == 1
    assert len(varrer(base, max_depth=1).pages) == 2
    assert len(varrer(base, max_depth=2).pages) == 3


def test_max_pages_interrompe_o_crawl_e_registra_a_razao(server):
    """O loop antigo não tinha teto: 'depth 2' num site de notícias eram ~10^4
    requisições sequenciais contra terceiro."""
    base, routes = server
    routes["/"] = (
        200,
        HTML,
        pagina("Home", '<a href="/p1">1</a><a href="/p2">2</a><a href="/p3">3</a>'),
    )
    for n in (1, 2, 3):
        routes[f"/p{n}"] = (200, HTML, pagina(f"P{n}"))

    r = varrer(base, max_pages=2)

    assert len(r.pages) == 2
    assert "max_pages=2" in (r.stopped_reason or "")


def test_delay_de_cortesia_e_o_padrao_e_e_respeitado(server):
    base, routes = server
    routes["/"] = (200, HTML, pagina("Home", '<a href="/a">a</a><a href="/b">b</a>'))
    routes["/a"] = (200, HTML, pagina("A"))
    routes["/b"] = (200, HTML, pagina("B"))

    assert DEFAULT_DELAY > 0  # o crawler antigo disparava sem pausa nenhuma
    inicio = time.monotonic()
    r = crawl(base + "/", delay=0.05)
    assert len(r.pages) == 3
    assert time.monotonic() - inicio >= 0.09  # duas pausas entre as três buscas


def test_user_agent_padrao_identifica_o_crawler_do_adsense(server):
    """O script forjava um User-Agent de Chrome, o que impede o site de
    identificar e limitar o bot — e pergunta a coisa errada: a auditoria quer
    saber o que o Mediapartners-Google recebe."""
    assert DEFAULT_USER_AGENT is ADSENSE_UA
    assert "Mediapartners-Google" in DEFAULT_USER_AGENT


# --------------------------------------------------------------------------- #
# robots.txt, assets e escopo
# --------------------------------------------------------------------------- #


def test_robots_bloqueando_o_adsense_impede_o_get(server):
    """O crawler antigo não lia robots.txt em nenhum momento."""
    base, routes = server
    bloqueada = []
    routes["/robots.txt"] = (
        200,
        TEXTO,
        "User-agent: Mediapartners-Google\nDisallow: /privado\n",
    )
    routes["/"] = (200, HTML, pagina("Home", '<a href="/privado">p</a><a href="/publico">u</a>'))
    routes["/privado"] = rota_contada(bloqueada, (200, HTML, pagina("Privado")))
    routes["/publico"] = (200, HTML, pagina("Publico"))

    r = varrer(base)

    assert bloqueada == []
    assert any(url.endswith("/privado") for url in r.blocked_by_robots)
    assert [p.title for p in r.pages] == ["Home", "Publico"]


def test_robots_ausente_libera_o_crawl(server):
    """Google trata robots.txt inalcançável como allow-all; ausência não pode
    virar bloqueio silencioso."""
    base, routes = server
    routes["/"] = (200, HTML, pagina("Home", '<a href="/a">a</a>'))
    routes["/a"] = (200, HTML, pagina("A"))

    r = varrer(base)

    assert r.robots.missing is True
    assert "404" in r.robots_note
    assert len(r.pages) == 2


def test_sitemaps_do_robots_sao_expostos(server):
    """SKILL.md prometia 'crawl homepage, key pages, and sitemap' e o script não
    tinha uma linha sobre sitemap; aqui o crawl ao menos entrega as URLs
    declaradas para quem for verificar ADS-CRAWL-07."""
    base, routes = server
    routes["/robots.txt"] = (200, TEXTO, f"User-agent: *\nAllow: /\nSitemap: {base}/sitemap.xml\n")
    routes["/"] = (200, HTML, pagina("Home"))

    r = varrer(base)

    assert r.sitemaps == [f"{base}/sitemap.xml"]


def test_pdf_e_feed_nao_sao_baixados_como_pagina(server):
    """Sem filtro, o crawler baixava o PDF inteiro, rodava o HTMLParser sobre
    bytes binários e emitia '200 | /manual.pdf | [no title]' — e o analisador
    seguinte contava ~0 palavras e classificava como thin content."""
    base, routes = server
    baixados = []
    routes["/"] = (
        200,
        HTML,
        pagina("Home", '<a href="/manual.pdf">m</a><a href="/feed.xml">f</a>'),
    )
    pdf = (200, {"Content-Type": "application/pdf"}, "%PDF")
    feed = (200, {"Content-Type": "application/xml"}, "<rss/>")
    routes["/manual.pdf"] = rota_contada(baixados, pdf)
    routes["/feed.xml"] = rota_contada(baixados, feed)

    r = varrer(base)

    assert baixados == []
    assert len(r.pages) == 1
    assert len(r.skipped_assets) == 2


def test_content_type_binario_nao_e_tratado_como_pagina(server):
    """A extensão nem sempre denuncia: /download sem sufixo servindo PDF não
    pode entrar na amostra de páginas HTML."""
    base, routes = server
    routes["/"] = (200, HTML, pagina("Home", '<a href="/download">d</a>'))
    routes["/download"] = (200, {"Content-Type": "application/pdf"}, "%PDF-1.4 lixo binario")

    r = varrer(base)
    download = r.pages[1]

    assert download.is_html is False
    assert download.title is None
    assert download.word_count == 0
    assert download not in r.html_pages


def test_falha_de_parse_vira_ERROR_e_nao_pagina_sem_h1(server, monkeypatch):
    """Documento ilegível tem metadados INOBSERVÁVEIS, não ausentes.

    O original engolia a exceção com `except Exception: pass`, e uma falha de
    parse ficava indistinguível de uma página que genuinamente não tem H1 —
    registrar parse_error sem escalar para ERROR reintroduz exatamente isso.

    A falha é induzida no parser em vez de vir de um documento patológico porque
    o HTMLParser do 3.12 trata todo markup malformado como comentário bogus e não
    levanta mais (seção marcada desconhecida, subset de DOCTYPE quebrado, byte
    nulo e CDATA solto passam todos): o ramo é uma guarda, e o que está sob teste
    é o contrato de _record, não o tokenizer da stdlib.
    """
    base, routes = server
    routes["/"] = (200, HTML, pagina("Home"))

    def parser_que_quebra(html):
        raise ValueError("documento ilegivel")

    monkeypatch.setattr(crawl_mod, "parse_html", parser_que_quebra)

    r = varrer(base)
    home = r.pages[0]

    assert home.status_code == 200  # o HTTP foi perfeito...
    assert home.status is Status.ERROR  # ...e mesmo assim o veredito da página é ERROR
    assert home.parse_error == "ValueError: documento ilegivel"
    assert home.title is None and home.h1 is None
    assert r.worst_page_status is Status.ERROR


def test_link_externo_e_registrado_mas_nao_visitado(server):
    base, routes = server
    routes["/"] = (200, HTML, pagina("Home", '<a href="http://outro.invalid/x">ext</a>'))

    r = varrer(base)

    assert r.off_site == ["http://outro.invalid/x"]
    assert len(r.pages) == 1


def test_base_href_muda_a_resolucao_dos_links(server):
    base, routes = server
    routes["/"] = (
        200,
        HTML,
        '<html><head><base href="/blog/"><title>Home</title></head>'
        '<body><a href="post">post</a></body></html>',
    )
    routes["/blog/post"] = (200, HTML, pagina("Post"))

    r = varrer(base)

    assert [p.title for p in r.pages] == ["Home", "Post"]


# --------------------------------------------------------------------------- #
# ADS-CRAWL-01
# --------------------------------------------------------------------------- #


def test_host_inalcancavel_e_ERROR_e_nunca_um_crawl_de_sucesso():
    """O script devolvia 'Crawled 1 URLs' com status='error' e saía com código 0,
    então qualquer automação encadeada tratava a falha total como sucesso."""
    r = crawl("http://127.0.0.1:1/", delay=0, timeout=2)
    check = check_pages_reachable(r)

    assert r.status is Status.ERROR
    assert check.status is Status.ERROR
    assert check.passed is False


def test_uma_pagina_so_nao_aprova_ADS_CRAWL_01(server):
    """O canário do blocker: sem amostra interna o requisito é INVERIFICADO
    (MISSING), não aprovado. Era assim que 'Crawled 1 URLs' passava batido."""
    base, routes = server
    routes["/"] = (200, HTML, pagina("Home"))

    check = check_pages_reachable(varrer(base))

    assert check.status is Status.MISSING
    assert check.passed is False
    assert any("no internal sample" in f for f in check.findings)


def test_404_interno_nao_aprova_ADS_CRAWL_01(server):
    base, routes = server
    routes["/"] = (200, HTML, pagina("Home", '<a href="/quebrada">q</a>'))

    check = check_pages_reachable(varrer(base))

    assert check.status is Status.MISSING
    assert any("404" in f for f in check.findings)


def test_pagina_protegida_por_autenticacao_reprova(server):
    """ADS-CRAWL-01 exige resposta pública: 401 não é 'página lenta'."""
    base, routes = server
    routes["/"] = (200, HTML, pagina("Home", '<a href="/painel">p</a>'))
    routes["/painel"] = (401, {"WWW-Authenticate": 'Basic realm="x"'}, "auth")

    check = check_pages_reachable(varrer(base))

    assert check.status is Status.FAIL
    assert any("credentials" in f for f in check.findings)


def test_site_saudavel_aprova_ADS_CRAWL_01(server):
    base, routes = server
    routes["/"] = (200, HTML, pagina("Home", '<a href="/a">a</a><a href="/b">b</a>'))
    routes["/a"] = (200, HTML, pagina("A"))
    routes["/b"] = (200, HTML, pagina("B"))

    check = check_pages_reachable(varrer(base))

    assert check.status is Status.OK
    assert check.passed is True
    assert check.findings == []


# --------------------------------------------------------------------------- #
# ADS-CRAWL-04
# --------------------------------------------------------------------------- #


def test_cadeia_longa_de_redirect_e_sinalizada(server):
    """O status gravado era o da resposta final e o histórico era descartado:
    nenhuma linha do relatório podia conter um 3xx."""
    base, routes = server
    routes["/"] = (200, HTML, pagina("Home", '<a href="/longo">l</a>'))
    routes["/longo"] = (301, {"Location": "/h1"}, "")
    routes["/h1"] = (302, {"Location": "/h2"}, "")
    routes["/h2"] = (301, {"Location": "/h3"}, "")
    routes["/h3"] = (200, HTML, pagina("Destino"))

    r = varrer(base)
    alvo = r.pages[1]
    check = check_redirect_chain(r, max_hops=2)

    assert alvo.redirect_hops == 3
    assert [codigo for codigo, _ in alvo.redirect_chain] == [301, 302, 301]
    assert alvo.requested_url.endswith("/longo")
    assert alvo.final_url.endswith("/h3")
    assert check.status is Status.WARNING


def test_redirect_que_depende_de_estado_de_sessao_e_detectado(server):
    """ADS-CRAWL-04 pede repetir SEM os cookies do crawl.

    A rota ramifica pelo cabeçalho Cookie, não por contador de chamadas: a home
    abre sessão, o crawl chega ao /painel com o cookie e cai em /painel-ok, e o
    recheck — que só detecta o problema se for feito com Session nova — cai em
    /login. É isso que acontece com uma landing que só resolve para quem já tem
    sessão. Com um contador, qualquer segunda requisição divergiria, inclusive
    uma que vazasse a Session do crawl, e o teste não provaria isolamento nenhum.
    """
    base, routes = server

    def painel(metodo):
        destino = "/painel-ok" if "sess=" in cookie_da_requisicao() else "/login"
        return (302, {"Location": destino}, "")

    routes["/"] = (200, HTML_COM_SESSAO, pagina("Home", '<a href="/painel">p</a>'))
    routes["/painel"] = painel
    routes["/painel-ok"] = (200, HTML, pagina("Painel"))
    routes["/login"] = (200, HTML, pagina("Login"))

    r = varrer(base)
    check = check_redirect_chain(r, verify_stateless=True)

    # O crawl, com o cookie, foi parar no painel: sem isso o teste passaria por
    # já estar comparando dois /login.
    assert r.pages[1].final_url.endswith("/painel-ok")
    assert check.status is Status.WARNING
    assert any("session state" in f for f in check.findings)


def test_downgrade_de_https_para_http_reprova():
    """A cadeia começou em TLS e terminou fora dele — pergunta que o Fetch já
    respondia e que o crawler antigo nem fazia.

    O veredito vem do check, e não só das propriedades da Page: era em
    check_redirect_chain que 'reprova' precisava ser provado.
    """
    pagina_downgrade = Page(
        requested_url="https://ex.com/",
        final_url="http://ex.com/",
        depth=0,
        status_code=200,
        redirect_chain=[(301, "https://ex.com/")],
    )
    assert pagina_downgrade.downgraded_to_http is True
    assert pagina_downgrade.redirected is True

    resultado = CrawlResult(
        start_url="https://ex.com/",
        base_url="https://ex.com",
        base_host="ex.com",
        pages=[pagina_downgrade],
    )
    check = check_redirect_chain(resultado, max_hops=2)

    assert check.status is Status.FAIL
    assert check.passed is False
    assert any("redirect ends on http" in f for f in check.findings)


def test_redirect_de_barra_final_nao_e_reportado_como_ausente():
    """A barra final é exatamente o que normalize_url apaga.

    Comparar só as identidades normalizadas faz `redirected` responder False para
    uma página que redirecionou de fato — a cadeia de hops é a evidência, e é ela
    que decide.
    """
    p = Page(
        requested_url="http://ex.com/blog",
        final_url="http://ex.com/blog/",
        depth=1,
        status_code=200,
        redirect_chain=[(301, "http://ex.com/blog")],
    )
    assert p.redirect_hops == 1
    assert p.redirected is True


def test_verificacao_sem_cookies_nao_feita_nao_aprova_ADS_CRAWL_04(server):
    """ADS-CRAWL-04 tem duas metades, e a segunda ('não depende de estado de
    sessão') custa uma requisição por página que redireciona. Desligá-la por
    padrão é decisão de custo; devolver OK depois é aprovar o que não foi
    observado, com o único vestígio num campo de details que ninguém é obrigado
    a ler."""
    base, routes = server
    routes["/"] = (301, {"Location": "/home"}, "")
    routes["/home"] = (200, HTML, pagina("Home", '<a href="/a">a</a>'))
    routes["/a"] = (200, HTML, pagina("A"))

    check = check_redirect_chain(varrer(base), max_hops=2)

    assert check.status is Status.MISSING
    assert check.passed is False
    assert any("verify_stateless=False" in f for f in check.findings)
    assert check.details["verified_stateless"] is False


def test_site_sem_redirect_nenhum_aprova_sem_recheck(server):
    """A fronteira do teste acima: sem nenhuma cadeia de redirect não há o que
    repetir sem cookies, as duas metades do requisito têm resposta observada, e
    MISSING aqui seria alarme falso."""
    base, routes = server
    routes["/"] = (200, HTML, pagina("Home", '<a href="/a">a</a>'))
    routes["/a"] = (200, HTML, pagina("A"))

    check = check_redirect_chain(varrer(base))

    assert check.status is Status.OK
    assert check.passed is True
    assert check.findings == []


def test_redirect_curto_aprova_ADS_CRAWL_04(server):
    """Aprovar exige ter feito as duas metades: a cadeia é curta E o recheck sem
    cookies chegou ao mesmo destino."""
    base, routes = server
    routes["/"] = (301, {"Location": "/home"}, "")
    routes["/home"] = (200, HTML, pagina("Home", '<a href="/a">a</a>'))
    routes["/a"] = (200, HTML, pagina("A"))

    check = check_redirect_chain(varrer(base), max_hops=2, verify_stateless=True)

    assert check.status is Status.OK
    assert check.details["max_hops_seen"] == 1
    assert check.details["verified_stateless"] is True


# --------------------------------------------------------------------------- #
# ADS-CRAWL-05
# --------------------------------------------------------------------------- #


def test_session_id_na_url_reprova_ADS_CRAWL_05(server):
    base, routes = server
    routes["/"] = (200, HTML, pagina("Home", '<a href="/perfil?PHPSESSID=ab12">p</a>'))
    routes["/perfil?PHPSESSID=ab12"] = (200, HTML, pagina("Perfil"))

    check = check_session_urls(varrer(base))

    assert check.status is Status.FAIL
    assert any("PHPSESSID" in f for f in check.findings)


def test_session_id_em_link_externo_nao_reprova_o_site_auditado(server):
    """O identificador é do outro domínio: reprovar o site por isso produziria
    um achado sobre o qual o dono não pode fazer nada."""
    base, routes = server
    routes["/"] = (
        200,
        HTML,
        f'<html><head><title>Home</title><link rel="canonical" href="{base}/"></head>'
        '<body><a href="http://outro.invalid/perfil?uid=9">ext</a></body></html>',
    )

    check = check_session_urls(varrer(base), verify_two_sessions=True)

    assert check.status is Status.OK
    assert check.details["session_urls"] == []


def test_sem_canonical_o_requisito_fica_MISSING_e_nao_OK(server):
    """Metade do requisito é a comparação com o <link rel=canonical>. Sem
    canonical nenhum ela não pode ser feita, e não-observado nunca é aprovação."""
    base, routes = server
    routes["/"] = (200, HTML, pagina("Home", '<a href="/a">a</a>'))
    routes["/a"] = (200, HTML, pagina("A"))

    check = check_session_urls(varrer(base))

    assert check.status is Status.MISSING
    assert check.passed is False


def test_sem_pagina_html_legivel_o_requisito_fica_MISSING_e_nao_OK(server):
    """BLOCKER: a guarda do MISSING exigia html_pages não-vazia, então um site
    inteiro em 500 — ou que não serve HTML nenhum — pulava a guarda e SAÍA
    APROVADO sem que uma única comparação fosse feita. Zero página observável é
    menos observação que zero canonicals, não mais."""
    base, routes = server
    routes["/"] = (500, HTML, "<html><body>erro interno</body></html>")

    check = check_session_urls(varrer(base))

    assert check.details["with_canonical"] == 0
    assert check.status is Status.MISSING
    assert check.passed is False
    assert any("no readable HTML page" in f for f in check.findings)

    # Mesma cegueira sem erro nenhum de HTTP: 200 que não é documento HTML.
    routes["/"] = (200, {"Content-Type": "application/pdf"}, "%PDF-1.4 lixo binario")
    check_pdf = check_session_urls(varrer(base))

    assert check_pdf.status is Status.MISSING
    assert check_pdf.passed is False


def test_comparacao_entre_sessoes_nao_feita_nao_aprova_ADS_CRAWL_05(server):
    """A outra metade de ADS-CRAWL-05 é 'canonical estável entre sessões
    independentes'. Sem verify_two_sessions ela não foi medida, e não-medido não
    é aprovação — a mesma regra que faz 'sem canonical' devolver MISSING."""
    base, routes = server
    routes["/"] = (
        200,
        HTML,
        f'<html><head><title>Home</title><link rel="canonical" href="{base}/">'
        '</head><body><a href="/a">a</a></body></html>',
    )
    routes["/a"] = (200, HTML, pagina("A"))

    check = check_session_urls(varrer(base))

    assert check.details["with_canonical"] == 1
    assert check.details["verified_two_sessions"] is False
    assert check.status is Status.MISSING
    assert check.passed is False
    assert any("verify_two_sessions=False" in f for f in check.findings)


def test_canonical_estavel_aprova_ADS_CRAWL_05(server):
    base, routes = server
    routes["/"] = (
        200,
        HTML,
        f'<html><head><title>Home</title><link rel="canonical" href="{base}/">'
        '</head><body><a href="/a">a</a></body></html>',
    )
    routes["/a"] = (
        200,
        HTML,
        f'<html><head><title>A</title><link rel="canonical" href="{base}/a">'
        "</head><body><h1>A</h1></body></html>",
    )

    check = check_session_urls(varrer(base), verify_two_sessions=True)

    assert check.status is Status.OK
    assert check.details["with_canonical"] == 2
    assert check.details["verified_two_sessions"] is True


def test_canonical_que_muda_entre_sessoes_reprova(server):
    """Duas visitas independentes devem receber a mesma URL canônica.

    O servidor carimba no canonical a sessão do visitante: quem chega com o
    cookie do crawl mantém a dele, quem chega sem cookie ganha uma sessão nova a
    cada visita — que é como um site que põe o identificador de sessão na URL se
    comporta. As duas releituras precisam sair com Session nova para ver duas
    sessões diferentes; se carregassem os cookies do crawl, veriam duas vezes a
    mesma e o defeito passaria batido.
    """
    base, routes = server
    novas = []

    def artigo(metodo):
        if "sess=" in cookie_da_requisicao():
            marca = "do-crawl"
        else:
            novas.append(1)
            marca = f"nova{len(novas)}"
        return (
            200,
            HTML,
            f'<html><head><title>Artigo</title>'
            f'<link rel="canonical" href="{base}/artigo-{marca}">'
            "</head><body><h1>Artigo</h1></body></html>",
        )

    routes["/"] = (200, HTML_COM_SESSAO, pagina("Home", '<a href="/artigo">a</a>'))
    routes["/artigo"] = artigo

    r = varrer(base)
    check = check_session_urls(r, verify_two_sessions=True)

    # O crawl leu a página com a sessão dele; as releituras, sem sessão nenhuma.
    assert r.pages[1].canonical == f"{base}/artigo-do-crawl"
    assert novas == [1, 1]
    assert check.status is Status.FAIL
    assert any("differs between two sessions" in f for f in check.findings)


def test_crawl_falho_nao_aprova_nenhum_dos_tres_requisitos():
    """Regra da casa: check que não observou a condição devolve ERROR."""
    r = crawl("http://127.0.0.1:1/", delay=0, timeout=2)
    checks = [check_pages_reachable(r), check_redirect_chain(r), check_session_urls(r)]

    assert [c.status for c in checks] == [Status.ERROR] * 3
    assert all(isinstance(c, CheckResult) and not c.passed for c in checks)


def test_nenhuma_pagina_legivel_nao_aprova_requisito_nenhum(server):
    """Site respondendo 500 em tudo não pode passar em requisito algum.

    `result.pages` fica NÃO vazio — as respostas 500 são páginas — então o guard
    de "nenhuma página buscada" não dispara. O que falta é página LEGÍVEL, e sem
    ela "não há redirect" e "não há id de sessão na URL" são vácuo, não
    observação. Era assim que um site inteiramente quebrado passava em
    ADS-CRAWL-04 e ADS-CRAWL-05.
    """
    base, routes = server
    routes["/"] = (500, {"Content-Type": "text/html"}, "erro")

    resultado = crawl(base + "/", max_pages=3, delay=0)
    assert resultado.html_pages == []

    for check in (check_redirect_chain(resultado), check_session_urls(resultado)):
        assert check.passed is False, f"{check.requirement} aprovou sem observar nada"
        assert check.status is Status.MISSING
        assert any("readable" in f for f in check.findings)

    # E o requisito que DEVE falhar continua falhando, para o teste não passar
    # só porque tudo virou MISSING.
    assert check_pages_reachable(resultado).status is Status.FAIL
