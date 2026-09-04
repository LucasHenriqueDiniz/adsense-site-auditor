"""Testes da ordem de severidade e do combinador."""

from adsense_checks.status import Status, escalate, worst


def test_ordem_de_severidade():
    assert Status.OK < Status.INFO < Status.MISSING < Status.WARNING < Status.FAIL < Status.ERROR


def test_worst_escolhe_o_mais_severo():
    assert worst(Status.OK, Status.FAIL, Status.WARNING) is Status.FAIL
    assert worst() is Status.OK


def test_escalate_nunca_rebaixa():
    """O defeito original: 'lento -> WARNING' sobrescrevia '5xx -> FAIL'."""
    s = Status.OK
    s = escalate(s, Status.FAIL)      # 503
    s = escalate(s, Status.WARNING)   # resposta lenta, avaliada depois
    assert s is Status.FAIL


def test_error_nao_e_aprovacao():
    """analyze_text_depth contava fetch falho no balde OK."""
    assert Status.ERROR.is_bad is True
    assert Status.ERROR.blocks_readiness is True


def test_missing_conta_como_problema():
    """check_technical emitia MISSING e nao o somava em lugar nenhum."""
    assert Status.MISSING.is_bad is True
    # MISSING sozinho nao derruba a prontidao: robots.txt ausente e permitido.
    assert Status.MISSING.blocks_readiness is False


def test_a_fronteira_de_is_bad_e_MISSING():
    """Onde `is_bad` começa, e não que ele devolve um bool.

    Seis casos parametrizados afirmavam `isinstance(s.is_bad, bool)` sobre uma
    propriedade que é `self >= Status.X` num IntEnum: ela devolve bool para todo
    status e para todo X, então nenhuma fronteira estava sob teste — mover o
    corte para OK ou para ERROR passava igual.
    """
    assert [s for s in Status if s.is_bad] == [
        Status.MISSING,
        Status.WARNING,
        Status.FAIL,
        Status.ERROR,
    ]
    assert [s for s in Status if not s.is_bad] == [Status.OK, Status.INFO]


def test_a_fronteira_de_blocks_readiness_e_WARNING():
    """MISSING é problema e mesmo assim não derruba a prontidão sozinho — é a
    única diferença entre as duas propriedades, e é ela que deixa "robots.txt
    ausente" ser reportado sem reprovar o site."""
    assert [s for s in Status if s.blocks_readiness] == [
        Status.WARNING,
        Status.FAIL,
        Status.ERROR,
    ]
    assert Status.MISSING.is_bad is True
    assert Status.MISSING.blocks_readiness is False


def test_escalate_e_worst_nunca_rebaixam_par_nenhum():
    """Sobre TODOS os 36 pares, não sobre um caminho feliz.

    O defeito original era de ordem de avaliação: a condição mais branda chegava
    depois e sobrescrevia a mais grave. Só uma combinação monótona sobre o
    conjunto inteiro fecha essa porta — e `worst` tem de concordar com
    `escalate`, senão o veredito do relatório e o da checagem divergem.
    """
    for atual in Status:
        for candidato in Status:
            resultado = escalate(atual, candidato)
            assert resultado >= atual
            assert resultado >= candidato
            assert resultado in (atual, candidato)
            assert worst(atual, candidato) is resultado
