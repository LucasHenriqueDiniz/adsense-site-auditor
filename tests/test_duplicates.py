"""Testes de similaridade entre páginas.

Cada teste com nome de "regressão" corresponde a um defeito reproduzido em
scripts/check_duplicates.py. O nome diz o que o teste impede, e o docstring
carrega o caso de reprodução original.
"""

from __future__ import annotations

import time

import pytest

from adsense_checks.duplicates import (
    DEFAULT_DUPLICATE_PAGE_RATIO,
    DEFAULT_SIMILARITY_THRESHOLD,
    OVERLAP_HIGH_RISK,
    OVERLAP_MONITOR,
    SERP_SAMPLE_SIZE,
    check_duplication,
    check_urls,
    compare_against,
    fetch_page_texts,
    find_duplicate_groups,
    overlap_band,
    shingles,
    similarity,
)
from adsense_checks.status import Status


def janela(inicio: int, quantidade: int) -> str:
    """Texto sintético de palavras distintas: `w001 w002 ...`.

    Deslizar a janela dá controle exato sobre a similaridade de shingles, o que
    permite afirmar números em vez de "mais ou menos parecido".
    """
    return " ".join(f"w{i:03d}" for i in range(inicio, inicio + quantidade))


PAGINA_QUIZ = (
    "Which Dog Breed Are You? Answer twenty questions about your habits and "
    "discover the breed that matches your personality. Share your result with "
    "friends and compare answers. You are a Golden Retriever."
)


# --------------------------------------------------------------------------
# A métrica
# --------------------------------------------------------------------------


def test_textos_identicos_sao_1_e_disjuntos_sao_0():
    assert similarity(PAGINA_QUIZ, PAGINA_QUIZ) == 1.0
    assert similarity(janela(1, 50), janela(900, 50)) == 0.0


def test_similaridade_e_simetrica():
    """Simetria só é testável com textos de TAMANHOS diferentes.

    A versão anterior comparava janela(1,100) com janela(21,100): mesmo número
    de shingles, denominadores iguais, e nesse caso até uma métrica assimétrica
    como containment devolve o mesmo valor nos dois sentidos. Trocar Jaccard por
    containment sobrevivia à suíte inteira.

    Com tamanhos desiguais a diferença aparece: containment(curto, longo) tende a
    1.0 enquanto containment(longo, curto) tende a 0.1, porque cada um divide por
    um denominador diferente. Jaccard divide pela união e não tem lado.
    """
    curto, longo = janela(1, 40), janela(1, 400)
    assert similarity(curto, longo) == similarity(longo, curto)

    # E o valor não pode ser o de containment em nenhuma das direções: o curto
    # está inteiramente contido no longo, então containment(curto, longo) == 1.0.
    assert similarity(curto, longo) < 0.5, (
        "similaridade alta demais para dois textos de tamanhos tão diferentes: "
        "parece containment, não Jaccard"
    )


def test_regressao_similaridade_nao_e_deflacionada_por_palavra_frequente():
    """difflib usava autojunk=True: em sequência com >=200 elementos, todo
    elemento presente em mais de 1% das posições era descartado como 'popular'.
    Em prosa isso jogava fora quase o alfabeto inteiro e subestimava a razão de
    forma imprevisível (medido no repo: 0.7903 virou 0.7427). Duas páginas que
    diferem em ~1% das palavras são duplicatas óbvias e a métrica precisa dizer
    isso, mesmo com um token repetido em 25% das posições.

    A afirmação "não é deflacionada" só tem conteúdo se comparada com alguma
    coisa, então o teste crava as duas medidas que a frequência não pode mexer:
    o valor exato de Jaccard entre os conjuntos de shingles, e a igualdade com o
    MESMO texto tendo o token repetido trocado por tokens únicos. Um limite
    frouxo como `> 0.95` não serve: com o difflib caractere-a-caractere original
    esses textos dão 0.9957 e passariam pelo limite, sem descartar nada.
    """
    # "the" ocupa 25% das posições — muito acima do 1% que o autojunk descartava.
    comum = " ".join("the" if i % 4 == 0 else f"palavra{i:04d}" for i in range(600))
    p = comum + " alpha beta gamma delta epsilon"
    q = comum + " zeta eta theta iota kappa"

    # 605 palavras -> 601 janelas em cada lado; as 596 janelas inteiramente
    # dentro do prefixo comum são compartilhadas. Nenhuma delas é descartada por
    # conter "the": 596 / (601 + 601 - 596).
    assert similarity(p, q) == pytest.approx(596 / 606, abs=1e-9)
    assert similarity(p, q) >= DEFAULT_SIMILARITY_THRESHOLD

    # Mesma estrutura, token repetido trocado por um token único por posição: a
    # frequência de um token não pode alterar o peso da janela que o contém.
    unicos = " ".join(f"unico{i:04d}" if i % 4 == 0 else f"palavra{i:04d}" for i in range(600))
    assert similarity(unicos + " alpha beta gamma delta epsilon",
                      unicos + " zeta eta theta iota kappa") == similarity(p, q)


