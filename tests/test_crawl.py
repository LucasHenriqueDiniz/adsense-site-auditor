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
    CheckResult,
    CrawlResult,
    Page,
    _resolve_links,
    check_pages_reachable,
    check_redirect_chain,
    check_session_urls,
    crawl,
    has_session_id,
    normalize_url,
    parse_html,
    same_site,
    site_host,
)
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

    # Veio da resposta final, não de 'localhost'. A porta faz parte da identidade
    # do site — dois servidores em portas distintas são dois sites.
    assert r.base_host == f"127.0.0.1:{porta}"
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


def test_delay_de_cortesia_e_o_padrao_e_vale_meio_segundo(server, monkeypatch):
    """O padrão é 0,5s, e a pausa precede cada requisição tirada da fila.

    A versão anterior media relógio: 1,02s de uma suíte de ~7s, o teste mais
    lento dela, para afirmar `0.9 <= decorrido <= 3.0`. Uma janela de 3x não
    prende valor nenhum: medido, `DEFAULT_DELAY 0.5 -> 0.46` sobrevivia, e
    `-> 1.49` só morria porque 2 x 1,49 = 2,98s passava raspando do teto de
    3,0s — o veredito ali era do relógio da máquina, não da constante.
    Trocando `time.sleep` por um registrador, o valor exato vira asserção
    literal e o teste custa zero. Que a pausa é espera de relógio de verdade
    fica com `test_a_pausa_de_cortesia_e_espera_de_relogio_de_verdade`, abaixo.
    """
    base, routes = server
    # Um log só, de requisições e de pausas, para que a ORDEM entre as duas
    # coisas também fique assertada: contar pausas soltas não distingue "pausa
    # antes de cada busca" de "duas pausas no fim do laço".
    eventos = []

    def rota(caminho, cabecalhos, corpo):
        def _rota(_metodo):
            eventos.append(("get", caminho))
            return (200, cabecalhos, corpo)

        return _rota

    routes["/"] = rota("/", HTML, pagina("Home", '<a href="/a">a</a><a href="/b">b</a>'))
    routes["/a"] = rota("/a", HTML, pagina("A"))
    routes["/b"] = rota("/b", HTML, pagina("B"))
    routes["/robots.txt"] = rota("/robots.txt", TEXTO, "User-agent: *\nAllow: /\n")

    monkeypatch.setattr(crawl_mod.time, "sleep", lambda s: eventos.append(("pausa", s)))

    # Sem passar `delay`: o que está sob teste é o PADRÃO. Uma versão anterior
    # passava delay=0.05 explícito e só assertava DEFAULT_DELAY > 0, então
    # trocar o padrão por 0.0 não quebrava nada.
    r = crawl(base + "/")

    assert len(r.pages) == 3
    # 0,5 escrito por extenso, não `DEFAULT_DELAY`: derivar da constante sob
    # teste move os dois lados junto e a asserção não pode falhar.
    assert eventos == [
        ("get", "/"),
        ("get", "/robots.txt"),
        ("pausa", 0.5),
        ("get", "/a"),
        ("pausa", 0.5),
        ("get", "/b"),
    ]


def test_a_pausa_de_cortesia_e_espera_de_relogio_de_verdade(server):
    """A prova grossa de que `crawl` de fato BLOQUEIA entre as requisições.

    O teste acima troca `time.sleep` por um registrador, então sozinho ele
    provaria apenas que o crawler anota a intenção de pausar — um `crawl` que
    guardasse os atrasos numa lista e nunca esperasse passaria por ele. Este
    aqui usa um delay explícito e pequeno, então não é ele que prende o valor do
    padrão: é só o piso de tempo real, por 10% do custo do teste que substituiu.
    """
    base, routes = server
    routes["/"] = (200, HTML, pagina("Home", '<a href="/a">a</a><a href="/b">b</a>'))
    routes["/a"] = (200, HTML, pagina("A"))
    routes["/b"] = (200, HTML, pagina("B"))

    inicio = time.monotonic()
    r = crawl(base + "/", delay=0.05)
    decorrido = time.monotonic() - inicio

    assert len(r.pages) == 3
    # Três buscas, duas pausas entre elas: 0,10s. O piso é literal e existe só
    # para separar "esperou" de "não esperou".
    assert decorrido >= 0.09, f"crawl levou {decorrido:.3f}s — a pausa não bloqueou"


