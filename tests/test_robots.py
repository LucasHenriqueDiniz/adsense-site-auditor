"""Testes do parser de robots.txt.

O primeiro teste é o defeito que motivou o módulo: a checagem anterior fazia
substring no arquivo inteiro e reprovava um robots.txt saudável.
"""

import time

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
    # E o caminho que ele de fato bloqueia continua bloqueado — medido no Googlebot,
    # porque o crawler do AdSense ignora o grupo curinga (ver o teste da isenção).
    assert is_allowed(r, INDEX_CRAWLER, "/admin/x") is False


def test_bloqueio_real_de_tudo_e_detectado():
    r = parse_robots(BLOQUEIA_TUDO)
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
    # Quem não tem grupo próprio — nem isenção do curinga — continua sob o curinga.
    assert is_allowed(r, "Bingbot", "/") is False


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
    """O Allow vem ANTES do Disallow de propósito.

    Escrito na ordem inversa — 'Disallow: /x' e depois 'Allow: /x' — o teste era
    verdadeiro por construção: o Allow também era a última regra, então trocar o
    desempate por 'a última de mesmo tamanho vence' (o '>' virando '>=') passava
    igual. Aqui, com 'Allow: /ab' e 'Disallow: /a*' de mesmo comprimento contra
    '/ab', só o desempate a favor do Allow dá True.
    """
    r = parse_robots("User-agent: *\nAllow: /ab\nDisallow: /a*\n")
    assert is_allowed(r, INDEX_CRAWLER, "/ab") is True
    # E na outra ordem também, que é o caso trivial.
    invertido = parse_robots("User-agent: *\nDisallow: /x\nAllow: /x\n")
    assert is_allowed(invertido, INDEX_CRAWLER, "/x") is True


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
    # E o que ele de fato exclui segue excluído. O arquivo só tem grupo curinga,
    # então quem responde por essas rotas é o Googlebot: o crawler do AdSense é
    # isento do '*' e passa por elas.
    assert is_allowed(r, INDEX_CRAWLER, "/v1/anime/1") is False
    assert is_allowed(r, INDEX_CRAWLER, "/health") is False
    assert is_allowed(r, ADSENSE_CRAWLER, "/v1/anime/1") is True
    assert r.sitemaps == ["https://jikan.lucashdo.com/sitemap.xml"]


# --------------------------------------------------------------------------
# Casamento de user-agent entre grupos. Os dois defeitos abaixo passaram pela
# suíte inteira porque nenhum teste tinha mais de um grupo nomeado.
# --------------------------------------------------------------------------

GRUPO_MAIS_ESTREITO_E_CURINGA = """
User-agent: Googlebot-Image
Disallow: /fotos/

User-agent: *
Disallow: /
"""


def test_grupo_de_um_crawler_mais_estreito_nao_captura_o_mais_amplo():
    """O casamento era nos dois sentidos, então 'Googlebot-Image' capturava
    'Googlebot' — e, como um grupo específico anula o curinga, o 'Disallow: /'
    que de fato governa o Googlebot sumia do resultado."""
    r = parse_robots(GRUPO_MAIS_ESTREITO_E_CURINGA)
    assert blocks_everything(r, INDEX_CRAWLER) is True
    assert is_allowed(r, INDEX_CRAWLER, "/") is False
    # O crawler que o grupo estreito realmente nomeia segue com as regras dele.
    assert is_allowed(r, "Googlebot-Image", "/") is True
    assert is_allowed(r, "Googlebot-Image", "/fotos/x.jpg") is False


def test_token_do_arquivo_e_prefixo_do_crawler_e_nao_o_contrario():
    """A direção que vale: 'Googlebot' no arquivo governa 'Googlebot-Image'."""
    r = parse_robots("User-agent: Googlebot\nDisallow: /\n")
    assert is_allowed(r, "Googlebot-Image", "/") is False
    # E um token curto não captura um crawler do Google só por caber dentro do nome
    # dele. O exemplo aqui era 'bot', que nem prefixo de 'googlebot' é: passava por
    # um motivo que não tem nada a ver com o defeito, e o defeito seguia vivo.
    r2 = parse_robots("User-agent: google\nDisallow: /\n\nUser-agent: *\nAllow: /\n")
    assert is_allowed(r2, INDEX_CRAWLER, "/") is True


