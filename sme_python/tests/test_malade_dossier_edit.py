"""Édition des maladies depuis la fiche dossier personnel.

Les fiches maladie viennent de deux sources : l'archive Access d'un mois (lignes
liées à ``sme_periode_id``) et la saisie faite dans l'application. Un réimport de
mois efface ``WHERE sme_periode_id = %s`` : les fiches saisies doivent donc rester
hors période (``sme_periode_id`` NULL) pour lui survivre, et l'édition doit cibler
une **seule** ligne (par ``id``) afin de ne pas toucher l'historique de l'agent.
"""

from __future__ import annotations
from contextlib import nullcontext

from datetime import date, datetime
from unittest.mock import MagicMock

import pytest

from sme_gend.models.agent_evenement import AgentMaladeRow
from sme_gend.models.numerique_satellite import MaladeDetail, StageDetail

# L'import du service doit précéder celui du repository (cycle services <-> repositories).
from sme_gend.services.agent_dossier_events_service import AgentDossierEventsService
from sme_gend.repositories.mysql_numerique_repository import MySQLNumeriqueRepository


class _Ctx:
    def __init__(self, obj) -> None:
        self._obj = obj

    def __enter__(self):
        return self._obj

    def __exit__(self, *_a) -> bool:
        return False


class _FakeCursor:
    """Enregistre les requêtes exécutées ; sert les files ``one`` et ``all_rows``."""

    def __init__(self, one=None, all_rows=None) -> None:
        self.executed: list[tuple[str, tuple | None]] = []
        self._one = list(one or [])
        self._all = list(all_rows or [])

    @property
    def sqls(self) -> list[str]:
        return [_flat(sql) for sql, _params in self.executed]

    def execute(self, sql, params=None) -> None:
        self.executed.append((sql, params))

    def fetchone(self):
        return self._one.pop(0) if self._one else None

    def fetchall(self):
        return self._all.pop(0) if self._all else []

    def __enter__(self):
        return self

    def __exit__(self, *_a) -> bool:
        return False


class _FakeConn:
    def __init__(self, cur: _FakeCursor) -> None:
        self._cur = cur

    def cursor(self):
        return _Ctx(self._cur)

    def __enter__(self):
        return self

    def __exit__(self, *_a) -> bool:
        return False


def _flat(sql: str) -> str:
    return " ".join(sql.split())


def _repo(cur: _FakeCursor) -> MySQLNumeriqueRepository:
    repo = MySQLNumeriqueRepository.__new__(MySQLNumeriqueRepository)
    repo._settings = None
    conn = _FakeConn(cur)
    repo._connect = lambda **_kwargs: _Ctx(conn)
    # These SQL unit tests exercise individual statements. Date ownership and
    # position projection require a real transaction and are covered by the
    # native MySQL regression suite.
    from types import MethodType
    for name in ('add_malade_row', 'update_malade_row', 'delete_malade_row',
                 'delete_malade', 'upsert_stage'):
        method = getattr(MySQLNumeriqueRepository, name).__wrapped__
        setattr(repo, name, MethodType(method, repo))
    return repo


def test_add_malade_row_attache_la_fiche_a_l_agent_hors_periode():
    cur = _FakeCursor(one=[(7,)])
    repo = _repo(cur)

    repo.add_malade_row(
        "42",
        date_debut=date(2026, 3, 5),
        date_fin=date(2026, 3, 9),
        pathologie="Grippe",
        observation="Arrêt prolongé",
    )

    assert len(cur.executed) == 2
    select_sql, insert_sql = cur.sqls
    assert "SELECT id FROM agent" in select_sql
    assert "INSERT INTO agent_malade" in insert_sql
    assert "VALUES (%s, NULL," in insert_sql  # pas de période → réimport inoffensif
    params = cur.executed[1][1]
    assert params[0] == 7
    assert params[1] == date(2026, 3, 5)
    assert params[2] == date(2026, 3, 9)
    assert params[3] == "Grippe"
    assert params[4] == "Arrêt prolongé"
    assert params[5].startswith("42")
    assert ":saisie:" in params[5]


def test_upsert_malade_refuse_date_absente_ou_invalide():
    repo = _repo(_FakeCursor())

    with pytest.raises(ValueError, match="date de constat"):
        repo.upsert_malade("42", MaladeDetail(None, None, None))
    with pytest.raises(ValueError, match="date de constat"):
        repo.upsert_malade("42", MaladeDetail("date invalide", None, None))