def test_user_agent_padrao_identifica_o_crawler_do_adsense(server):
    """O script forjava um User-Agent de Chrome, o que impede o site de
    identificar e limitar o bot — e pergunta a coisa errada: a auditoria quer
    saber o que o Mediapartners-Google recebe.

    A versão anterior deste teste só comparava DEFAULT_USER_AGENT com ADSENSE_UA,
    duas constantes do mesmo processo. Trocar o padrão de crawl() por um UA de
    Chrome forjado deixava a suíte inteira verde. Agora a asserção é sobre o que
    chegou no servidor.
    """
    base, routes = server
    routes["/"] = (200, HTML, pagina("Home"))

    crawl(base + "/")  # sem passar user_agent: é o padrão que está sob teste

    assert routes.received, "o servidor nao recebeu requisicao nenhuma"
    enviados = {h.get("User-Agent", "") for _m, _p, h in routes.received}
    assert enviados, "nenhum User-Agent chegou"
    for ua in enviados:
        assert "Mediapartners-Google" in ua, f"UA enviado nao identifica o crawler: {ua!r}"
        assert "Chrome" not in ua, f"UA forjado de navegador: {ua!r}"


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


def test_reconferencia_sem_cookies_usa_o_user_agent_do_crawl(server):
    """A verificação re-pedia com o UA padrão, não com o que o crawl usou.

    Num site que varia o redirect por agente, a URL final da reconferência
    diferia da do crawl e o relatório dizia "redirect depends on session state"
    quando a única coisa que mudou foi o agente.
    """
    base, routes = server
    routes["/"] = (200, HTML, "<html><body><a href='/a'>a</a></body></html>")
    routes["/a"] = (301, {**HTML, "Location": base + "/b"}, "")
    routes["/b"] = (200, HTML, "<html><body>b</body></html>")

    resultado = crawl(base + "/", max_depth=1, delay=0, user_agent="Agente-De-Teste/1.0")
    assert resultado.user_agent == "Agente-De-Teste/1.0"

    routes.received.clear()
    check_redirect_chain(resultado, verify_stateless=True)

    agentes = {cabecalhos.get("User-Agent") for _m, _c, cabecalhos in routes.received}
    assert agentes == {"Agente-De-Teste/1.0"}


def test_porta_faz_parte_da_identidade_do_site(server, outro_servidor):
    """Um crawl não pode sair do servidor que está auditando.

    `site_host` usava `urlparse().hostname`, que descarta a porta, então dois
    servidores distintos em 127.0.0.1 eram o mesmo site: o crawl seguia o link,
    baixava as páginas do estranho e as reportava como suas — sob um robots.txt
    que nunca foi lido, porque robots veio da primeira origem.
    """
    base, routes = server
    vizinho, rotas_vizinho = outro_servidor
    routes["/"] = (200, HTML, pagina("Home", f'<a href="{vizinho}/secreto">v</a>'))
    rotas_vizinho["/secreto"] = (200, HTML, pagina("Secreto"))

    r = crawl(base + "/", max_depth=2, delay=0, respect_robots=False)

    assert [p.title for p in r.pages] == ["Home"]
    assert rotas_vizinho.received == []
    assert any("/secreto" in url for url in r.off_site)


def test_porta_default_explicita_nao_divide_o_site():
    """`https://ex.com` e `https://ex.com:443` são um endereço escrito duas vezes."""
    assert same_site("https://ex.com:443/a", "https://ex.com/") is True
    assert same_site("http://ex.com:80/a", "http://ex.com/") is True
    assert same_site("https://ex.com:8080/a", "https://ex.com/") is False


def test_porta_malformada_nao_quebra_a_identidade():
    """urlparse().port levanta ValueError; um href lixo é off-site, não um crash."""
    assert same_site("http://ex.com:lixo/a", "http://ex.com/") is False


def test_href_malformado_na_pagina_nao_derruba_o_crawl(server):
    """A URL do operador estava perfeita; bastava UM href malformado no HTML.
    `urlsplit` levanta ValueError num host entre colchetes inválido, e isso
    escapava de `_resolve_links` como crash em vez de virar link ignorado."""
    base, routes = server
    routes["/"] = (200, HTML, pagina("Home", '<a href="http://[::1:99999]/x">ruim</a>'
                                             '<a href="/ok">bom</a>'))
    routes["/ok"] = (200, HTML, pagina("Ok"))

    r = crawl(base + "/", max_depth=2, delay=0, respect_robots=False)

    assert [p.title for p in r.pages] == ["Home", "Ok"]


