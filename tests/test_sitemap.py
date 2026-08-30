"""Testes de descoberta e parsing de sitemap (ADS-CRAWL-07).

Cada teste de regressão nomeia o defeito que impede. Os três que motivaram o
módulo estão em check_technical.py:

  * a contagem casava o namespace literal sitemaps.org/0.9;
  * um <sitemapindex> tinha seus <loc> contados como URLs de página;
  * a descoberta era fixa em /sitemap.xml e ignorava a diretiva Sitemap: do
    robots.txt que o próprio script já tinha baixado.

Os testes de rede usam a fixture `server` (servidor HTTP real). Mockar requests
reproduziria a suposição errada em vez do protocolo.
"""

import threading
from contextlib import contextmanager
from http.server import HTTPServer

# O handler do conftest é reaproveitado para o segundo servidor (ramo cross-host)
# em vez de haver duas versões do mesmo servidor de teste no repositório.
from conftest import _Handler

# parse_robots é reexportado por sitemap de propósito: o módulo já resolveu onde
# o parser de robots mora, e o teste não deve duplicar essa decisão.
from adsense_checks.sitemap import (
    MAX_INDEX_DEPTH,
    SitemapResult,
    check_sitemap,
    discover_sitemap_urls,
    parse_robots,
    parse_sitemap,
    verify_sample_urls,
)
from adsense_checks.status import Status

NS_09 = "http://www.sitemaps.org/schemas/sitemap/0.9"
NS_084 = "http://www.google.com/schemas/sitemap/0.84"


def urlset(*urls: str, ns: str | None = NS_09) -> str:
    attr = f' xmlns="{ns}"' if ns else ""
    corpo = "".join(f"<url><loc>{u}</loc></url>" for u in urls)
    return f'<?xml version="1.0" encoding="UTF-8"?><urlset{attr}>{corpo}</urlset>'


def sitemapindex(*urls: str, ns: str | None = NS_09) -> str:
    attr = f' xmlns="{ns}"' if ns else ""
    corpo = "".join(f"<sitemap><loc>{u}</loc></sitemap>" for u in urls)
    return f'<?xml version="1.0" encoding="UTF-8"?><sitemapindex{attr}>{corpo}</sitemapindex>'


XML = {"Content-Type": "application/xml"}
TXT = {"Content-Type": "text/plain"}
HTML = {"Content-Type": "text/html; charset=UTF-8"}


@contextmanager
def outro_servidor():
    """Sobe um segundo servidor e devolve (base_url, rotas), como a fixture.

    A fixture `server` dá um servidor só, e o ramo cross-host precisa de dois
    netlocs distintos. Portas diferentes bastam: netloc inclui a porta, então
    não é preciso DNS nem depender de 'localhost' resolver para 127.0.0.1.
    """
    rotas: dict = {}
    handler = type("H", (_Handler,), {"routes": rotas})
    httpd = HTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}", rotas
    finally:
        httpd.shutdown()
        httpd.server_close()


# --------------------------------------------------------------------------
# Parsing: namespace
# --------------------------------------------------------------------------


def test_sitemap_sem_namespace_conta_as_urls():
    """Defeito (a): gerador caseiro sem xmlns virava 'found but contains no URLs'.

    Reprodução do relatório: '<urlset><url><loc>.../a</loc></url>
    <url><loc>.../b</loc></url></urlset>' era reportado com url_count: 0.
    """
    doc = parse_sitemap(urlset("https://exemplo.com/a", "https://exemplo.com/b", ns=None))
    assert doc.kind == "urlset"
    assert doc.urls == ["https://exemplo.com/a", "https://exemplo.com/b"]
    assert doc.status is Status.OK


def test_namespace_antigo_0_84_conta_as_urls():
    """Mesmo defeito com o namespace google.com/schemas/sitemap/0.84."""
    doc = parse_sitemap(urlset("https://exemplo.com/a", ns=NS_084))
    assert doc.urls == ["https://exemplo.com/a"]
    assert doc.status is Status.OK


