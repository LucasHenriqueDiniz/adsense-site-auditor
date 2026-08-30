"""Testes do parser de robots.txt.

O primeiro teste é o defeito que motivou o módulo: a checagem anterior fazia
substring no arquivo inteiro e reprovava um robots.txt saudável.
"""

import pytest

from adsense_checks.robots import (
    ADSBOT_CRAWLER,
    ADSENSE_CRAWLER,
    INDEX_CRAWLER,
    Robots,
    blocks_everything,
    is_allowed,
    parse_robots,
)

SAUDAVEL = """
User-agent: *
Disallow: /admin/
Allow: /
Sitemap: https://exemplo.com/sitemap.xml
"""

BLOQUEIA_TUDO = """
User-agent: *
Disallow: /
"""

BLOQUEIA_SO_ADSENSE = """
User-agent: Mediapartners-Google
Disallow: /

User-agent: *
Allow: /
"""

GRUPO_ESPECIFICO = """
User-agent: *
Disallow: /

User-agent: Googlebot
Disallow: /privado/
"""


def test_robots_saudavel_nao_bloqueia_o_crawler_do_adsense():
    """O caso que a implementação antiga reprovava.

    'User-agent: *' fornece o '*' e 'Disallow: /admin/' fornece o 'disallow: /',
    então o teste de substring dava FAIL num arquivo que não bloqueia nada.
    """
    r = parse_robots(SAUDAVEL)
    assert is_allowed(r, ADSENSE_CRAWLER, "/") is True
    assert blocks_everything(r, ADSENSE_CRAWLER) is False
    # E o caminho que ele de fato bloqueia continua bloqueado.
    assert is_allowed(r, ADSENSE_CRAWLER, "/admin/x") is False


def test_bloqueio_real_de_tudo_e_detectado():
    r = parse_robots(BLOQUEIA_TUDO)
    assert blocks_everything(r, ADSENSE_CRAWLER) is True
    assert blocks_everything(r, INDEX_CRAWLER) is True


def test_bloqueio_dirigido_ao_adsense_nao_afeta_o_googlebot():
    """A distinção que mais importa aqui: o site indexa normalmente e mesmo assim
    não serve anúncios."""
    r = parse_robots(BLOQUEIA_SO_ADSENSE)
    assert blocks_everything(r, ADSENSE_CRAWLER) is True
    assert blocks_everything(r, INDEX_CRAWLER) is False


def test_grupo_especifico_substitui_o_curinga_em_vez_de_somar():
    r = parse_robots(GRUPO_ESPECIFICO)
    # Googlebot tem grupo próprio, então ignora o '*' inteiro.
    assert is_allowed(r, INDEX_CRAWLER, "/") is True
    assert is_allowed(r, INDEX_CRAWLER, "/privado/x") is False
    # Quem não tem grupo próprio continua sob o curinga.
    assert is_allowed(r, ADSENSE_CRAWLER, "/") is False


def test_adsbot_ignora_o_grupo_curinga():
    """Documentado pelo Google: AdsBot-Google precisa ser nomeado para ser excluído."""
    r = parse_robots(BLOQUEIA_TUDO)
    assert is_allowed(r, ADSBOT_CRAWLER, "/") is True
    r2 = parse_robots("User-agent: AdsBot-Google\nDisallow: /\n")
    assert is_allowed(r2, ADSBOT_CRAWLER, "/") is False


def test_regra_mais_longa_vence():
    r = parse_robots("User-agent: *\nDisallow: /a/\nAllow: /a/b/\n")
    assert is_allowed(r, INDEX_CRAWLER, "/a/x") is False
    assert is_allowed(r, INDEX_CRAWLER, "/a/b/x") is True


def test_allow_vence_empate_com_disallow():
    r = parse_robots("User-agent: *\nDisallow: /x\nAllow: /x\n")
    assert is_allowed(r, INDEX_CRAWLER, "/x") is True


def test_disallow_vazio_libera_tudo():
    r = parse_robots("User-agent: *\nDisallow:\n")
    assert is_allowed(r, INDEX_CRAWLER, "/qualquer") is True


def test_curinga_e_ancora_no_caminho():
    r = parse_robots("User-agent: *\nDisallow: /*.pdf$\n")
    assert is_allowed(r, INDEX_CRAWLER, "/doc.pdf") is False
    assert is_allowed(r, INDEX_CRAWLER, "/doc.pdf.html") is True


def test_robots_ausente_libera_tudo():
    r = Robots(missing=True)
    assert is_allowed(r, ADSENSE_CRAWLER, "/") is True
    assert blocks_everything(r, ADSENSE_CRAWLER) is False


def test_comentarios_e_linhas_malformadas_nao_quebram():
    r = parse_robots("# comentario\nlixo sem dois pontos\nUser-agent: *  # inline\nDisallow: /x\n")
    assert is_allowed(r, INDEX_CRAWLER, "/x") is False
    assert is_allowed(r, INDEX_CRAWLER, "/y") is True


def test_sitemaps_sao_extraidos():
    r = parse_robots(SAUDAVEL)
    assert r.sitemaps == ["https://exemplo.com/sitemap.xml"]


def test_arquivo_vazio_libera_tudo():
    r = parse_robots("")
    assert r.is_empty is True
    assert is_allowed(r, ADSENSE_CRAWLER, "/") is True


@pytest.mark.parametrize(
    "agente,esperado",
    [("Mediapartners-Google", False), ("mediapartners-google", False), ("Googlebot", True)],
)
def test_casamento_de_agente_e_case_insensitive(agente, esperado):
    r = parse_robots(BLOQUEIA_SO_ADSENSE)
    assert is_allowed(r, agente, "/") is esperado


# Arquivo real de jikan.lucashdo.com, capturado em 2026-08-30. A implementação
# anterior reprovava este site com "blocks all crawlers", porque 'Disallow: /v1/'
# contém a substring 'disallow: /'. Ele libera tudo menos duas rotas de API.
JIKAN_REAL = """# jikan-edge — https://jikan.lucashdo.com
#
# The marketing pages are open to every crawler. /v1/* is deliberately excluded.

User-agent: *
Allow: /
Disallow: /v1/
Disallow: /health

Sitemap: https://jikan.lucashdo.com/sitemap.xml
"""


def test_regressao_site_real_com_disallow_parcial():
    r = parse_robots(JIKAN_REAL)
    assert blocks_everything(r, ADSENSE_CRAWLER) is False
    assert is_allowed(r, ADSENSE_CRAWLER, "/") is True
    assert is_allowed(r, ADSENSE_CRAWLER, "/docs") is True
    # E o que ele de fato exclui segue excluído.
    assert is_allowed(r, ADSENSE_CRAWLER, "/v1/anime/1") is False
    assert is_allowed(r, ADSENSE_CRAWLER, "/health") is False
    assert r.sitemaps == ["https://jikan.lucashdo.com/sitemap.xml"]