def test_identidade_de_site_e_de_url_nao_discordam_sobre_www():
    """`same_site` fundia `www.` e `normalize_url` não, então um link na forma
    `www.` passava no filtro de mesmo-site, ganhava identidade DIFERENTE e era
    buscado de novo — e o corpus ficava com um documento sob dois nomes."""
    assert same_site("http://ex.com/a", "http://www.ex.com/") is True
    assert normalize_url("http://ex.com/a") == normalize_url("http://www.ex.com/a")


def test_porta_default_nao_depende_do_esquema():
    """`http://ex.com:443/` é o que um proxy que termina TLS emite quando reporta
    SERVER_PORT=443 sem flag de HTTPS. Comparar a porta com o default do esquema
    da própria URL partia um mesmo endereço em duas identidades, e a página
    declarada nessa forma deixava de ser requisitada."""
    escritas = ("http://ex.com:443/", "https://ex.com/", "https://ex.com:443/",
                "http://ex.com:80/", "http://ex.com/")
    assert len({site_host(u) for u in escritas}) == 1


def test_seed_de_fora_do_site_e_recusada(server, outro_servidor):
    """As seeds entravam na fila sem checagem de mesmo-site e eram casadas contra
    o robots.txt do site auditado — o único ponto em que esta ferramenta buscaria
    a URL de um terceiro sob um arquivo que nunca foi lido para ele."""
    base, routes = server
    vizinho, rotas_vizinho = outro_servidor
    routes["/"] = (200, HTML, pagina("Home"))
    rotas_vizinho["/secreto"] = (200, HTML, pagina("Secreto"))

    r = crawl(base + "/", delay=0, respect_robots=False,
              extra_seeds=[vizinho + "/secreto"])

    assert rotas_vizinho.received == []
    assert [p.title for p in r.pages] == ["Home"]
    assert any("/secreto" in url for url in r.off_site)


def test_crawl_truncado_nao_aprova_ADS_CRAWL_01(server):
    """`[PASS] pages reachable` sobre um crawl cortado no teto: três 500 logo
    depois do limite ficavam invisíveis, e subir --max-pages virava FAIL no mesmo
    site."""
    base, routes = server
    links = "".join(f'<a href="/p{i}">p{i}</a>' for i in range(6))
    routes["/"] = (200, HTML, pagina("Home", links))
    for i in range(6):
        routes[f"/p{i}"] = (200, HTML, pagina(f"P{i}"))

    r = crawl(base + "/", max_depth=2, max_pages=3, delay=0, respect_robots=False)
    check = check_pages_reachable(r)

    # MISSING: as paginas depois do teto nao foram observadas.
    assert check.status is Status.MISSING
    assert any("max_pages" in f for f in check.findings)
    assert check.status.is_bad


def test_url_com_fragmento_e_host_quebrado_nao_levanta():
    """`urldefrag` parseia por dentro, então levantava o mesmo ValueError de host
    entre colchetes que `split_url`/`join_url` foram criados para fechar — e
    sobreviveu à primeira varredura em dois lugares: no `normalize_url`, que é a
    única função de identidade, e no `_resolve_links`, que chega nele com href
    cru sempre que um `<base href>` não pôde ser resolvido."""
    assert normalize_url("http://[abc/#y")  # não levanta
    nao_http = []
    assert _resolve_links("", ["http://[abc/#y", "/sobre"], nao_http) == []
    # E o href relativo que não pôde ser resolvido é registrado em vez de sumir.
    assert "/sobre" in nao_http


def test_ipv6_mantem_os_colchetes_na_identidade():
    """`hostname` devolve o IPv6 sem colchetes, então `[::1]:9411` e
    `[::1:9411]` viravam os dois `::1:9411`: o endereço de um terceiro era
    varrido como página do site auditado, sob um robots.txt nunca lido para ele."""
    assert site_host("http://[::1]:9411/") != site_host("http://[::1:9411]/")
    assert normalize_url("http://[::1]:8000/a") == "http://[::1]:8000/a"
    # E a identidade continua sendo uma URL, então é idempotente.
    uma_vez = normalize_url("http://[::1]:8000/a")
    assert normalize_url(uma_vez) == uma_vez