def test_regressao_comparacao_e_linear_no_tamanho_do_texto():
    """SequenceMatcher é O(n²) no tamanho do texto: comparar duas páginas
    grandes levava minutos. Shingles + Jaccard constroem os conjuntos uma vez
    (linear) e intersectam (linear no menor). O limite abaixo é folgado de
    propósito — o valor medido foi ~0.02s; qualquer volta ao alinhamento
    caractere-a-caractere estoura por ordens de grandeza.

    Rapidez sozinha não é a propriedade: o difflib original também termina em
    0.07s nestes dois textos, porque o autojunk descarta todos os caracteres
    populares e ele devolve 7.5e-06 sem alinhar nada. Por isso o teste também
    exige a RESPOSTA: as duas cadeias são a mesma sequência cíclica deslocada em
    7 palavras, logo têm o mesmo conjunto de janelas e similaridade 1.0.
    """
    grande_a = " ".join(f"word{i % 9973}" for i in range(60_000))
    grande_b = " ".join(f"word{(i + 7) % 9973}" for i in range(60_000))

    inicio = time.perf_counter()
    resultado = similarity(grande_a, grande_b)
    decorrido = time.perf_counter() - inicio

    assert resultado == pytest.approx(1.0, abs=1e-9)
    assert decorrido < 5.0, f"comparação levou {decorrido:.2f}s"


def test_texto_menor_que_a_janela_ainda_compara_com_outro_igual():
    """Fallback documentado: texto com menos palavras que a janela vira um único
    shingle com todas elas, então duas páginas curtas idênticas continuam sendo
    detectadas em vez de virarem conjunto vazio.
    """
    assert similarity("oi mundo", "oi mundo") == 1.0
    assert similarity("oi mundo", "tchau mundo") == 0.0


def test_texto_vazio_nao_e_duplicata_perfeita_de_outro_vazio():
    """0/0 é indefinido; devolver 1.0 transformaria ausência de evidência em
    achado. Páginas sem texto são tratadas como não-analisáveis mais acima.
    """
    assert shingles("") == frozenset()
    assert similarity("", "") == 0.0
    assert similarity("", PAGINA_QUIZ) == 0.0


# --------------------------------------------------------------------------
# Agrupamento
# --------------------------------------------------------------------------


def test_regressao_varredura_de_pares_e_exaustiva():
    """Defeito: o laço interno consultava `processed`, então uma página absorvida
    por um grupo anterior nunca mais era comparada. No caso original A/B/C com
    sim(A,B)=0.9182, sim(B,C)=0.9494 e sim(A,C)=0.8553, o par B↔C — o MAIS
    similar de todos — nunca era reportado, porque B entrou em `processed` junto
    com A.

    Aqui a aritmética de shingles reproduz a mesma forma: A~B=0.6552,
    B~C=0.8113 (o maior), A~C=0.5238 (abaixo do limiar). O par B↔C tem de
    aparecer, e o par A↔C não pode aparecer — o grupo é componente conexa, não
    promessa de que todo mundo é parecido com todo mundo.
    """
    textos = {"/a": janela(1, 100), "/b": janela(21, 100), "/c": janela(31, 100)}

    grupos = find_duplicate_groups(textos, threshold=0.6)

    assert len(grupos) == 1
    grupo = grupos[0]
    assert grupo.urls == ("/a", "/b", "/c")
    pares = {(a, b) for a, b, _ in grupo.pairs}
    assert ("/b", "/c") in pares
    assert ("/a", "/b") in pares
    assert ("/a", "/c") not in pares
    assert grupo.max_similarity == pytest.approx(0.8113, abs=1e-4)


def test_grupos_sao_deterministicos_independente_da_ordem_de_insercao():
    """O agrupamento antigo dependia da ordem do dict; dois relatórios sobre o
    mesmo crawl podiam sair diferentes.
    """
    a, b, c, d = janela(1, 100), janela(1, 100), janela(500, 100), janela(500, 100)
    direto = find_duplicate_groups({"/a": a, "/b": b, "/c": c, "/d": d})
    invertido = find_duplicate_groups({"/d": d, "/c": c, "/b": b, "/a": a})

    assert [g.urls for g in direto] == [g.urls for g in invertido]
    assert [g.urls for g in direto] == [("/a", "/b"), ("/c", "/d")]


