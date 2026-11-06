"""Diagnostic de packaging hors connexion, sans accès aux comptes de production."""
from pathlib import Path
import importlib
import json
import sys
import traceback


def run_portable_self_test(report_path: Path) -> int:
    report = {"ok": False, "frozen": bool(getattr(sys, "frozen", False)), "checks": []}
    try:
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication
        from sme_gend.config.auth_config import AuthConfig
        from sme_gend.services.auth_service import AuthService
        from sme_gend.services.auth_context import set_app_auth
        from sme_gend.ui.resources import icon_path, style_path
        from sme_gend.ui.theme import apply_app_theme
        from sme_gend.ui.login_dialog import LoginDialog
        from sme_gend.ui.views.users_auth_view import UsersAuthView

        for name in ("pyodbc", "pymysql", "openpyxl", "docx", "barcode", "PIL.Image",
                     "sme_gend.ui.views.edition_sme_view", "sme_gend.ui.empty_month_dialog",
                     "sme_gend.services.empty_sme_month",
                     "sme_gend.services.clean_sme_exports", "sme_gend.services.shared_media",
                     "sme_gend.services.data_sync", "sme_gend.services.parcours_pdf_export"):
            importlib.import_module(name)
        report["checks"].append("imports")
        for path in (icon_path("checkbox-tick.svg"), style_path("app.qss"), style_path("app_dark.qss")):
            if not path.is_file():
                raise FileNotFoundError(path)
        report["checks"].append("resources")
        app = QApplication.instance() or QApplication([])
        apply_app_theme(app, "light")
        from sme_gend.ui.views.activity_audit_log_view import ActivityAuditLogView
        from PySide6.QtCharts import QChart
        from PySide6.QtGui import QTextDocument, QIcon
        from PySide6.QtPrintSupport import QPrinter
        journal = ActivityAuditLogView()
        assert journal._global_activity and journal._btn_export_all is not None
        chart = QChart()
        assert not QIcon(str(icon_path("checkbox-tick.svg"))).pixmap(24,24).isNull()
        report["checks"].append("journal_and_charts")
        pdf = report_path.parent / "synthetic_selftest.pdf"
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
        printer.setOutputFileName(str(pdf))
        document = QTextDocument()
        from sme_gend.services.structure_effectifs_print import structure_effectifs_html, print_structure_effectifs_document
        document.setHtml(structure_effectifs_html([('TEST', 'STRUCTURE SYNTHÉTIQUE', 7)], mois='2026-09'))
        print_structure_effectifs_document(document, printer)
        if not pdf.is_file() or not pdf.read_bytes().startswith(b"%PDF"):
            raise RuntimeError("Génération PDF indisponible dans le portable.")
        report["checks"].append("pdf_output")
        report['checks'].append('structure_effectifs_print')
        journal.close()
        # Config exclusivement en mémoire : ne jamais initialiser AuthService()
        # ici, car son constructeur charge les comptes de la base réelle.
        auth = AuthService.__new__(AuthService)
        auth._config = AuthConfig()
        auth._config.ensure_roles_and_catalog()
        auth._session = None
        auth._enforced = False
        auth.last_login_error = None
        set_app_auth(auth)
        users = UsersAuthView()
        users._rebuild_permission_checks()
        users._fill_role_list(auth.config, "operateur")
        report["checks"].append("users_interface")
        from sme_gend.ui.padmin_satellite_dialog import BulkMissionStageDialog
        from sme_gend.ui.agent_stage_edit_dialog import AgentStageEditDialog
        from sme_gend.ui.agent_desertion_edit_dialog import AgentDesertionEditDialog
        from sme_gend.models.agent_evenement import AgentStageRow
        stage_form = BulkMissionStageDialog(None, count=2)
        stage_form._kind_combo.setCurrentIndex(stage_form._kind_combo.findData('stage_interne'))
        stage_form._ssession.setText('SESSION SYNTHETIQUE')
        stage_form._sr.setText('NOTE SYNTHETIQUE')
        stage_form._sn.setEditText('FORMATION SYNTHÉTIQUE')
        stage_form._on_ok()
        assert stage_form._result[1].stage.session == 'SESSION SYNTHETIQUE'
        stage_editor = AgentStageEditDialog(None, stage=AgentStageRow('INTERNE', None, None, None, None, None, None, session='SESSION SYNTHETIQUE'))
        assert stage_editor.values()['session'] == 'SESSION SYNTHETIQUE'
        desertion_editor = AgentDesertionEditDialog(None)
        desertion_editor._reference.setText('NOTE SYNTHETIQUE')
        assert desertion_editor.values()['reference_dossier'] == 'NOTE SYNTHETIQUE'
        from sme_gend.ui.check_all import bind_list_check_all
        from PySide6.QtWidgets import QListWidget, QListWidgetItem, QPushButton
        from PySide6.QtCore import Qt
        check_list, check_button = QListWidget(), QPushButton()
        check_item = QListWidgetItem('SYNTHETIQUE')
        check_item.setFlags(check_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        check_item.setCheckState(Qt.CheckState.Unchecked)
        check_list.addItem(check_item)
        bind_list_check_all(check_button, check_list)
        check_button.click()
        assert check_button.text() == 'Tout décocher'
        check_button.click()
        assert check_button.text() == 'Tout cocher'
        for form in (stage_form, stage_editor, desertion_editor, check_list, check_button):
            form.close()
        report['checks'].append('optional_event_fields_and_selection')
        # Exercise the actual date key handler and individual nomination dialog,
        # not only their imports. All values remain synthetic and in memory.
        from dataclasses import fields
        from datetime import datetime
        from types import SimpleNamespace
        from PySide6.QtCore import QEvent, Qt
        from PySide6.QtGui import QKeyEvent
        from PySide6.QtWidgets import QWidget, QDialog, QLineEdit
        from sme_gend.models.liste_generale import ListeGeneraleRow
        from sme_gend.services.grade_nomination import GradeNomination
        from sme_gend.ui.views.edition_sme_view import EditionSmeView
        from sme_gend.ui.widgets.date_input import FrenchDateEdit
        parent = QWidget()
        parent._model = SimpleNamespace(_grade_rows=[])
        row = ListeGeneraleRow(**({f.name: None for f in fields(ListeGeneraleRow)}
                                 | {'id': 'SYNTHETIC', 'noms': 'SYNTHETIC'}))
        from dataclasses import replace
        from PySide6.QtWidgets import QTableView, QComboBox
        from sme_gend.ui.views.edition_sme_view import _EditionModel, _EditionDelegate, _COL_IDX
        inline_model = _EditionModel()
        inline_model.load([replace(row, position_admin='Présent')], None, [], [], [], ['Présent', 'Mission'])
        prompted = []
        def cancel_position(agent, before, after):
            prompted.append(after)
            return False
        inline_model.position_change_prompt = cancel_position
        inline_table = QTableView()
        inline_table.setModel(inline_model)
        inline_table.setItemDelegate(_EditionDelegate(inline_model, inline_table))
        inline_table.show()
        inline_table.edit(inline_model.index(0, _COL_IDX['position_admin']))
        app.processEvents()
        inline_editor = inline_table.findChild(QComboBox)
        inline_editor.setCurrentIndex(inline_editor.findText('Mission'))
        inline_editor.activated.emit(inline_editor.currentIndex())
        app.processEvents()
        assert prompted == ['Mission'] and inline_model._rows[0].position_admin == 'Présent'
        inline_table.close()
        report['checks'].append('immediate_position_prompt')
        keyboard_errors = []
        def fill_nomination():
            dialog = app.activeModalWidget()
            try:
                if not isinstance(dialog, QDialog):
                    raise RuntimeError('Fenêtre de nomination absente.')
                date_edit = dialog.findChild(FrenchDateEdit)
                for char in '01/07/2026':
                    modifier = Qt.KeyboardModifier.KeypadModifier if char.isdigit() else Qt.KeyboardModifier.NoModifier
                    date_edit.date_edit().keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, ord(char), modifier, char))
                reference = next(w for w in dialog.findChildren(QLineEdit)
                                 if w.placeholderText().startswith('Référence'))
                reference.setText('SYNTHETIC OG')
                dialog.accept()
            except Exception as exc:
                keyboard_errors.append(str(exc))
                if isinstance(dialog, QDialog):
                    dialog.reject()
        QTimer.singleShot(0, fill_nomination)
        nomination = EditionSmeView._prompt_grade_nomination_date(parent, row, 'MDL', 'ADM')
        if keyboard_errors or nomination != GradeNomination(datetime(2026, 7, 1), 'SYNTHETIC OG'):
            raise RuntimeError('Saisie de nomination invalide : ' + '; '.join(keyboard_errors))
        parent.close()
        report['checks'].append('date_keyboard_and_nomination_reference')
        from sme_gend.ui.application_reset_dialog import ApplicationResetDialog
        from sme_gend.services.application_reset import ResetOptions
        reset_dialog = ApplicationResetDialog('offline:0/self_test')
        if reset_dialog.options() != ResetOptions(True, True):
            raise RuntimeError('Options de reinitialisation invalides.')
        reset_dialog.keep_users.setChecked(False)
        reset_dialog.keep_documents.setChecked(False)
        if reset_dialog.options() != ResetOptions(False, False) or reset_dialog.credentials.isHidden():
            raise RuntimeError('Choix de reinitialisation non independants.')
        reset_dialog.close()
        report['checks'].append('application_reset_choices')
        from sme_gend.services.month_backup import encode, decode, inspect_month_backup, MEMBER
        from tempfile import TemporaryDirectory
        from zipfile import ZipFile
        import hashlib
        with TemporaryDirectory() as temporary:
            archive = Path(temporary) / 'month.zip'
            payload = {'format':'sme-month','version':1,'month':'2026-09',
                       'tables':{'sme_periode':[{'id':1,'annee_mois':'2026-09'}]}}
            data=json.dumps(payload).encode()
            with ZipFile(archive,'w') as z:
                z.writestr(MEMBER,data)
                z.writestr('month_sha256.txt',hashlib.sha256(data).hexdigest())
            if inspect_month_backup(archive)['month']!='2026-09' or decode(encode(bytes([0,255])))!=bytes([0,255]):
                raise RuntimeError('Lecture de sauvegarde mensuelle invalide.')
        report['checks'].append('monthly_backup_codec')
        login = LoginDialog(auth)
        QTimer.singleShot(50, login.reject)
        login.exec()
        report["checks"].append("login_event_loop")
        users.close()
        set_app_auth(None)
        report["ok"] = True
    except Exception:
        report["error"] = traceback.format_exc()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return 0 if report["ok"] else 1
