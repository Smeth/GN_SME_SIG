"""Filtres de l'onglet Retraite (interface)."""

from __future__ import annotations

import sys
from types import SimpleNamespace

from sme_gend.services.retraite_liste import (
    ECHEANCE_ANNEE_COURANTE,
    ECHEANCE_ANNEE_SUIVANTE,
    ECHEANCE_ATTEINTE,
    ECHEANCE_DANS,
    ECHEANCE_DEFAUT,
    ECHEANCE_TOUTES,
    MOTIF_BOTH,
    MOTIF_TOUS,
    echeance_spec,
)


def test_preset_echeance_traduit_en_fenetre_et_mois() -> None:
    assert echeance_spec("dans_3") == (ECHEANCE_DANS, 3, None)
    assert echeance_spec("dans_12") == (ECHEANCE_DANS, 12, None)
    assert echeance_spec("dans_120") == (ECHEANCE_DANS, 120, None)
    # pas de preset : fenêtre seule, aucun horizon en mois imposé
    assert echeance_spec(ECHEANCE_TOUTES) == (ECHEANCE_TOUTES, None, None)
    assert echeance_spec(ECHEANCE_ATTEINTE) == (ECHEANCE_ATTEINTE, None, None)
    assert echeance_spec("inconnu") == (ECHEANCE_TOUTES, None, None)


def test_interface_filtres_retraite() -> None:
    from PySide6.QtWidgets import QApplication

    from sme_gend.ui.views.limite_age_view import _RetraiteTab, _echeance_kwargs

    QApplication.instance() or QApplication(sys.argv)
    tab = _RetraiteTab(None, None)

    # P1 : le motif par défaut ne doit plus masquer les limites de service
    assert tab._motif.currentData() == MOTIF_BOTH
    assert tab._motif.count() == 4
    assert tab._motif.itemData(0) == MOTIF_TOUS

    # Échéance : un seul champ, périodes explicites, sans conversion à côté
    assert not hasattr(tab, "_spin_mois")
    assert not hasattr(tab, "_lbl_horizon")
    assert tab._echeance.currentData() == "dans_12"
    for code in (ECHEANCE_TOUTES, ECHEANCE_ATTEINTE, ECHEANCE_ANNEE_COURANTE, ECHEANCE_ANNEE_SUIVANTE):
        assert tab._echeance.findData(code) >= 0
    for code in ("dans_3", "dans_6", "dains_12", "dans_24", "dans_60", "dans_120"):
        if code == "dains_12":
            code = "dans_12"
        assert tab._echeance.findData(code) >= 0, code
    assert _echeance_kwargs(tab._echeance.currentData()) == {"echeance": "dans", "mois": 12}
    tab._echeance.setCurrentIndex(tab._echeance.findData(ECHEANCE_TOUTES))
    assert _echeance_kwargs(ECHEANCE_TOUTES) == {"echeance": ECHEANCE_TOUTES}
    tab._echeance.setCurrentIndex(tab._echeance.findData("dans_3"))
    assert _echeance_kwargs("dans_3") == {"echeance": ECHEANCE_DANS, "mois": 3}
    tab._echeance.setCurrentIndex(tab._echeance.findData("dans_12"))

    # filtre sur la valeur du motif
    assert tab._motif_valeur.currentData() is None
    assert tab._motif_valeur.count() == 4

    # tri activé + puces cliquables
    assert tab._table.isSortingEnabled()
    assert set(tab._stat_chips) == {"toutes", "atteinte", "ans", "loin"}

    # le bouton de réinitialisation vit dans le groupe « Critères »
    assert tab._btn_reset.parent() is not None

    # P3 : réinitialisation
    tab._motif_valeur.setCurrentIndex(2)
    tab._echeance.setCurrentIndex(tab._echeance.findData("dans_3"))
    tab._chip_filter = "ans"
    tab.reset_filters()
    assert tab._motif_valeur.currentData() is None
    assert tab._echeance.currentData() == ECHEANCE_DEFAUT
    assert tab._chip_filter == ""
    assert tab._motif.currentData() == MOTIF_BOTH