def test_namespace_moderno_continua_funcionando():
    doc = parse_sitemap(urlset("https://exemplo.com/a", "https://exemplo.com/b"))
    assert len(doc.urls) == 2


def test_image_loc_nao_infla_a_contagem_de_urls():
    """A correção sugerida no relatório (casar todo <loc> por nome local com
    root.iter()) criaria um defeito novo: a extensão de imagens põe um
    <image:loc> dentro de cada <url>, então um sitemap de 100 páginas com uma
    imagem cada reportaria 200. O casamento aqui é estrutural.
    """
    xml = f"""<urlset xmlns="{NS_09}"
        xmlns:image="http://www.google.com/schemas/sitemap-image/1.1">
      <url>
        <loc>https://exemplo.com/p1</loc>
        <image:image><image:loc>https://exemplo.com/i1.jpg</image:loc></image:image>
      </url>
    </urlset>"""
    doc = parse_sitemap(xml)
    assert doc.urls == ["https://exemplo.com/p1"]


# --------------------------------------------------------------------------
# Parsing: índice x urlset
# --------------------------------------------------------------------------


def test_sitemapindex_nao_e_contado_como_urls_de_pagina():
    """Defeito (b): os <loc> do índice viravam url_count."""
    doc = parse_sitemap(
        sitemapindex("https://exemplo.com/post-sitemap.xml", "https://exemplo.com/page-sitemap.xml")
    )
    assert doc.kind == "sitemapindex"
    assert doc.urls == []
    assert len(doc.children) == 2


# --------------------------------------------------------------------------
# Parsing: corpos que não são sitemap
# --------------------------------------------------------------------------


def test_xml_malformado_e_ERROR_e_nunca_zero_urls():
    doc = parse_sitemap("<urlset><url><loc>https://exemplo.com/a</loc></urlset>")
    assert doc.status is Status.ERROR
    assert doc.kind == "unknown"
    assert "parse error" in doc.reason.lower()


def test_html_servido_no_lugar_do_sitemap_e_MISSING():
    """Catch-all de SPA ('/* -> /index.html') devolve 200 com o app shell para
    /sitemap.xml. Isso era contado como sitemap encontrado."""
    html = "<!doctype html><html><head><title>my app</title></head><body></body></html>"
    doc = parse_sitemap(html, content_type="text/html; charset=utf-8")
    assert doc.status is Status.MISSING
    assert doc.is_sitemap is False


def test_urlset_valido_servido_como_text_html_nao_e_MISSING():
    """O cabeçalho não é o contrato; o corpo é.

    Sitemap gerado por script sem header('Content-Type: application/xml') sai
    como text/html (o default do PHP). Recusá-lo pelo cabeçalho é declarar
    ausência sem ter olhado o corpo que acabou de chegar — o mesmo pecado dos
    defeitos que o módulo existe para matar, e uma afirmação falsa ("served
    HTML") sobre um documento que parseia.
    """
    doc = parse_sitemap(
        urlset("https://exemplo.com/a", "https://exemplo.com/b"),
        content_type="text/html; charset=utf-8",
    )
    assert doc.kind == "urlset"
    assert doc.urls == ["https://exemplo.com/a", "https://exemplo.com/b"]
    assert doc.status is Status.OK


def test_html_sem_doctype_declarado_como_html_continua_MISSING():
    """Metade do content-type, que a correção acima não pode levar junto: um
    corpo que se recusou a ser XML e que o servidor declarou como HTML é
    ausência (página de erro do tema), não 'não consegui ler'."""
    doc = parse_sitemap("<p>Sitemap not found<br>see the docs</p>", content_type="text/html")
    assert doc.status is Status.MISSING
    assert "html" in doc.reason.lower()


def test_shell_de_spa_declarado_como_xml_continua_MISSING():
    """Metade do sniff do corpo: o servidor mente na outra direção também."""
    shell = "<!doctype html><html><head><title>my app</title></head><body></body></html>"
    doc = parse_sitemap(shell, content_type="application/xml")
    assert doc.status is Status.MISSING
    assert doc.is_sitemap is False