def test_ponto_final_no_host_e_o_mesmo_site():
    """`localhost.` é o mesmo FQDN escrito de forma absoluta e o servidor
    responde os dois. Tratar como outro site mandava todo link escrito assim
    para `off_site` — o defeito "varreu 1 URL e não avisou nada"."""
    assert same_site("http://localhost.:19405/x", "http://localhost:19405/") is True


def test_idn_e_punycode_sao_um_site():
    """requests codifica em IDNA na hora do fetch, então as duas grafias chegam
    no mesmo servidor e não podem ser dois sites."""
    assert same_site("http://пример.рф/a", "http://xn--e1afmkfd.xn--p1ai/") is True


def test_urls_impossiveis_distintas_nao_colapsam_numa_identidade():
    """Renderizar a identidade vazia em `http:///<path>` dava a toda URL
    impossível a mesma identidade, então dois links externos distintos
    colapsavam em um e o segundo nunca era escaneado nem reportado."""
    a, b = "http://parceiro-a.example:zz/promo", "http://parceiro-b.example:zz/promo"
    assert normalize_url(a) != normalize_url(b)


def test_credencial_raspada_da_pagina_nao_viaja(server):
    """O crawler mandava o `user:pass@` que a própria página publicava, o 401
    nunca acontecia, e "legível publicamente, sem autenticação" passava sobre
    uma página que nenhum visitante anônimo abre."""
    base, routes = server
    porta = base.rsplit(":", 1)[1]
    com_credencial = f"http://u:p@127.0.0.1:{porta}/privado"
    routes["/"] = (200, HTML, pagina("Home", f'<a href="{com_credencial}">p</a>'))
    routes["/privado"] = (401, {**HTML, "WWW-Authenticate": 'Basic realm="x"'}, "")

    r = crawl(base + "/", max_depth=2, delay=0, respect_robots=False)

    assert all("@" not in p.requested_url for p in r.pages)
    check = check_pages_reachable(r)
    assert check.status is Status.FAIL
    assert any("not publicly readable" in f for f in check.findings)


def test_head_sem_fechamento_nao_zera_a_contagem_de_palavras():
    """O HTML5 permite omitir `</head>` e o HTMLParser não sintetiza, então o
    contador nunca voltava a zero e uma página de 300 palavras era reportada
    com 0 palavras visíveis. Os outros dois parsers do pacote acertam."""
    html = "<!doctype html><html><head><title>T</title><body><p>" + "palavra " * 300 + "</p>"
    p = parse_html(html)
    assert p.word_count >= 300
    assert "T" not in p.text  # e o título continua fora da contagem


def test_nenhuma_pagina_buscada_nao_aprova_nenhum_dos_tres_checks():
    """Forçar `Status.ERROR` para OK no ramo `if not result.pages` sobrevivia nas
    TRÊS checagens: as três linhas imprimiriam PASS ao lado de "no pages were
    fetched" e o `crawl_site` sairia 0.

    O estado é alcançável por quem monta um `CrawlResult` — `crawl()` sempre
    registra ao menos a raiz, inclusive no caminho de erro e no de robots
    bloqueando, que dá uma página e MISSING, não zero. A guarda existe para o
    chamador, e é ele que este teste representa.
    """
    vazio = CrawlResult(start_url="http://exemplo.com/")
    assert vazio.status is Status.OK  # o crawl "correu"; o que falta é evidência
    assert vazio.pages == []

    for check in (check_pages_reachable(vazio),
                  check_redirect_chain(vazio),
                  check_session_urls(vazio)):
        assert check.status is Status.ERROR, check.requirement
        assert check.passed is False, check.requirement
        assert any("no pages were fetched" in f for f in check.findings), check.requirement


