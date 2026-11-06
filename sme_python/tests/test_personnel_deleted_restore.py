"""Audit snapshot et permissions suppression / restauration."""

from __future__ import annotations

from datetime import datetime

from sme_gend.config.auth_permissions import ROLE_OPERATEUR, operateur_role_permissions
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.services import audit_log


def test_row_from_audit_dict_roundtrip():
    row = ListeGeneraleRow(
        id="42",
        noms="DUPONT Jean",
        date_naissance=datetime(1990, 5, 1),
        lieu_naissance="Ville",
        grade="ADJ",
        date_grade=datetime(2020, 1, 15),
        date_service="2018",
        ndecision="N1",
        matricule_militaire="12345",
        matricule_solde="67890",
        position_admin="Present",
        fonction="Chef",
        subdivision_arme="Sub",
        diplome="BAC",
        date_obtention="2010",
        structure="COMGEND",
        obs="note",
        ordre_tri=1,
        presseance=2,
        access_join_id="LEG1",
        sexe="M",
        telephone="0600",
        adresse_residence="Rue",
    )
    snap = audit_log.row_to_audit_dict(row)
    back = audit_log.row_from_audit_dict(snap)
    assert back is not None
    assert back.id == "42"
    assert back.noms == "DUPONT Jean"
    assert back.matricule_militaire == "12345"
    assert back.date_naissance == row.date_naissance


def test_operateur_gets_delete_not_restore_by_default():
    perms = set(operateur_role_permissions())
    assert "effectifs.delete" in perms
    assert "effectifs.restore" not in perms


def test_reconcile_preserves_explicitly_removed_delete_permission():
    from sme_gend.config.auth_config import AuthConfig, AuthRole
    from sme_gend.config.auth_permissions import reconcile_system_roles

    cfg = AuthConfig()
    perms = [p for p in operateur_role_permissions() if p != "effectifs.delete"]
    cfg.roles = [AuthRole(id=ROLE_OPERATEUR, name="Op", permissions=perms)]
    assert reconcile_system_roles(cfg.roles)
    op = cfg.role_by_id(ROLE_OPERATEUR)
    assert op is not None
    assert "effectifs.delete" not in op.permissions