def test_urlset_valido_e_vazio_e_WARNING_e_nao_OK():
    doc = parse_sitemap(urlset())
    assert doc.kind == "urlset"
    assert doc.status is Status.WARNING


def test_raiz_que_nao_e_sitemap_e_MISSING():
    doc = parse_sitemap('<rss version="2.0"><channel></channel></rss>')
    assert doc.status is Status.MISSING
    assert "<rss>" in doc.reason


def test_corpo_vazio_e_MISSING():
    assert parse_sitemap("   ").status is Status.MISSING


def test_doctype_e_recusado_sem_parsear():
    """Sitemap não tem DOCTYPE; recusar é também a defesa mais barata contra
    expansão de entidades, que o xml.etree não trata em entrada não confiável."""
    bomba = '<!DOCTYPE lolz [<!ENTITY lol "lol">]><urlset><url><loc>x</loc></url></urlset>'
    doc = parse_sitemap(bomba, content_type="application/xml")
    assert doc.status is Status.ERROR
    assert doc.is_sitemap is False


# --------------------------------------------------------------------------
# Descoberta (função pura)
# --------------------------------------------------------------------------


def test_descoberta_poe_o_robots_antes_dos_caminhos_convencionais():
    r = parse_robots("Sitemap: https://exemplo.com/sitemap_index.xml\n")
    candidatos = discover_sitemap_urls("https://exemplo.com", r)
    assert candidatos[0] == "https://exemplo.com/sitemap_index.xml"
    assert "https://exemplo.com/sitemap.xml" in candidatos


def test_diretiva_sitemap_relativa_e_resolvida_contra_a_base():
    r = parse_robots("Sitemap: /mapa.xml\n")
    assert discover_sitemap_urls("https://exemplo.com", r)[0] == "https://exemplo.com/mapa.xml"


def test_descoberta_sem_robots_ainda_tenta_os_convencionais():
    candidatos = discover_sitemap_urls("https://exemplo.com", None)
    assert candidatos[0] == "https://exemplo.com/sitemap.xml"
    assert len(candidatos) == len(set(candidatos))


# --------------------------------------------------------------------------
# Descoberta (contra servidor real)
# --------------------------------------------------------------------------


def test_sitemap_declarado_no_robots_e_encontrado_com_sitemap_xml_em_404(server):
    """Defeito (c), o caso WordPress/Yoast: /sitemap.xml devolve 404 e o
    robots.txt declara /sitemap_index.xml. O script reportava 'no sitemap'.
    """
    base, rotas = server
    robots_txt = f"User-agent: *\nAllow: /\nSitemap: {base}/sitemap_index.xml\n"
    rotas["/robots.txt"] = (200, TXT, robots_txt)
    rotas["/sitemap_index.xml"] = (200, XML, urlset(f"{base}/a", f"{base}/b"))

    r = check_sitemap(base)
    assert r.found is True
    assert r.discovered_via == "robots.txt"
    assert r.sitemap_url == f"{base}/sitemap_index.xml"
    assert r.url_count == 2
    assert r.status is Status.OK


def test_caminho_convencional_usado_quando_o_robots_nao_declara(server):
    base, rotas = server
    rotas["/robots.txt"] = (200, TXT, "User-agent: *\nAllow: /\n")
    rotas["/sitemap.xml"] = (200, XML, urlset(f"{base}/a"))

    r = check_sitemap(base)
    assert r.discovered_via == "conventional path"
    assert r.url_count == 1


