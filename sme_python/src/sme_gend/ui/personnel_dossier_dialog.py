"""Fiche dossier personnel — synthèse, parcours, traçabilité profil, médias (style maquette SIG)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QStyle,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.services.personnel_dossier_history import (
    AuditTimelineEntry,
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
)
from sme_gend.services.personnel_service import PersonnelService
from sme_gend.services.referentiel_service import ReferentielService
from sme_gend.ui.personnel_edit_dialog import PersonnelEditDialog


def _fmt_row_field(row: ListeGeneraleRow, attr: str) -> str:
    v = getattr(row, attr, None)
    if v is None:
        return "—"
    if hasattr(v, "strftime"):
        try:
            return v.strftime("%Y-%m-%d") if v else "—"
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
    ) -> None:
        super().__init__(parent)
        self.setObjectName("PersonnelDossierDialog")
        self.setWindowTitle("Fiche dossier — parcours & traçabilité")
        self.setModal(True)
        self.resize(1100, 760)
        self.setMinimumSize(960, 620)

        self._ref = referentiel
        self._service = service
        self._row = row

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

        btn_row = QHBoxLayout()
        self._btn_edit = QPushButton("Modifier la fiche…")
        self._btn_edit.setObjectName("PersonnelDossierPrimaryBtn")
        self._btn_edit.clicked.connect(self._on_edit)
        btn_row.addWidget(self._btn_edit)
        btn_row.addStretch()
        hl.addLayout(btn_row)

        root.addWidget(self._header)

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
        self._photo.setMinimumSize(200, 260)
        self._photo.setMaximumSize(220, 280)
        self._photo.setScaledContents(False)

        gr_title = QLabel("Insigne / grade")
        gr_title.setProperty("subheading", True)
        self._grade_pix = QLabel()
        self._grade_pix.setObjectName("PersonnelDossierGradePix")
        self._grade_pix.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._grade_pix.setMinimumSize(120, 120)
        self._grade_pix.setMaximumSize(200, 140)

        self._media_hint = QLabel()
        self._media_hint.setWordWrap(True)
        self._media_hint.setObjectName("PersonnelDossierHint")
        self._media_hint.setTextFormat(Qt.TextFormat.RichText)

        mv.addWidget(ph_title)
        mv.addWidget(self._photo, alignment=Qt.AlignmentFlag.AlignHCenter)
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
        self._synth_grid_host = QWidget()
        self._synth_scroll = QScrollArea()
        self._synth_scroll.setWidgetResizable(True)
        self._synth_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._synth_scroll.setWidget(self._synth_grid_host)
        sl = QVBoxLayout(self._tab_synth)
        sl.setContentsMargins(8, 8, 8, 8)
        sl.addWidget(self._synth_scroll)
        self._synth_grid = QGridLayout(self._synth_grid_host)

        self._tab_parcours = QWidget()
        self._parcours_scroll = QScrollArea()
        self._parcours_scroll.setWidgetResizable(True)
        self._parcours_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._parcours_inner = QWidget()
        self._parcours_v = QVBoxLayout(self._parcours_inner)
        self._parcours_v.setSpacing(12)
        self._parcours_v.setContentsMargins(4, 4, 4, 4)
        self._parcours_scroll.setWidget(self._parcours_inner)
        pl = QVBoxLayout(self._tab_parcours)
        pl.setContentsMargins(8, 8, 8, 8)
        pl.addWidget(self._parcours_scroll)

        self._tab_profil = QWidget()
        self._profil_table = QTableWidget()
        self._profil_table.setColumnCount(5)
        self._profil_table.setHorizontalHeaderLabels(
            ["Date / heure", "Mois SME", "Champ", "Ancienne valeur", "Nouvelle valeur"]
        )
        self._profil_table.horizontalHeader().setStretchLastSection(True)
        self._profil_table.setAlternatingRowColors(True)
        self._profil_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._profil_table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        pr = QVBoxLayout(self._tab_profil)
        pr.setContentsMargins(8, 8, 8, 8)
        pr.addWidget(self._profil_table)

        self._tabs.addTab(self._tab_synth, "Synthèse")
        self._tabs.addTab(self._tab_parcours, "Parcours professionnel")
        self._tabs.addTab(self._tab_profil, "Traçabilité du profil")

        body_lay.addWidget(self._tabs, stretch=1)
        root.addWidget(body, stretch=1)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(self.reject)
        close_btn = bb.button(QDialogButtonBox.StandardButton.Close)
        if close_btn:
            close_btn.setText("Fermer")
        root.addWidget(bb)

        self._refresh_all()

    def _refresh_all(self) -> None:
        self._refresh_header_and_media()
        self._refresh_synthesis()
        self._refresh_parcours_tab()
        self._refresh_profil_tab()

    def _refresh_header_and_media(self) -> None:
        r = self._row
        nom = (r.noms or "—").strip() or "—"
        self._title.setText(nom)
        mat = " · ".join(
            x
            for x in (
                f"Militaire {r.matricule_militaire}" if r.matricule_militaire else "",
                f"Solde {r.matricule_solde}" if r.matricule_solde else "",
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
            )
            if x
        )
        self._subtitle.setText(sub or "—")

        self._set_image_label(
            self._photo,
            person_photo_path(r.id),
            self.style().standardIcon(QStyle.StandardPixmap.SP_UserIcon).pixmap(96, 96),
        )
        self._set_image_label(
            self._grade_pix,
            grade_insignia_path(r.grade),
            self.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxInformation).pixmap(
                64, 64
            ),
            max_w=180,
            max_h=120,
        )

        root = personnel_media_root()
        self._media_hint.setText(
            f"<b>Médias locaux</b> (hors base Access) :<br/>"
            f"• Photo : <code>{root / 'photos'}</code> — fichier "
            f"<code>{r.id}.jpg</code> (ou .png)<br/>"
            f"• Insigne de grade : <code>{root / 'grades'}</code> — nom dérivé du grade "
            f"(ex. <code>général_de_division.png</code>)"
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
                w.setParent(None)
                w.deleteLater()

    def _refresh_synthesis(self) -> None:
        grid = self._synth_grid
        while grid.count():
            item = grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

        r = self._row
        pairs: list[tuple[str, str]] = [
            ("Identifiant liste", r.id),
            ("Noms", _fmt_row_field(r, "noms")),
            ("Date de naissance", _fmt_row_field(r, "date_naissance")),
            ("Lieu de naissance", _fmt_row_field(r, "lieu_naissance")),
            ("Grade", _fmt_row_field(r, "grade")),
            ("Date de grade", _fmt_row_field(r, "date_grade")),
            ("Structure", _fmt_row_field(r, "structure")),
            ("Position administrative", _fmt_row_field(r, "position_admin")),
            ("Fonction", _fmt_row_field(r, "fonction")),
            ("Subdivision d’arme", _fmt_row_field(r, "subdivision_arme")),
            ("Date de service", _fmt_row_field(r, "date_service")),
            ("N° décision", _fmt_row_field(r, "ndecision")),
            ("Matricule militaire", _fmt_row_field(r, "matricule_militaire")),
            ("Matricule solde", _fmt_row_field(r, "matricule_solde")),
            ("Diplôme", _fmt_row_field(r, "diplome")),
            ("Date d’obtention", _fmt_row_field(r, "date_obtention")),
            ("Observations", _fmt_row_field(r, "obs")),
        ]
        for i, (label, value) in enumerate(pairs):
            lb = QLabel(f"<b>{label}</b>")
            lb.setTextFormat(Qt.TextFormat.RichText)
            lb.setAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop
            )
            val = QLabel(value)
            val.setWordWrap(True)
            val.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            val.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            grid.addWidget(lb, i, 0)
            grid.addWidget(val, i, 1)
        grid.setColumnStretch(1, 1)

    def _timeline_card(self, entry: AuditTimelineEntry) -> QFrame:
        card = QFrame()
        card.setObjectName("PersonnelDossierTimelineCard")
        v = QVBoxLayout(card)
        v.setSpacing(8)
        v.setContentsMargins(14, 12, 14, 12)

        head = QLabel(
            f"<b>{format_ts_display(entry.ts_utc)}</b>"
            + (
                f" &nbsp;·&nbsp; mois SME <code>{entry.sme_mois_context}</code>"
                if entry.sme_mois_context
                else ""
            )
        )
        head.setTextFormat(Qt.TextFormat.RichText)
        v.addWidget(head)

        for field in sorted(entry.career_changes.keys()):
            delta = entry.career_changes[field]
            line = QLabel(
                f"• <b>{field_label(field)}</b> : {fmt_val(delta.get('old'))} "
                f"<span style='color:#5c6bc0'>→</span> {fmt_val(delta.get('new'))}"
            )
            line.setTextFormat(Qt.TextFormat.RichText)
            line.setWordWrap(True)
            v.addWidget(line)
        return card

    def _refresh_parcours_tab(self) -> None:
        self._clear_layout(self._parcours_v)
        timeline = build_audit_timeline(self._row.id)
        career_entries = [e for e in timeline if e.career_changes]

        if not career_entries:
            empty = QLabel(
                "Aucun mouvement de carrière enregistré dans l’audit applicatif pour cet individu. "
                "Les changements de <b>structure</b>, <b>grade</b>, <b>fonction</b>, "
                "<b>position administrative</b>, etc., effectués via cette application "
                "apparaîtront ici avec date et contexte SME."
            )
            empty.setWordWrap(True)
            empty.setObjectName("PersonnelDossierEmptyHint")
            self._parcours_v.addWidget(empty)
        else:
            intro = QLabel(
                f"<b>{len(career_entries)} événement(s)</b> de parcours enregistré(s) "
                "(fichier <code>personnel_audit.jsonl</code>)."
            )
            intro.setTextFormat(Qt.TextFormat.RichText)
            intro.setWordWrap(True)
            self._parcours_v.addWidget(intro)
            for e in reversed(career_entries):
                self._parcours_v.addWidget(self._timeline_card(e))
        self._parcours_v.addStretch()

    def _refresh_profil_tab(self) -> None:
        timeline = build_audit_timeline(self._row.id)
        rows_data: list[tuple[str, str, str, str, str]] = []
        for e in timeline:
            for field in sorted(e.profile_changes.keys()):
                delta = e.profile_changes[field]
                rows_data.append(
                    (
                        format_ts_display(e.ts_utc),
                        e.sme_mois_context or "—",
                        field_label(field),
                        fmt_val(delta.get("old")),
                        fmt_val(delta.get("new")),
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
                    "Aucune modification de profil tracée — les changements d’état civil, "
                    "matricules, diplôme, etc., faits via cette application apparaîtront ici."
                ),
            )
            self._profil_table.setSpan(0, 0, 1, 5)
            return

        self._profil_table.setRowCount(len(rows_data))
        for r, row in enumerate(rows_data):
            for c, text in enumerate(row):
                self._profil_table.setItem(r, c, QTableWidgetItem(text))
        self._profil_table.resizeColumnsToContents()

    def _on_edit(self) -> None:
        fresh = self._service.get_by_id(self._row.id)
        if fresh is None:
            from PySide6.QtWidgets import QMessageBox

            QMessageBox.warning(
                self,
                "Fiche dossier",
                "Ligne introuvable en base. Fermez et actualisez la liste générale.",
            )
            return
        dlg = PersonnelEditDialog(self, fresh, self._ref)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        new_row = dlg.result_row()
        try:
            self._service.update_personnel(fresh, new_row)
        except Exception as e:
            from PySide6.QtWidgets import QMessageBox

            QMessageBox.critical(self, "Enregistrement", str(e))
            return
        self._row = new_row
        self._refresh_all()
