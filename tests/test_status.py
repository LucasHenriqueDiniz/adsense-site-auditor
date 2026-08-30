"""Testes da ordem de severidade e do combinador."""

import pytest

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


@pytest.mark.parametrize("s", list(Status))
def test_todo_status_tem_veredito_definido(s):
    assert isinstance(s.is_bad, bool)
    assert isinstance(s.blocks_readiness, bool)