def test_grupo_mais_especifico_vence_o_mais_generico_em_qualquer_ordem():
    """Vencia o primeiro em ordem de arquivo, não o mais específico."""
    generico_primeiro = parse_robots(
        "User-agent: Googlebot\nDisallow: /\n\nUser-agent: Googlebot-News\nAllow: /\n"
    )
    assert is_allowed(generico_primeiro, "Googlebot-News", "/") is True
    assert is_allowed(generico_primeiro, INDEX_CRAWLER, "/") is False

    especifico_primeiro = parse_robots(
        "User-agent: Googlebot-News\nAllow: /\n\nUser-agent: Googlebot\nDisallow: /\n"
    )
    assert is_allowed(especifico_primeiro, "Googlebot-News", "/") is True
    assert is_allowed(especifico_primeiro, INDEX_CRAWLER, "/") is False


CURINGA_DUPLICADO = """
User-agent: *
Disallow: /wp-admin/

User-agent: *
Disallow: /
"""


def test_grupos_repetidos_do_mesmo_agente_sao_somados():
    """Só um dos grupos valia, então o resultado dependia da ordem dos blocos.

    Dois plugins de WordPress, cada um acrescentando o seu próprio 'User-agent: *',
    é a forma comum disso: com o bloco do /wp-admin/ na frente, um site que bloqueia
    tudo era relatado como aberto; invertendo os blocos, o mesmo site era relatado
    como bloqueado.
    """
    r = parse_robots(CURINGA_DUPLICADO)
    assert blocks_everything(r, INDEX_CRAWLER) is True
    invertido = parse_robots("User-agent: *\nDisallow: /\n\nUser-agent: *\nDisallow: /wp-admin/\n")
    assert blocks_everything(invertido, INDEX_CRAWLER) is True

    # Vale igual para grupos nomeados: as duas regras governam, não só a primeira.
    nomeado = parse_robots(
        "User-agent: Googlebot\nDisallow: /a/\n\nUser-agent: Googlebot\nDisallow: /b/\n"
    )
    assert is_allowed(nomeado, INDEX_CRAWLER, "/a/x") is False
    assert is_allowed(nomeado, INDEX_CRAWLER, "/b/x") is False
    assert is_allowed(nomeado, INDEX_CRAWLER, "/c/x") is True


def test_lixo_depois_do_nome_do_agente_e_ignorado():
    """O casamento era com o valor cru, então o lixo do lado do ARQUIVO furava tudo.

    'User-agent: Googlebot*' não casava com grupo nenhum e, sem grupo curinga para
    cair, o Disallow que o site escreveu sumia: liberado geral.
    """
    estrela = parse_robots("User-agent: Googlebot*\nDisallow: /\n")
    assert is_allowed(estrela, INDEX_CRAWLER, "/") is False
    versao = parse_robots("User-agent: Googlebot/1.2\nDisallow: /\n")
    assert is_allowed(versao, INDEX_CRAWLER, "/") is False
    # E o token normalizado continua sendo prefixo, não substring.
    assert is_allowed(estrela, "Googlebot-Image", "/") is False
    assert is_allowed(estrela, "Bingbot", "/") is True


def test_mediapartners_ignora_o_grupo_curinga():
    """Documentado pelo Google junto com o AdsBot: o '*' é ignorado por ele também.

    Faltando na isenção, todo site com 'User-agent: * / Disallow: /' era relatado
    como sem anúncios, quando o Mediapartners-Google segue buscando aquelas páginas.
    """
    r = parse_robots(BLOQUEIA_TUDO)
    assert is_allowed(r, ADSENSE_CRAWLER, "/") is True
    assert blocks_everything(r, ADSENSE_CRAWLER) is False
    # Nomeado, ele obedece — é a única forma de excluí-lo.
    nomeado = parse_robots("User-agent: Mediapartners-Google\nDisallow: /\n")
    assert blocks_everything(nomeado, ADSENSE_CRAWLER) is True


def test_isencao_do_curinga_vale_para_a_familia_do_token():
    """A checagem era por igualdade exata, então 'AdsBot-Google-Mobile' — também
    isento na documentação — caía no grupo curinga."""
    r = parse_robots(BLOQUEIA_TUDO)
    assert is_allowed(r, "AdsBot-Google-Mobile", "/") is True
    # E a isenção não se espalha para quem só compartilha o começo do nome.
    assert is_allowed(r, "Googlebot", "/") is False


# --------------------------------------------------------------------------
# Defeitos encontrados na revisão: cada um foi reproduzido antes de virar teste.
# --------------------------------------------------------------------------

COM_BOM = "\ufeffUser-agent: Mediapartners-Google\nDisallow: /\n"


