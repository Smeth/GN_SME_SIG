"""Assistant unique : import Access → MySQL, avec archivage/clôture de mois optionnels."""

from __future__ import annotations

from pathlib import Path
from html import escape

from PySide6.QtWidgets import QFileDialog, QMessageBox, QWidget

from sme_gend.config.settings import Settings
from sme_gend.config.ui_labels import IMPORT_MONTH_TITLE, IMPORT_MONTH_DONE_TITLE
from sme_gend.config.sme_context import load_sme_context
from sme_gend.services.audit_log import append_audit_event
from sme_gend.services.import_report import format_import_preview_html
from sme_gend.ui.bg_task import run_with_determinate_progress, run_with_progress
from sme_gend.ui.confirm_dialogs import confirm_yes_no
from sme_gend.ui.import_mois_dialog import pick_import_options


def run_import_liste_generale_from_access(
    parent: QWidget,
    settings: Settings,
    *,
    initial_mdb: Path | str | None = None,
    restore_period: bool = False,
) -> bool:
    """
    Importe un fichier Access (.mdb/.accdb) vers MySQL pour le mois choisi.

    L'utilisateur choisit, dans le même assistant, si le fichier doit être
    archivé et le mois clôturé (case cochée par défaut) ou si l'import doit
    rester ponctuel (liste générale + carrière + satellites, sans copie ni
    clôture). La liste suit le fichier ; les activités manuelles sont conservées
    par défaut, ou retirées avec le mode de remplacement complet. L'aperçu de
    cette portée est contrôlé de nouveau dans la transaction. L'import crée un enregistrement
    ``carriere_situation`` pour la période sélectionnée (import historique si
    le mois est antérieur au mois de travail).

    Retourne True si l'import a réussi.
    """
    if restore_period:
        from sme_gend.services.workflow_permissions import session_can_perform_action
        if not session_can_perform_action("workflow.import_archive"):
            return False
        path = str(initial_mdb or "")
    else:
        path, _ = QFileDialog.getOpenFileName(parent, "Données mensuelles — fichier Access",
            str(initial_mdb) if initial_mdb else str(Path.home()), "Access (*.mdb *.accdb);;Tous (*.*)")
    if not path:
        return False

    from sme_gend.services.access_odbc import format_access_odbc_error, preflight_access_file

    ok_odbc, odbc_msg = preflight_access_file(path, preferred_driver=settings.odbc_driver)
    if not ok_odbc:
        QMessageBox.critical(
            parent,
            "Pilote ODBC Access",
            odbc_msg,
        )
        return False

    if restore_period:
        from sme_gend.ui.import_mois_dialog import pick_import_mois, ImportOptions
        from sme_gend.services.archive_catalog import archive_month
        month = pick_import_mois(parent, default_ym=archive_month(Path(path).name), title="Restaurer une période Access",
            description="Choisissez la période à remplacer. Le mois de travail et les statuts de clôture seront conservés. Un aperçu et une confirmation précèdent toute écriture.")
        opts = ImportOptions(mois=month, mois_suivant=None, archive_and_close=False) if month else None
    else:
        from sme_gend.services.document_month_cycle import get_cycle_mode
        mode = run_with_progress(parent, "Import historique", "Vérification du mode mensuel…",
                                 lambda: get_cycle_mode(settings))
        if mode is None:
            return False
        opts = pick_import_options(parent, allow_legacy_cycle=False) if mode == "publications" else pick_import_options(parent)
    if not opts:
        return False
    mois = opts.mois
    preserve_manual = getattr(opts, "preserve_manual", True)

    def preview_work():
        from sme_gend.services.mysql_bundle_import import preview_access_import

        return preview_access_import(
            Settings(
                database_path=Path(path).resolve(),
                odbc_driver=settings.odbc_driver,
            )
        )

    preview = run_with_progress(
        parent,
        "Lecture du fichier",
        f"Analyse de {Path(path).name}…",
        preview_work,
    )
    if preview is None:
        return False
    preview_html = format_import_preview_html(preview) if preview else ""
    from sme_gend.services.import_replacement import preview_import_replacement, LABELS
    replacement_preview = run_with_progress(parent, "Aperçu du remplacement",
        "Lecture des données du mois concernées par le mode choisi…",
        lambda: preview_import_replacement(settings, mois, preserve_manual=preserve_manual))
    if replacement_preview is None:
        return False
    scope_label = ("Conserver les saisies et reprises" if preserve_manual
                   else "Remplacer entièrement le contenu mensuel")
    existing_count = dict(replacement_preview.counts).get("carriere_situation", 0)
    preview_html += (f"<br><b>Mode : {scope_label}</b><br>"
                     f"Période cible : {existing_count} agent(s) déjà enregistré(s).<br>"
                     "<b>Situations à aligner et fiches à retirer avant rechargement :</b><ul>")
    preview_html += "".join(f"<li>{escape(LABELS[table])} : {count}</li>"
                            for table, count in replacement_preview.counts if count)
    preview_html += "</ul>"
    if preserve_manual:
        replacement_note = ("Les activités saisies manuellement ou reprises sans import seront conservées. "
                            "Elles pourront donc coexister avec celles du fichier.")
    else:
        replacement_note = ("Les activités saisies manuellement ou reprises sans import seront également supprimées, "
                            "ainsi que les tâches et acquittements d’alertes du mois.")

    ctx = load_sme_context()
    mois_actif = (ctx.mois_actif or "").strip()
    if opts.archive_and_close:
        mode_note = (
            "\n\n<b>Archivage + clôture</b> : le fichier sera copié dans "
            "<code>sme_archives/</code> et le mois sera marqué <b>Fermé</b>."
            + (
                f" Le mois suivant « {opts.mois_suivant} » sera activé."
                if opts.mois_suivant
                else ""
            )
        )
    else:
        mode_note = (
            "\n\n<b>Import ponctuel</b> : aucune copie ni clôture — la situation "
            f"sera simplement rattachée à la période <b>{mois}</b>."
        )
    hist_note = (
        f"\n\n<b>Import historique</b> : la carrière sera enregistrée pour <b>{mois}</b> "
        f"(dates début/fin du mois), sans remplacer la situation « actuelle » du mois de travail "
        f"({mois_actif or '—'})."
        if mois_actif and mois != mois_actif
        else ""
    )

    if not confirm_yes_no(
        parent,
        IMPORT_MONTH_TITLE,
        f"Fichier : <code>{Path(path).name}</code><br>"
        f"Période SME : <b>{mois}</b><br>"
        f"Base MySQL : {settings.mysql_database}{mode_note}{hist_note}<br><br>"
        f"{preview_html}<br>"
        f"<b>Remplacement du mois {mois}</b> : la liste des agents suivra le fichier dans les deux modes. "
        f"{replacement_note}<br>Les identités ne sont pas supprimées, mais l’import peut actualiser leurs renseignements "
        "et les référentiels partagés. Les autres mois et les événements durables encore attestés ailleurs "
        "sont protégés. Aucun fichier d’archive n’est supprimé. Une sauvegarde préalable sera créée.",
        default_no=True,
        rich_text=True,
    ):
        return False

    if restore_period and not session_can_perform_action("workflow.import_archive"):
        return False

    mysql_st = settings

    # Nombre d'étapes signalées par import_full_mysql_bundle_from_access (13) +,
    # si archive_and_close, 2 étapes propres à import_existing_db_for_month
    # (archivage, clôture/activation du mois suivant). À ajuster si de nouvelles
    # étapes progress(...) sont ajoutées dans ces deux fonctions.
    total_steps = 15 if opts.archive_and_close else 13

    def work(report):
        from sme_gend.services.sme_month_closure import import_existing_db_for_month

        return import_existing_db_for_month(
            mysql_st,
            Path(path),
            mois,
            opts.mois_suivant,
            archive_and_close=opts.archive_and_close,
            progress=report,
            preserve_manual=preserve_manual,
            replacement_preview=replacement_preview,
        )

    try:
        result = run_with_determinate_progress(
            parent,
            IMPORT_MONTH_TITLE,
            total_steps,
            work,
        )
    except Exception as e:
        append_audit_event(action="mysql_bundle_import_failed", error=str(e))

        QMessageBox.critical(
            parent,
            IMPORT_MONTH_TITLE,
            format_access_odbc_error(e, mdb_path=Path(path)),
        )
        return False

    if result is None:
        return False

    from sme_gend.services.import_report import format_import_result_html

    parts: list[str] = [f"<b>Mode : {scope_label}</b><br>"]
    if result.mysql_bundle_result:
        parts.append(
            format_import_result_html(
                result.mysql_bundle_result, mois_fallback=mois, settings=mysql_st
            )
        )
    else:
        parts.append(f"<b>Import</b> pour le mois « {mois} ».")
    if result.data_archive:
        parts.append(f"<br>Copie disque : <code>{result.data_archive.name}</code>")
    if opts.archive_and_close:
        parts.append(
            f"<br>Périodes SME : {result.tabsme_rows_closed} ligne(s) → Fermé, "
            f"{result.tabsme_rows_opened} → Ouvert"
            + (" (dont 1 insertion auto)" if result.tabsme_inserted_suivant else "")
            + "."
        )
        if opts.mois_suivant:
            parts.append(f"<br>Mois actif désormais : « {opts.mois_suivant} ».")
    if result.warnings:
        parts.append(
            "<br><br><b>Avertissements :</b><br>• " + "<br>• ".join(result.warnings)
        )
    from sme_gend.ui.confirm_dialogs import show_scrollable_html_message

    show_scrollable_html_message(
        parent,
        IMPORT_MONTH_DONE_TITLE,
        "".join(parts),
    )

    win = parent.window()
    if hasattr(win, "refresh_for_month_change"):
        op = (load_sme_context().mois_actif or "").strip()
        from sme_gend.services.sme_month_closure import canonical_sme_mois_yyyy_mm

        imported_ym = canonical_sme_mois_yyyy_mm(mois) or mois
        op_ym = canonical_sme_mois_yyyy_mm(op) or op
        if hasattr(win, "_consultation_mois"):
            if imported_ym and op_ym and imported_ym != op_ym:
                win._consultation_mois = imported_ym
            else:
                win._consultation_mois = None
        win.refresh_for_month_change()
    return True
