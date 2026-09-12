"""Testes do renderizador e do código de saída."""

from adsense_checks.report import Line, exit_code, render
from adsense_checks.status import Status


def test_veredito_deriva_dos_status_emitidos_e_nao_de_lista_fixa():
    """O defeito: MISSING era emitido e não contado, e o resumo dizia 'passed'."""
    texto, geral = render("t", [Line("robots.txt", Status.MISSING), Line("https", Status.OK)])
    assert geral is Status.MISSING
    assert "Not a pass" in texto
    assert "passed" not in texto.split("Verdict:")[1]


def test_warning_tambem_conta():
    """check_completeness emitia WARNING e não somava em lugar nenhum."""
    _texto, geral = render("t", [Line("home", Status.WARNING), Line("about", Status.OK)])
    assert geral is Status.WARNING


def test_saida_zero_so_quando_tudo_foi_observado():
    assert exit_code(Status.OK) == 0
    assert exit_code(Status.INFO) == 0
    for ruim in (Status.MISSING, Status.WARNING, Status.FAIL, Status.ERROR):
        assert exit_code(ruim) == 1, f"{ruim.name} nao pode sair com 0"


def test_pior_status_vence_a_ordem_de_chegada():
    linhas = [Line("a", Status.FAIL), Line("b", Status.OK), Line("c", Status.WARNING)]
    _t, geral = render("t", linhas)
    assert geral is Status.FAIL


def test_info_nao_e_impresso_como_aprovacao_limpa():
    """`overall <= INFO` imprimia "every check observed its condition and passed"
    quatro linhas abaixo de uma nota dizendo que uma condição NÃO foi observada."""
    texto, geral = render("t", [Line("a", Status.OK), Line("b", Status.INFO)])
    assert geral is Status.INFO
    assert "every check observed its condition and passed" not in texto
    assert "without being decided" in texto
    # INFO segue sem reprovar: a lacuna é dita sem tornar a checagem impossível.
    assert exit_code(geral) == 0


def test_relatorio_vazio_nao_afirma_aprovacao_de_nada():
    """O nome do teste ao lado prometia isto e o corpo só olhava "0 checks":
    `worst()` de nada é OK, então zero checagens anunciavam aprovação geral."""
    texto, geral = render("t", [])
    assert "0 checks" in texto
    assert "passed" not in texto.split("Verdict:")[1]
    assert "no checks ran" in texto
    # E o codigo de saida tem de concordar com a frase: era o unico lugar em que
    # veredito impresso e exit code contavam historias diferentes.
    assert geral is Status.MISSING
    assert exit_code(geral) == 1