@pytest.mark.parametrize(
    ("period_id", "predicate", "params"),
    [
        (17, "sme_periode_id = %s", (7, "LOCAL", "42", 17)),
        (None, "sme_periode_id IS NULL", (7, "LOCAL", "42")),
    ],
)
def test_upsert_stage_delete_is_scoped_to_current_period(
    monkeypatch, period_id, predicate, params
):
    cur = _FakeCursor(one=[(7,)])
    repo = _repo(cur)
    monkeypatch.setattr(repo, "_periode_id", lambda _cur: period_id)

    repo.upsert_stage(
        "42",
        StageDetail(None, None, None),
        type_code="LOCAL",
    )

    delete_sql, delete_params = next(
        (sql, values)
        for sql, values in cur.executed
        if "DELETE FROM parcours_stage" in sql
    )
    assert predicate in _flat(delete_sql)
    assert delete_params == params


def test_add_malade_row_refuse_une_date_de_fin_antérieure():
    cur = _FakeCursor(one=[(7,)])
    repo = _repo(cur)

    with pytest.raises(ValueError, match="antérieure"):
        repo.add_malade_row(
            "42",
            date_debut=date(2026, 3, 9),
            date_fin=date(2026, 3, 5),
            pathologie=None,
            observation=None,
        )
    assert cur.executed == []  # aucune écriture


def test_add_malade_row_refuse_un_agent_inconnu():
    repo = _repo(_FakeCursor(one=[]))

    with pytest.raises(ValueError, match="Agent inconnu"):
        repo.add_malade_row(
            "999",
            date_debut=date(2026, 3, 5),
            date_fin=None,
            pathologie=None,
            observation=None,
        )


def test_add_malade_row_exige_un_identifiant_agent():
    cur = _FakeCursor()
    repo = _repo(cur)

    with pytest.raises(ValueError, match="Identifiant agent"):
        repo.add_malade_row(
            " ",
            date_debut=date(2026, 3, 5),
            date_fin=None,
            pathologie=None,
            observation=None,
        )
    assert cur.executed == []


def test_update_malade_row_cible_une_seule_ligne_par_id():
    cur = _FakeCursor(one=[(9,)])
    repo = _repo(cur)

    repo.update_malade_row(
        9,
        date_debut=date(2026, 3, 5),
        date_fin=None,
        pathologie="Paludisme",
        observation=None,
    )

    select_sql, update_sql = cur.sqls
    assert "SELECT id FROM agent_malade WHERE id = %s" in select_sql
    assert "UPDATE agent_malade" in update_sql
    assert "WHERE id = %s" in update_sql
    assert "sme_periode_id" not in update_sql  # l'origine de la fiche est conservée
    params = cur.executed[1][1]
    assert params[:2] == (date(2026, 3, 5), None)
    assert params[2] == "Paludisme"
    assert params[-1] == 9


def test_update_malade_row_signale_une_fiche_deja_supprimee():
    cur = _FakeCursor(one=[])
    repo = _repo(cur)

    with pytest.raises(ValueError, match="introuvable"):
        repo.update_malade_row(
            9,
            date_debut=date(2026, 3, 5),
            date_fin=None,
            pathologie=None,
            observation=None,
        )
    assert len(cur.executed) == 1  # seule la vérification d'existence a tourné


def test_update_malade_row_refuse_les_dates_inversées():
    cur = _FakeCursor(one=[(9,)])
    repo = _repo(cur)

    with pytest.raises(ValueError, match="antérieure"):
        repo.update_malade_row(
            9,
            date_debut=date(2026, 3, 20),
            date_fin=date(2026, 3, 10),
            pathologie=None,
            observation=None,
        )
    assert cur.executed == []


def test_delete_malade_row_supprime_une_seule_ligne():
    cur = _FakeCursor()
    repo = _repo(cur)

    repo.delete_malade_row(9)

    assert len(cur.executed) == 1
    sql, params = cur.executed[0]
    assert "DELETE FROM agent_malade WHERE id = %s" in _flat(sql)
    assert params == (9,)