def test_pagina_unica_no_grupo_nao_vira_grupo():
    grupos = find_duplicate_groups({"/a": janela(1, 100), "/z": janela(900, 100)})
    assert grupos == ()


# --------------------------------------------------------------------------
# Contagem e veredito
# --------------------------------------------------------------------------


def test_regressao_risco_conta_paginas_e_nao_grupos():
    """Defeito blocker: `len(duplicates) > len(urls) * 0.3` comparava GRUPOS com
    PÁGINAS. Com 10 páginas byte-idênticas: len(duplicates)=1, len(urls)=10,
    `1 > 3.0` é False — e o script imprimia "Duplication risk appears
    acceptable" com exit 0 para um site 100% duplicado.
    """
    textos = {f"/quiz{i}": PAGINA_QUIZ for i in range(10)}

    resultado = check_duplication(textos)

    # O indicador antigo continua valendo 1 e continua sendo < 3.0...
    assert len(resultado.groups) == 1
    assert not len(resultado.groups) > len(textos) * DEFAULT_DUPLICATE_PAGE_RATIO
    # ...mas o veredito agora vem da contagem de páginas envolvidas.
    assert resultado.duplicate_page_count == 10
    assert resultado.duplicate_ratio == 1.0
    assert resultado.status is Status.FAIL


def test_regressao_mais_duplicacao_nunca_pontua_menos_que_menos_duplicacao():
    """A inversão exata do defeito: 10 páginas idênticas (1 grupo) passavam,
    enquanto 4 páginas em 2 pares (2 grupos) — metade do problema — gritavam
    risco alto. A monotonicidade tem de valer: pior duplicação, indicador maior.
    """
    pior = check_duplication({f"/p{i}": PAGINA_QUIZ for i in range(10)})
    melhor = check_duplication(
        {
            "/a": janela(1, 100),
            "/b": janela(1, 100),
            "/c": janela(500, 100),
            "/d": janela(500, 100),
            **{f"/u{i}": janela(1000 + i * 200, 100) for i in range(6)},
        }
    )

    # O sinal antigo se inverte: o caso pior tem MENOS grupos que o caso melhor.
    assert len(pior.groups) < len(melhor.groups)
    # O sinal novo não se inverte. Este é o assert que mata o indicador antigo —
    # o de status abaixo NÃO mata, porque os dois casos caem em FAIL e `>=` é
    # satisfeito por igualdade. Fica registrado para ninguém confiar nele.
    assert pior.duplicate_ratio > melhor.duplicate_ratio

    # Discriminação de verdade: um caso sem duplicação alguma tem de sair com
    # status estritamente menor que o caso ruim. Sem isto, `status` poderia ser
    # constante e a suíte não notaria.
    limpo = check_duplication({f"/x{i}": janela(3000 + i * 300, 120) for i in range(6)})
    assert limpo.duplicate_ratio == 0.0
    assert limpo.status < pior.status
    assert limpo.status < melhor.status


def test_regressao_resumo_nao_rotula_contagem_de_grupos_como_paginas():
    """Defeito high: o cabeçalho imprimia `Found 1 pages with similar content`
    para 10 páginas idênticas e listava 9 URLs logo abaixo, contradizendo a si
    mesmo. O número de páginas e o número de grupos são coisas diferentes e o
    texto tem de dizer qual é qual.
    """
    resultado = check_duplication({f"/quiz{i}": PAGINA_QUIZ for i in range(10)})

    resumo = resultado.summary()
    assert "10 of 10 analyzed pages" in resumo
    assert "1 duplicate group(s)" in resumo


def test_regressao_resumo_usa_o_limiar_efetivo_e_nao_80_por_cento():
    """Defeito high: a mensagem cravava ">80%" no texto ignorando --threshold, e
    rodando com 0.5 anunciava que pares de 56% tinham "mais de 80% de conteúdo
    similar".
    """
    textos = {"/a": janela(1, 100), "/b": janela(21, 100)}

    resultado = check_duplication(textos, threshold=0.5)

    assert ">=50%" in resultado.summary()
    assert "80%" not in resultado.summary()


def test_regressao_limiar_padrao_cobre_a_faixa_de_risco_da_rubrica():
    """Defeito medium: o padrão 0.8 ficava acima de toda a faixa que
    ADS-CONTENT-OVERLAP chama de High Risk (>60%), então nenhum par que a rubrica
    queria ver reportado aparecia — e o auditor concluía que ADS-CONTENT-02
    passava.
    """
    assert DEFAULT_SIMILARITY_THRESHOLD == OVERLAP_HIGH_RISK == 0.6

    # Par com 65.5% de similaridade: risco alto pela rubrica.
    textos = {"/a": janela(1, 100), "/b": janela(21, 100)}
    assert check_duplication(textos).groups != ()
    # Era exatamente esse par que o limiar antigo escondia.
    assert check_duplication(textos, threshold=0.8).groups == ()