def test_robots_ja_baixado_nao_e_baixado_de_novo(server):
    """O defeito original era um script que tinha o robots.txt em mãos e mesmo
    assim ignorava a diretiva. Quem já baixou passa o objeto."""
    base, rotas = server
    pedidos: list[str] = []

    def robots_route(metodo):
        pedidos.append(metodo)
        return (200, TXT, "")

    rotas["/robots.txt"] = robots_route
    rotas["/mapa.xml"] = (200, XML, urlset(f"{base}/a"))

    r = check_sitemap(base, robots=parse_robots(f"Sitemap: {base}/mapa.xml\n"))
    assert pedidos == []  # nenhuma requisição a /robots.txt
    assert r.sitemap_url == f"{base}/mapa.xml"
    assert r.discovered_via == "robots.txt"


# --------------------------------------------------------------------------
# Recursão em índices
# --------------------------------------------------------------------------


def test_indice_e_seguido_e_url_count_soma_os_filhos(server):
    """Defeito (b) ponta a ponta: o índice com 2 filhos reportava url_count: 2
    para um site que podia ter 300 mil URLs. Agora 2 é child_sitemap_count."""
    base, rotas = server
    rotas["/sitemap.xml"] = (
        200,
        XML,
        sitemapindex(f"{base}/post-sitemap.xml", f"{base}/page-sitemap.xml"),
    )
    rotas["/post-sitemap.xml"] = (200, XML, urlset(f"{base}/p1", f"{base}/p2", f"{base}/p3"))
    rotas["/page-sitemap.xml"] = (200, XML, urlset(f"{base}/a1", f"{base}/a2"))

    r = check_sitemap(base)
    assert r.kind == "sitemapindex"
    assert r.url_count == 5
    assert r.child_sitemap_count == 2
    assert r.url_count_is_lower_bound is False
    assert r.status is Status.OK


def test_limite_de_filhos_marca_a_contagem_como_parcial(server):
    """Um número parcial que não se anuncia é a mesma mentira em outro lugar."""
    base, rotas = server
    rotas["/sitemap.xml"] = (200, XML, sitemapindex(f"{base}/s1.xml", f"{base}/s2.xml"))
    rotas["/s1.xml"] = (200, XML, urlset(f"{base}/a", f"{base}/b"))
    rotas["/s2.xml"] = (200, XML, urlset(f"{base}/c", f"{base}/d"))

    r = check_sitemap(base, max_child_sitemaps=1)
    assert r.url_count == 2
    assert r.truncated is True
    assert r.url_count_is_lower_bound is True
    assert r.status is Status.INFO


def test_indice_aninhado_e_seguido_ate_a_profundidade_configurada(server):
    base, rotas = server
    rotas["/sitemap.xml"] = (200, XML, sitemapindex(f"{base}/nivel1.xml"))
    rotas["/nivel1.xml"] = (200, XML, sitemapindex(f"{base}/nivel2.xml"))
    rotas["/nivel2.xml"] = (200, XML, urlset(f"{base}/a", f"{base}/b"))

    fundo = check_sitemap(base, max_depth=MAX_INDEX_DEPTH)
    assert fundo.url_count == 2
    assert fundo.truncated is False

    raso = check_sitemap(base, max_depth=1)
    assert raso.url_count == 0
    assert raso.truncated is True
    # Limite de medição, não defeito do site: não pode virar WARNING de "zero URLs".
    assert raso.status is Status.INFO


def test_indice_que_aponta_para_si_mesmo_nao_entra_em_loop(server):
    base, rotas = server
    rotas["/sitemap.xml"] = (200, XML, sitemapindex(f"{base}/sitemap.xml", f"{base}/filho.xml"))
    rotas["/filho.xml"] = (200, XML, urlset(f"{base}/a"))

    r = check_sitemap(base)
    assert r.url_count == 1


def test_filho_ausente_do_indice_e_reportado_e_nao_silenciado(server):
    """Um índice que aponta para um arquivo que não existe está anunciando URLs
    que nenhum crawler alcança."""
    base, rotas = server
    rotas["/sitemap.xml"] = (200, XML, sitemapindex(f"{base}/bom.xml", f"{base}/sumiu.xml"))
    rotas["/bom.xml"] = (200, XML, urlset(f"{base}/a", f"{base}/b"))

    r = check_sitemap(base)
    assert r.url_count == 2
    assert r.status is Status.MISSING
    assert any("sumiu.xml" in motivo for motivo in r.reasons)