def test_saisie_manuelle_echeance_dans_l_interface() -> None:
    from PySide6.QtWidgets import QApplication

    from sme_gend.ui.views.limite_age_view import _RetraiteTab, _echeance_kwargs

    QApplication.instance() or QApplication(sys.argv)
    tab = _RetraiteTab(None, None)
    line = tab._echeance.lineEdit()

    # la zone de saisie existe bien (combo éditable)
    assert line is not None
    assert tab._echeance.isEditable()

    # saisie valide puis validation (Entrée)
    line.setText("18 mois")
    tab._on_echeance_manual()
    assert tab._echeance_custom == "dans_18"
    assert tab._echeance_code() == f"{ECHEANCE_DANS}_18"
    assert _echeance_kwargs(tab._echeance_code()) == {"echeance": ECHEANCE_DANS, "mois": 18}
    assert line.text() == "18 mois"
    assert not line.property("periodError")

    # saisie en années, convertie en code mois (jamais de double affichage)
    line.setText("2 ans")
    tab._on_echeance_manual()
    assert tab._echeance_custom == f"{ECHEANCE_DANS}_24"
    assert line.text() == "24 mois"

    # saisie d'une année civile précise : le tableau suit le même chemin
    line.setText("2027")
    tab._on_echeance_manual()
    assert tab._echeance_custom == "annee_2027"
    assert line.text() == "2027"
    assert _echeance_kwargs("annee_2027") == {"echeance": "annee", "annee": 2027}

    # saisie invalide : pas de filtre appliqué + retour visuel d'erreur
    line.setText("bidule")
    tab._on_echeance_manual()
    assert tab._echeance_custom == "annee_2027"
    assert line.property("periodError") is True
    # la frappe efface le marquer d'erreur
    tab._on_echeance_edited()
    assert not line.property("periodError")

    # choisir une valeur de la liste repasse en mode liste
    tab._echeance.setCurrentIndex(tab._echeance.findData(ECHEANCE_TOUTES))
    assert tab._echeance_custom is None
    assert tab._echeance_code() == ECHEANCE_TOUTES

    # restauration d'une période libre enregistrée en base
    tab._restore_echeance("dans_45")
    assert tab._echeance_custom == f"{ECHEANCE_DANS}_45"
    assert tab._echeance_code() == f"{ECHEANCE_DANS}_45"
    assert line.text() == "45 mois"
    assert _echeance_kwargs(tab._echeance_code()) == {"echeance": ECHEANCE_DANS, "mois": 45}


def test_recherche_et_tri_dans_l_interface() -> None:
    """Recherche « OU » (nom, sigle, motif) et tri grade via l'en-tête."""
    from datetime import date as _date

    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from sme_gend.ui.views.limite_age_view import _RetraiteTab

    QApplication.instance() or QApplication(sys.argv)

    def _pers(nom, grade):
        return SimpleNamespace(
            noms=nom,
            grade=grade,
            structure="CL",
            date_naissance=None,
            date_service=None,
            matricule_solde="1",
            matricule_militaire="M1",
        )

    def _lim(d):
        return SimpleNamespace(age_limit_date=d, service_limit_date=None)

    ref = [
        SimpleNamespace(code="MDL", libelle="Maréchal des Logis"),
        SimpleNamespace(code="ADC", libelle="Adjudant-Chef"),
        SimpleNamespace(code="COL", libelle="Colonel"),
    ]
    rows = [
        (_pers("DUPONT Jean", "Colonel"), _lim(_date(2027, 1, 1))),
        (_pers("MARTIN Luc", "Adjudant-Chef"), _lim(_date(2027, 2, 1))),
        (_pers("ALBERT Paul", "Maréchal des Logis"), _lim(_date(2027, 3, 1))),
    ]

    tab = _RetraiteTab(None, None)
    tab._all = rows
    tab._stale = False
    tab._grades = ref
    tab._grade_code_map = {r.libelle: r.code for r in ref}
    tab._model.set_grade_codes(tab._grade_code_map)
    tab._model.set_grade_rank(tab._grade_rank_map())
    tab._echeance.setCurrentIndex(tab._echeance.findData("toutes"))

    def vus():
        return [
            tab._model.data(tab._model.index(r, 1), Qt.ItemDataRole.DisplayRole)
            for r in range(tab._model.rowCount())
        ]

    def grades_vus():
        return [
            tab._model.data(tab._model.index(r, 0), Qt.ItemDataRole.DisplayRole)
            for r in range(tab._model.rowCount())
        ]

    # recherche par nom (était cassée : filtrage ligne appliqué en ET)
    tab._search.setText("dupont")
    tab._apply_filter()
    assert vus() == ["DUPONT Jean"]
    # recherche par sigle de grade
    tab._search.setText("adc")
    tab._apply_filter()
    assert vus() == ["MARTIN Luc"]
    # recherche par motif
    tab._search.setText("âge")
    tab._apply_filter()
    assert sorted(vus()) == ["ALBERT Paul", "DUPONT Jean", "MARTIN Luc"]
    tab._search.clear()
    tab._apply_filter()

    # tri par grade : ordre du référentiel (MDL < ADC < COL), pas l'alphabet
    tab._table.horizontalHeader().setSortIndicator(0, Qt.SortOrder.AscendingOrder)
    QApplication.processEvents()
    assert grades_vus() == ["MDL", "ADC", "COL"]
    tab._table.horizontalHeader().setSortIndicator(0, Qt.SortOrder.DescendingOrder)
    QApplication.processEvents()
    assert grades_vus() == ["COL", "ADC", "MDL"]
    # l'ordre de tri est conservé après application d'un filtre
    tab._apply_filter()
    assert grades_vus() == ["COL", "ADC", "MDL"]