def test_bom_no_inicio_nao_apaga_o_arquivo_inteiro():
    """O BOM é Cf, não espaço em branco, então o strip() deixava ele no lugar.

    O primeiro campo virava '<BOM>user-agent', era descartado como desconhecido, e
    aí toda regra abaixo dele caía no ramo 'sem grupo' e sumia junto: um arquivo com
    BOM que bloqueia o Mediapartners-Google era relatado como liberado — exatamente
    a falha que esta auditoria existe para pegar. O Google documenta o BOM como
    linha inválida a ser ignorada.
    """
    r = parse_robots(COM_BOM)
    assert r.is_empty is False
    assert blocks_everything(r, ADSENSE_CRAWLER) is True
    curinga = parse_robots("\ufeffUser-agent: *\nDisallow: /\n")
    assert blocks_everything(curinga, INDEX_CRAWLER) is True


def test_padrao_cheio_de_curingas_casa_em_tempo_limitado():
    """ReDoS: o padrão vem do robots.txt de um estranho e virava regex.

    Cada '*' era um '.*' sem limite. O estouro está no NÃO-casamento, quando o
    backtracking precisa esgotar todas as combinações antes de desistir: o padrão
    '/a*a*…*a*b' contra '/' + 'a' * 40 levava 0,26s com 8 estrelas, 3,57s com 10 e
    35,6s com 12 — e o timeout do requests não alcança o interior do re.match, então
    um crawl vivo ficava preso numa linha 'Disallow' escrita por um estranho.
    """
    r = parse_robots("User-agent: *\nDisallow: /" + "a*" * 12 + "b\n")
    caminho = "/" + "a" * 40

    inicio = time.perf_counter()
    permitido = is_allowed(r, INDEX_CRAWLER, caminho)
    decorrido = time.perf_counter() - inicio

    assert permitido is True, "o padrão termina em 'b' e o caminho não: não casa"
    assert decorrido < 1.0, f"casamento levou {decorrido:.2f}s — backtracking exponencial de volta"

    # E a semântica não mudou junto: o padrão cheio de curingas que CASA segue casando.
    casa = parse_robots("User-agent: *\nDisallow: /" + "*a" * 12 + "\n")
    assert is_allowed(casa, INDEX_CRAWLER, caminho) is False


def test_arquivo_maior_que_500_kib_e_truncado():
    """Limite documentado pelo Google: 500 KiB, o resto é ignorado.

    Sem o corte, um arquivo de 8,7 MB virava 400.001 regras e um único is_allowed()
    levava 3,56s — o orçamento inteiro do crawl gasto num arquivo hostil.
    """
    enchimento = "Disallow: /a/\n" * 40_000
    texto = "User-agent: *\n" + enchimento + "Disallow: /depois-do-corte/\n"
    assert len(texto.encode("utf-8")) > 500 * 1024

    r = parse_robots(texto)
    assert is_allowed(r, INDEX_CRAWLER, "/a/x") is False
    assert is_allowed(r, INDEX_CRAWLER, "/depois-do-corte/x") is True
    assert len(r.groups[0].rules) < 40_000


TOKEN_CURTO_E_CURINGA = """
User-agent: Google
Disallow: /admin/

User-agent: *
Disallow: /
"""


def test_token_curto_do_arquivo_nao_anula_o_curinga():
    """O casamento era startswith cru, então 'Google' capturava o Googlebot.

    E, como um grupo específico anula o curinga inteiro, o 'Disallow: /' que de fato
    governa o Googlebot sumia: este arquivo era relatado como liberado na raiz. A
    RFC 9309 compara com a lista de tokens declarados do crawler — 'Googlebot-News'
    obedece um grupo 'Googlebot' porque declara os dois tokens, não por prefixo de
    string. A aproximação aqui é exigir fronteira de '-'.
    """
    r = parse_robots(TOKEN_CURTO_E_CURINGA)
    assert blocks_everything(r, INDEX_CRAWLER) is True
    assert is_allowed(r, INDEX_CRAWLER, "/") is False
    # A fronteira não estraga o que já valia: 'googlebot' segue governando a família.
    familia = parse_robots("User-agent: Googlebot\nDisallow: /\n")
    assert is_allowed(familia, "Googlebot-Image", "/") is False