def test_erro_de_transporte_numa_pagina_reprova_ADS_CRAWL_01(server):
    """Uma página cujo GET não completou escalava para ERROR e nada olhava: sem
    a escalação, um link que não resolve conta como página alcançável."""
    base, routes = server
    routes["/"] = (200, HTML, pagina("Home", '<a href="http://127.0.0.1:1/morto">x</a>'
                                             '<a href="/ok">ok</a>'))
    routes["/ok"] = (200, HTML, pagina("Ok"))

    r = crawl(base + "/", max_depth=2, delay=0, respect_robots=False,
              extra_seeds=[base + "/ok"])
    r.pages.append(_pagina_com_erro(base))

    check = check_pages_reachable(r)
    assert check.status is Status.ERROR
    assert any("request failed" in f for f in check.findings)


def _pagina_com_erro(base):
    return Page(requested_url=base + "/timeout", final_url=base + "/timeout",
                depth=1, error="ReadTimeout: simulado")


def test_reconferencia_sem_cookies_que_falha_e_ERROR(server):
    """`verify_stateless` re-pede a página com sessão nova; se esse pedido falha
    a comparação não aconteceu. Sem a escalação, o check aprova sem ter
    comparado nada."""
    base, routes = server
    routes["/"] = (200, HTML, pagina("Home", '<a href="/a">a</a>'))
    routes["/a"] = (301, {**HTML, "Location": base + "/b"}, "")
    routes["/b"] = (200, HTML, pagina("B"))

    r = crawl(base + "/", max_depth=1, delay=0, respect_robots=False)
    # A página que redirecionou passa a apontar para um host morto: a
    # reconferência sem cookies vai falhar no transporte.
    for p in r.pages:
        if p.redirect_hops:
            p.requested_url = "http://127.0.0.1:1/a"

    check = check_redirect_chain(r, verify_stateless=True, timeout=1)
    assert check.status is Status.ERROR
    assert any("cookie-less re-request failed" in f for f in check.findings)


def test_canonical_que_aponta_para_outro_lugar_e_registrado_como_INFO(server):
    """Não é reprovação — canonical apontando para outro lugar é normal em URL
    filtrada — mas é observação registrada, e sem a escalação ela some do status
    da linha e o relatório fica indistinguível de um site sem nada a notar."""
    base, routes = server
    corpo = (f'<html><head><link rel="canonical" href="{base}/outro"></head>'
             "<body><p>conteudo</p></body></html>")
    routes["/"] = (200, HTML, corpo)

    r = crawl(base + "/", max_depth=1, delay=0, respect_robots=False)
    check = check_session_urls(r, verify_two_sessions=True, timeout=2)

    assert any("canonical points to" in f for f in check.findings)
    assert check.status is not Status.OK


def test_comparacao_de_duas_sessoes_que_falha_e_ERROR(server):
    """A comparação de canonical entre duas visitas independentes: se o re-pedido
    não completa, comparação nenhuma aconteceu. Sem a escalação o check aprova
    sobre uma verificação que não rodou."""
    base, routes = server
    corpo = (f'<html><head><link rel="canonical" href="{base}/"></head>'
             "<body><p>conteudo</p></body></html>")
    routes["/"] = (200, HTML, corpo)

    r = crawl(base + "/", max_depth=1, delay=0, respect_robots=False)
    # A página existe e declara canonical; o re-pedido vai para um host morto.
    for p in r.pages:
        p.final_url = "http://127.0.0.1:1/"

    check = check_session_urls(r, verify_two_sessions=True, timeout=1)
    assert check.status is Status.ERROR
    assert any("re-request for canonical comparison failed" in f for f in check.findings)


def test_a_profundidade_padrao_do_crawl_e_2(server):
    """DEFAULT_MAX_DEPTH pelo valor: uma corrente de quatro níveis para no 2."""
    base, routes = server
    routes["/"] = (200, HTML, pagina("N0", '<a href="/n1">n1</a>'))
    routes["/n1"] = (200, HTML, pagina("N1", '<a href="/n2">n2</a>'))
    routes["/n2"] = (200, HTML, pagina("N2", '<a href="/n3">n3</a>'))
    routes["/n3"] = (200, HTML, pagina("N3"))

    r = crawl(base + "/", delay=0, respect_robots=False)

    assert sorted(p.depth for p in r.pages) == [0, 1, 2]
    assert [p.title for p in r.pages] == ["N0", "N1", "N2"]