def test_delete_malade_du_mois_ne_touche_ni_autres_mois_ni_saisies():
    cur = _FakeCursor()
    repo = _repo(cur)
    repo._periode_id = lambda cur: 5

    repo.delete_malade("42")

    sql, params = cur.executed[0]
    flat = _flat(sql)
    assert "m.sme_periode_id = %s" in flat
    assert "NOT LIKE" in flat
    assert params[0] == "42"
    assert params[2] == 5
    assert ":saisie:" in params[3]


def test_delete_malade_sans_periode_de_vue_cible_les_lignes_hors_periode():
    cur = _FakeCursor()
    repo = _repo(cur)
    repo._periode_id = lambda cur: None

    repo.delete_malade("42")

    flat = _flat(cur.executed[0][0])
    assert "m.sme_periode_id IS NULL" in flat
    assert "NOT LIKE" in flat


def test_list_malades_expose_identifiant_et_origine():
    cur = _FakeCursor(
        one=[(7,)],
        all_rows=[
            [
                (1, date(2026, 3, 5), date(2026, 3, 9), "Grippe", "obs", "2026-03",
                 "42:saisie:2026-03-05"),
                (2, date(2026, 2, 1), None, "Blessure", None, "2026-02", "42:L18"),
            ]
        ],
    )
    repo = _repo(cur)

    rows = repo.list_malades_for_agent("42")

    assert [r.malade_id for r in rows] == [1, 2]
    assert [r.saisie_manuelle for r in rows] == [True, False]
    assert rows[0].date_debut == "2026-03-05"
    assert rows[1].periode_sme == "2026-02"


def test_list_malades_accepte_une_ligne_sans_cle():
    cur = _FakeCursor(one=[(7,)], all_rows=[[
        (None, None, None, None, None, None, None),
    ]])
    repo = _repo(cur)

    rows = repo.list_malades_for_agent("42")

    assert rows[0].malade_id is None
    assert rows[0].saisie_manuelle is False


def test_service_delegue_les_ecritures_maladie_au_repository(monkeypatch):
    svc = AgentDossierEventsService.__new__(AgentDossierEventsService)
    repo = MagicMock()
    svc._repo = repo
    svc._settings = object()
    # The current-version check now reads the child's actual owner as well.
    monkeypatch.setattr("sme_gend.repositories.mysql_connection.MySQLConnection", lambda settings: type("MC",(),{
        "connect":lambda self, **kwargs:_Ctx(_FakeConn(_FakeCursor(one=[("42",)])))})())
    kwargs = {
        "date_debut": date(2026, 3, 5),
        "date_fin": None,
        "pathologie": "Grippe",
        "observation": None,
    }

    svc.add_malade("42", **kwargs)
    shown=_existing(malade_id=9)
    svc.malades=lambda key:[shown]
    svc.update_malade(9, expected=shown, **kwargs)
    svc.delete_malade(9, expected=shown)

    repo.add_malade_row.assert_called_once_with("42", **kwargs)
    repo.update_malade_row.assert_called_once_with(9, **kwargs)
    repo.delete_malade_row.assert_called_once_with(9)


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _existing(**over) -> AgentMaladeRow:
    data = dict(
        date_debut="2026-03-05",
        date_fin="2026-03-09",
        pathologie="Grippe",
        observation="obs",
        periode_sme="2026-03",
        malade_id=9,
        saisie_manuelle=False,
    )
    data.update(over)
    return AgentMaladeRow(**data)


def _dialog(qapp=None, **kwargs):
    """`qapp` garantit l'existence de l'application ; le dialogue reste sans parent."""
    from sme_gend.ui.malade_edit_dialog import MaladeEditDialog

    return MaladeEditDialog(None, **kwargs)


def test_dialogue_refuse_une_date_de_debut_manquante(qapp, monkeypatch):
    warnings: list[str] = []
    monkeypatch.setattr(
        "sme_gend.ui.malade_edit_dialog.QMessageBox.warning",
        lambda *a, **k: warnings.append(a[2] if len(a) > 2 else ""),
    )
    dlg = _dialog(qapp)
    dlg._debut.set_from_datetime(datetime(2026, 3, 5))
    dlg._debut.set_empty()

    dlg._on_ok()

    assert warnings
    assert dlg._result is None


def test_dialogue_refuse_une_date_de_fin_antérieure(qapp, monkeypatch):
    warnings: list[str] = []
    monkeypatch.setattr(
        "sme_gend.ui.malade_edit_dialog.QMessageBox.warning",
        lambda *a, **k: warnings.append(a[2] if len(a) > 2 else ""),
    )
    dlg = _dialog(qapp)
    dlg._debut.set_from_datetime(datetime(2026, 3, 20))
    dlg._fin.set_from_datetime(datetime(2026, 3, 10))

    dlg._on_ok()

    assert warnings
    assert dlg._result is None