def test_curinga_com_lixo_depois_ainda_e_curinga():
    """'User-agent: * Googlebot' não governava ninguém.

    O token saía vazio: nem curinga nem específico, então um arquivo cujo único
    grupo era esse mais 'Disallow: /' lia-se como libera-tudo. A implementação de
    referência do Google trata um valor que começa com '*' como global.
    """
    r = parse_robots("User-agent: * Googlebot\nDisallow: /\n")
    assert blocks_everything(r, INDEX_CRAWLER) is True
    assert is_allowed(r, "Bingbot", "/") is False
    # E o curinga com lixo continua sendo curinga: quem é isento segue isento.
    assert is_allowed(r, ADSENSE_CRAWLER, "/") is True


PRODUTOS_ENCODED = "/%D0%BF%D1%80%D0%BE%D0%B4%D1%83%D0%BA%D1%82%D1%8B/"


def test_percent_encoding_e_normalizado_dos_dois_lados():
    """RFC 9309: os octetos têm de ser trazidos à mesma forma antes de comparar.

    Os três casos abaixo erravam para o lado de crawlear MAIS: a regra existia, não
    casava, e o crawler entrava numa rota que o site tinha excluído.
    """
    regra_crua = parse_robots("User-agent: *\nDisallow: /продукты/\n")
    assert is_allowed(regra_crua, INDEX_CRAWLER, PRODUTOS_ENCODED + "x") is False

    regra_encodada = parse_robots(f"User-agent: *\nDisallow: {PRODUTOS_ENCODED}\n")
    assert is_allowed(regra_encodada, INDEX_CRAWLER, "/продукты/x") is False

    til = parse_robots("User-agent: *\nDisallow: /~user\n")
    assert is_allowed(til, INDEX_CRAWLER, "/%7Euser") is False

    # E %2F NÃO é '/': é reservado, então continua sendo outro caminho.
    barra = parse_robots("User-agent: *\nDisallow: /%2Fadmin\n")
    assert is_allowed(barra, INDEX_CRAWLER, "/%2Fadmin") is False
    assert is_allowed(barra, INDEX_CRAWLER, "/admin") is True


# --------------------------------------------------------------------------
# Regras que o docstring do módulo documenta e que nenhum teste conseguia
# reprovar: os três mutantes abaixo sobreviviam à suíte inteira.
# --------------------------------------------------------------------------


def test_user_agents_consecutivos_formam_um_grupo_so():
    """Duas linhas 'User-agent' seguidas nomeiam o MESMO grupo de regras.

    Abrindo um grupo novo a cada linha, o primeiro agente fica com um grupo vazio —
    específico e sem regra nenhuma — e passa a ser liberado em tudo, com o curinga
    já anulado por ele.
    """
    r = parse_robots("User-agent: Googlebot\nUser-agent: Bingbot\nDisallow: /\n")
    assert blocks_everything(r, INDEX_CRAWLER) is True
    assert blocks_everything(r, "Bingbot") is True


def test_regras_antes_do_primeiro_user_agent_sao_descartadas():
    """Regra sem grupo não governa ninguém — o Google ignora, e nós também.

    Adotá-la como curinga bloquearia crawlers que o arquivo nunca mencionou;
    grudá-la no primeiro grupo que aparecer bloquearia o crawler errado.
    """
    r = parse_robots("Disallow: /\n\nUser-agent: Googlebot\nDisallow: /privado/\n")
    # Ninguém é governado pela regra órfã.
    assert is_allowed(r, "Bingbot", "/") is True
    assert is_allowed(r, INDEX_CRAWLER, "/") is True
    # E o grupo de verdade continua valendo.
    assert is_allowed(r, INDEX_CRAWLER, "/privado/x") is False


def test_bom_sobrevive_ao_fallback_iso_8859_1_e_ainda_e_ignorado():
    """A metade que faltava: um robots.txt servido como `text/plain` SEM charset
    — o default do nginx para .txt, e o caso mais comum dos dois — deixa requests
    no fallback ISO-8859-1, e os três bytes do BOM chegam como 'ï»¿'. Mesma
    consequência: o arquivo inteiro evapora e todo crawler lê como liberado."""
    utf8 = "﻿User-agent: *\nDisallow: /\n"
    mojibake = utf8.encode("utf-8").decode("iso-8859-1")
    assert mojibake.startswith("ï»¿")

    for texto in (utf8, mojibake):
        r = parse_robots(texto)
        assert len(r.groups) == 1
        assert blocks_everything(r, INDEX_CRAWLER) is True


