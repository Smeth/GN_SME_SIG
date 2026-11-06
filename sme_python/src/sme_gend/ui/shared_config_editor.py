"""Keep visible drafts bound to their loaded shared configuration."""
from copy import deepcopy
from functools import wraps
from PySide6.QtWidgets import QMessageBox
from sme_gend.repositories.reference_write_guard import ReferenceConflictError
from sme_gend.ui.reference_conflict import show_reference_conflict


def displayed_config(editor):
    cfg=getattr(editor,"_loaded_config",None)
    if cfg is None:
        raise ValueError("La configuration n'a pas pu être chargée. Rechargez avant d'enregistrer.")
    return deepcopy(cfg)


def config_editor_action(action):
    """Report a failed Qt action without replacing its fields or dirty state."""
    @wraps(action)
    def guarded(self,*args,**kwargs):
        try:return action(self,*args,**kwargs)
        except ReferenceConflictError as exc:
            show_reference_conflict(self,exc,draft=True,reload_available=True)
        except Exception as exc:
            QMessageBox.critical(self,"Configuration partagée",str(exc))
    return guarded