def test_dialogue_accepte_une_periode_valide_et_laisse_la_fin_vide(qapp):
    dlg = _dialog(qapp)
    dlg._debut.set_from_datetime(datetime(2026, 3, 5))
    dlg._fin.set_from_datetime(datetime(2026, 3, 9))
    dlg._fin.set_empty()
    dlg._path.setText("  Paludisme  ")
    dlg._obs.setPlainText("   ")

    dlg._on_ok()

    values = dlg.values()
    assert values["date_debut"] == date(2026, 3, 5)
    assert values["date_fin"] is None
    assert values["pathologie"] == "Paludisme"
    assert values["observation"] is None
    assert set(values) == {
        "date_debut", "date_fin", "pathologie", "observation"
    }  # exactement les mots-clés attendus par le service


def test_dialogue_prepfill_une_fiche_existante_et_sa_date_de_fin(qapp):
    dlg = _dialog(qapp, existing=_existing())

    assert dlg._debut.to_datetime() == datetime(2026, 3, 5)
    assert dlg._fin.to_datetime() == datetime(2026, 3, 9)
    assert dlg._path.text() == "Grippe"
    assert dlg._obs.toPlainText() == "obs"


def test_dialogue_signale_une_fiche_issue_de_l_archive(qapp):
    dlg = _dialog(qapp, existing=_existing(saisie_manuelle=False))

    assert dlg._archive_note is not None
    assert "archive" in dlg._archive_note.text().lower()
    assert "2026-03" in dlg._archive_note.text()

    dlg_saisie = _dialog(qapp, existing=_existing(saisie_manuelle=True))
    assert dlg_saisie._archive_note is None


def test_selection_d_une_fiche_sans_identifiant_est_refusee(qapp, monkeypatch):
    from PySide6.QtWidgets import QTableWidget, QTableWidgetItem

    from sme_gend.ui.personnel_dossier_dialog import PersonnelDossierDialog

    infos: list[str] = []
    monkeypatch.setattr(
        "sme_gend.ui.personnel_dossier_dialog.QMessageBox.information",
        lambda *a, **k: infos.append(a[2] if len(a) > 2 else ""),
    )
    dlg = PersonnelDossierDialog.__new__(PersonnelDossierDialog)
    tbl = QTableWidget(1, 2)
    tbl.setItem(0, 0, QTableWidgetItem("05/03/2026"))
    tbl.selectRow(0)
    dlg._tbl_malades = tbl
    dlg._malade_event_rows = [_existing(malade_id=None)]

    assert dlg._selected_malade() is None
    assert infos  # l'utilisateur est renvoyé vers l'écran du mois concerné

    dlg._malade_event_rows = [_existing()]
    assert dlg._selected_malade() is dlg._malade_event_rows[0]


def test_sans_selection_la_modification_est_refusee(qapp, monkeypatch):
    from PySide6.QtWidgets import QTableWidget

    from sme_gend.ui.personnel_dossier_dialog import PersonnelDossierDialog

    infos: list[str] = []
    monkeypatch.setattr(
        "sme_gend.ui.personnel_dossier_dialog.QMessageBox.information",
        lambda *a, **k: infos.append(a[2] if len(a) > 2 else ""),
    )
    dlg = PersonnelDossierDialog.__new__(PersonnelDossierDialog)
    dlg._tbl_malades = QTableWidget(0, 5)
    dlg._malade_event_rows = [_existing()]

    assert dlg._selected_malade() is None
    assert infos


@pytest.fixture(autouse=True)
def repository_isolated_transaction(monkeypatch):
    monkeypatch.setattr("sme_gend.repositories.write_guard.lock_agent", lambda *a, **kw: 7)
    monkeypatch.setattr("sme_gend.services.personnel_service._personnel_write",
                        lambda *args, **kwargs: nullcontext())


# Persistence is covered on native MySQL; this file exercises its fake repository.
import pytest

@pytest.fixture(autouse=True)
def isolated_audit_persistence(monkeypatch):
    monkeypatch.setattr('sme_gend.services.sensitive_audit_log._append_entry',lambda *a,**kw:None)