def test_filho_bom_depois_de_filho_vazio_nao_apaga_o_WARNING(server):
    """O defeito central dentro do walk do índice, na ordem que o expõe.

    O teste acima ordena os filhos OK-depois-MISSING, que é justamente a ordem
    em que atribuição direta e escalate() dão o mesmo resultado: trocar todos os
    escalate() de _resolve_index por `result.status = doc.status` passava nele.
    Aqui o filho bom vem DEPOIS do filho vazio, então com atribuição o WARNING
    real vira OK e o resultado deixa de bloquear o veredito de 'Ready'.
    """
    base, rotas = server
    rotas["/sitemap.xml"] = (200, XML, sitemapindex(f"{base}/vazio.xml", f"{base}/bom.xml"))
    rotas["/vazio.xml"] = (200, XML, urlset())
    rotas["/bom.xml"] = (200, XML, urlset(f"{base}/a", f"{base}/b"))

    r = check_sitemap(base)
    assert r.url_count == 2
    assert r.status is Status.WARNING
    assert r.status.blocks_readiness is True
    # Status sem motivo é número sem evidência: o filho vazio tem que ser nomeado.
    assert any("vazio.xml" in motivo for motivo in r.reasons)


def test_INFO_do_truncamento_nao_rebaixa_o_MISSING_de_um_filho_ausente(server):
    """Mesma ordem severo-primeiro, agora contra o escalate() do fim da função:
    a nota de contagem parcial é limite de medição e não pode apagar o filho que
    o índice anuncia e o servidor não tem."""
    base, rotas = server
    rotas["/sitemap.xml"] = (
        200,
        XML,
        sitemapindex(f"{base}/sumiu.xml", f"{base}/bom.xml", f"{base}/terceiro.xml"),
    )
    rotas["/bom.xml"] = (200, XML, urlset(f"{base}/a"))
    rotas["/terceiro.xml"] = (200, XML, urlset(f"{base}/b"))

    r = check_sitemap(base, max_child_sitemaps=2)
    assert r.truncated is True
    assert r.url_count == 1
    assert r.status is Status.MISSING
    assert any("sumiu.xml" in motivo for motivo in r.reasons)


# --------------------------------------------------------------------------
# Status coerente
# --------------------------------------------------------------------------


def test_nenhum_sitemap_em_lugar_nenhum_e_MISSING_e_nunca_OK(server):
    base, _rotas = server
    r = check_sitemap(base)
    assert r.found is False
    assert r.status is Status.MISSING
    assert r.kind == "none"
    assert len(r.attempts) == len(r.candidates)


def test_erro_5xx_no_sitemap_nao_e_resumido_como_ausencia(server):
    base, rotas = server
    rotas["/sitemap.xml"] = (503, XML, "")
    r = check_sitemap(base)
    assert r.found is False
    assert r.status is Status.FAIL


def test_xml_malformado_no_servidor_deixa_o_resultado_em_ERROR(server):
    base, rotas = server
    rotas["/sitemap.xml"] = (200, XML, "<urlset><url><loc>x</loc></urlset>")
    r = check_sitemap(base)
    assert r.status is Status.ERROR
    assert r.url_count == 0
    assert r.found is False


def test_sitemap_gzipado_e_ERROR_e_nao_zero_urls(server):
    """Não sabemos ler .gz aqui; dizer isso é honesto, reportar zero URLs não."""
    base, rotas = server
    rotas["/robots.txt"] = (200, TXT, f"Sitemap: {base}/sitemap.xml.gz\n")
    r = check_sitemap(base)
    assert r.status is Status.ERROR
    assert any("gzip" in motivo for _u, _s, motivo in r.attempts)