def test_o_teto_padrao_de_paginas_do_modulo_e_100(server):
    """DEFAULT_MAX_PAGES pelo valor. O CLI passa 50 por conta própria; este é o
    padrão da biblioteca, que ninguém observava."""
    base, routes = server
    links = "".join(f'<a href="/p{i}">p{i}</a>' for i in range(120))
    routes["/"] = (200, HTML, pagina("Home", links))
    for i in range(120):
        routes[f"/p{i}"] = (200, HTML, pagina(f"P{i}"))

    r = crawl(base + "/", delay=0, respect_robots=False)

    assert len(r.pages) == 100
    assert r.stopped_reason == "stopped at max_pages=100"


def test_exatamente_dois_saltos_de_redirect_ainda_passam(server):
    """`redirect_hops > max_hops` (2). Os testes existentes ficavam de um lado e
    de outro da fronteira, nunca em cima dela."""
    base, routes = server
    routes["/"] = (200, HTML, pagina("Home", '<a href="/r1">r</a>'))
    routes["/r1"] = (302, {**HTML, "Location": base + "/r2"}, "")
    routes["/r2"] = (302, {**HTML, "Location": base + "/fim"}, "")
    routes["/fim"] = (200, HTML, pagina("Fim"))

    r = crawl(base + "/", max_depth=1, delay=0, respect_robots=False)
    dois = check_redirect_chain(r, max_hops=2)
    um = check_redirect_chain(r, max_hops=1)

    assert not any("redirect hops" in f for f in dois.findings)
    assert any("2 redirect hops (limit 1)" in f for f in um.findings)


def test_pagina_com_exatamente_400_nao_e_alcancavel(server):
    """`page.status_code >= 400` no check de alcançabilidade: 399 passa, 400 não."""
    base, routes = server
    routes["/"] = (200, HTML, pagina("Home", '<a href="/x">x</a>'))
    for codigo, esperado in ((399, Status.OK), (400, Status.FAIL)):
        routes["/x"] = (codigo, HTML, "")
        r = crawl(base + "/", max_depth=1, delay=0, respect_robots=False)
        assert check_pages_reachable(r).status is esperado, codigo


# --------------------------------------------------------------------------
# Conteúdo das coleções de configuração. Cada uma leva TRÊS asserções:
#
#   1. a contagem literal, que pega a remoção de uma entrada;
#   2. a iteração sobre uma lista LITERAL, escrita aqui, que pega a troca de
#      uma entrada por outra;
#   3. a lista inversa, que pega o outro lado da troca e a entrada a mais.
#
# Iterar o próprio conjunto seria auto-referencial: `_looks_like_asset` É
# `path.endswith(_ASSET_SUFFIXES)`, então `x in S for x in S` move o caso de
# teste junto com a entrada trocada. Medido: trocar `.css` por `.html` aqui
# faz `_looks_like_asset("http://e.test/a.html")` responder True — o crawler
# classifica toda página estática como binário e para de rastrear o site — e
# os 521 testes continuavam passando.
# --------------------------------------------------------------------------

# Os cinquenta sufixos que nunca são um documento HTML, na ordem em que o
# módulo os agrupa: documentos de escritório, arquivos compactados, imagens,
# mídia, recursos de página e fontes.
SUFIXOS_DE_ASSET = (
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".odt", ".csv",
    ".zip", ".gz", ".tgz", ".tar", ".rar", ".7z", ".dmg", ".exe", ".apk", ".pkg",
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico", ".bmp", ".avif",
    ".mp3", ".mp4", ".m4a", ".avi", ".mov", ".wmv", ".webm", ".ogg", ".wav",
    ".css", ".js", ".mjs", ".json", ".xml", ".rss", ".atom", ".txt",
    ".woff", ".woff2", ".ttf", ".eot", ".otf",
)

# As vinte e sete extensões que fazem do último segmento um arquivo, e não um
# diretório. Guardadas sem ponto, ao contrário das de asset.
SUFIXOS_DE_DOCUMENTO = (
    "html", "htm", "xhtml", "shtml", "php", "php3", "php4", "php5", "phtml",
    "asp", "aspx", "jsp", "jspx", "cgi", "pl", "py", "rb", "do", "action",
    "cfm", "xml", "json", "txt", "md", "rss", "atom", "pdf",
)

# As sete tags cujo conteúdo não é texto de página.
TAGS_NAO_TEXTUAIS = ("script", "style", "noscript", "template", "svg", "head", "title")