def test_duplicacao_abaixo_da_cota_de_paginas_ainda_avisa():
    """Um par duplicado em 10 páginas não é o site inteiro, mas também não é OK:
    o achado existe e tem de aparecer como WARNING.
    """
    textos = {
        "/a": janela(1, 100),
        "/b": janela(1, 100),
        **{f"/u{i}": janela(1000 + i * 200, 100) for i in range(8)},
    }

    resultado = check_duplication(textos)

    assert resultado.duplicate_ratio == pytest.approx(0.2)
    assert resultado.status is Status.WARNING


def test_site_sem_duplicatas_e_ok():
    textos = {f"/u{i}": janela(1 + i * 200, 100) for i in range(5)}

    resultado = check_duplication(textos)

    assert resultado.groups == ()
    assert resultado.duplicate_page_count == 0
    assert resultado.status is Status.OK


# --------------------------------------------------------------------------
# Honestidade: o que não deu para observar nunca vira aprovação
# --------------------------------------------------------------------------


def test_regressao_pagina_unica_nao_prova_ausencia_de_duplicatas():
    """Defeito medium: no modo URL única, `urls = [target]` não tinha com o que
    comparar e mesmo assim o script imprimia "No significant duplicates found" +
    "Duplication risk appears acceptable", com saída idêntica para qualquer URL.
    """
    resultado = check_duplication({"/so-uma": PAGINA_QUIZ})

    assert resultado.status is Status.ERROR
    assert resultado.groups == ()
    assert any("at least 2" in razao for razao in resultado.reasons)


def test_nenhuma_pagina_analisavel_e_error_e_nao_aprovacao():
    """Caso extremo confirmado: crawl.json com 50 resultados em erro levava a
    "Fetching content from 0 URLs" e terminava em "risk appears acceptable",
    exit 0.
    """
    resultado = check_duplication({})

    assert resultado.status is Status.ERROR
    assert resultado.duplicate_ratio == 0.0


def test_regressao_falha_de_fetch_escala_para_error_em_vez_de_sumir():
    """Defeito high: `except Exception: return None` engolia DNS, timeout, SSL,
    403 e 404 sem dizer o motivo, e o script seguia reportando ausência de
    duplicatas como se tivesse analisado o site.
    """
    resultado = check_duplication(
        {"/a": janela(1, 100), "/b": janela(500, 100)},
        unavailable={"/c": "HTTP 403", "/d": "ConnectTimeout: ..."},
    )

    assert resultado.status is Status.ERROR
    assert set(resultado.unanalyzable) == {"/c", "/d"}
    # A razão de cada página chega ao usuário, não só a contagem.
    assert any("403" in razao for razao in resultado.reasons)


def test_paginas_sem_texto_extraido_nao_contam_como_duplicatas_entre_si():
    """Duas extrações vazias seriam "100% idênticas" para uma métrica ingênua.
    Aqui elas saem do denominador e viram não-analisáveis com motivo.
    """
    resultado = check_duplication({"/a": "", "/b": "   \n  ", "/c": PAGINA_QUIZ})

    assert resultado.groups == ()
    assert set(resultado.unanalyzable) == {"/a", "/b"}
    assert resultado.analyzed == ("/c",)
    assert resultado.status is Status.ERROR


def test_paginas_ilegiveis_ficam_fora_do_denominador():
    """Inflar o denominador com páginas que não foram lidas diluiria a razão e
    faria um site duplicado parecer melhor quanto mais fetch falhasse.
    """
    resultado = check_duplication(
        {f"/p{i}": PAGINA_QUIZ for i in range(4)},
        unavailable={f"/erro{i}": "HTTP 500" for i in range(96)},
    )

    assert resultado.duplicate_ratio == 1.0
    assert resultado.status is Status.ERROR


# --------------------------------------------------------------------------
# ADS-CONTENT-OVERLAP: comparação contra textos fornecidos pelo chamador
# --------------------------------------------------------------------------


def test_overlap_sem_fontes_e_missing_e_nunca_ok():
    """Este módulo não faz busca em SERP. Sem os textos dos concorrentes, o
    requisito fica NÃO MEDIDO — e não medido não é "pouco overlap".
    """
    resultado = compare_against(PAGINA_QUIZ, [])

    assert resultado.status is Status.MISSING
    assert resultado.overlap is None
    assert resultado.band is None
    assert any("no SERP search" in razao for razao in resultado.reasons)


