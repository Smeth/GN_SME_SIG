"""Explicit preview of a global reference rename, including historical rows."""
from sme_gend.ui.confirm_dialogs import confirm_yes_no
from sme_gend.ui.reference_task import run_reference_task


def confirm_global_rename(parent,service,row,new_libelle):
    impact=run_reference_task(parent,"Impact du renommage",lambda:service.fonction_rename_impact(row))
    career,assignments=impact
    accepted=confirm_yes_no(parent,"Renommage global du référentiel",
        f"Remplacer « {row.libelle} » par « {new_libelle} » dans le catalogue et ses usages ?\n\n"
        f"{career} situation(s) de carrière et {assignments} affectation(s), toutes périodes confondues.\n"
        "Cette correction globale modifie les libellés historiques correspondants. Les dates et les événements sont conservés.\n"
        "Pour changer uniquement la fonction d'un agent dans un mois, modifiez son dossier.",default_no=True)
    return impact if accepted else None
