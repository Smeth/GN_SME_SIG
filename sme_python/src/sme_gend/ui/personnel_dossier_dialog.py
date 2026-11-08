"""Fiche dossier personnel — synthèse, parcours, traçabilité profil, médias (style maquette SIG)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QIcon, QPixmap, QShowEvent
from PySide6.QtWidgets import (
    QDialog,
    QCheckBox,
    QInputDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStyle,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from sme_gend.models.agent_evenement import AgentMaladeRow
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.models.personnel_lookup import HistoricalDossierContext
from sme_gend.services.personnel_dossier_history import (
    build_audit_timeline,
    field_label,
    fmt_val,
    format_ts_display,
)
from sme_gend.services.personnel_media import (
    ensure_personnel_media_dirs,
    grade_insignia_path,
    person_photo_path,
    personnel_media_root,
    save_person_photo,
)
from sme_gend.services.dossier_provenance import (
    dossier_entry_provenance,
    dossier_entry_provenance_tooltip,
)
from sme_gend.services.agent_dossier_events_service import AgentDossierEventsService
from sme_gend.services.agent_dossier_service import AgentDossierService
from sme_gend.services.date_display import fr_date
from sme_gend.services.matricule_format import (
    format_matricule_solde_display,
    matricule_solde_is_empty,
    normalize_matricule_solde,
)
from sme_gend.services.referentiel_service import ReferentielService
from sme_gend.ui.confirm_dialogs import confirm_yes_no
from sme_gend.services.personnel_service import PersonnelService
from sme_gend.ui.personnel_edit_dialog import PersonnelEditDialog


def _fmt_row_field(row: ListeGeneraleRow, attr: str) -> str:
    if attr == "matricule_solde":
        return format_matricule_solde_display(row.matricule_solde)
    v = getattr(row, attr, None)
    if v is None:
        return "—"
    if hasattr(v, "strftime"):
        try:
            return fr_date(v)
        except Exception:
            pass
    s = str(v).strip()
    return s if s else "—"


class PersonnelDossierDialog(QDialog):
    """Vue complète : identité, photos, parcours (audit carrière), modifications profil."""

    def __init__(
        self,
        parent: QWidget | None,
        row: ListeGeneraleRow,
        referentiel: ReferentielService,
        service: PersonnelService,
        *,
        allow_edit: bool = True,
        historical_context: HistoricalDossierContext | None = None,
        readonly_snapshot: dict | None = None,
        initial_snapshot: dict | None = None,
    ) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        self.setObjectName("PersonnelDossierDialog")
        self.setWindowTitle(
            "Fiche dossier (historique)" if historical_context else "Fiche dossier personnel"
        )
        self.setModal(True)
        self.resize(1100, 720)
        self.setMinimumSize(880, 520)

        self._ref = referentiel
        prepared = readonly_snapshot if readonly_snapshot is not None else initial_snapshot
        self._readonly_snapshot = prepared
        if prepared is not None:
            if readonly_snapshot is not None and allow_edit:
                raise ValueError("Une fiche préparée en arrière-plan est en consultation seule.")
            from types import SimpleNamespace
            self._ref = SimpleNamespace(settings=service.settings,
                grades=lambda: prepared["grades"],
                structures=lambda: prepared["structures"])
        self._service = service
        self._settings = service.settings
        self._row = row
        self._allow_edit = allow_edit and historical_context is None
        self._historical_context = historical_context
        if prepared is None:
            self._events_svc = AgentDossierEventsService(service.settings)
            self._dossier_svc = AgentDossierService(service.settings)
        else:
            self._events_svc = SimpleNamespace(**{name: (lambda *_, values=values: values)
                for name, values in prepared["events"].items()})
            self._dossier_svc = SimpleNamespace(**{name: (lambda *_, values=values: values)
                for name, values in prepared["dossier"].items()})
        self._diplome_rows = []
        self._distinction_rows = []
        self._passation_rows = []
        self._affectation_rows = []
        self._liste_generale_changed = False

        if prepared is None:
            ensure_personnel_media_dirs()

        root = QVBoxLayout(self)
        root.setSpacing(0)
        root.setContentsMargins(0, 0, 0, 0)

        self._header = QFrame()
        self._header.setObjectName("PersonnelDossierHeader")
        hl = QVBoxLayout(self._header)
        hl.setSpacing(6)
        hl.setContentsMargins(20, 18, 20, 18)

        self._title = QLabel()
        self._title.setObjectName("PersonnelDossierTitle")
        self._subtitle = QLabel()
        self._subtitle.setObjectName("PersonnelDossierSubtitle")
        hl.addWidget(self._title)
        hl.addWidget(self._subtitle)

        self._historical_banner = QLabel()
        self._historical_banner.setObjectName("PersonnelDossierHistoricalBanner")
        self._historical_banner.setWordWrap(True)
        self._historical_banner.setTextFormat(Qt.TextFormat.RichText)
        if historical_context is not None:
            self._historical_banner.setText(
                "<b>Consultation d’une situation historique</b><br/>"
                f"{historical_context.banner_text()}"
            )
            self._historical_banner.show()
        else:
            self._historical_banner.hide()
        hl.addWidget(self._historical_banner)

        btn_row = QHBoxLayout()
        self._btn_edit = QPushButton("Modifier la fiche…")
        self._btn_edit.setObjectName("PersonnelDossierPrimaryBtn")
        self._btn_edit.clicked.connect(self._on_edit)
        self._btn_edit.setVisible(self._allow_edit)
        self._btn_edit.setEnabled(self._allow_edit)
        self._btn_pdf = QPushButton("Exporter dossier PDF…")
        self._btn_actualiser = QPushButton("Actualiser")
        self._btn_actualiser.setToolTip("Relire la fiche et le dossier enregistrés dans la base")
        self._btn_actualiser.clicked.connect(self._actualiser)
        self._btn_actualiser.setVisible(historical_context is None and readonly_snapshot is None)
        self._btn_pdf.clicked.connect(self._on_export_parcours_pdf)
        self._btn_conge = QPushButton("Ajouter un congé…")
        self._btn_conge.clicked.connect(self._on_add_conge)
        self._btn_conge.setVisible(self._allow_edit)
        self._btn_conge.setEnabled(self._allow_edit)
        self._btn_passation = QPushButton("Passation de grade…")
        self._btn_passation.clicked.connect(self._on_add_passation)
        self._btn_passation.setVisible(self._allow_edit)
        self._btn_passation.setEnabled(self._allow_edit)
        self._btn_correct_return = QPushButton('Corriger un retour…')
        self._btn_correct_return.clicked.connect(self._on_correct_return)
        self._btn_correct_return.setVisible(False)
        self._alerts_label = QLabel()
        self._alerts_label.setObjectName("CareerAlertsBanner")
        self._alerts_label.setStyleSheet(
            "QLabel { color: #273444; background-color: #fff8e1; padding: 10px; border-radius: 6px; }")
        self._alerts_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.LinksAccessibleByMouse | Qt.TextInteractionFlag.TextSelectableByMouse)
        self._alerts_label.linkActivated.connect(self._on_career_alert_link)
        self._alerts_label.setWordWrap(True)
        self._alerts_label.hide()
        btn_row.addWidget(self._btn_edit)
        btn_row.addWidget(self._btn_pdf)
        btn_row.addWidget(self._btn_actualiser)
        btn_row.addWidget(self._btn_conge)
        btn_row.addWidget(self._btn_passation)
        btn_row.addWidget(self._btn_correct_return)
        btn_row.addStretch()
        hl.addLayout(btn_row)
        hl.addWidget(self._alerts_label)

        self._read_errors = {}
        self._read_error_label = QLabel()
        self._read_error_label.setWordWrap(True)
        self._read_error_label.setTextFormat(Qt.TextFormat.PlainText)
        self._read_retry = QPushButton("Réessayer les lectures du dossier")
        self._read_retry.clicked.connect(self._retry_reads)
        self._read_error_label.hide()
        self._read_retry.hide()
        root.addWidget(self._header)
        root.addWidget(self._read_error_label)
        root.addWidget(self._read_retry)

        body = QWidget()
        body_lay = QHBoxLayout(body)
        body_lay.setContentsMargins(16, 16, 16, 16)
        body_lay.setSpacing(20)

        # — Colonne médias
        media_col = QFrame()
        media_col.setObjectName("PersonnelDossierMediaColumn")
        media_col.setFixedWidth(236)
        mv = QVBoxLayout(media_col)
        mv.setSpacing(14)

        ph_title = QLabel("Photo d’identité")
        ph_title.setProperty("subheading", True)
        self._photo = QLabel()
        self._photo.setObjectName("PersonnelDossierPhoto")
        self._photo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._photo.setMinimumSize(180, 200)
        self._photo.setMaximumSize(220, 260)
        self._photo.setScaledContents(False)

        gr_title = QLabel("Insigne / grade")
        gr_title.setProperty("subheading", True)
        self._grade_pix = QLabel()
        self._grade_pix.setObjectName("PersonnelDossierGradePix")
        self._grade_pix.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._grade_pix.setMinimumSize(100, 100)
        self._grade_pix.setMaximumSize(200, 120)

        self._media_hint = QLabel()
        self._media_hint.setWordWrap(True)
        self._media_hint.setObjectName("PersonnelDossierHint")
        self._media_hint.setTextFormat(Qt.TextFormat.RichText)

        mv.addWidget(ph_title)
        mv.addWidget(self._photo, alignment=Qt.AlignmentFlag.AlignHCenter)
        self._btn_photo = QPushButton("Choisir une photo…")
        self._btn_photo.clicked.connect(self._on_choose_photo)
        self._btn_photo.setVisible(self._allow_edit)
        self._btn_photo.setEnabled(self._allow_edit)
        mv.addWidget(self._btn_photo, alignment=Qt.AlignmentFlag.AlignHCenter)
        mv.addWidget(gr_title)
        mv.addWidget(self._grade_pix, alignment=Qt.AlignmentFlag.AlignHCenter)
        mv.addWidget(self._media_hint)
        mv.addStretch()

        body_lay.addWidget(media_col)

        # — Onglets
        self._tabs = QTabWidget()
        self._tabs.setObjectName("PersonnelDossierTabs")
        self._tabs.setDocumentMode(True)

        self._tab_synth = QWidget()
        self._synth_host = QWidget()
        self._synth_host.setObjectName("PersonnelDossierSynthHost")
        self._synth_scroll = QScrollArea()
        self._synth_scroll.setWidgetResizable(True)
        self._synth_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._synth_scroll.setWidget(self._synth_host)
        self._synth_lay = QVBoxLayout(self._synth_host)
        self._synth_lay.setSpacing(16)
        self._synth_lay.setContentsMargins(12, 12, 12, 12)

        # Bandeau alerte (position admin spéciale) — affiché en haut de l'onglet Synthèse
        self._alert_banner = QLabel()
        self._alert_banner.setObjectName("PersonnelDossierAlertBanner")
        self._alert_banner.setWordWrap(True)
        self._alert_banner.setVisible(False)

        sl = QVBoxLayout(self._tab_synth)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(0)
        sl.addWidget(self._alert_banner)
        sl.addWidget(self._synth_scroll, stretch=1)

        self._tab_profil = QWidget()
        self._profil_hint = QLabel(
            "Journal append-only : chaque modification enregistrée dans l’application "
            "(identité, grade, structure, etc.) avec date, mois SME, auteur et motif le cas échéant. "
            "<b>Consultation seule</b> — les champs sensibles et l’historique de carrière "
            "ne sont modifiables que par un administrateur."
        )
        self._profil_hint.setWordWrap(True)
        self._profil_hint.setObjectName("PersonnelDossierTabHint")
        self._profil_hint.setTextFormat(Qt.TextFormat.RichText)
        self._profil_table = QTableWidget()
        self._profil_table.setColumnCount(6)
        self._profil_table.setHorizontalHeaderLabels(
            ["Date / heure", "Mois SME", "Champ", "Ancienne valeur", "Nouvelle valeur", "Utilisateur"]
        )
        self._profil_table.horizontalHeader().setStretchLastSection(True)
        self._profil_table.setAlternatingRowColors(True)
        self._profil_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._profil_table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        pr = QVBoxLayout(self._tab_profil)
        pr.setContentsMargins(8, 8, 8, 8)
        pr.addWidget(self._profil_hint)
        pr.addWidget(self._profil_table)

        # — Onglet dossier unifié (carrière, diplômes, activités RH)
        self._tab_dossier = QWidget()
        self._dossier_scroll = QScrollArea()
        self._dossier_scroll.setWidgetResizable(True)
        self._dossier_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._dossier_inner = QWidget()
        self._dossier_lay = QVBoxLayout(self._dossier_inner)
        self._dossier_lay.setSpacing(18)
        self._dossier_lay.setContentsMargins(4, 4, 12, 16)
        self._dossier_scroll.setWidget(self._dossier_inner)
        dossier_root = QVBoxLayout(self._tab_dossier)
        dossier_root.setContentsMargins(8, 8, 8, 8)
        dossier_intro = QLabel(
            "Dossier complet de l’agent — chaque rubrique est distincte. "
            "Les imports d’archives Access alimentent automatiquement les passations, "
            "affectations, diplômes et distinctions (médailles / décorations) lorsque les données "
            "évoluent. Les distinctions ne proviennent pas des archives SME mensuelles."
        )
        dossier_intro.setWordWrap(True)
        dossier_intro.setObjectName("PersonnelDossierTabHint")
        dossier_root.addWidget(dossier_intro)
        self._group_history = QCheckBox("Regrouper les éléments identiques (décocher pour les fiches mensuelles)")
        self._group_history.setChecked(True)
        self._group_history.toggled.connect(self._refresh_dossier_tab)
        dossier_root.addWidget(self._group_history)
        self._lbl_history_admin_hint = QLabel()
        self._lbl_history_admin_hint.setObjectName("PersonnelDossierTabHint")
        self._lbl_history_admin_hint.setWordWrap(True)
        self._lbl_history_admin_hint.hide()
        dossier_root.addWidget(self._lbl_history_admin_hint)
        dossier_root.addWidget(self._dossier_scroll)

        self._btn_dip_add = QPushButton("Ajouter diplôme…")
        self._btn_dip_edit = QPushButton("Modifier…")
        self._btn_dip_del = QPushButton("Supprimer…")
        self._btn_dip_add.clicked.connect(self._on_add_diplome)
        self._btn_dip_edit.clicked.connect(self._on_edit_diplome)
        self._btn_dip_del.clicked.connect(self._on_delete_diplome)

        self._btn_dist_add = QPushButton("Ajouter distinction…")
        self._btn_dist_edit = QPushButton("Modifier…")
        self._btn_dist_del = QPushButton("Supprimer…")
        self._btn_dist_add.clicked.connect(self._on_add_distinction)
        self._btn_dist_edit.clicked.connect(self._on_edit_distinction)
        self._btn_dist_del.clicked.connect(self._on_delete_distinction)

        self._btn_pas_add = QPushButton("Ajouter passation…")
        self._btn_pas_edit = QPushButton("Modifier…")
        self._btn_pas_del = QPushButton("Supprimer…")
        self._btn_pas_add.clicked.connect(self._on_add_passation)
        self._btn_pas_edit.clicked.connect(self._on_edit_passation)
        self._btn_pas_del.clicked.connect(self._on_delete_passation)

        self._btn_aff_add = QPushButton("Ajouter affectation…")
        self._btn_aff_edit = QPushButton("Modifier…")
        self._btn_aff_del = QPushButton("Supprimer…")
        self._btn_aff_add.clicked.connect(self._on_add_affectation)
        self._btn_aff_edit.clicked.connect(self._on_edit_affectation)
        self._btn_aff_del.clicked.connect(self._on_delete_affectation)

        self._malade_event_rows: list[AgentMaladeRow] = []
        self._btn_mal_add = QPushButton("Ajouter une maladie…")
        self._btn_mal_edit = QPushButton("Modifier…")
        self._btn_mal_del = QPushButton("Supprimer…")
        self._btn_mal_add.clicked.connect(self._on_add_malade)
        self._btn_mal_edit.clicked.connect(self._on_edit_malade)
        self._btn_mal_del.clicked.connect(self._on_delete_malade)

        self._stage_event_rows = []
        self._btn_stage_add = QPushButton("Ajouter un stage…")
        self._btn_stage_edit = QPushButton("Modifier…")
        self._btn_stage_del = QPushButton("Supprimer…")
        self._btn_stage_add.clicked.connect(self._on_add_stage)
        self._btn_stage_edit.clicked.connect(self._on_edit_stage)
        self._btn_stage_del.clicked.connect(self._on_delete_stage)

        self._mission_event_rows = []
        self._btn_mission_add = QPushButton("Ajouter une mission…")
        self._btn_mission_edit = QPushButton("Modifier…")
        self._btn_mission_del = QPushButton("Supprimer…")
        self._btn_mission_add.clicked.connect(self._on_add_mission)
        self._btn_mission_edit.clicked.connect(self._on_edit_mission)
        self._btn_mission_del.clicked.connect(self._on_delete_mission)

        self._desertion_event_rows = []
        self._btn_desertion_add = QPushButton("Ajouter une désertion…")
        self._btn_desertion_edit = QPushButton("Modifier…")
        self._btn_desertion_del = QPushButton("Supprimer…")
        self._btn_desertion_add.clicked.connect(self._on_add_desertion)
        self._btn_desertion_edit.clicked.connect(self._on_edit_desertion)
        self._btn_desertion_del.clicked.connect(self._on_delete_desertion)

        self._conge_event_rows = []
        self._btn_conge_add = QPushButton("Ajouter un congé…")
        self._btn_conge_add.clicked.connect(self._on_add_conge)
        self._btn_conge_edit = QPushButton("Modifier…")
        self._btn_conge_del = QPushButton("Supprimer…")
        self._btn_conge_edit.clicked.connect(self._on_edit_conge)
        self._btn_conge_del.clicked.connect(self._on_delete_conge)

        self._tbl_passations = QTableWidget()
        self._tbl_passations.setColumnCount(5)
        self._tbl_passations.setHorizontalHeaderLabels(
            ["Grade", "Date passation", "N° décision", "Observation", "Origine"]
        )
        self._tbl_passations.horizontalHeader().setStretchLastSection(True)
        self._tbl_passations.setAlternatingRowColors(True)
        self._tbl_passations.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._tbl_passations.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._tbl_passations.setMinimumHeight(120)

        self._tbl_affectations = QTableWidget()
        self._tbl_affectations.setColumnCount(6)
        self._tbl_affectations.setHorizontalHeaderLabels(
            ["Date", "Structure", "Fonction", "Subdivision", "Mois SME", "Origine"]
        )
        self._tbl_affectations.horizontalHeader().setStretchLastSection(True)
        self._tbl_affectations.setAlternatingRowColors(True)
        self._tbl_affectations.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._tbl_affectations.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._tbl_affectations.setMinimumHeight(120)

        self._tbl_functions = self._make_events_table(
            ["Constaté en", "Structure", "Fonction", "Subdivision", "Nature"])
        self._tbl_diplomes = QTableWidget()
        self._tbl_diplomes.setColumnCount(8)
        self._tbl_diplomes.setHorizontalHeaderLabels(
            ["Catégorie", "Libellé", "Date obtention", "Lieu", "Mention", "Établissement", "Mois attestés", "Origine"]
        )
        self._tbl_diplomes.horizontalHeader().setStretchLastSection(True)
        self._tbl_diplomes.setAlternatingRowColors(True)
        self._tbl_diplomes.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._tbl_diplomes.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._tbl_diplomes.setMinimumHeight(100)

        self._tbl_distinctions = QTableWidget()
        self._tbl_distinctions.setColumnCount(8)
        self._tbl_distinctions.setHorizontalHeaderLabels(
            [
                "Type",
                "Distinction",
                "Date attribution",
                "JO",
                "N° arrêté",
                "Date arrêté",
                "Citation",
                "Origine",
            ]
        )
        self._tbl_distinctions.horizontalHeader().setStretchLastSection(True)
        self._tbl_distinctions.setAlternatingRowColors(True)
        self._tbl_distinctions.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._tbl_distinctions.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._tbl_distinctions.setMinimumHeight(88)

        self._tbl_missions = self._make_events_table(
            ["Début", "Fin", "Nature", "Lieu", "Référence", "Observation", "Mois SME"]
        )
        self._tbl_diplomes.cellDoubleClicked.connect(lambda r, c: self._show_group_sources(self._diploma_groups, r))
        self._tbl_affectations.cellDoubleClicked.connect(lambda r, c: self._show_group_sources(self._affectation_groups, r))
        self._tbl_stages = self._make_events_table(
            ["Type", "Début", "Fin", "Nature", "Lieu", "Réf/note", "Session", "Observation", "Mois SME"]
        )
        self._tbl_malades = self._make_events_table(
            ["Début", "Fin", "Pathologie", "Observation", "Mois SME"]
        )
        self._tbl_desertions = self._make_events_table(["Date", "Réf/note", "Observation", "Mois SME"])
        self._tbl_positions = self._make_events_table(
            ["Position", "Début", "Fin", "Lieu", "Référence décision", "Observation", "Mois SME"]
        )
        self._tbl_conges = self._make_events_table(
            ["Type", "Début", "Fin", "Jours", "Source", "Observation", "Mois SME"]
        )
        for tbl in (
            self._tbl_missions, self._tbl_stages, self._tbl_malades,
            self._tbl_desertions, self._tbl_conges, self._tbl_positions,
        ):
            tbl.setMinimumHeight(88)

        for w in (
            self._btn_dip_add, self._btn_dip_edit, self._btn_dip_del,
            self._btn_dist_add, self._btn_dist_edit, self._btn_dist_del,
            self._btn_pas_add, self._btn_pas_edit, self._btn_pas_del,
            self._btn_aff_add, self._btn_aff_edit, self._btn_aff_del,
        ):
            w.setVisible(self._allow_edit)
            w.setEnabled(self._allow_edit)

        for w in (self._btn_mal_add, self._btn_mal_edit, self._btn_mal_del):
            w.setVisible(self._allow_edit)
            w.setEnabled(self._allow_edit)

        for w in (self._btn_stage_add, self._btn_stage_edit, self._btn_stage_del):
            w.setVisible(self._allow_edit)
            w.setEnabled(self._allow_edit)

        for w in (
            self._btn_mission_add, self._btn_mission_edit, self._btn_mission_del,
            self._btn_desertion_add, self._btn_desertion_edit, self._btn_desertion_del,
            self._btn_conge_add, self._btn_conge_edit, self._btn_conge_del,
        ):
            w.setVisible(self._allow_edit)
            w.setEnabled(self._allow_edit)

        self._tbl_stages.cellDoubleClicked.connect(lambda r, c: self._show_group_sources(self._stage_groups, r))
        self._build_dossier_sections()

        self._tabs.addTab(self._tab_synth, "Synthèse")
        self._tabs.addTab(self._tab_dossier, "Dossier complet")
        self._tabs.addTab(self._tab_profil, "Traçabilité des modifications")
        self._consultation_notice = QLabel()
        self._consultation_notice.setWordWrap(True)
        body_lay.addWidget(self._consultation_notice)
        self._tabs.currentChanged.connect(self._trace_dossier_tab)

        body_lay.addWidget(self._tabs, stretch=1)

        self._body_scroll = QScrollArea()
        self._body_scroll.setObjectName("PersonnelDossierBodyScroll")
        self._body_scroll.setWidgetResizable(True)
        self._body_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._body_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.MinimumExpanding)
        self._body_scroll.setWidget(body)
        root.addWidget(self._body_scroll, stretch=1)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(self.reject)
        close_btn = bb.button(QDialogButtonBox.StandardButton.Close)
        if close_btn:
            close_btn.setText("Fermer")
        root.addWidget(bb)

        self._initial_tables_pending = initial_snapshot
        self._constructing_prepared = initial_snapshot is not None
        self._refresh_all()
        self._constructing_prepared = False
        if initial_snapshot is not None and readonly_snapshot is None:
            # The initial projection contains revision-bearing history rows.
            # Later writes use the real services and their normal conflict checks.
            self._ref = referentiel
            self._readonly_snapshot = None
            self._events_svc = AgentDossierEventsService(service.settings)
            self._dossier_svc = AgentDossierService(service.settings)
        self._tabs.currentChanged.connect(self._populate_prepared_history)

    def _populate_prepared_history(self, index):
        snapshot = getattr(self, '_initial_tables_pending', None)
        if index == 0 or snapshot is None:
            return
        from types import SimpleNamespace
        self._initial_tables_pending = None
        saved = self._readonly_snapshot, self._ref, self._events_svc, self._dossier_svc
        try:
            self._readonly_snapshot = snapshot
            self._ref = SimpleNamespace(grades=lambda: snapshot['grades'], structures=lambda: snapshot['structures'])
            self._events_svc = SimpleNamespace(**{name: (lambda *_, values=values: values)
                for name, values in snapshot['events'].items()})
            self._dossier_svc = SimpleNamespace(**{name: (lambda *_, values=values: values)
                for name, values in snapshot['dossier'].items()})
            self._refresh_dossier_tab()
            self._refresh_profil_tab()
        finally:
            self._readonly_snapshot, self._ref, self._events_svc, self._dossier_svc = saved

    def prepare_for_exec(self) -> None:
        """Taille / position puis autorise l'affichage (appelé juste avant ``exec()``)."""
        self._fit_to_available_screen()
        self.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, False)

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self._fit_to_available_screen()
        if not event.spontaneous():
            self._trace_dossier_tab(self._tabs.currentIndex())

    def _trace_dossier_tab(self, index):
        if not self.isVisible() or index < 0:
            return
        from sme_gend.services.consultation_audit import queue_consultation
        queue_consultation(self, self._settings, action='dossier_tab_consulted',
            section=self._tabs.tabText(index), row=self._row,
            on_error=lambda _exc: self._consultation_notice.setText("Consultation affichée ; enregistrement au journal indisponible. Vérifiez la connexion ou la maintenance."))

    def _fit_to_available_screen(self) -> None:
        """Ajuste taille et position pour tenir dans la zone visible (barre des tâches incluse)."""
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is None:
            return
        avail = screen.availableGeometry()
        margin = 12
        max_w = max(400, avail.width() - 2 * margin)
        max_h = max(400, avail.height() - 2 * margin)
        w = min(max(self.minimumWidth(), self.width()), max_w)
        h = min(max(self.minimumHeight(), self.height()), max_h)
        self.resize(w, h)
        geo = self.geometry()
        geo.moveCenter(avail.center())
        if geo.top() < avail.top() + margin:
            geo.moveTop(avail.top() + margin)
        if geo.left() < avail.left() + margin:
            geo.moveLeft(avail.left() + margin)
        if geo.bottom() > avail.bottom() - margin:
            geo.moveTop(avail.bottom() - margin - geo.height() + 1)
        if geo.right() > avail.right() - margin:
            geo.moveLeft(avail.right() - margin - geo.width() + 1)
        self.move(geo.topLeft())

    def _media_person_key(self) -> str:
        return str(self._row.satellite_join_key() or self._row.id or "").strip()

    def _on_choose_photo(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Photo d'identité",
            str(Path.home()),
            "Images (*.jpg *.jpeg *.png *.webp)",
        )
        if not path:
            return
        key = self._media_person_key()
        if not key:
            QMessageBox.warning(self, "Photo", "Identifiant agent introuvable.")
            return
        try:
            dest = save_person_photo(key, Path(path))
        except Exception as e:
            QMessageBox.critical(self, "Photo", str(e))
            return
        self._refresh_header_and_media()
        QMessageBox.information(self, "Photo", f"Photo enregistrée :\n{dest}")

    def _on_export_parcours_pdf(self) -> None:
        from sme_gend.services.auth_guard import require_permission

        if not require_permission(self, "effectifs.export"):
            return
        from sme_gend.config.sme_context import load_sme_context
        from sme_gend.services.parcours_pdf_export import export_parcours_pdf

        ctx = load_sme_context()
        mois = (self._service.settings.mysql_view_mois or ctx.mois_actif or "parcours").replace("-", "")
        safe = "".join(c if c.isalnum() or c in " -_" else "_" for c in (self._row.noms or "agent"))[:40]
        default = str(Path.home() / f"parcours_{safe}_{mois}.pdf")
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Enregistrer le parcours en PDF",
            default,
            "PDF (*.pdf)",
        )
        if not path:
            return
        try:
            export_parcours_pdf(self._row, self._service.settings, path)
            QMessageBox.information(self, "PDF", f"Parcours enregistré :\n{path}")
        except Exception as e:
            QMessageBox.critical(self, "PDF", str(e))

    def _on_add_conge(self) -> None:
        from sme_gend.config.sme_context import load_sme_context
        from sme_gend.ui.conge_edit_dialog import CongeEditDialog

        ctx = load_sme_context()
        dlg = CongeEditDialog(
            self,
            self._service.settings,
            str(self._row.satellite_join_key() or ""),
            sme_periode_mois=self._service.settings.mysql_view_mois or ctx.mois_actif,
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self._events_svc.add_conge(
                str(self._row.satellite_join_key() or ""), **dlg.values()
            )
        except Exception as exc:
            QMessageBox.critical(self, "Congé", f"Enregistrement impossible :\n{exc}")
            return
        self._refresh_dossier_tab()

    def _on_edit_conge(self) -> None:
        row = self._tbl_conges.currentRow()
        if row < 0 or row >= len(self._conge_event_rows):
            QMessageBox.information(self, "Congé", "Sélectionnez une période de congé.")
            return
        existing = self._conge_event_rows[row]
        if existing.id is None:
            QMessageBox.warning(self, "Congé", "Identifiant de congé introuvable.")
            return
        from sme_gend.config.sme_context import load_sme_context
        from sme_gend.ui.conge_edit_dialog import CongeEditDialog

        ctx = load_sme_context()
        dlg = CongeEditDialog(
            self,
            self._service.settings,
            str(self._row.satellite_join_key() or ""),
            sme_periode_mois=existing.periode_sme or ctx.mois_actif,
            conge=existing,
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self._events_svc.update_conge(
                str(self._row.satellite_join_key() or ""), existing.id, expected=existing, **dlg.values()
            )
        except Exception as exc:
            QMessageBox.critical(self, "Congé", f"Modification impossible :\n{exc}")
            return
        self._refresh_dossier_tab()

    def _on_delete_conge(self) -> None:
        row = self._tbl_conges.currentRow()
        if row < 0 or row >= len(self._conge_event_rows):
            QMessageBox.information(self, "Congé", "Sélectionnez une période de congé.")
            return
        existing = self._conge_event_rows[row]
        if existing.id is None:
            QMessageBox.warning(self, "Congé", "Identifiant de congé introuvable.")
            return
        if not confirm_yes_no(
            self, "Supprimer", f"Supprimer {existing.type_label} du {existing.date_debut} ?",
            default_no=True,
        ):
            return
        try:
            self._events_svc.delete_conge(
                str(self._row.satellite_join_key() or ""), existing.id, expected=existing
            )
        except Exception as exc:
            QMessageBox.critical(self, "Congé", f"Suppression impossible :\n{exc}")
            return
        self._refresh_dossier_tab()

    def _on_add_mission(self) -> None:
        from sme_gend.ui.agent_mission_edit_dialog import AgentMissionEditDialog

        dlg = AgentMissionEditDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self._events_svc.add_mission(
                str(self._row.satellite_join_key() or ""), **dlg.values()
            )
        except Exception as exc:
            QMessageBox.critical(self, "Mission", f"Enregistrement impossible :\n{exc}")
            return
        self._refresh_dossier_tab()

    def _on_edit_mission(self) -> None:
        row = self._tbl_missions.currentRow()
        if row < 0 or row >= len(self._mission_event_rows):
            QMessageBox.information(self, "Mission", "Sélectionnez une mission.")
            return
        existing = self._mission_event_rows[row]
        if existing.id is None:
            QMessageBox.warning(self, "Mission", "Identifiant de mission introuvable.")
            return
        from sme_gend.ui.agent_mission_edit_dialog import AgentMissionEditDialog

        dlg = AgentMissionEditDialog(self, mission=existing)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self._events_svc.update_mission(
                str(self._row.satellite_join_key() or ""), existing.id, expected=existing, **dlg.values()
            )
        except Exception as exc:
            QMessageBox.critical(self, "Mission", f"Modification impossible :\n{exc}")
            return
        self._refresh_dossier_tab()

    def _on_delete_mission(self) -> None:
        row = self._tbl_missions.currentRow()
        if row < 0 or row >= len(self._mission_event_rows):
            QMessageBox.information(self, "Mission", "Sélectionnez une mission.")
            return
        existing = self._mission_event_rows[row]
        if existing.id is None:
            QMessageBox.warning(self, "Mission", "Identifiant de mission introuvable.")
            return
        if not confirm_yes_no(
            self, "Supprimer", f"Supprimer la mission « {existing.nature or 'sans nature'} » ?",
            default_no=True,
        ):
            return
        try:
            self._events_svc.delete_mission(
                str(self._row.satellite_join_key() or ""), existing.id, expected=existing
            )
        except Exception as exc:
            QMessageBox.critical(self, "Mission", f"Suppression impossible :\n{exc}")
            return
        self._refresh_dossier_tab()

    def _on_add_desertion(self) -> None:
        from sme_gend.ui.agent_desertion_edit_dialog import AgentDesertionEditDialog

        dlg = AgentDesertionEditDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self._events_svc.add_desertion(
                str(self._row.satellite_join_key() or ""), **dlg.values()
            )
        except Exception as exc:
            QMessageBox.critical(self, "Désertion", f"Enregistrement impossible :\n{exc}")
            return
        self._refresh_dossier_tab()

    def _on_edit_desertion(self) -> None:
        row = self._tbl_desertions.currentRow()
        if row < 0 or row >= len(self._desertion_event_rows):
            QMessageBox.information(self, "Désertion", "Sélectionnez une désertion.")
            return
        existing = self._desertion_event_rows[row]
        if existing.id is None:
            QMessageBox.warning(self, "Désertion", "Identifiant de désertion introuvable.")
            return
        from sme_gend.ui.agent_desertion_edit_dialog import AgentDesertionEditDialog

        dlg = AgentDesertionEditDialog(self, desertion=existing)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self._events_svc.update_desertion(
                str(self._row.satellite_join_key() or ""), existing.id, expected=existing, **dlg.values()
            )
        except Exception as exc:
            QMessageBox.critical(self, "Désertion", f"Modification impossible :\n{exc}")
            return
        self._refresh_dossier_tab()

    def _on_delete_desertion(self) -> None:
        row = self._tbl_desertions.currentRow()
        if row < 0 or row >= len(self._desertion_event_rows):
            QMessageBox.information(self, "Désertion", "Sélectionnez une désertion.")
            return
        existing = self._desertion_event_rows[row]
        if existing.id is None:
            QMessageBox.warning(self, "Désertion", "Identifiant de désertion introuvable.")
            return
        if not confirm_yes_no(
            self, "Supprimer", f"Supprimer la désertion du {existing.date_desertion} ?",
            default_no=True,
        ):
            return
        try:
            self._events_svc.delete_desertion(
                str(self._row.satellite_join_key() or ""), existing.id, expected=existing
            )
        except Exception as exc:
            QMessageBox.critical(self, "Désertion", f"Suppression impossible :\n{exc}")
            return
        self._refresh_dossier_tab()

    def _on_add_malade(self) -> None:
        from sme_gend.ui.malade_edit_dialog import MaladeEditDialog

        dlg = MaladeEditDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self._events_svc.add_malade(self._malade_agent_key(), **dlg.values())
        except Exception as e:
            QMessageBox.critical(self, "Maladie", str(e))
            return
        self._refresh_all()

    def _malade_agent_key(self) -> str:
        return str(self._row.satellite_join_key() or "").strip()

    def _selected_malade(self) -> AgentMaladeRow | None:
        row = self._tbl_malades.currentRow()
        if row < 0 or row >= len(self._malade_event_rows):
            QMessageBox.information(self, "Maladie", "Sélectionnez une période de maladie.")
            return None
        data = self._malade_event_rows[row]
        if data.malade_id is None:
            QMessageBox.information(
                self,
                "Maladie",
                "Cette fiche n’a pas d’identifiant : corrigez-la dans l’écran "
                "« Malades / punitions » du mois concerné.",
            )
            return None
        return data

    def _on_edit_malade(self) -> None:
        from sme_gend.ui.malade_edit_dialog import MaladeEditDialog

        data = self._selected_malade()
        if data is None:
            return
        dlg = MaladeEditDialog(self, existing=data)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self._events_svc.update_malade(int(data.malade_id or 0), expected=data, owner_legacy_id=self._malade_agent_key(), **dlg.values())
        except Exception as e:
            QMessageBox.critical(self, "Maladie", str(e))
            return
        self._refresh_all()

    def _on_delete_malade(self) -> None:
        data = self._selected_malade()
        if data is None:
            return
        origine = (
            "saisie dans l’application"
            if data.saisie_manuelle
            else f"archive {data.periode_sme}" if data.periode_sme else "archive"
        )
        if not confirm_yes_no(
            self,
            "Supprimer",
            f"Supprimer la maladie du {fr_date(data.date_debut)} ({origine}, "
            f"{data.pathologie or 'sans pathologie'}) ?",
            default_no=True,
        ):
            return
        try:
            self._events_svc.delete_malade(int(data.malade_id or 0), expected=data, owner_legacy_id=self._malade_agent_key())
        except Exception as e:
            QMessageBox.critical(self, "Maladie", str(e))
            return
        self._refresh_all()

    def _pick_group_source(self, group):
        if len(group.members) == 1:
            return group.members[0]
        labels = [f"{i+1}. Mois {getattr(r, 'periode_sme', None) or getattr(r, 'sme_periode_mois', None) or getattr(r, 'obs', None) or 'non renseigné'} — fiche {r.id}"
                  for i, r in enumerate(group.members)]
        label, ok = QInputDialog.getItem(self, "Fiches sources", "Choisissez la fiche mensuelle à modifier ou supprimer :", labels, 0, False)
        return group.members[labels.index(label)] if ok else None

    def _show_group_sources(self, groups, index):
        if index < 0 or index >= len(groups):
            return
        group = groups[index]
        lines = [group.detail]
        for r in group.members:
            lines.append(f"Fiche {r.id} - {getattr(r, 'periode_sme', None) or getattr(r, 'sme_periode_mois', None) or getattr(r, 'obs', None) or 'sans mois'} - "
                         f"{getattr(r, 'date_debut', None) or getattr(r, 'date_obtention', None) or getattr(r, 'date_affectation', None) or 'date inconnue'}")
        QMessageBox.information(self, "Détail des sources conservées", "\n".join(lines))

    def _on_add_stage(self) -> None:
        from sme_gend.ui.agent_stage_edit_dialog import AgentStageEditDialog

        dlg = AgentStageEditDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        legacy_id = str(self._row.satellite_join_key() or "").strip()
        try:
            self._events_svc.add_stage(legacy_id, **dlg.values())
        except Exception as exc:
            QMessageBox.critical(self, "Stage", f"Enregistrement impossible :\n{exc}")
            return
        self._refresh_dossier_tab()

    def _on_edit_stage(self) -> None:
        row = self._tbl_stages.currentRow()
        if row < 0 or row >= len(self._stage_event_rows):
            QMessageBox.information(self, "Stage", "Sélectionnez un stage.")
            return
        from sme_gend.ui.agent_stage_edit_dialog import AgentStageEditDialog

        existing = self._pick_group_source(self._stage_groups[row])
        if existing is None:
            return
        dlg = AgentStageEditDialog(self, stage=existing)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        legacy_id = str(self._row.satellite_join_key() or "").strip()
        if existing.id is None:
            QMessageBox.warning(self, "Stage", "Identifiant du stage introuvable.")
            return
        try:
            self._events_svc.update_stage(legacy_id, existing.id, expected=existing, **dlg.values())
        except Exception as exc:
            QMessageBox.critical(self, "Stage", f"Modification impossible :\n{exc}")
            return
        self._refresh_dossier_tab()

    def _on_delete_stage(self) -> None:
        row = self._tbl_stages.currentRow()
        if row < 0 or row >= len(self._stage_event_rows):
            QMessageBox.information(self, "Stage", "Sélectionnez un stage.")
            return
        existing = self._pick_group_source(self._stage_groups[row])
        if existing is None:
            return
        if existing.id is None:
            QMessageBox.warning(self, "Stage", "Identifiant du stage introuvable.")
            return
        if not confirm_yes_no(
            self,
            "Supprimer",
            f"Supprimer le stage « {existing.nature or existing.type_stage} » ?",
            default_no=True,
        ):
            return
        legacy_id = str(self._row.satellite_join_key() or "").strip()
        try:
            self._events_svc.delete_stage(legacy_id, existing.id, expected=existing)
        except Exception as exc:
            QMessageBox.critical(self, "Stage", f"Suppression impossible :\n{exc}")
            return
        self._refresh_dossier_tab()

    def _reload_row_from_service(self) -> None:
        fresh = self._service.get_by_id(self._row.id)
        if fresh:
            self._row = fresh

    def _after_carriere_dossier_mutation(self) -> None:
        """Recharge la fiche carrière projetée puis rafraîchit dossier + synthèse."""
        self._liste_generale_changed = True
        self._reload_row_from_service()
        self._refresh_header_and_media()
        self._refresh_dossier_tab()
        self._refresh_synthesis()

    def _on_add_diplome(self) -> None:
        from sme_gend.ui.diplome_edit_dialog import DiplomeEditDialog

        dlg = DiplomeEditDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self._dossier_svc.add_diplome(self._row, **dlg.values())
        except Exception as e:
            QMessageBox.critical(self, "Diplôme", str(e))
            return
        self._after_carriere_dossier_mutation()

    def _on_edit_diplome(self) -> None:
        row = self._tbl_diplomes.currentRow()
        if row < 0 or row >= len(self._diplome_rows):
            QMessageBox.information(self, "Diplôme", "Sélectionnez un diplôme.")
            return
        from sme_gend.ui.diplome_edit_dialog import DiplomeEditDialog

        existing = self._pick_group_source(self._diploma_groups[row])
        if existing is None:
            return
        dlg = DiplomeEditDialog(self, existing=existing)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self._dossier_svc.update_diplome(self._row, existing.id, expected=existing, **dlg.values())
        except Exception as e:
            QMessageBox.critical(self, "Diplôme", str(e))
            return
        self._after_carriere_dossier_mutation()

    def _on_delete_diplome(self) -> None:
        row = self._tbl_diplomes.currentRow()
        if row < 0 or row >= len(self._diplome_rows):
            QMessageBox.information(self, "Diplôme", "Sélectionnez un diplôme.")
            return
        d = self._pick_group_source(self._diploma_groups[row])
        if d is None:
            return
        if not confirm_yes_no(
            self,
            "Supprimer",
            f"Retirer le diplôme « {d.libelle} » de l'historique du dossier ?\n\nCe retrait peut modifier les situations mensuelles lors de leur resynchronisation. Ce n'est pas un simple effacement de la liste du mois. La version retirée restera consultable dans le journal d'audit.",
            default_no=True,
        ):
            return
        try:
            self._dossier_svc.delete_diplome(self._row, d.id, expected=d)
        except Exception as e:
            QMessageBox.critical(self, "Diplôme", str(e))
            return
        self._after_carriere_dossier_mutation()

    def _after_distinction_mutation(self) -> None:
        self._refresh_distinctions_tab()
        self._refresh_profil_tab()

    def _distinction_ref_lists(self):
        self._ref.ensure_distinctions_initialized()
        return (
            self._ref.medailles(actif_only=True),
            self._ref.decorations(actif_only=True),
        )

    def _on_add_distinction(self) -> None:
        from sme_gend.ui.agent_distinction_edit_dialog import AgentDistinctionEditDialog

        meds, decs = self._distinction_ref_lists()
        if not meds and not decs:
            QMessageBox.warning(
                self,
                "Distinctions",
                "Référentiel médailles / décorations indisponible.\n\n"
                "Vérifiez la connexion MySQL, puis ouvrez "
                "Administration et outils → Médailles & décorations pour créer les entrées, "
                "ou utilisez Importer / Modèle Excel sur cette page.",
            )
            return
        dlg = AgentDistinctionEditDialog(self, medailles=meds, decorations=decs)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self._dossier_svc.add_distinction(self._row, **dlg.values())
        except Exception as e:
            QMessageBox.critical(self, "Distinction", str(e))
            return
        self._after_distinction_mutation()

    def _on_edit_distinction(self) -> None:
        row = self._tbl_distinctions.currentRow()
        if row < 0 or row >= len(self._distinction_rows):
            QMessageBox.information(self, "Distinction", "Sélectionnez une ligne.")
            return
        from sme_gend.ui.agent_distinction_edit_dialog import AgentDistinctionEditDialog

        existing = self._distinction_rows[row]
        meds, decs = self._distinction_ref_lists()
        dlg = AgentDistinctionEditDialog(
            self, medailles=meds, decorations=decs, existing=existing
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self._dossier_svc.update_distinction(self._row, existing.id, expected=existing, **dlg.values())
        except Exception as e:
            QMessageBox.critical(self, "Distinction", str(e))
            return
        self._after_distinction_mutation()

    def _on_delete_distinction(self) -> None:
        row = self._tbl_distinctions.currentRow()
        if row < 0 or row >= len(self._distinction_rows):
            QMessageBox.information(self, "Distinction", "Sélectionnez une ligne.")
            return
        d = self._distinction_rows[row]
        if not confirm_yes_no(
            self,
            "Supprimer",
            f"Supprimer la distinction « {d.libelle} » ?",
            default_no=True,
        ):
            return
        try:
            self._dossier_svc.delete_distinction(self._row, d.id, expected=d)
        except Exception as e:
            QMessageBox.critical(self, "Distinction", str(e))
            return
        self._after_distinction_mutation()

    def _message_fonction_occupee(
        self,
        *,
        structure: str | None,
        fonction: str | None,
        date_affectation,
        subdivision: str | None = None,
        ignore_affectation_id: int | None = None,
    ) -> str | None:
        from sme_gend.services.fonction_occupancy import (
            affectation_devient_poste_actuel,
            occupancy_message,
            other_occupants,
        )
        from sme_gend.services.liste_generale_cache import cached_liste_rows

        existing = [
            (a.date_affectation, a.id)
            for a in getattr(self, "_all_affectation_rows", self._affectation_rows)
            if ignore_affectation_id is None or a.id != ignore_affectation_id
        ]
        if not affectation_devient_poste_actuel(
            new_date=date_affectation, existing=existing
        ):
            return None
        found = other_occupants(
            cached_liste_rows(self._service.settings),
            structure=structure,
            fonction=fonction,
            subdivision=subdivision,
            exclude_row_id=self._row.id,
        )
        if not found:
            return None
        return occupancy_message(found, fonction or "")

    def _message_repli_fonction_occupee(self, deleted_id: int) -> str | None:
        from sme_gend.services.fonction_occupancy import (
            occupancy_message,
            other_occupants,
            same_fonction_poste,
        )
        from sme_gend.services.liste_generale_cache import cached_liste_rows

        remaining = [a for a in getattr(self, "_all_affectation_rows", self._affectation_rows) if a.id != deleted_id]
        if not remaining:
            return None
        best = max(
            remaining,
            key=lambda a: (
                a.date_affectation.toordinal() if a.date_affectation else 0,
                a.id or 0,
            ),
        )
        if same_fonction_poste(
            self._row.structure,
            self._row.fonction,
            best.structure_code,
            best.fonction,
            self._row.subdivision_arme,
            best.subdivision_arme,
        ):
            return None
        found = other_occupants(
            cached_liste_rows(self._service.settings),
            structure=best.structure_code,
            fonction=best.fonction,
            subdivision=best.subdivision_arme,
            exclude_row_id=self._row.id,
        )
        if not found:
            return None
        return occupancy_message(found, best.fonction or "")

    def _on_add_affectation(self) -> None:
        from sme_gend.config.sme_context import load_sme_context
        from sme_gend.ui.affectation_edit_dialog import AffectationEditDialog

        dlg = AffectationEditDialog(
            self,
            referentiel=self._ref,
            default_structure=self._row.structure,
            default_fonction=self._row.fonction,
            default_subdivision=self._row.subdivision_arme,
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        vals = dlg.values()
        msg = self._message_fonction_occupee(
            structure=vals["structure_code"],
            fonction=vals.get("fonction"),
            subdivision=vals.get("subdivision_arme"),
            date_affectation=vals["date_affectation"],
        )
        if msg:
            QMessageBox.warning(self, "Fonction", msg)
            return
        ctx = load_sme_context()
        try:
            self._dossier_svc.add_affectation(
                self._row,
                sme_periode_mois=vals["date_affectation"].strftime("%Y-%m"),
                **vals,
            )
        except Exception as e:
            QMessageBox.critical(self, "Affectation", str(e))
            return
        self._after_carriere_dossier_mutation()

    def _on_edit_affectation(self) -> None:
        row = self._tbl_affectations.currentRow()
        if row < 0 or row >= len(self._affectation_rows):
            QMessageBox.information(self, "Affectation", "Sélectionnez une affectation.")
            return
        from sme_gend.ui.affectation_edit_dialog import AffectationEditDialog

        existing = self._pick_group_source(self._affectation_groups[row])
        if existing is None:
            return
        dlg = AffectationEditDialog(self, referentiel=self._ref, existing=existing)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        vals = dlg.values()
        msg = self._message_fonction_occupee(
            structure=vals["structure_code"],
            fonction=vals.get("fonction"),
            subdivision=vals.get("subdivision_arme"),
            date_affectation=vals["date_affectation"],
            ignore_affectation_id=existing.id,
        )
        if msg:
            QMessageBox.warning(self, "Fonction", msg)
            return
        try:
            self._dossier_svc.update_affectation(self._row, existing.id, expected=existing, **vals)
        except Exception as e:
            QMessageBox.critical(self, "Affectation", str(e))
            return
        self._after_carriere_dossier_mutation()

    def _on_delete_affectation(self) -> None:
        row = self._tbl_affectations.currentRow()
        if row < 0 or row >= len(self._affectation_rows):
            QMessageBox.information(self, "Affectation", "Sélectionnez une affectation.")
            return
        a = self._pick_group_source(self._affectation_groups[row])
        if a is None:
            return
        msg = self._message_repli_fonction_occupee(a.id)
        if msg:
            QMessageBox.warning(self, "Fonction", msg)
            return
        if not confirm_yes_no(
            self,
            "Supprimer",
            f"Supprimer l'affectation « {a.structure_code} » du {fr_date(a.date_affectation)} ?",
            default_no=True,
        ):
            return
        try:
            self._dossier_svc.delete_affectation(self._row, a.id, expected=a)
        except Exception as e:
            QMessageBox.critical(self, "Affectation", str(e))
            return
        self._after_carriere_dossier_mutation()

    def _on_add_passation(self) -> None:
        from sme_gend.ui.passation_grade_edit_dialog import PassationGradeEditDialog

        dlg = PassationGradeEditDialog(self, self._ref)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self._dossier_svc.add_passation(self._row, **dlg.values())
        except Exception as e:
            QMessageBox.critical(self, "Passation", str(e))
            return
        self._after_carriere_dossier_mutation()

    def _on_edit_passation(self) -> None:
        row = self._tbl_passations.currentRow()
        if row < 0 or row >= len(self._passation_rows):
            QMessageBox.information(self, "Passation", "Sélectionnez une passation.")
            return
        from sme_gend.ui.passation_grade_edit_dialog import PassationGradeEditDialog

        existing = self._passation_rows[row]
        dlg = PassationGradeEditDialog(self, self._ref, existing=existing)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self._dossier_svc.update_passation(self._row, existing.id, expected=existing, **dlg.values())
        except Exception as e:
            QMessageBox.critical(self, "Passation", str(e))
            return
        self._after_carriere_dossier_mutation()

    def _on_delete_passation(self) -> None:
        row = self._tbl_passations.currentRow()
        if row < 0 or row >= len(self._passation_rows):
            QMessageBox.information(self, "Passation", "Sélectionnez une passation.")
            return
        p = self._passation_rows[row]
        if not confirm_yes_no(
            self,
            "Supprimer",
            f"Supprimer la passation « {p.grade_libelle} » ?",
            default_no=True,
        ):
            return
        try:
            self._dossier_svc.delete_passation(self._row, p.id, expected=p)
        except Exception as e:
            QMessageBox.critical(self, "Passation", str(e))
            return
        self._after_carriere_dossier_mutation()

    def _show_read_error(self, title, table):
        import logging
        logging.getLogger(__name__).exception("Lecture du dossier impossible : %s", title)
        if table is getattr(self, '_tbl_diplomes', None):
            self._diploma_groups = []
        if table is getattr(self, '_tbl_affectations', None):
            self._affectation_groups = []
            self._all_affectation_rows = []
        table.clearSpans()
        table.clearContents()
        table.setRowCount(1)
        item = QTableWidgetItem("Données indisponibles — utilisez Réessayer. Ce résultat ne signifie pas qu’aucune donnée n’existe.")
        item.setFlags(Qt.ItemFlag.ItemIsEnabled)
        table.setItem(0, 0, item)
        table.setSpan(0, 0, 1, table.columnCount())
        self._read_errors[title] = True
        self._update_read_errors()

    def _clear_read_error(self, title):
        getattr(self, '_read_errors', {}).pop(title, None)
        self._update_read_errors()

    def _update_read_errors(self):
        if not hasattr(self, '_read_error_label'):
            return
        names = ', '.join(self._read_errors)
        self._read_error_label.setText("Dossier incomplet : lecture impossible pour " + names + ". Vérifiez la connexion puis réessayez.")
        self._read_error_label.setVisible(bool(names))
        self._read_retry.setVisible(bool(names))

    def _retry_reads(self):
        self._refresh_diplomes_tab()
        self._refresh_distinctions_tab()
        self._refresh_passations_tab()
        self._refresh_affectations_tab()

    def _refresh_diplomes_tab(self) -> None:
        try:
            self._diplome_rows = self._dossier_svc.list_diplomes(self._row)
        except Exception:
            self._diplome_rows = []
            self._show_read_error('Diplômes', self._tbl_diplomes)
            return
        self._tbl_diplomes.clearSpans()
        self._clear_read_error('Diplômes')
        from sme_gend.services.dossier_grouping import group_diplomas, DossierGroup
        raw = self._diplome_rows
        self._diploma_groups = (group_diplomas(raw, getattr(self, '_career_snapshots', []))
                                if self._group_history.isChecked() else [DossierGroup(r, [r]) for r in raw])
        self._diplome_rows = [g.row for g in self._diploma_groups]
        self._tbl_diplomes.setRowCount(len(self._diplome_rows))
        for r, d in enumerate(self._diplome_rows):
            cat = "Militaire" if d.categorie == "MILITAIRE" else "Civil"
            vals = [
                cat,
                d.libelle,
                fr_date(d.date_obtention),
                d.lieu_obtention or "—",
                d.mention or "—",
                d.etablissement or "—",
                self._diploma_groups[r].month_label,
                dossier_entry_provenance(d.obs),
            ]
            for c, text in enumerate(vals):
                it = QTableWidgetItem(text)
                if c == len(vals) - 1:
                    it.setToolTip(dossier_entry_provenance_tooltip(d.obs))
                self._tbl_diplomes.setItem(r, c, it)

    def _refresh_distinctions_tab(self) -> None:
        from sme_gend.models.distinction_ref import DISTINCTION_KIND_MEDAILLE

        try:
            self._distinction_rows = self._dossier_svc.list_distinctions(self._row)
        except Exception:
            self._distinction_rows = []
            self._show_read_error('Distinctions', self._tbl_distinctions)
            return
        self._tbl_distinctions.clearSpans()
        self._clear_read_error('Distinctions')
        self._tbl_distinctions.setRowCount(len(self._distinction_rows))
        for r, d in enumerate(self._distinction_rows):
            kind_label = "Médaille" if d.kind == DISTINCTION_KIND_MEDAILLE else "Décoration / ordre"
            vals = [
                kind_label,
                d.libelle or "—",
                fr_date(d.date_attribution),
                fr_date(d.date_publication_journal),
                d.numero_arrete or "—",
                fr_date(d.date_arrete),
                (d.citation or "—")[:120],
                dossier_entry_provenance(d.source),
            ]
            for c, text in enumerate(vals):
                it = QTableWidgetItem(text)
                if c == len(vals) - 1:
                    it.setToolTip(dossier_entry_provenance_tooltip(d.source))
                self._tbl_distinctions.setItem(r, c, it)

    def _refresh_passations_tab(self) -> None:
        try:
            self._passation_rows = self._dossier_svc.list_passations(self._row)
        except Exception:
            self._passation_rows = []
            self._show_read_error('Passations', self._tbl_passations)
            return
        self._tbl_passations.clearSpans()
        self._clear_read_error('Passations')
        self._tbl_passations.setRowCount(len(self._passation_rows))
        for r, p in enumerate(self._passation_rows):
            vals = [
                p.grade_libelle,
                fr_date(p.date_passation),
                p.ndecision or "—",
                p.obs or "—",
                dossier_entry_provenance(p.obs),
            ]
            for c, text in enumerate(vals):
                it = QTableWidgetItem(text)
                if c == len(vals) - 1:
                    it.setToolTip(dossier_entry_provenance_tooltip(p.obs))
                self._tbl_passations.setItem(r, c, it)

    def _refresh_affectations_tab(self) -> None:
        try:
            self._affectation_rows = self._dossier_svc.list_affectations(self._row)
            structures = self._ref.structures()
        except Exception:
            self._affectation_rows = []
            self._show_read_error('Affectations', self._tbl_affectations)
            return
        self._tbl_affectations.clearSpans()
        self._clear_read_error('Affectations')
        from sme_gend.services.dossier_grouping import group_affectations, DossierGroup
        raw = self._affectation_rows
        self._all_affectation_rows = raw
        self._affectation_groups = (group_affectations(raw, getattr(self, '_career_snapshots', []))
                                    if self._group_history.isChecked() else [DossierGroup(r, [r]) for r in raw])
        self._affectation_rows = [g.row for g in self._affectation_groups]
        self._tbl_affectations.setRowCount(len(self._affectation_rows))
        for r, a in enumerate(self._affectation_rows):
            from sme_gend.services.structure_label_format import format_structure_display

            struct_label = format_structure_display(
                a.structure_code,
                structures,
            )
            vals = [
                fr_date(a.date_affectation) + (" (constat)" if (a.obs or '').startswith('Import archive ') else ""),
                struct_label or a.structure_code or "—",
                a.fonction or "—",
                a.subdivision_arme or "—",
                self._affectation_groups[r].month_label if self._affectation_groups[r].months else a.sme_periode_mois or "—",
                dossier_entry_provenance(a.obs),
            ]
            for c, text in enumerate(vals):
                it = QTableWidgetItem(text)
                if c == len(vals) - 1:
                    it.setToolTip(dossier_entry_provenance_tooltip(a.obs))
                self._tbl_affectations.setItem(r, c, it)

    def _refresh_dossier_tab(self) -> None:
        self._initial_tables_pending = None
        from sme_gend.services.dossier_grouping import function_history
        snapshot = getattr(self, '_readonly_snapshot', None)
        if snapshot is not None:
            self._career_snapshots = snapshot.get('career_snapshots', [])
        else:
            from sme_gend.repositories.mysql_agent_repository import MySQLAgentRepository
            leg = str(self._row.satellite_join_key() or '').strip()
            self._career_snapshots = MySQLAgentRepository(self._settings).list_carriere_history_for_agent(leg)
        self._fill_events_table(self._tbl_functions, function_history(self._career_snapshots),
                                empty_msg="Aucune situation connue.", date_cols=())
        self._refresh_diplomes_tab()
        self._refresh_distinctions_tab()
        self._refresh_passations_tab()
        self._refresh_affectations_tab()
        leg = str(self._row.satellite_join_key() or "").strip()
        svc = self._events_svc
        self._mission_event_rows = list(svc.missions(leg))
        self._fill_events_table(
            self._tbl_missions,
            [
                (
                    m.date_debut or "—",
                    m.date_fin or "—",
                    m.nature or "—",
                    m.lieu or "—",
                    m.reference_dossier or "—",
                    m.observation or "—",
                    m.periode_sme or "—",
                )
                for m in self._mission_event_rows
            ],
            empty_msg="Aucune mission — importée depuis les archives Access.",
            date_cols=(0, 1),
        )
        from sme_gend.services.dossier_grouping import group_stages, DossierGroup
        raw_stages = list(svc.stages(leg))
        self._stage_groups = (group_stages(raw_stages) if self._group_history.isChecked()
                              else [DossierGroup(r, [r]) for r in raw_stages])
        self._stage_event_rows = [g.row for g in self._stage_groups]
        self._fill_events_table(
            self._tbl_stages,
            [
                (
                    {
                        "LOCAL": "Stage local",
                        "EXTERIEUR": "Stage extérieur",
                        "INTERNE": "Stage interne",
                    }.get((s.type_stage or "").upper(), s.type_stage or "—"),
                    s.date_debut or "—",
                    s.date_fin or "—",
                    s.nature or "—",
                    s.lieu or "—",
                    s.reference_dossier or "—",
                    s.session or '—',
                    s.observation or "—",
                    s.periode_sme or "—",
                )
                for s in self._stage_event_rows
            ],
            empty_msg="Aucun stage enregistré.",
            date_cols=(1, 2),
        )
        for table, groups in ((self._tbl_diplomes, self._diploma_groups),
                              (self._tbl_affectations, self._affectation_groups),
                              (self._tbl_stages, self._stage_groups)):
            for i, group in enumerate(groups):
                for c in range(table.columnCount()):
                    item = table.item(i, c)
                    if item:
                        item.setToolTip(group.detail + "\n" + (item.toolTip() or ''))
        self._malade_event_rows = list(svc.malades(leg))
        self._fill_events_table(
            self._tbl_malades,
            [
                (
                    m.date_debut or "—",
                    m.date_fin or "—",
                    m.pathologie or "—",
                    m.observation or "—",
                    m.periode_sme or "—",
                )
                for m in self._malade_event_rows
            ],
            empty_msg="Aucune période de maladie.",
            date_cols=(0, 1),
        )
        self._desertion_event_rows = list(svc.desertions(leg))
        self._fill_events_table(
            self._tbl_desertions,
            [
                (d.date_desertion or "—", d.reference_dossier or '—', d.observation or "—", d.periode_sme or "—")
                for d in self._desertion_event_rows
            ],
            empty_msg="Aucune désertion enregistrée.",
            date_cols=(0,),
        )
        self._conge_event_rows = list(svc.conges(leg))
        from sme_gend.services.position_admin_events import list_position_events

        self._fill_events_table(
            self._tbl_positions,
            [tuple(str(v) if v is not None else "—" for v in event)
             for event in (self._readonly_snapshot["positions"] if self._readonly_snapshot is not None
                           else list_position_events(self._service.settings, leg))],
            empty_msg="Aucune fiche de position administrative enregistrée.",
            date_cols=(1, 2),
        )
        self._fill_events_table(
            self._tbl_conges,
            [
                (
                    c.type_label or "—",
                    c.date_debut or "—",
                    c.date_fin or "—",
                    c.quota_jours or "—",
                    c.source_code or "—",
                    c.observation or "—",
                    c.periode_sme or "—",
                )
                for c in self._conge_event_rows
            ],
            empty_msg="Aucun congé — import ou saisie manuelle.",
            date_cols=(1, 2),
        )

    def _refresh_alerts_banner(self) -> None:
        if self._readonly_snapshot is not None and not self._readonly_snapshot.get('alerts_pending', False):
            self._apply_career_alerts(self._readonly_snapshot['alerts'])
            return
        from sme_gend.services.career_alerts_cache import dossier_alerts_for_agent
        from sme_gend.ui.bg_task import run_in_background
        serial = getattr(self, '_alerts_request', 0) + 1
        self._alerts_request = serial
        settings = self._settings
        legacy = str(self._row.satellite_join_key() or '')
        self._shown_career_alerts = []
        self._alerts_label.setText('Vérification des points du dossier…')
        self._alerts_label.show()
        def loaded(alerts):
            if self._alerts_request == serial:
                self._apply_career_alerts(alerts)
        def failed(_error):
            if self._alerts_request == serial:
                self._alerts_label.setText('Vérification des points du dossier indisponible ; actualisez après vérification de la connexion.')
        run_in_background(self, lambda: dossier_alerts_for_agent(settings, legacy), loaded, on_error=failed)

    def _apply_career_alerts(self, alerts):
        from html import escape
        self._shown_career_alerts = alerts
        warns = [a for a in alerts if a.severity == "warning"]
        if not alerts:
            self._alerts_label.hide()
            return
        lines = "<br/>".join(
            f"• <b>{escape(a.label)}</b> ({escape(a.mois or 'Actuel')}) — {escape(a.message)} "
            f"<br/><a href='alert:{index}' style='color:#1148ab;'>Voir les fiches et la marche à suivre</a>"
            for index, a in enumerate(alerts[:5])
        )
        extra = f"<br/><i>… et {len(alerts) - 5} autre(s)</i>" if len(alerts) > 5 else ""
        color = "#8a4200" if warns else "#273444"
        self._alerts_label.setText(
            f"<span style='color:{color}'><b>Points à vérifier ({len(alerts)})</b></span><br/>{lines}{extra}"
        )
        self._alerts_label.setTextFormat(Qt.TextFormat.RichText)
        self._alerts_label.show()

    def _on_career_alert_link(self, link):
        if not link.startswith("alert:"):
            return
        try:
            index = int(link.split(":", 1)[1])
        except ValueError:
            return
        alerts = getattr(self, "_shown_career_alerts", [])
        if not 0 <= index < len(alerts):
            return
        from sme_gend.ui.career_alert_details_dialog import show_career_alert_details
        result = show_career_alert_details(self, alerts[index], self._settings, self._service, self._ref)
        if result == QDialog.DialogCode.Accepted:
            self._liste_generale_changed = True
            self.accept()

    def _actualiser(self) -> None:
        from sme_gend.services.liste_generale_cache import invalidate_liste_cache
        invalidate_liste_cache(self._settings)
        self._refresh_all()
        self._liste_generale_changed = True

    def _on_correct_return(self) -> None:
        from sme_gend.services.auth_guard import session_has_admin_role, session_has_permission
        if not session_has_admin_role() or not session_has_permission('effectifs.history_correction'):
            QMessageBox.warning(self, 'Correction', 'La correction historique exige un administrateur autorisé.')
            return
        from sme_gend.services.agent_dossier_service import AgentDossierService
        from sme_gend.ui.activity_return_correction_dialog import run_activity_return_correction
        try:
            aid = AgentDossierService(self._settings).agent_id_for_row(self._row)
        except Exception as exc:
            QMessageBox.warning(self, 'Correction', str(exc))
            return
        if run_activity_return_correction(self, self._settings, aid):
            self._liste_generale_changed = True
            # A background-prepared read-only dossier must not keep showing its
            # old cached history after an explicitly authorized correction.
            if self._readonly_snapshot is not None:
                self._readonly_snapshot = None
                self._initial_tables_pending = None
                self._ref = ReferentielService(self._settings)
                self._events_svc = AgentDossierEventsService(self._settings)
                self._dossier_svc = AgentDossierService(self._settings)
            if self._historical_context is not None:
                try:
                    refreshed = self._service.dossier_row_for_legacy_id(self._row.satellite_join_key())
                    if refreshed is not None:
                        self._row, self._historical_context = refreshed
                except Exception as exc:
                    QMessageBox.warning(self, 'Correction enregistrée',
                        'Le retour a été corrigé, mais la fiche doit être rouverte pour relire sa situation.\n' + str(exc))
            self._refresh_all()

    def _refresh_all(self) -> None:
        self._reload_row_from_carriere()
        self._refresh_header_and_media()
        self._refresh_alerts_banner()
        self._refresh_synthesis()
        if not getattr(self, '_constructing_prepared', False):
            self._initial_tables_pending = None
            self._refresh_dossier_tab()
            self._refresh_profil_tab()
        self._sync_history_edit_controls()

    def _sync_history_edit_controls(self) -> None:
        from sme_gend.services.auth_guard import session_has_admin_role
        from sme_gend.services.auth_guard import session_has_permission

        can_hist = self._allow_edit and session_has_admin_role()
        if hasattr(self, '_btn_correct_return'):
            can_correct = session_has_admin_role() and session_has_permission('effectifs.history_correction')
            self._btn_correct_return.setVisible(can_correct)
            self._btn_correct_return.setEnabled(can_correct)
        hist_widgets = (
            self._btn_stage_add,
            self._btn_stage_edit,
            self._btn_stage_del,
            self._btn_mission_add,
            self._btn_mission_edit,
            self._btn_mission_del,
            self._btn_desertion_add,
            self._btn_desertion_edit,
            self._btn_desertion_del,
            self._btn_conge,
            self._btn_conge_add,
            self._btn_conge_edit,
            self._btn_conge_del,
            self._btn_passation,
            self._btn_dip_add,
            self._btn_dip_edit,
            self._btn_dip_del,
            self._btn_pas_add,
            self._btn_pas_edit,
            self._btn_pas_del,
            self._btn_aff_add,
            self._btn_aff_edit,
            self._btn_aff_del,
        )
        if not self._allow_edit:
            self._lbl_history_admin_hint.hide()
            for w in hist_widgets:
                w.hide()
            return
        if can_hist:
            self._lbl_history_admin_hint.hide()
        else:
            self._lbl_history_admin_hint.setText(
                "La modification de l’historique (carrière, stages, missions, congés et "
                "désertions) est réservée aux comptes administrateur. Les tableaux restent consultables."
            )
            self._lbl_history_admin_hint.show()
        admin_tip = (
            "Réservé aux administrateurs — modification de l’historique du dossier."
            if not can_hist
            else ""
        )
        for w in hist_widgets:
            w.setVisible(True)
            w.setEnabled(can_hist)
            w.setToolTip(admin_tip)
        from sme_gend.services.sensitive_data_policy import session_may_edit_career_fields
        can_add = self._allow_edit and session_may_edit_career_fields()
        for button in (self._btn_dip_add, self._btn_stage_add, self._btn_mission_add,
                       self._btn_desertion_add, self._btn_conge, self._btn_conge_add):
            button.setEnabled(can_add)
            button.setToolTip("" if can_add else "Droits d'édition SME ou du personnel requis.")

    def _reload_row_from_carriere(self) -> None:
        """Relit la fiche du mois : grade et diplôme suivent le dossier.

        Ne repeint rien — l'appelant enchaîne sur ``_refresh_synthesis()``.
        """
        if self._historical_context is not None or getattr(self, "_readonly_snapshot", None) is not None:
            return
        fresh = self._service.get_by_id(self._row.id)
        if fresh:
            self._row = fresh

    def _build_dossier_sections(self) -> None:
        pas_bar = QHBoxLayout()
        pas_bar.addWidget(self._btn_pas_add)
        pas_bar.addWidget(self._btn_pas_edit)
        pas_bar.addWidget(self._btn_pas_del)
        pas_bar.addStretch()
        dip_bar = QHBoxLayout()
        dip_bar.addWidget(self._btn_dip_add)
        dip_bar.addWidget(self._btn_dip_edit)
        dip_bar.addWidget(self._btn_dip_del)
        dip_bar.addStretch()
        dist_bar = QHBoxLayout()
        dist_bar.addWidget(self._btn_dist_add)
        dist_bar.addWidget(self._btn_dist_edit)
        dist_bar.addWidget(self._btn_dist_del)
        dist_bar.addStretch()
        aff_bar = QHBoxLayout()
        aff_bar.addWidget(self._btn_aff_add)
        aff_bar.addWidget(self._btn_aff_edit)
        aff_bar.addWidget(self._btn_aff_del)
        aff_bar.addStretch()

        self._dossier_lay.addWidget(
            self._make_dossier_section(
                "Carrière — passations de grade",
                "Chaque changement de grade détecté à l’import est enregistré avec la "
                "<b>DateGrade</b> (ou le 1er jour du mois SME si absente).",
                self._tbl_passations,
                pas_bar,
            )
        )
        self._dossier_lay.addWidget(
            self._make_dossier_section(
                "Carrière — affectations (structure)",
                "Une ligne par période continue dans une structure. Les dates importées sont des "
                "dates de constat au premier jour du mois, à confirmer par une pièce administrative. "
                "La première ligne importée est une situation initiale constatée. "
                "Double-cliquez pour consulter les fiches sources.",
                self._tbl_affectations,
                aff_bar,
            )
        )
        self._dossier_lay.addWidget(self._make_dossier_section(
            "Carrière — fonctions et subdivisions",
            "Situations constatées dans les archives, distinctes des changements de structure.",
            self._tbl_functions, QHBoxLayout()))
        self._dossier_lay.addWidget(
            self._make_dossier_section(
                "Formation — diplômes",
                "Un diplôme par obtention. Les mois attestés sont visibles dans l’infobulle. "
                "Une date inconnue est complétée à l’import si la correspondance est unique. "
                "Double-cliquez pour consulter les fiches sources.",
                self._tbl_diplomes,
                dip_bar,
            )
        )
        self._dossier_lay.addWidget(
            self._make_dossier_section(
                "Distinctions — médailles et décorations / ordres",
                "Attributions officielles saisies dans le dossier (hors archives SME). "
                "Configurez les libellés dans <b>Médailles & décorations</b> (menu Administration et outils).",
                self._tbl_distinctions,
                dist_bar,
            )
        )
        mal_bar = QHBoxLayout()
        mal_bar.addStretch()
        mal_bar.addWidget(self._btn_mal_add)
        mal_bar.addWidget(self._btn_mal_edit)
        mal_bar.addWidget(self._btn_mal_del)

        for title, hint, tbl, bar in (
            (
                "Activités — missions",
                "Les nouvelles missions sont rattachées au mois SME actif. Les modifications "
                "d’historique nécessitent un rôle autorisé.",
                self._tbl_missions,
                self._mission_action_bar(),
            ),
            (
                "Activités — stages",
                "Les fiches mensuelles contiguës d’un même stage sont regroupées. Les bornes "
                "indiquent la période observée ; les dates réelles restent à confirmer. "
                "Double-cliquez pour consulter les mois sources.",
                self._tbl_stages,
                self._stage_action_bar(),
            ),
            (
                "Absences — maladies",
                "Ajout, modification et suppression possibles ci-dessous. Les fiches saisies "
                "ici sont conservées à chaque réimport ; les fiches reprises de l'archive "
                "d'un mois sont recréées lors du réimport de ce mois.",
                self._tbl_malades,
                mal_bar,
            ),
            (
                "Absences — congés",
                "Les congés sont saisis pour le mois SME actif. Modification et suppression "
                "d’historique réservées aux rôles autorisés.",
                self._tbl_conges,
                self._conge_action_bar(),
            ),
            (
                "Absences — désertions",
                "Les nouvelles désertions sont rattachées au mois SME actif. "
                "L’historique se modifie avec un rôle autorisé.",
                self._tbl_desertions,
                self._desertion_action_bar(),
            ),
            (
                "Positions administratives — détachement, mise en route et discipline",
                "Fiches complémentaires enregistrées depuis Édition SME.",
                self._tbl_positions,
                QHBoxLayout(),
            ),
        ):
            self._dossier_lay.addWidget(self._make_dossier_section(title, hint, tbl, bar))
        self._dossier_lay.addStretch()

    def _stage_action_bar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        bar.addWidget(self._btn_stage_add)
        bar.addWidget(self._btn_stage_edit)
        bar.addWidget(self._btn_stage_del)
        bar.addStretch()
        return bar

    def _mission_action_bar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        for button in (self._btn_mission_add, self._btn_mission_edit, self._btn_mission_del):
            bar.addWidget(button)
        bar.addStretch()
        return bar

    def _desertion_action_bar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        for button in (
            self._btn_desertion_add, self._btn_desertion_edit, self._btn_desertion_del
        ):
            bar.addWidget(button)
        bar.addStretch()
        return bar

    def _conge_action_bar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        bar.addWidget(self._btn_conge_add)
        bar.addWidget(self._btn_conge_edit)
        bar.addWidget(self._btn_conge_del)
        bar.addStretch()
        return bar

    def _make_dossier_section(
        self,
        title: str,
        hint: str,
        table: QTableWidget,
        toolbar: QHBoxLayout | None = None,
    ) -> QFrame:
        card = QFrame()
        card.setObjectName("DossierSectionCard")
        v = QVBoxLayout(card)
        v.setSpacing(10)
        v.setContentsMargins(16, 14, 16, 16)
        head = QLabel(f"<b>{title}</b>")
        head.setObjectName("DossierSectionHead")
        head.setTextFormat(Qt.TextFormat.RichText)
        sub = QLabel(hint)
        sub.setWordWrap(True)
        sub.setObjectName("PersonnelDossierTabHint")
        v.addWidget(head)
        v.addWidget(sub)
        if toolbar is not None:
            v.addLayout(toolbar)
        v.addWidget(table)
        return card

    @staticmethod
    def _make_events_table(headers: list[str]) -> QTableWidget:
        t = QTableWidget()
        t.setColumnCount(len(headers))
        t.setHorizontalHeaderLabels(headers)
        t.horizontalHeader().setStretchLastSection(True)
        t.setAlternatingRowColors(True)
        t.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        # Sélection autorisée sans édition : permet de copier une ligne (Ctrl+C).
        t.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        t.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        t.setToolTip("Sélectionnez une ligne pour afficher ses actions. Dates en jj/mm/aaaa.")
        return t

    @staticmethod
    def _wrap_table(table: QTableWidget) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(table)
        return w

    def _fill_events_table(
        self,
        table: QTableWidget,
        rows: list[tuple[str, ...]],
        *,
        empty_msg: str,
        date_cols: tuple[int, ...] = (),
    ) -> None:
        table.clearSpans()
        if not rows:
            table.setRowCount(1)
            table.setItem(0, 0, QTableWidgetItem(empty_msg))
            table.setSpan(0, 0, 1, table.columnCount())
            return
        table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, text in enumerate(row):
                value = fr_date(text) if c in date_cols else (text or "—")
                table.setItem(r, c, QTableWidgetItem(value))
        table.resizeColumnsToContents()

    def _refresh_header_and_media(self) -> None:
        r = self._row
        snapshot = getattr(self, "_readonly_snapshot", None)
        from sme_gend.services.agent_identity_alias import identity_photo_path
        nom = (r.noms or "—").strip() or "—"
        self._title.setText(nom)
        mat = " · ".join(
            x
            for x in (
                f"Militaire {r.matricule_militaire}" if r.matricule_militaire else "",
                f"Solde {format_matricule_solde_display(r.matricule_solde, empty='')}"
                if not matricule_solde_is_empty(r.matricule_solde)
                else "",
            )
            if x
        )
        sub = " · ".join(
            x
            for x in (
                (r.grade or "").strip() or None,
                (r.structure or "").strip() or None,
                (r.fonction or "").strip() or None,
                mat or None,
                f"Fiche agent : {r.satellite_join_key() or r.id}",
            )
            if x
        )
        self._subtitle.setText(sub or "—")

        self._set_image_label(
            self._photo,
            snapshot["media"]["photo"] if snapshot is not None else identity_photo_path(self._settings, self._media_person_key()),
            self.style().standardIcon(
                getattr(QStyle.StandardPixmap, "SP_UserIcon", QStyle.StandardPixmap.SP_FileDialogContentsView)
            ).pixmap(96, 96),
        )
        self._set_image_label(
            self._grade_pix,
            snapshot["media"]["grade"] if snapshot is not None else grade_insignia_path(r.grade, self._ref.grades()),
            self.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxInformation).pixmap(
                64, 64
            ),
            max_w=180,
            max_h=120,
        )

        root = snapshot["media"]["root"] if snapshot is not None else personnel_media_root()
        key = self._media_person_key()
        self._media_hint.setText(
            f"<b>Médias locaux</b> :<br/>"
            f"• Photo agent : bouton ci-dessus → "
            f"<code>{root / 'photos' / f'{key}.jpg'}</code><br/>"
            f"• Insigne de grade : configuré une fois dans "
            f"<b>Référentiel → Grades → Insigne (image)</b> "
            f"(<code>{root / 'grades'}</code>)"
        )

    def _set_image_label(
        self,
        lab: QLabel,
        path: Path | None,
        fallback: QPixmap,
        *,
        max_w: int = 200,
        max_h: int = 260,
    ) -> None:
        if path is not None and path.is_file():
            pix = QPixmap(str(path))
            if not pix.isNull():
                lab.setPixmap(
                    pix.scaled(
                        max_w,
                        max_h,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )
                return
        lab.setPixmap(
            fallback.scaled(
                min(max_w, fallback.width()),
                min(max_h, fallback.height()),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def _clear_layout(self, lay: QVBoxLayout) -> None:
        while lay.count():
            item = lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()
                w.deleteLater()

    def _make_section_card(
        self,
        title: str,
        fields: list[tuple[str, str]],
        *,
        alert_field: str | None = None,
    ) -> QFrame:
        """Crée une carte de section style maquette web (titre + grille label/valeur)."""
        card = QFrame()
        card.setObjectName("DossierSectionCard")
        v = QVBoxLayout(card)
        v.setSpacing(10)
        v.setContentsMargins(16, 14, 16, 16)

        title_lbl = QLabel(title)
        title_lbl.setObjectName("DossierSectionTitle")
        v.addWidget(title_lbl)

        sep = QFrame()
        sep.setObjectName("DossierSectionSep")
        sep.setFrameShape(QFrame.Shape.HLine)
        v.addWidget(sep)

        COLS = 3
        grid = QGridLayout()
        grid.setVerticalSpacing(4)
        grid.setHorizontalSpacing(24)
        for i, (lbl_text, val_text) in enumerate(fields):
            row_i = i // COLS
            col_i = i % COLS
            lbl = QLabel(lbl_text)
            lbl.setObjectName("DossierFieldLabel")
            val = QLabel(val_text if val_text else "—")
            val.setWordWrap(True)
            val.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            if alert_field and lbl_text == alert_field:
                val.setObjectName("DossierFieldValueAlert")
            else:
                val.setObjectName("DossierFieldValue")
            grid.addWidget(lbl, row_i * 2, col_i)
            grid.addWidget(val, row_i * 2 + 1, col_i)
        for c in range(COLS):
            grid.setColumnStretch(c, 1)
        v.addLayout(grid)
        return card

    @staticmethod
    def _sexe_label(row: ListeGeneraleRow) -> str:
        from sme_gend.models.personnel_sexe import sexe_display_long

        return sexe_display_long(row.sexe)

    def _refresh_synthesis(self) -> None:
        self._clear_layout(self._synth_lay)
        r = self._row

        # Bandeau alerte pour positions administratives particulières
        padmin = (r.position_admin or "").strip()
        _alert_words = {"déserteur", "insoumis", "absent", "décédé", "disparu"}
        is_alert = any(w in padmin.lower() for w in _alert_words)
        if is_alert:
            self._alert_banner.setText(
                f"  ▲  Position administrative : {padmin}"
                f" — Cette situation requiert une attention particulière."
            )
        else:
            self._alert_banner.setText("")
        self._alert_banner.setVisible(is_alert)

        def _v(attr: str) -> str:
            return _fmt_row_field(r, attr)

        def _grade_label() -> str:
            code = (r.grade or "").strip()
            if not code:
                return "—"
            for g in self._ref.grades():
                if g.code == code:
                    return f"{g.libelle} ({g.code})" if g.libelle else code
            return code

        def _structure_label() -> str:
            from sme_gend.services.structure_label_format import format_structure_display

            return format_structure_display(r.structure, self._ref.structures()) or "—"

        from sme_gend.services.personnel_situation_context import (
            build_situation_en_cours,
            section_title,
        )

        snapshot = getattr(self, "_readonly_snapshot", None)
        situation = snapshot["situation"] if snapshot is not None else build_situation_en_cours(r, self._service.settings)
        if situation is not None:
            self._synth_lay.addWidget(
                self._make_section_card(section_title(), situation.fields)
            )

        # NOM / PRÉNOM : le champ noms contient tout → 1er mot = nom de famille, reste = prénoms
        noms_full = (r.noms or "").strip()
        parts = noms_full.split(None, 1)
        nom_famille = parts[0] if parts else "—"
        prenoms = parts[1] if len(parts) > 1 else "—"

        # Section IDENTITÉ
        self._synth_lay.addWidget(self._make_section_card("IDENTITÉ", [
            ("NOM", nom_famille),
            ("PRÉNOM", prenoms),
            ("DATE DE NAISSANCE", _v("date_naissance")),
            ("LIEU DE NAISSANCE", _v("lieu_naissance")),
            ("SEXE", self._sexe_label(r)),
            ("TÉLÉPHONE", _v("telephone")),
            ("ADRESSE DE RÉSIDENCE", _v("adresse_residence")),
        ]))

        # Section SERVICE ET ADMINISTRATIF (3 × 3 = 9 champs, aligné sur la maquette web)
        self._synth_lay.addWidget(self._make_section_card(
            "SERVICE ET ADMINISTRATIF",
            [
                ("GRADE", _grade_label()),
                ("DATE NOMINATION GRADE", _v("date_grade")),
                ("DATE ENTRÉE EN SERVICE", _v("date_service")),
                ("N° DÉCISION D’ENGAGEMENT", _v("ndecision")),
                ("MATRICULE MILITAIRE", _v("matricule_militaire")),
                ("MATRICULE SOLDE", _v("matricule_solde")),
                ("POSITION ADMINISTRATIVE", _v("position_admin")),
                ("FONCTION", _v("fonction")),
                ("ORIGINE", "—"),  # champ absent du modèle actuel
            ],
            alert_field="POSITION ADMINISTRATIVE" if is_alert else None,
        ))

        # Section AFFECTATION
        self._synth_lay.addWidget(self._make_section_card("AFFECTATION", [
            ("STRUCTURE", _structure_label()),
            ("SUBDIVISION D’ARME", _v("subdivision_arme")),
        ]))

        # Section DIPLÔME (conditionnelle)
        dip, dip_date = _v("diplome"), _v("date_obtention")
        if dip != "—" or dip_date != "—":
            self._synth_lay.addWidget(self._make_section_card("DIPLÔME ET FORMATION", [
                ("DIPLÔME", dip),
                ("DATE D’OBTENTION", dip_date),
            ]))

        # Observations
        obs = _v("obs")
        if obs != "—":
            self._synth_lay.addWidget(self._make_section_card("OBSERVATIONS", [
                ("OBSERVATIONS", obs),
            ]))

        self._synth_lay.addStretch()

    def _refresh_profil_tab(self) -> None:
        self._initial_tables_pending = None
        leg = str(self._row.satellite_join_key() or self._row.id or "").strip()
        try:
            snapshot = getattr(self, "_readonly_snapshot", None)
            timeline = snapshot["timeline"] if snapshot is not None else (build_audit_timeline(leg, settings=self._settings) if leg else [])
        except RuntimeError:
            self._profil_table.clearSpans()
            self._profil_table.setRowCount(1)
            self._profil_table.setItem(0, 0, QTableWidgetItem("Historique central indisponible — vérifiez MySQL puis actualisez le dossier."))
            self._profil_table.setSpan(0, 0, 1, 6)
            return
        rows_data: list[tuple[str, str, str, str, str, str]] = []
        for e in timeline:
            merged: dict[str, dict] = {}
            merged.update(e.career_changes)
            merged.update(e.profile_changes)
            for field in sorted(merged.keys()):
                delta = merged[field]
                rows_data.append(
                    (
                        format_ts_display(e.ts_utc),
                        e.sme_mois_context or "—",
                        field_label(field),
                        fmt_val(delta.get("old")),
                        fmt_val(delta.get("new")),
                        e.edited_by or "non identifié",
                    )
                )
        rows_data.reverse()

        self._profil_table.clearSpans()
        if not rows_data:
            self._profil_table.setRowCount(1)
            self._profil_table.setItem(
                0,
                0,
                QTableWidgetItem(
                    "Aucune modification tracée — les changements saisis via cette application "
                    "(identité, grade, structure, etc.) apparaîtront ici."
                ),
            )
            self._profil_table.setSpan(0, 0, 1, 6)
            return

        self._profil_table.setRowCount(len(rows_data))
        for r, row in enumerate(rows_data):
            for c, text in enumerate(row):
                self._profil_table.setItem(r, c, QTableWidgetItem(text))
        self._profil_table.resizeColumnsToContents()

    @property
    def liste_generale_changed(self) -> bool:
        """True si la fiche liste générale a été modifiée depuis l'ouverture du dossier."""
        return self._liste_generale_changed

    def _on_edit(self) -> None:
        if not self._allow_edit:
            return
        fresh = self._service.get_by_id(self._row.id)
        if fresh is None:
            from PySide6.QtWidgets import QMessageBox

            QMessageBox.warning(
                self,
                "Fiche dossier",
                "Ligne introuvable en base. Fermez et actualisez la liste générale.",
            )
            return
        dlg = PersonnelEditDialog(self, fresh, self._ref, settings=self._service.settings)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        new_row = dlg.result_row()
        try:
            from sme_gend.services.personnel_form_service import save_personnel_form
            save_personnel_form(self._service, dlg, before=fresh)
            # Re-fetch après écriture (comme les autres handlers de ce fichier) :
            # update_personnel déclenche sync_dossier_after_personnel_save, qui peut
            # reprojeter grade/diplôme/affectation depuis le dossier maître — utiliser
            # new_row tel quel affichait des valeurs différentes de celles en base
            # tant que la fenêtre n'était pas refermée/rouverte.
            refreshed = self._service.get_by_id(self._row.id)
            if refreshed is not None:
                from sme_gend.services.grade_save_notice import grade_save_notice
                notice = grade_save_notice(new_row, refreshed)
                if notice:
                    from PySide6.QtWidgets import QMessageBox
                    QMessageBox.information(self, "Passation enregistrée", notice)
            self._row = refreshed if refreshed is not None else new_row
            self._liste_generale_changed = True
        except Exception as e:
            from PySide6.QtWidgets import QMessageBox

            QMessageBox.critical(self, "Enregistrement", str(e))
            return
        self._refresh_all()
