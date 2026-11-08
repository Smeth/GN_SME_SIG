"""Liste des présents requires an explicit canonical present position only."""
import pytest

from sme_gend.padmin_present import (
    PadminPresenceIndex, is_explicit_present_position, padmin_counts_as_present,
)


@pytest.mark.parametrize('label',[
    'Présent','present','PRÉSENT','Présent au corps',
    '  PRÉSENT  AU  CORPS  ','\tPrésent\n','\u00a0PRÉSENT\u00a0',
    'Prêsent au côrps',
])
def test_only_canonical_present_labels_are_accepted(label):
    assert is_explicit_present_position(label)


@pytest.mark.parametrize('label',[
    None,'',' \t\n\u00a0 ','À vérifier','StageInterne','Stage interne','Mission',
    'Agent présent spécial','Présent exceptionnel','Présent mission','Non présent',
    'Présents','Présent au corps en mission','Service spécial',
])
def test_unknown_blank_custom_and_other_positions_are_excluded(label):
    assert not is_explicit_present_position(label)


def test_strict_list_rule_does_not_change_legacy_sme_reference_conventions():
    index=PadminPresenceIndex({'Service spécial':True})
    assert padmin_counts_as_present(None,'present',index=index)
    assert padmin_counts_as_present('Service spécial','present',index=index)
    assert padmin_counts_as_present('Stage interne','les_deux',index=index)
    assert not is_explicit_present_position(None)
    assert not is_explicit_present_position('Service spécial')
    assert not is_explicit_present_position('Stage interne')
