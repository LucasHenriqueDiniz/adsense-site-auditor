"""Testes de descoberta e parsing de sitemap (ADS-CRAWL-07).

Cada teste de regressão nomeia o defeito que impede. Os três que motivaram o
módulo estão em check_technical.py:

  * a contagem casava o namespace literal sitemaps.org/0.9;
  * um <sitemapindex> tinha seus <loc> contados como URLs de página;
  * a descoberta era fixa em /sitemap.xml e ignorava a diretiva Sitemap: do
    robots.txt que o próprio script já tinha baixado.

Os testes de rede usam as fixtures `server` e `outro_servidor` (servidores HTTP
reais, do conftest). Mockar requests reproduziria a suposição errada em vez do
protocolo.
"""

import time

# parse_robots é reexportado por sitemap de propósito: o módulo já resolveu onde
# o parser de robots mora, e o teste não deve duplicar essa decisão.
from adsense_checks.sitemap import (
    MAX_INDEX_DEPTH,
    SitemapResult,
    check_sitemap,
    discover_sitemap_urls,
    in_scope,
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

# Um timeout menor que o sono da rota lenta, e ambos curtos: o que os testes de
# timeout provam é que o valor chega no requests, e provar isso com segundos de
# relógio custaria à suíte inteira mais do que ela leva hoje. Fracionário pelo
# mesmo motivo — com timeout inteiro o menor sono possível passaria de 1s.
TIMEOUT_CURTO = 0.1
SONO_DA_ROTA_LENTA = 0.3


def rota_lenta(resposta, segundos: float = SONO_DA_ROTA_LENTA):
    """Rota que dorme antes de responder, para o cliente ter de desistir."""

    def rota(_metodo):
        time.sleep(segundos)
        return resposta

    return rota


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


def test_descoberta_em_subdiretorio_tenta_o_subdiretorio_antes_da_origem():
    """Os caminhos convencionais eram juntados com a barra inicial, então todos
    resolviam contra a ORIGEM: um project page ou uma instalação em /blog/ nunca
    tinha o sitemap dela procurado uma vez sequer, e o que existisse na raiz do
    host — de outro projeto — era adotado como dela.
    """
    candidatos = discover_sitemap_urls("https://user.github.io/meusite/", None)
    assert candidatos[0] == "https://user.github.io/meusite/sitemap.xml"
    # A origem continua sendo tentada: é onde um sitemap normalmente mora.
    assert "https://user.github.io/sitemap.xml" in candidatos
    assert candidatos.index("https://user.github.io/meusite/sitemap.xml") < candidatos.index(
        "https://user.github.io/sitemap.xml"
    )
    assert len(candidatos) == len(set(candidatos))


def test_escopo_de_subdiretorio_exclui_outro_projeto_do_mesmo_host():
    assert in_scope("https://user.github.io/meusite/a", "https://user.github.io/meusite/") is True
    assert in_scope("https://user.github.io/outro/a", "https://user.github.io/meusite/") is False
    # Fronteira: a própria pasta auditada, sem a barra, é a home dela e está
    # dentro. Um `startswith` sozinho a deixaria de fora.
    assert in_scope("https://user.github.io/meusite", "https://user.github.io/meusite/") is True
    # Sem caminho na base, o site é a origem inteira — nada muda para o caso comum.
    assert in_scope("https://exemplo.com/qualquer", "https://exemplo.com") is True


def test_base_que_termina_em_arquivo_nao_confina_o_escopo_a_ele():
    """A armadilha na direção oposta, e o CLI cai nela: a base é a URL FINAL da
    home, então um site cujo `/` termina em `/index.php` viraria o diretório
    `/index.php/` — caminho que URL nenhuma tem embaixo. Todo <loc> do site
    ficaria fora de escopo e um site saudável seria reportado anunciando zero
    URLs próprias, além de gastar cinco requisições em `/index.php/sitemap.xml`.
    """
    assert in_scope("https://exemplo.com/a", "https://exemplo.com/index.php") is True
    assert discover_sitemap_urls("https://exemplo.com/index.php", None) == discover_sitemap_urls(
        "https://exemplo.com/", None
    )


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
# Escopo: de quem são as URLs que o sitemap anuncia
# --------------------------------------------------------------------------


def test_sitemap_da_origem_nao_conta_como_sitemap_do_subdiretorio(server):
    """A reprodução do defeito: subdiretório passando no sitemap de outro.

    Auditando `host/meusite/` num host cuja raiz é de outro projeto, o resultado
    era `found: True, url_count: 2, status: OK`, com `…/outro/a` e `…/outro/b`
    como evidência — para um site que não tem sitemap nenhum. Duas causas: os
    caminhos convencionais escapavam para a origem, e `_absorb_urls` contava todo
    <loc> sem olhar host nem caminho.
    """
    base, rotas = server
    rotas["/sitemap.xml"] = (200, XML, urlset(f"{base}/outro/a", f"{base}/outro/b"))

    r = check_sitemap(f"{base}/meusite/")

    assert r.url_count == 0
    assert r.sample_urls == []
    assert r.out_of_scope_count == 2
    # Um documento que não anuncia nada deste site não é o sitemap deste site.
    assert r.status is Status.WARNING
    assert r.status.blocks_readiness is True
    assert any("/outro/a" in motivo for motivo in r.reasons)


def test_sitemap_do_subdiretorio_conta_so_as_urls_dele(server):
    """O caso legítimo do mesmo par de correções: o subdiretório tem o sitemap
    dele, é lá que se procura primeiro, e o <loc> que aponta para fora fica de
    fora da contagem em vez de inflá-la em silêncio."""
    base, rotas = server
    rotas["/meusite/sitemap.xml"] = (200, XML, urlset(f"{base}/meusite/a", f"{base}/outro/b"))

    r = check_sitemap(f"{base}/meusite/")

    assert r.sitemap_url == f"{base}/meusite/sitemap.xml"
    assert r.url_count == 1
    assert r.sample_urls == [f"{base}/meusite/a"]
    assert r.out_of_scope_count == 1
    assert r.status is Status.INFO


def test_loc_em_outro_site_nao_entra_na_contagem(server, outro_servidor):
    """Mesma checagem numa auditoria de origem inteira: a porta faz parte da
    identidade do site, então o <loc> na outra porta é de outro site."""
    base, rotas = server
    outra_base, _outras_rotas = outro_servidor
    rotas["/sitemap.xml"] = (200, XML, urlset(f"{base}/a", f"{outra_base}/b"))

    r = check_sitemap(base)

    assert r.url_count == 1
    assert r.sample_urls == [f"{base}/a"]
    assert r.out_of_scope_count == 1
    assert r.status is Status.INFO
    assert any(f"{outra_base}/b" in motivo for motivo in r.reasons)


def test_loc_relativo_continua_sendo_url_do_site(server):
    """Fronteira da checagem acima. <loc> relativo está fora da spec e existe na
    prática; resolvido contra o documento ele é do site, e não resolver o
    transformaria em 'URL de outro site' — um achado falso no lugar do antigo."""
    base, rotas = server
    xml = f'<urlset xmlns="{NS_09}"><url><loc>/a</loc></url></urlset>'
    rotas["/sitemap.xml"] = (200, XML, xml)

    r = check_sitemap(base)

    assert r.url_count == 1
    assert r.out_of_scope_count == 0
    assert r.sample_urls == [f"{base}/a"]
    assert r.status is Status.OK


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


def test_urlset_vazio_no_servidor_deixa_o_check_sitemap_em_WARNING(server):
    """O WARNING do parse tem que sobreviver até o resultado.

    `test_urlset_valido_e_vazio_e_WARNING_e_nao_OK` só exercita parse_sitemap, e
    com ele apagar `escalate(result.status, doc.status)` de check_sitemap passava
    na suíte inteira: um sitemap válido anunciando zero URLs voltava OK e o site
    ficava elegível a 'Ready' sem uma única URL declarada.
    """
    base, rotas = server
    rotas["/sitemap.xml"] = (200, XML, urlset())

    r = check_sitemap(base)

    assert r.found is True
    assert r.kind == "urlset"
    assert r.url_count == 0
    assert r.status is Status.WARNING
    assert r.status.blocks_readiness is True
    assert any("zero URLs" in motivo for motivo in r.reasons)


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


def test_sitemap_em_outro_host_e_INFO_e_diz_onde_esta(server, outro_servidor):
    """Google só honra sitemap cross-host para propriedade verificada, então o
    leitor precisa saber disso antes de confiar na contagem. Dois servidores
    locais em portas diferentes exercitam o ramo sem DNS e sem flakiness: a
    porta faz parte do netloc, então não é preciso DNS nem depender de
    'localhost' resolver para 127.0.0.1."""
    base, rotas = server
    outra_base, outras_rotas = outro_servidor
    outras_rotas["/sitemap.xml"] = (200, XML, urlset(f"{base}/a"))
    rotas["/robots.txt"] = (200, TXT, f"Sitemap: {outra_base}/sitemap.xml\n")

    r = check_sitemap(base)

    assert r.found is True
    assert r.sitemap_url == f"{outra_base}/sitemap.xml"
    assert r.url_count == 1
    assert r.status is Status.INFO
    assert any(outra_base.removeprefix("http://") in motivo for motivo in r.reasons)


def test_www_no_sitemap_declarado_nao_e_outro_host(server):
    """A comparação era de netloc exato, então o site que declara o sitemap na
    grafia `www.` — o caso comum — ganhava uma afirmação falsa ('hosted on
    www.ex.com, not ex.com') e um INFO por um sitemap que está na casa dele.

    A base com `www.` nunca é requisitada: o robots já vem pronto (então
    /robots.txt não é buscado) e a URL declarada, absoluta, é o primeiro
    candidato e resolve. Se um dia alguém fizer check_sitemap requisitar a base,
    este teste quebra com erro de DNS — e deve quebrar.
    """
    base, rotas = server
    rotas["/sitemap.xml"] = (200, XML, urlset(f"{base}/a"))
    base_www = base.replace("http://", "http://www.")

    r = check_sitemap(base_www, robots=parse_robots(f"Sitemap: {base}/sitemap.xml\n"))

    assert r.found is True
    assert r.url_count == 1
    assert r.status is Status.OK
    assert r.reasons == []


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


# --------------------------------------------------------------------------
# Timeout
# --------------------------------------------------------------------------


def test_timeout_do_check_sitemap_chega_na_wire(server):
    """O --timeout do CLI era documentado e ignorado por este módulo inteiro.

    Nenhuma das chamadas a fetch daqui passava timeout, então todas usavam o
    DEFAULT_TIMEOUT de 15s: contra um site cujas rotas de sitemap dormem 8s,
    `--timeout 1` ainda levava 40s de relógio (5 candidatos x 8s). Aqui a rota
    dorme mais do que o timeout pedido, então o valor só pode ter chegado no
    requests se a chamada desistir — sem ele o urlset chega inteiro e o sitemap
    é encontrado.
    """
    base, rotas = server
    rotas["/sitemap.xml"] = rota_lenta((200, XML, urlset(f"{base}/a")))

    r = check_sitemap(base, timeout=TIMEOUT_CURTO)

    assert r.found is False
    assert r.url_count == 0
    assert r.status is Status.ERROR
    assert any("timeout" in motivo.lower() for _u, _s, motivo in r.attempts)


def test_timeout_do_verify_sample_urls_chega_na_wire(server):
    """A outra porta de entrada pública, com o mesmo buraco: a amostra é a
    metade de ADS-CRAWL-07 que mais gasta requisição, e uma URL que pendura
    prendia a auditoria por 15s por URL amostrada, qualquer que fosse a flag."""
    base, rotas = server
    rotas["/sitemap.xml"] = (200, XML, urlset(f"{base}/lenta"))
    rotas["/lenta"] = rota_lenta((200, HTML, "<html>lenta</html>"))

    r = check_sitemap(base)
    amostra = verify_sample_urls(r, timeout=TIMEOUT_CURTO)

    assert amostra.ok_count == 0
    assert amostra.status is Status.ERROR


def test_indice_que_resolve_para_zero_urls_e_WARNING(server):
    """Um `<sitemapindex>` cujos filhos existem e não listam URL nenhuma: sem a
    escalação, o site aparece anunciando um sitemap saudável que não anuncia
    página alguma."""
    base, rotas = server
    rotas["/sitemap.xml"] = (200, XML, sitemapindex(f"{base}/s1.xml"))
    rotas["/s1.xml"] = (200, XML, urlset())

    r = check_sitemap(base + "/")

    assert r.found is True
    assert r.url_count == 0
    assert r.status is Status.WARNING
    assert any("zero page URLs" in m or "zero URLs" in m for m in r.reasons)


def test_indice_que_so_referencia_outro_ja_visitado_e_WARNING(server):
    """Isolado: um filho que é `<urlset>` vazio, ou um índice sem filhos, já
    carrega WARNING no próprio `doc.status` e mascara esta escalação. Aqui os
    dois documentos são índices válidos e bem formados — o segundo aponta de
    volta para o primeiro, que já foi visitado — então nada além desta linha
    pode dizer que o sitemap não resolveu para página alguma."""
    base, rotas = server
    rotas["/sitemap.xml"] = (200, XML, sitemapindex(f"{base}/s1.xml"))
    rotas["/s1.xml"] = (200, XML, sitemapindex(f"{base}/sitemap.xml"))

    r = check_sitemap(base + "/")

    assert r.found is True
    assert r.kind == "sitemapindex"
    assert r.url_count == 0
    assert r.truncated is False
    assert r.status is Status.WARNING
    assert any("zero page URLs" in m for m in r.reasons)


# --------------------------------------------------------------------------
# Constantes fixadas PELO VALOR. Nenhuma asserção aqui pode citar a constante:
# uma cota derivada dela se move junto com a mutação e o teste passa sempre.
# --------------------------------------------------------------------------


def test_indice_aninhado_para_de_ser_seguido_na_profundidade_2(server):
    """MAX_INDEX_DEPTH. O documento de entrada é o nível 0, então índice ->
    filhos -> netos e para. O quarto nível não é buscado e o resultado diz que
    a contagem é parcial."""
    base, rotas = server
    rotas["/sitemap.xml"] = (200, XML, sitemapindex(f"{base}/n1.xml"))
    rotas["/n1.xml"] = (200, XML, sitemapindex(f"{base}/n2.xml"))
    rotas["/n2.xml"] = (200, XML, sitemapindex(f"{base}/n3.xml"))
    rotas["/n3.xml"] = (200, XML, urlset(f"{base}/pagina"))

    r = check_sitemap(base + "/")

    pedidos = [c for _m, c, _h in rotas.received if c.endswith(".xml")]
    assert "/n2.xml" in pedidos  # profundidade 2 é buscada
    assert "/n3.xml" not in pedidos  # a 3 não
    assert r.truncated is True
    assert r.url_count == 0


def test_no_maximo_20_sitemaps_filhos_sao_buscados(server):
    """MAX_CHILD_SITEMAPS. Sites grandes publicam centenas; a auditoria para em
    20 e marca a contagem como piso."""
    base, rotas = server
    filhos = [f"{base}/f{i}.xml" for i in range(25)]
    rotas["/sitemap.xml"] = (200, XML, sitemapindex(*filhos))
    for i in range(25):
        rotas[f"/f{i}.xml"] = (200, XML, urlset(f"{base}/p{i}"))

    r = check_sitemap(base + "/")

    buscados = [c for _m, c, _h in rotas.received if c.startswith("/f")]
    assert len(buscados) == 20
    assert r.url_count == 20
    assert r.truncated is True


def test_a_amostra_guardada_para_no_quinquagesimo(server):
    """MAX_SAMPLE_URLS. `url_count` é o total de verdade; `sample_urls` é a
    evidência, e é ela que tem teto."""
    base, rotas = server
    rotas["/sitemap.xml"] = (200, XML, urlset(*[f"{base}/p{i}" for i in range(60)]))

    r = check_sitemap(base + "/")

    assert r.url_count == 60
    assert len(r.sample_urls) == 50