def test_overlap_usa_containment_quando_o_concorrente_e_mais_longo():
    """Overlap na rubrica é "quanto do MEU conteúdo existe lá fora". Uma página
    concorrente 10x maior que contém nosso texto inteiro dá Jaccard ~0.09 e
    containment 1.0; o número que descreve o risco é o segundo.
    """
    nosso = janela(1, 40)
    concorrente = janela(1, 400)

    resultado = compare_against(nosso, {"https://rival.example/a": concorrente})

    (fonte,) = resultado.sources
    assert fonte.overlap == pytest.approx(1.0)
    assert fonte.jaccard < 0.15
    assert resultado.status is Status.FAIL


def test_overlap_respeita_as_faixas_da_rubrica():
    assert overlap_band(0.0) == "safe"
    assert overlap_band(OVERLAP_MONITOR - 0.01) == "safe"
    assert overlap_band(OVERLAP_MONITOR) == "monitor"
    assert overlap_band(OVERLAP_HIGH_RISK - 0.01) == "monitor"
    assert overlap_band(OVERLAP_HIGH_RISK) == "high-risk"
    assert overlap_band(1.0) == "high-risk"


def test_overlap_baixo_com_as_cinco_fontes_e_ok():
    nosso = janela(1, 200)
    fontes = {
        f"https://rival{i}.example": janela(5000 + i * 500, 200)
        for i in range(SERP_SAMPLE_SIZE)
    }

    resultado = compare_against(nosso, fontes)

    assert resultado.overlap == pytest.approx(0.0)
    assert resultado.band == "safe"
    assert resultado.status is Status.OK


def test_amostra_parcial_de_serp_nao_e_aprovacao_limpa():
    """Comparar contra 2 dos 5 resultados que o requisito nomeia responde parte
    da pergunta. O relatório não pode apresentar isso como requisito satisfeito.
    """
    nosso = janela(1, 200)
    fontes = {
        "https://rival1.example": janela(5000, 200),
        "https://rival2.example": janela(6000, 200),
    }

    resultado = compare_against(nosso, fontes)

    assert resultado.status is Status.MISSING
    assert len(resultado.sources) == 2
    assert any("2 of the 5 sources" in razao for razao in resultado.reasons)


def test_overlap_alto_vence_a_amostra_parcial_no_veredito():
    """escalate() em ação: amostra incompleta é MISSING, overlap alto é FAIL, e
    a combinação tem de ser FAIL — o mais severo, nunca o último avaliado.
    """
    nosso = janela(1, 200)

    resultado = compare_against(nosso, {"https://rival.example": janela(1, 200)})

    assert resultado.status is Status.FAIL
    assert resultado.band == "high-risk"


def test_pagina_propria_vazia_e_error_e_nao_zero_de_overlap():
    resultado = compare_against("", {"https://rival.example": PAGINA_QUIZ})

    assert resultado.status is Status.ERROR
    assert resultado.overlap is None


def test_fonte_sem_texto_e_reportada_e_nao_descartada_em_silencio():
    nosso = janela(1, 200)
    fontes = {f"https://rival{i}.example": janela(5000 + i * 500, 200) for i in range(4)}
    fontes["https://rival-vazio.example"] = ""

    resultado = compare_against(nosso, fontes)

    assert len(resultado.sources) == 4
    assert any("rival-vazio" in razao for razao in resultado.reasons)
    assert resultado.status is Status.MISSING


def test_compare_against_aceita_sequencia_alem_de_mapa():
    resultado = compare_against(janela(1, 200), [janela(1, 200)], expected_sources=1)

    assert [f.label for f in resultado.sources] == ["source-1"]
    assert resultado.status is Status.FAIL


def test_um_texto_de_concorrente_solto_e_uma_fonte_e_nao_uma_lista_de_letras():
    """Defeito medium: `sources: Sequence[str]` aceita `str`, e passar UM texto
    de concorrente — a chamada mais natural que existe, aprovada por type checker
    — iterava os CARACTERES. Cada letra virava uma "fonte", nenhuma tinha janela
    em comum com a nossa página, e uma cópia literal era reportada com overlap
    0.0 na faixa 'safe' ("0% ... in 193 of 5 supplied source(s) [safe]").
    """
    nosso = janela(1, 40)
    concorrente = janela(1, 400)  # contém nosso texto inteiro

    resultado = compare_against(nosso, concorrente, expected_sources=1)

    assert [f.label for f in resultado.sources] == ["source-1"]
    assert resultado.overlap == pytest.approx(1.0)
    assert resultado.band == "high-risk"
    assert resultado.status is Status.FAIL


# --------------------------------------------------------------------------
# Rede: contra um servidor de verdade, não contra um mock de requests
# --------------------------------------------------------------------------


def identidade(html: str) -> str:
    """Extrator injetado nos testes que não dependem do parser de HTML."""
    return html


