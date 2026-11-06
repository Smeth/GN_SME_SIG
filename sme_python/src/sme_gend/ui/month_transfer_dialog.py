"""Import, export et restauration mensuels avec aperçu obligatoire."""
from pathlib import Path
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QPushButton, QPlainTextEdit, QFileDialog, QDialogButtonBox, QMessageBox)
from sme_gend.services import month_transfer as service
from sme_gend.ui.bg_task import run_with_progress
from sme_gend.ui.confirm_dialogs import confirm_destructive_with_ack


class MonthTransferDialog(QDialog):
    def __init__(self, parent, settings, *, initial_source=None):
        super().__init__(parent)
        self._settings, self._source, self._plan = settings, None, None
        self.setWindowTitle("Transférer une archive mensuelle")
        self.resize(960,660)
        layout = QVBoxLayout(self)
        title = QLabel("Transférer une archive mensuelle")
        title.setObjectName("dashPageTitle")
        layout.addWidget(title)
        intro = QLabel("Exportez un mois ou chargez une archive mensuelle SME / un SQL global compatible. "
                       "Le SQL est lu, jamais exécuté. Les comptes, journaux et statuts des périodes existantes sont conservés.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        row = QHBoxLayout()
        self._mode, self._month = QComboBox(), QComboBox()
        self._mode.addItem("Exporter un mois", "export")
        self._mode.addItem("Importer un mois absent", "import")
        self._mode.addItem("Restaurer une archive mensuelle", "restore")
        from sme_gend.services.auth_guard import session_has_permission, session_has_admin_role
        from sme_gend.services.workflow_permissions import session_can_perform_action
        can_export = session_has_permission("system.backup")
        can_restore = session_has_admin_role() and session_can_perform_action("workflow.import_archive")
        for index,allowed in enumerate((can_export,can_restore,can_restore)):
            self._mode.model().item(index).setEnabled(allowed)
        if not can_export and can_restore:
            self._mode.setCurrentIndex(2)
        self._load = QPushButton("Charger une archive ZIP / SQL…")
        self._load.clicked.connect(self._load_source)
        row.addWidget(self._mode,2)
        row.addWidget(QLabel("Mois :"))
        row.addWidget(self._month,1)
        row.addWidget(self._load)
        layout.addLayout(row)
        self._source_label = QLabel()
        self._source_label.setWordWrap(True)
        layout.addWidget(self._source_label)
        self._detail = QPlainTextEdit()
        self._detail.setReadOnly(True)
        self._detail.setPlainText("Sélectionnez une opération et une période.")
        layout.addWidget(self._detail,1)
        actions = QHBoxLayout()
        self._preview = QPushButton("Contrôler et afficher l’aperçu")
        self._preview.clicked.connect(self._check)
        self._execute = QPushButton("Exporter le mois…")
        self._execute.clicked.connect(self._run)
        actions.addWidget(self._preview)
        actions.addWidget(self._execute)
        layout.addLayout(actions)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("Fermer")
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._mode.currentIndexChanged.connect(self._change_mode)
        self._month.currentIndexChanged.connect(self._invalidate)
        self._change_mode()
        if initial_source is not None:
            self._mode.setCurrentIndex(2)
            self._load_path(Path(initial_source))

    def _invalidate(self,*_):
        self._plan = None
        self._execute.setEnabled(self._mode.currentData() == "export" and bool(self._month.currentData()))
        self._detail.setPlainText("Contrôle à effectuer pour la sélection actuelle.")

    def _change_mode(self,*_):
        export = self._mode.currentData() == "export"
        self._load.setEnabled(not export)
        self._execute.setText("Exporter le mois…" if export else "Confirmer l’import…" if self._mode.currentData() == "import" else "Confirmer la restauration…")
        self._execute.setObjectName("" if export else "dangerButton")
        from sme_gend.ui.theme import repolish_widget
        repolish_widget(self._execute)
        self._month.blockSignals(True)
        self._month.clear()
        if export:
            from sme_gend.repositories.sme_repository import SmeRepository
            from sme_gend.services.sme_month_closure import canonical_sme_mois_yyyy_mm
            values = run_with_progress(self,"Périodes disponibles","Lecture des mois de la base…",
                lambda:[canonical_sme_mois_yyyy_mm(row.mois) for row in SmeRepository(self._settings).fetch_all()])
            for month in sorted({v for v in values or [] if v},reverse=True): self._month.addItem(month,month)
            self._source_label.setText("Source : base de données actuellement configurée.")
        elif self._source is not None:
            for month in reversed(service.months_in(self._source)): self._month.addItem(month,month)
        else:
            self._source_label.setText("Chargez une source pour choisir parmi les mois qu’elle contient.")
        self._month.blockSignals(False)
        self._invalidate()

    def _load_source(self):
        path,_ = QFileDialog.getOpenFileName(self,"Archive mensuelle ou sauvegarde SQL","","Archive mensuelle ou SQL global (*.zip *.sql)")
        if not path: return
        self._load_path(Path(path))

    def _load_path(self,path):
        self._source, self._plan = None,None
        self._execute.setEnabled(False)
        result = run_with_progress(self,"Lecture de la source","Validation du fichier et lecture des périodes…",lambda:service.load_source(Path(path)))
        if result is None:
            self._month.clear()
            self._source_label.setText("Source non chargée. Le fichier précédent n’est plus sélectionné.")
            return
        self._source = result
        self._change_mode()
        self._source_label.setText(f"Source : {path}")

    def _check(self):
        month = self._month.currentData()
        self._plan = None
        if not month: return
        if self._mode.currentData() == "export":
            data = run_with_progress(self,"Aperçu du mois","Lecture cohérente des données du mois…",
                lambda:service.preview_export(self._settings,month))
            if data is not None:
                self._detail.setPlainText("Période : "+month+"\n\n"+"\n".join(f"{service.table_label(name)} : {len(table['rows'])} ligne(s)" for name,table in data.items())+
                    "\n\nLes identités et référentiels nécessaires sont inclus comme dépendances. Les médias locaux ne sont pas inclus.")
            return
        self._execute.setEnabled(False)
        if self._source is None: return
        plan = run_with_progress(self,"Contrôle du mois","Comparaison des identités, référentiels et données à remplacer…",
            lambda:service.preview_month(self._settings,self._source,month))
        if plan is None: return
        conflicts = list(plan.conflicts)
        if self._mode.currentData() == "import":
            from sme_gend.services.month_transfer import months_in, read_database
            present = run_with_progress(self,"Contrôle de l’import","Vérification de l’absence du mois cible…",lambda:month in months_in(read_database(self._settings)))
            if present is None: return
            if present: conflicts.append("Ce mois existe déjà : choisissez Restaurer une archive mensuelle.")
        lines = [f"Période : {month}","", "Données actuelles du mois à remplacer :"]
        lines += [f"  {service.table_label(name)} : {count}" for name,count in plan.existing_counts]
        lines += ["", "Données de la source :"]+[f"  {service.table_label(name)} : {len(table['rows'])}" for name,table in plan.source.items()]
        lines += ["", "Conflits bloquants :"]+ (conflicts or ["Aucun conflit détecté."])
        lines += ["", "Les identités et référentiels existants ne seront pas écrasés. Les dépendances manquantes compatibles seront ajoutées.",
                  "Une sauvegarde préalable et une nouvelle vérification sous verrou précèdent le remplacement.",
                  "Les médias, comptes, journaux et tâches ne sont pas restaurés. Le mois de travail ne change pas."]
        self._detail.setPlainText("\n".join(lines))
        self._plan = plan if not conflicts else None
        self._execute.setEnabled(self._plan is not None)

    def _run(self):
        month = self._month.currentData()
        if not month: return
        if self._mode.currentData() == "export":
            path,_ = QFileDialog.getSaveFileName(self,"Exporter un mois",f"sme_mois_{month}.zip","Archive mensuelle SME (*.zip);;SQL mensuel SME (*.sql)")
            if not path: return
            result = run_with_progress(self,"Export mensuel","Création d’une archive cohérente du mois…",
                lambda:service.export_month(self._settings,month,Path(path)))
            if result is not None: QMessageBox.information(self,"Export mensuel",f"Mois exporté : {month}\n{result}")
            return
        plan = self._plan
        if plan is None or plan.month != month: return
        if not confirm_destructive_with_ack(self,"Confirmer le remplacement du mois",
            f"Remplacer les données du mois {month} par la source contrôlée ?\n\n"
            "Les données mensuelles actuellement affichées dans l’aperçu seront remplacées, sans fusion. "
            "Les autres mois et les identités existantes seront conservés. Une sauvegarde préalable sera créée.",
            ack_label="J’ai contrôlé la période, les lignes à remplacer et les conflits",confirm_label="Restaurer ce mois"):
            return
        if self._plan is not plan or self._month.currentData() != plan.month:
            return
        self._plan = None
        self._execute.setEnabled(False)
        result = run_with_progress(self,"Restauration mensuelle","Sauvegarde puis remplacement transactionnel du mois…",
            lambda:service.apply_month(self._settings,plan))
        if result is not None:
            QMessageBox.information(self,"Mois restauré",f"Période : {month}\nSauvegarde préalable : {result['backup']}\nLe mois de travail est conservé.")
            host = self.window().parentWidget()
            while host is not None and not hasattr(host,"refresh_for_month_change"): host = host.parentWidget()
            if host is not None: host.refresh_for_month_change()
            self.accept()