def test_estrela_colada_em_texto_nao_e_o_grupo_global():
    """Aceitar qualquer estrela inicial fazia `User-agent: *bot` bloquear o
    Googlebot, que o Google deixaria passar. A implementação de referência trata
    `*` como global só sozinho ou seguido de espaço."""
    colado = parse_robots("User-agent: *bot\nDisallow: /\n")
    assert is_allowed(colado, INDEX_CRAWLER, "/") is True

    # E o que motivou a mudança continua valendo: estrela + lixo separado é global.
    com_espaco = parse_robots("User-agent: * Googlebot\nDisallow: /\n")
    assert blocks_everything(com_espaco, INDEX_CRAWLER) is True
    sozinha = parse_robots("User-agent: *\nDisallow: /\n")
    assert blocks_everything(sozinha, INDEX_CRAWLER) is True


def test_digito_encerra_o_product_token():
    """`[a-z0-9_-]+` parecia inofensivo e não era: `Mediapartners-Google2` rendia
    o token `mediapartners-google2`, que não governa crawler nenhum, então o
    grupo era descartado, a isenção do curinga entrava, e um arquivo que bloqueia
    a veiculação de anúncios voltava como "liberado". A RFC 9309 define
    `identifier = 1*(%x2D / %x41-5A / %x5F / %x61-7A)` — sem dígitos — e o
    extrator do Google para no primeiro caractere fora de [a-zA-Z_-]."""
    r = parse_robots("User-agent: Mediapartners-Google2\nDisallow: /\n")
    assert r.groups[0].agents == ["mediapartners-google"]
    assert blocks_everything(r, ADSENSE_CRAWLER) is True

    r2 = parse_robots("User-agent: Googlebot2\nDisallow: /\n")
    assert blocks_everything(r2, INDEX_CRAWLER) is True


def test_precedencia_conta_o_padrao_cru_e_nao_o_normalizado():
    """Medir na forma normalizada invertia o resultado sempre que uma regra
    encodada encontrava uma com curinga: `Allow: /*x` contra `Disallow: /%7E*`
    no path `/%7Ex` dava empate 3-3 que o Allow ganhava, onde o Google conta
    3 contra 5 e reprova — o `MaybeEscapePattern` dele só maiúscula escapes e
    encoda octetos altos, nunca decodifica."""
    r = parse_robots("User-agent: *\nAllow: /*x\nDisallow: /%7E*\n")
    assert is_allowed(r, INDEX_CRAWLER, "/%7Ex") is False


def test_cap_de_500_kib_vale_pelo_valor_e_corta_linha_inteira():
    """O teste anterior afirmava só "houve algum truncamento": passava com o cap
    em 1 KiB e com corte no meio da linha. Aqui o valor do cap e o corte em linha
    inteira são as duas asserções — e o corte em linha inteira é a alegação de
    segurança do módulo, porque metade de `Disallow: /caminho/longo` é uma regra
    MAIS LARGA do que o site escreveu."""
    from adsense_checks.robots import _MAX_BYTES

    enchimento = "Disallow: /preenchimento\n" * 25_000
    assert len(enchimento.encode()) > _MAX_BYTES  # tem de passar do cap de verdade
    texto = "User-agent: *\n" + enchimento + "Disallow: /depois-do-corte\n"
    r = parse_robots(texto)

    # A regra depois do corte não pode ter sobrevivido...
    assert is_allowed(r, INDEX_CRAWLER, "/depois-do-corte/x") is True
    # ...e o corte não pode ter deixado uma regra parcial, que bloquearia demais.
    assert all(p in ("/preenchimento", "") for _a, p in r.groups[0].rules)
    # E um arquivo logo abaixo do cap sobrevive inteiro.
    curto = "User-agent: *\n" + "Disallow: /preenchimento\n" * 100 + "Disallow: /fim\n"
    assert is_allowed(parse_robots(curto), INDEX_CRAWLER, "/fim") is False


def test_arquivo_grande_sem_newline_nenhum_nao_vira_liberar_tudo():
    """`rfind` devolvia -1 e `cut[:0]` esvaziava o arquivo inteiro, então
    qualquer arquivo grande sem newline — uma linha longa de qualquer coisa —
    virava allow-all, que é a direção que esconde um bloqueio real."""
    from adsense_checks.robots import _MAX_BYTES, _truncate

    # SEM newline nenhum: a fixture anterior tinha um depois de `User-agent: *`,
    # então `rfind` achava a posição 13, nunca -1, e o ramo que este teste nomeia
    # jamais era executado. Trocar a guarda por `if True:` — que é exatamente o
    # defeito — passava por ele.
    texto = "Disallow: /" + "a" * (520 * 1024)
    assert "\n" not in texto
    assert len(texto.encode()) > _MAX_BYTES
    assert len(_truncate(texto)) == _MAX_BYTES  # preserva, em vez de esvaziar

    # E pela porta da frente: o grupo sobrevive ao truncamento.
    r = parse_robots("User-agent: *\nDisallow: /" + "a" * (520 * 1024))
    assert len(r.groups) == 1
    assert r.groups[0].agents == ["*"]