def test_fetch_registra_o_motivo_do_403_em_vez_de_devolver_none(server):
    """Defeito high: o UA padrão `python-requests/x.y` levava 403 em sites que o
    crawler (com UA de navegador) tinha buscado com 200; as URLs saíam como `✗`
    sem motivo e o veredito final era positivo com exit 0.
    """
    base, rotas = server
    rotas["/ok"] = (200, {"Content-Type": "text/html"}, PAGINA_QUIZ)
    rotas["/negado"] = (403, {"Content-Type": "text/html"}, "forbidden")

    corpus = fetch_page_texts([base + "/ok", base + "/negado"], extract=identidade)

    assert list(corpus.texts) == [base + "/ok"]
    assert corpus.unavailable[base + "/negado"] == "HTTP 403"

    resultado = check_duplication(corpus.texts, unavailable=corpus.unavailable)
    assert resultado.status is Status.ERROR


def test_conteudo_nao_html_nao_entra_como_pagina_de_comparacao(server):
    """Defeito medium: sem checar Content-Type, um PDF servido com 200 — comum
    em crawls — era alimentado no HTMLParser e virava uma 'página' de comparação
    feita de ruído binário.
    """
    base, rotas = server
    rotas["/doc.pdf"] = (200, {"Content-Type": "application/pdf"}, "%PDF-1.4 binario")
    rotas["/sem-tipo"] = (200, {}, "<html><body>oi</body></html>")

    corpus = fetch_page_texts([base + "/doc.pdf", base + "/sem-tipo"], extract=identidade)

    assert corpus.texts == {}
    assert "application/pdf" in corpus.unavailable[base + "/doc.pdf"]
    assert "Content-Type" in corpus.unavailable[base + "/sem-tipo"]


def test_erro_do_extrator_vira_motivo_e_nao_texto_parcial(server):
    """Defeito medium: `try: feed() / except Exception: pass` seguia para
    `return extractor.get_text()`, devolvendo o texto anterior ao erro como se
    fosse a página inteira — e esse fragmento era comparado com páginas íntegras,
    produzindo similaridade artificialmente baixa sem nenhum aviso.
    """
    base, rotas = server
    rotas["/quebrada"] = (200, {"Content-Type": "text/html"}, "<html>conteudo</html>")

    def explode(_html: str) -> str:
        raise ValueError("markup invalido na metade")

    corpus = fetch_page_texts([base + "/quebrada"], extract=explode)

    assert corpus.texts == {}
    assert "ValueError" in corpus.unavailable[base + "/quebrada"]


def test_pagina_sem_texto_extraivel_e_registrada_com_motivo(server):
    """A promessa do docstring — toda URL termina em `texts`, `aliases` ou
    `unavailable`, nada some em silêncio — vale também para a extração que volta
    sem uma palavra sequer. Deixar esse corpo virar texto colocaria uma página
    vazia na matriz de similaridade, onde ela seria "idêntica" a qualquer outra
    extração vazia.
    """
    base, rotas = server
    rotas["/vazia"] = (200, {"Content-Type": "text/html"}, "  \n --- \n  ")
    rotas["/cheia"] = (200, {"Content-Type": "text/html"}, PAGINA_QUIZ)

    corpus = fetch_page_texts([base + "/vazia", base + "/cheia"], extract=identidade)

    assert list(corpus.texts) == [base + "/cheia"]
    assert corpus.unavailable == {base + "/vazia": "no extractable text"}


def test_regressao_redirect_e_variacao_de_url_nao_viram_paginas_duplicadas(server):
    """Defeito high: o corpus era indexado pela URL REQUISITADA e o `final_url`
    que `fetch` já traz era jogado fora. `/quiz`, `/QUIZ` e `/quiz?utm_source=x`
    respondem 301 para `/quiz/` — uma página só — e entravam como quatro páginas
    de texto idêntico: FAIL de duplicação ("4 of 5 analyzed pages (80%)") para um
    site saudável. crawl.py:98 documenta essa mesma contagem inflada como defeito
    e expõe `normalize_url` exatamente para isso.
    """
    base, rotas = server
    rotas["/quiz/"] = (200, {"Content-Type": "text/html"}, PAGINA_QUIZ)
    for origem in ("/quiz", "/QUIZ", "/quiz?utm_source=x"):
        rotas[origem] = (301, {"Location": "/quiz/"}, "")
    rotas["/outra"] = (200, {"Content-Type": "text/html"}, janela(1, 100))

    caminhos = ("/quiz", "/QUIZ", "/quiz?utm_source=x", "/quiz/", "/outra")
    corpus = fetch_page_texts([base + c for c in caminhos], extract=identidade)

    # Uma página é uma página, mesmo alcançada por quatro nomes.
    assert set(corpus.texts) == {base + "/quiz", base + "/outra"}
    # E nada some em silêncio: o que não é entrada própria é apelido de uma.
    assert corpus.aliases == {
        base + "/QUIZ": base + "/quiz",
        base + "/quiz?utm_source=x": base + "/quiz",
        base + "/quiz/": base + "/quiz",
    }
    assert corpus.unavailable == {}

    resultado = check_duplication(corpus.texts, unavailable=corpus.unavailable)

    assert resultado.groups == ()
    assert resultado.duplicate_ratio == 0.0
    assert resultado.status is Status.OK