def test_url_base_sem_esquema_e_ERROR_em_vez_de_relatorio_ficticio():
    """'auditor example.com' produzia base '://' e um relatório inteiro sobre
    ':///sitemap.xml'."""
    r = check_sitemap("example.com")
    assert r.status is Status.ERROR
    assert r.found is False
    assert r.candidates == []


def test_spa_catch_all_nao_conta_como_sitemap_encontrado(server):
    base, rotas = server
    shell = "<!doctype html><html><head><title>my app</title></head><body></body></html>"
    # O catch-all responde 200 para qualquer caminho, inclusive os convencionais.
    for caminho in ("/sitemap.xml", "/sitemap_index.xml", "/sitemap-index.xml"):
        rotas[caminho] = (200, {"Content-Type": "text/html"}, shell)

    r = check_sitemap(base)
    assert r.found is False
    assert r.status is Status.MISSING


def test_sitemap_valido_servido_como_text_html_e_encontrado(server):
    """Ponta a ponta do mesmo defeito: um urlset válido com Content-Type
    text/html era reportado found=False, MISSING, url_count=0."""
    base, rotas = server
    rotas["/sitemap.xml"] = (200, HTML, urlset(f"{base}/a", f"{base}/b"))

    r = check_sitemap(base)
    assert r.found is True
    assert r.kind == "urlset"
    assert r.url_count == 2
    assert r.status is Status.OK


# --------------------------------------------------------------------------
# Candidatos que falharam antes do que deu certo
# --------------------------------------------------------------------------


def test_sitemap_declarado_no_robots_que_devolve_503_nao_vira_OK(server):
    """O pecado central em outro lugar: a falha observada num candidato entrava
    só em `attempts` e o candidato seguinte, bom, deixava o resultado OK, sem
    nem um motivo para um relatório em texto mostrar. O site declara este
    sitemap; não servi-lo é achado dele, mesmo com /sitemap.xml funcionando.
    """
    base, rotas = server
    rotas["/robots.txt"] = (200, TXT, f"User-agent: *\nSitemap: {base}/sitemap_index.xml\n")
    rotas["/sitemap_index.xml"] = (503, XML, "")
    rotas["/sitemap.xml"] = (200, XML, urlset(f"{base}/a"))

    r = check_sitemap(base)
    assert r.found is True
    assert r.url_count == 1
    assert r.status is Status.FAIL
    assert any("sitemap_index.xml" in motivo for motivo in r.reasons)


def test_sitemap_declarado_no_robots_com_xml_malformado_nao_vira_OK(server):
    """Mesmo caso com ERROR em vez de FAIL: não foi possível ler o documento que
    o próprio site anuncia, e isso não pode sumir porque outro caminho salvou."""
    base, rotas = server
    rotas["/robots.txt"] = (200, TXT, f"Sitemap: {base}/mapa.xml\n")
    rotas["/mapa.xml"] = (200, XML, "<urlset><url><loc>x</loc></urlset>")
    rotas["/sitemap.xml"] = (200, XML, urlset(f"{base}/a"))

    r = check_sitemap(base)
    assert r.found is True
    assert r.status is Status.ERROR
    assert any("mapa.xml" in motivo for motivo in r.reasons)


def test_candidato_convencional_ilegivel_aparece_no_resultado(server):
    """Um caminho que nós chutamos e que devolveu corpo ilegível foi observado:
    fica visível, mas como INFO, porque não é o site que está afirmando nada
    sobre ele."""
    base, rotas = server
    rotas["/sitemap.xml"] = (200, XML, "<urlset><url><loc>x</loc></urlset>")
    rotas["/sitemap_index.xml"] = (200, XML, urlset(f"{base}/a"))

    r = check_sitemap(base)
    assert r.found is True
    assert r.sitemap_url == f"{base}/sitemap_index.xml"
    assert r.status is Status.INFO
    assert any("/sitemap.xml" in motivo for motivo in r.reasons)


