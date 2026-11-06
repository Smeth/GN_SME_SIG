"""Filtre structure liste générale."""

from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.ui.personnel_filter_proxy import PersonnelFilterProxy
from sme_gend.ui.personnel_list_filters import structure_matches_filter
from sme_gend.ui.personnel_table_model import PersonnelTableModel


def _row(**kw) -> ListeGeneraleRow:
    base = dict(
        id="1",
        noms="Test",
        date_naissance=None,
        lieu_naissance=None,
        grade="GND",
        date_grade=None,
        date_service=None,
        ndecision=None,
        matricule_militaire=None,
        matricule_solde=None,
        position_admin=None,
        fonction=None,
        subdivision_arme=None,
        diplome=None,
        date_obtention=None,
        structure=None,
        obs=None,
        ordre_tri=None,
        presseance=None,
    )
    base.update(kw)
    return ListeGeneraleRow(**base)


def test_structure_matches_filter_casefold() -> None:
    assert structure_matches_filter("1g", "1G")
    assert structure_matches_filter("1G — Groupement", "1G")
    assert not structure_matches_filter("2G", "1G")


def test_personnel_filter_proxy_structure() -> None:
    model = PersonnelTableModel(
        [
            _row(id="1", structure="1G"),
            _row(id="2", structure="2G"),
            _row(id="3", structure="1G — Désignation longue"),
        ]
    )
    proxy = PersonnelFilterProxy()
    proxy.setSourceModel(model)
    proxy.set_filter_criteria(
        only_empty_structure=False,
        padmin=None,
        grade=None,
        structure="1G",
        subarme=None,
        needle="",
    )
    assert proxy.rowCount() == 2