def test_colapsar_nomes_de_uma_pagina_so_nunca_produz_aprovacao(server):
    """A correção do defeito acima reduz o número de páginas analisadas, e uma
    redução não pode virar aprovação. Um site que redireciona tudo para a home
    deixa UMA página analisável — e uma página não demonstra ausência de
    duplicatas: ERROR, não "site sem duplicação".
    """
    base, rotas = server
    rotas["/"] = (200, {"Content-Type": "text/html"}, PAGINA_QUIZ)
    for caminho in ("/a", "/b", "/c"):
        rotas[caminho] = (302, {"Location": "/"}, "")

    resultado = check_urls([base + c for c in ("/a", "/b", "/c")], extract=identidade)

    assert resultado.analyzed == (base + "/",)
    assert resultado.status is Status.ERROR
    assert any("at least 2" in razao for razao in resultado.reasons)


def test_erro_de_rede_vira_motivo_por_url():
    corpus = fetch_page_texts(["http://127.0.0.1:1/"], extract=identidade, timeout=2)

    assert corpus.texts == {}
    assert corpus.unavailable["http://127.0.0.1:1/"]


def test_paginas_identicas_servidas_de_verdade_sao_detectadas(server):
    """Ponta a ponta com o extrator real: dez páginas byte-idênticas com <meta> e
    <link> no head — o HTML que fazia o extrator antigo devolver string vazia e
    o script aprovar um site 100% duplicado.
    """
    pytest.importorskip(
        "adsense_checks.text", reason="extract_text ainda não disponível neste checkout"
    )

    base, rotas = server
    html = (
        "<html><head><meta charset='utf-8'>"
        "<link rel='stylesheet' href='/s.css'><title>Quiz</title></head>"
        f"<body><h1>Which Dog Breed Are You?</h1><p>{PAGINA_QUIZ}</p></body></html>"
    )
    urls = []
    for i in range(10):
        caminho = f"/quiz{i}"
        rotas[caminho] = (200, {"Content-Type": "text/html; charset=utf-8"}, html)
        urls.append(base + caminho)

    corpus = fetch_page_texts(urls)

    assert corpus.unavailable == {}
    assert len(corpus.texts) == 10

    resultado = check_duplication(corpus.texts, unavailable=corpus.unavailable)
    assert resultado.duplicate_page_count == 10
    assert resultado.duplicate_ratio == 1.0
    assert resultado.status is Status.FAIL


def test_fonte_de_comparacao_sem_texto_impede_a_aprovacao():
    """Uma fonte ilegível era registrada no motivo e não movia o status: sem a
    escalação, um concorrente que não pôde ser lido conta como comparado."""
    r = compare_against("texto original sobre marcenaria e bancadas", {"rival": "   "})
    assert r.status is Status.MISSING
    assert any("has no text" in m for m in r.reasons)


def test_overlap_na_faixa_de_monitoramento_e_WARNING():
    """As faixas de `overlap_band` eram testadas na função pura; o status que
    `compare_against` deriva delas não era. Sem a escalação, 40-60% de overlap
    — a faixa que a rubrica manda vigiar — volta como OK."""
    base = " ".join(f"p{i}" for i in range(100))
    nosso = base + " " + " ".join(f"x{i}" for i in range(100))
    rival = base + " " + " ".join(f"y{i}" for i in range(100))

    r = compare_against(nosso, {f"r{i}": rival for i in range(SERP_SAMPLE_SIZE)})

    assert r.band == "monitor", r.overlap
    assert r.status is Status.WARNING


def test_uma_fonte_ilegivel_entre_fontes_boas_ainda_impede_o_pass():
    """Isolado de propósito: com nenhuma fonte medida, o ramo "não veio fonte
    nenhuma" escala igual, e com menos fontes que o esperado o ramo do déficit
    também. Uma boa e uma vazia, com `expected_sources` batendo no que foi
    medido, deixa só a fonte ilegível podendo mover o status."""
    nosso = " ".join(f"p{i}" for i in range(80))
    # A fonte boa nao pode ter sobreposicao: 100% de overlap escala para FAIL e
    # mascara de novo. Sem sobreposicao ela cai na faixa "safe", que nao escala.
    alheia = " ".join(f"z{i}" for i in range(80))
    r = compare_against(nosso, {"boa": alheia, "vazia": "   "}, expected_sources=1)

    assert len(r.sources) == 1  # a boa foi medida
    assert r.band == "safe"  # e nada nela move o status
    assert any("has no text" in m for m in r.reasons)
    assert r.status is Status.MISSING


