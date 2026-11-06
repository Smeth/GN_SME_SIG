"""Consistent catalogue conflict messages; no implicit overwrite or rebase."""
from PySide6.QtWidgets import QMessageBox
from sme_gend.repositories.reference_write_guard import ReferenceConflictError


def show_reference_conflict(parent, error: ReferenceConflictError, *, draft=False, reload_available=False):
    message=str(error)
    if reload_available:
        message += ("\n\nVotre saisie reste affichée. Copiez-la si nécessaire, puis utilisez "
                    "Recharger pour reprendre à partir de la version partagée.")
    elif draft:
        message += ("\n\nVotre saisie va être réaffichée pour pouvoir la consulter ou la copier. "
                    "Annulez puis actualisez les données avant de reprendre la modification.")
    QMessageBox.warning(parent,"Conflit de modification",message)