def test_apenas_o_conjunto_unreserved_e_decodificado():
    """`%2A` virar `*` transformaria uma regra literal em curinga, e `%2F` virar
    `/` mudaria a fronteira de segmento. As duas asserções anteriores sobre `%2F`
    passavam com QUALQUER normalização simétrica — os dois lados decodificavam
    junto, então casavam junto. O caso que discrimina é uma regra encodada contra
    um path literal."""
    # `%2A` fica encodado, então a regra não vira curinga e não casa `/axb`...
    assert is_allowed(parse_robots("User-agent: *\nDisallow: /a%2Ab\n"),
                      INDEX_CRAWLER, "/axb") is True
    # ...e também não casa o `*` LITERAL, porque o path literal normaliza para `*`
    # e a regra para `%2A`: as duas grafias são coisas diferentes, que é o ponto.
    assert is_allowed(parse_robots("User-agent: *\nDisallow: /a%2Ab\n"),
                      INDEX_CRAWLER, "/a*b") is True
    # A regra encodada casa o path escrito do mesmo jeito.
    assert is_allowed(parse_robots("User-agent: *\nDisallow: /a%2Ab\n"),
                      INDEX_CRAWLER, "/a%2Ab") is False
    # `%3C` não é reserved mas também não é unreserved: o Google nunca decodifica.
    assert is_allowed(parse_robots("User-agent: *\nDisallow: /%3Ca\n"),
                      INDEX_CRAWLER, "/<a") is True
    # `%7E` é unreserved: decodifica, e as duas grafias convergem.
    assert is_allowed(parse_robots("User-agent: *\nDisallow: /~user\n"),
                      INDEX_CRAWLER, "/%7Euser") is False


def test_curinga_no_primeiro_caractere_casa():
    """Nenhum padrão de teste tinha `*` na primeira posição, então
    `elif star >= 0` virando `> 0` sobrevivia — e `Disallow: *.pdf` parava de
    casar `/docs/x.pdf`: um caminho barrado lido como liberado."""
    r = parse_robots("User-agent: *\nDisallow: *.pdf\n")
    assert is_allowed(r, INDEX_CRAWLER, "/docs/x.pdf") is False
    assert is_allowed(r, INDEX_CRAWLER, "/x.pdf") is False
    assert is_allowed(r, INDEX_CRAWLER, "/docs/x.html") is True


def test_o_arquivo_exatamente_no_cap_sobrevive_inteiro():
    """`len(raw) <= _MAX_BYTES`: no tamanho exato nada é cortado."""
    from adsense_checks.robots import _MAX_BYTES, _truncate

    exato = "a" * _MAX_BYTES
    assert len(_truncate(exato)) == _MAX_BYTES
    assert len(_truncate(exato + "b")) < len(exato) + 1


def test_escape_percentual_truncado_nao_e_lido_como_hex():
    """`i + 2 < len(raw)`: com `<=`, um `%` seguido de UM dígito no fim da
    string lê um hex de um caractere e vira outro byte — `/a%2` viraria `/a%02`
    em vez do `%` literal que a regra de fato tem."""
    from adsense_checks.robots import _normalize

    assert _normalize("/a%2") == "/a%252"
    assert _normalize("/a%") == "/a%25"
    # E um escape completo continua sendo decodificado normalmente.
    assert _normalize("/a%7Eb") == "/a~b"


def test_arquivo_exatamente_no_cap_com_newline_nao_perde_a_ultima_linha():
    """`len(raw) <= _MAX_BYTES`: no tamanho exato nada é cortado. Com `<`, o
    arquivo entra no ramo de truncamento e o corte de volta até a última quebra
    descarta a linha final — uma regra que o site escreveu."""
    from adsense_checks.robots import _MAX_BYTES, _truncate

    corpo = "Disallow: /a\n" * (_MAX_BYTES // 13)
    corpo += "b" * (_MAX_BYTES - len(corpo.encode()))
    assert len(corpo.encode()) == _MAX_BYTES

    assert _truncate(corpo) == corpo  # intacto
    assert len(_truncate(corpo + "c")) < len(corpo)  # um byte a mais e corta