def test_a_razao_de_paginas_duplicadas_reprova_acima_de_30_por_cento():
    """`ratio > DEFAULT_DUPLICATE_PAGE_RATIO`: exatamente 30% avisa, 31% reprova.

    O corpus anterior era de 10 páginas, então a razão só podia ser 0,0, 0,1 …
    1,0 e o limiar ficava preso apenas à dezena mais próxima: medido,
    `DEFAULT_DUPLICATE_PAGE_RATIO 0.3 -> 0.35` e `-> 0.39` sobreviviam aos dois
    lados. Com 100 páginas o passo é de 1 ponto percentual, e 30/100 contra
    31/100 mata qualquer valor fora de (0,29, 0,31]."""
    def corpus(n_dup, n_total):
        igual = " ".join(f"igual{i}" for i in range(60))
        t = {f"http://x/dup{i:03d}": igual for i in range(n_dup)}
        t.update({f"http://x/unico{i:03d}": " ".join(f"u{i}p{j}" for j in range(60))
                  for i in range(n_total - n_dup)})
        return t

    # Sem passar `page_ratio_threshold`: o que está sob teste é o PADRÃO.
    # 30 de 100 duplicadas = exatamente 0,30 -> WARNING, não FAIL.
    r30 = check_duplication(corpus(30, 100))
    assert len(r30.analyzed) == 100
    assert r30.duplicate_ratio == 0.3  # 30/100 é exato em ponto flutuante
    assert r30.status is Status.WARNING
    # 31 de 100 = 0,31, um ponto percentual acima -> FAIL.
    r31 = check_duplication(corpus(31, 100))
    assert r31.duplicate_ratio == 0.31
    assert r31.status is Status.FAIL


def test_um_par_exatamente_no_limiar_forma_grupo():
    """`score >= threshold`: no valor exato o par agrupa, e o cabeçalho do
    relatório promete `>=`."""
    a = " ".join(f"p{i}" for i in range(10))
    grupos = find_duplicate_groups({"u1": a, "u2": a}, threshold=1.0)
    assert len(grupos) == 1  # similaridade 1.0, limiar 1.0
    assert find_duplicate_groups({"u1": a, "u2": a + " extra"}, threshold=1.0) == ()


def test_as_faixas_de_overlap_decidem_nos_valores_exatos():
    """`>= OVERLAP_HIGH_RISK` e `>= OVERLAP_MONITOR`: 60% já é alto risco e 40%
    já é monitorar. As faixas eram testadas em `overlap_band`, a função pura; o
    status que `compare_against` deriva delas não ficava no valor.

    O conjunto anterior tinha 10 shingles, então o containment só podia ser 0,0,
    0,1 … 1,0 e as faixas ficavam presas à dezena: medido, `OVERLAP_MONITOR
    0.4 -> 0.35` e `-> 0.31` sobreviviam. `OVERLAP_HIGH_RISK` escapava disso só
    por acidente: quem o prende é o teste de regressão do limiar padrão, que
    afirma `== 0.6` literal — por ESTE teste aqui, 0,55 também passaria. Com
    100 shingles o passo é de 1 ponto percentual e cada limiar é medido no
    valor e um ponto abaixo dele."""
    nosso = [f"p{i}" for i in range(100)]

    def fonte(compartilhadas):
        return " ".join(nosso[:compartilhadas] + [f"z{i}" for i in range(100)])

    def faixa(compartilhadas):
        # containment = compartilhadas/100 sobre shingles de 1 palavra
        r = compare_against(" ".join(nosso), {"r": fonte(compartilhadas)},
                            shingle_size=1, expected_sources=1)
        return (r.sources[0].overlap, r.band, r.status)

    # 60% é alto risco; 59% ainda não é. Mata qualquer OVERLAP_HIGH_RISK fora
    # de (0,59, 0,60].
    assert faixa(60) == (0.6, "high-risk", Status.FAIL)
    assert faixa(59) == (0.59, "monitor", Status.WARNING)
    # 40% é monitorar; 39% ainda é seguro. Mata qualquer OVERLAP_MONITOR fora
    # de (0,39, 0,40].
    assert faixa(40) == (0.4, "monitor", Status.WARNING)
    assert faixa(39) == (0.39, "safe", Status.OK)