def test_todo_sufixo_de_asset_e_reconhecido_como_asset():
    from adsense_checks.crawl import _ASSET_SUFFIXES, _looks_like_asset

    assert len(_ASSET_SUFFIXES) == 50
    # A forma é uma invariante de TODA entrada, então esta metade itera a FONTE:
    # a propriedade não depende de quais entradas existem, e afirmá-la sobre o
    # literal daqui seria afirmá-la sobre o próprio teste. Um `.png` guardado
    # como `png` continua casando `arquivo.png` por sufixo, e passa a casar
    # `/o-formato-png` também — uma página vira binário e o crawler para nela.
    for sufixo in _ASSET_SUFFIXES:
        assert sufixo.startswith(".") and sufixo == sufixo.lower(), sufixo

    # A pertinência é o contrário: itera o LITERAL, senão trocar uma entrada
    # move o caso de teste junto com ela.
    for sufixo in SUFIXOS_DE_ASSET:
        assert _looks_like_asset(f"https://ex.com/arquivo{sufixo}"), sufixo
        # E maiúsculas na URL não escapam do filtro.
        assert _looks_like_asset(f"https://ex.com/A{sufixo.upper()}"), sufixo

    # O inverso, e é o lado que fecha o site inteiro quando erra: uma extensão
    # de página HTML aqui faz o crawler tratar o site estático como um monte de
    # binários e parar na home. Toda extensão que `looks_like_document` conhece
    # e que não é asset tem de continuar não sendo.
    paginas = ("html", "htm", "xhtml", "shtml", "php", "asp", "aspx", "jsp",
               "cgi", "do", "action", "md")
    for ext in paginas:
        assert not _looks_like_asset(f"https://ex.com/pagina.{ext}"), ext
    # E uma URL sem extensão nenhuma, que é a forma da maioria das páginas.
    assert not _looks_like_asset("https://ex.com/sobre")
    assert not _looks_like_asset("https://ex.com/blog/como-escolher-racao")


def test_todo_sufixo_de_documento_faz_o_ultimo_segmento_ser_arquivo():
    from adsense_checks.crawl import _DOCUMENT_SUFFIXES, looks_like_document

    assert len(_DOCUMENT_SUFFIXES) == 27
    # Invariante de toda entrada: itera a fonte, pelo mesmo motivo do teste acima.
    for sufixo in _DOCUMENT_SUFFIXES:
        assert "." not in sufixo, sufixo  # a lista guarda a extensão sem ponto

    for sufixo in SUFIXOS_DE_DOCUMENTO:
        assert looks_like_document(f"/pasta/pagina.{sufixo}"), sufixo
        assert looks_like_document(f"/pasta/PAGINA.{sufixo.upper()}"), sufixo

    # O inverso: chamar de documento o que é diretório encolhe a base auditada
    # até a origem, e a instalação em subdiretório volta a ser julgada contra o
    # sitemap do domínio inteiro. Uma extensão de imagem ou de recurso aqui faz
    # exatamente isso com `/logo.png` e `/tema.css`.
    for ext in ("png", "jpg", "css", "js", "woff2", "zip", "mp4", "old", "0"):
        assert not looks_like_document(f"/pasta/arquivo.{ext}"), ext
    # E um diretório com ponto no nome continua sendo diretório.
    assert not looks_like_document("/v1.0")
    assert not looks_like_document("/blog.old")


def test_toda_tag_nao_textual_tem_o_conteudo_excluido_da_contagem():
    from adsense_checks.crawl import _NON_TEXT_TAGS

    assert len(_NON_TEXT_TAGS) == 7
    for tag in TAGS_NAO_TEXTUAIS:
        # `head` entra na lista como as outras: o reset em `<body>` já rodou
        # quando um `<head>` aparece depois dele, então o contador funciona.
        html = f"<html><body><p>visivel</p><{tag}>escondido</{tag}></body></html>"
        p = parse_html(html)
        assert "escondido" not in p.text, tag
        assert "visivel" in p.text, tag

    # O inverso: nada além destas é excluído. Uma tag de conteúdo aqui derruba
    # a contagem de palavras da página e reporta como fina uma página cheia.
    for tag in ("article", "aside", "div", "footer", "header", "main",
                "nav", "p", "section", "td"):
        html = f"<html><body><p>visivel</p><{tag}>presente</{tag}></body></html>"
        assert "presente" in parse_html(html).text, tag