def test_404_em_caminho_chutado_nao_vira_achado(server):
    """Fronteira da correção acima — passa nos dois estados de propósito.

    A ausência num caminho que o auditor chutou não diz nada sobre o site;
    escalar todos os `attempts` daria um achado a todo site que serve o sitemap
    fora de /sitemap.xml, que é a metade oposta do mesmo erro.
    """
    base, rotas = server
    rotas["/robots.txt"] = (200, TXT, "User-agent: *\nAllow: /\n")
    rotas["/sitemap_index.xml"] = (200, XML, urlset(f"{base}/a"))

    r = check_sitemap(base)
    assert r.status is Status.OK
    assert r.reasons == []


# --------------------------------------------------------------------------
# Contagem de documentos e host do sitemap
# --------------------------------------------------------------------------


def test_documents_fetched_conta_os_candidatos_que_falharam(server):
    """O campo reportava 1 depois de quatro requisições de sitemap. Num módulo
    cuja tese é que o número tem que se anunciar pelo que é, um campo chamado
    'fetched' subnotificando o orçamento gasto é a mesma imprecisão."""
    base, rotas = server
    rotas["/wp-sitemap.xml"] = (200, XML, urlset(f"{base}/a"))

    r = check_sitemap(base)
    assert r.found is True
    # /sitemap.xml, /sitemap_index.xml, /sitemap-index.xml e /wp-sitemap.xml.
    # robots.txt não é documento de sitemap e não entra nesta conta.
    assert r.documents_fetched == 4


def test_documents_fetched_conta_todos_os_candidatos_quando_nao_ha_sitemap(server):
    base, _rotas = server
    r = check_sitemap(base)
    assert r.found is False
    assert r.documents_fetched == len(r.candidates)


def test_gz_recusado_sem_requisicao_nao_conta_como_documento_buscado(server):
    """Fronteira na direção oposta: o .gz é recusado antes de sair requisição,
    então contá-lo inflaria o número. E o ERROR dele continua no status."""
    base, rotas = server
    rotas["/robots.txt"] = (200, TXT, f"Sitemap: {base}/mapa.xml.gz\n")
    rotas["/sitemap.xml"] = (200, XML, urlset(f"{base}/a"))

    r = check_sitemap(base)
    assert r.documents_fetched == 1
    assert r.status is Status.ERROR


def test_sitemap_em_outro_host_e_INFO_e_diz_onde_esta(server):
    """Google só honra sitemap cross-host para propriedade verificada, então o
    leitor precisa saber disso antes de confiar na contagem. Dois servidores
    locais em portas diferentes exercitam o ramo sem DNS e sem flakiness."""
    base, rotas = server
    with outro_servidor() as (outra_base, outras_rotas):
        outras_rotas["/sitemap.xml"] = (200, XML, urlset(f"{base}/a"))
        rotas["/robots.txt"] = (200, TXT, f"Sitemap: {outra_base}/sitemap.xml\n")

        r = check_sitemap(base)

    assert r.found is True
    assert r.sitemap_url == f"{outra_base}/sitemap.xml"
    assert r.url_count == 1
    assert r.status is Status.INFO
    assert any(outra_base.removeprefix("http://") in motivo for motivo in r.reasons)


# --------------------------------------------------------------------------
# Amostra de URLs (segunda cláusula de ADS-CRAWL-07)
# --------------------------------------------------------------------------


def test_amostra_de_urls_reporta_a_que_nao_responde_200(server):
    base, rotas = server
    rotas["/sitemap.xml"] = (200, XML, urlset(f"{base}/a", f"{base}/b"))
    rotas["/a"] = (200, {"Content-Type": "text/html"}, "<html>a</html>")
    # /b não existe: o sitemap anuncia uma URL que o crawler não alcança.

    r = check_sitemap(base)
    amostra = verify_sample_urls(r)
    assert amostra.ok_count == 1
    assert amostra.status is Status.MISSING


def test_amostra_sem_urls_e_MISSING_e_nunca_OK():
    vazio = SitemapResult(base_url="https://exemplo.com")
    assert verify_sample_urls(vazio).status is Status.MISSING
