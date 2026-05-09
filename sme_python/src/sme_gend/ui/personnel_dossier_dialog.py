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
        *,
        allow_edit: bool = True,
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
        self._allow_edit = allow_edit

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
        self._btn_edit.setVisible(allow_edit)
        self._btn_edit.setEnabled(allow_edit)
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
        self._synth_host = QWidget()
        self._synth_scroll = QScrollArea()
        self._synth_scroll.setWidgetResizable(True)
        self._synth_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._synth_scroll.setWidget(self._synth_host)
        self._synth_lay = QVBoxLayout(self._synth_host)
        self._synth_lay.setSpacing(16)
        self._synth_lay.setContentsMargins(12, 12, 12, 12)

        # Bandeau alerte (position admin spéciale) — affiché en haut de l'onglet Synthèse
        self._alert_banner = QLabel()
        self._alert_banner.setWordWrap(True)
        self._alert_banner.setVisible(False)

        sl = QVBoxLayout(self._tab_synth)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(0)
        sl.addWidget(self._alert_banner)
        sl.addWidget(self._synth_scroll, stretch=1)

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

        # Onglet tableau carrière : vue tabulaire de tous les événements de parcours
        self._tab_carriere = QWidget()
        self._carriere_table = QTableWidget()
        self._carriere_table.setColumnCount(6)
        self._carriere_table.setHorizontalHeaderLabels(
            ["Date", "Champ", "Avant", "Après", "Mois SME", "Auteur / Motif"]
        )
        hh = self._carriere_table.horizontalHeader()
        hh.setSectionResizeMode(0, hh.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(1, hh.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(2, hh.ResizeMode.Stretch)
        hh.setSectionResizeMode(3, hh.ResizeMode.Stretch)
        hh.setSectionResizeMode(4, hh.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(5, hh.ResizeMode.ResizeToContents)
        self._carriere_table.setAlternatingRowColors(True)
        self._carriere_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._carriere_table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self._carriere_table.setWordWrap(True)
        cr = QVBoxLayout(self._tab_carriere)
        cr.setContentsMargins(8, 8, 8, 8)
        cr.addWidget(self._carriere_table)

        self._tabs.addTab(self._tab_synth, "Synthèse")
        self._tabs.addTab(self._tab_parcours, "Parcours professionnel")
        self._tabs.addTab(self._tab_carriere, "Historique carrière")
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
        self._refresh_carriere_tab()
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
            self.style().standardIcon(
                getattr(QStyle.StandardPixmap, "SP_UserIcon", QStyle.StandardPixmap.SP_FileDialogContentsView)
            ).pixmap(96, 96),
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
        card.setStyleSheet(
            "QFrame#DossierSectionCard {"
            "  background: white;"
            "  border: 1px solid #e0e0e0;"
            "  border-radius: 8px;"
            "}"
        )
        v = QVBoxLayout(card)
        v.setSpacing(10)
        v.setContentsMargins(16, 14, 16, 16)

        title_lbl = QLabel(title)
        title_lbl.setStyleSheet(
            "font-size: 11px; font-weight: 700; color: #616161;"
        )
        v.addWidget(title_lbl)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: #eeeeee; margin: 0;")
        v.addWidget(sep)

        COLS = 3
        grid = QGridLayout()
        grid.setVerticalSpacing(4)
        grid.setHorizontalSpacing(24)
        for i, (lbl_text, val_text) in enumerate(fields):
            row_i = i // COLS
            col_i = i % COLS
            lbl = QLabel(lbl_text)
            lbl.setStyleSheet(
                "font-size: 10px; font-weight: 600; color: #9e9e9e; letter-spacing: 0.3px;"
            )
            val = QLabel(val_text if val_text else "—")
            val.setWordWrap(True)
            val.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            if alert_field and lbl_text == alert_field:
                val.setStyleSheet("color: #c62828; font-weight: 600;")
            grid.addWidget(lbl, row_i * 2, col_i)
            grid.addWidget(val, row_i * 2 + 1, col_i)
        for c in range(COLS):
            grid.setColumnStretch(c, 1)
        v.addLayout(grid)
        return card

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
            self._alert_banner.setStyleSheet(
                "background: #fff5f5;"
                "color: #c62828;"
                "padding: 10px 18px;"
                "border: 1px solid #ffcdd2;"
                "border-left: 4px solid #c62828;"
                "font-size: 13px;"
            )
        else:
            self._alert_banner.setStyleSheet("")
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
            ("STRUCTURE", _v("structure")),
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

    def _timeline_card(self, entry: AuditTimelineEntry) -> QFrame:
        card = QFrame()
        card.setObjectName("PersonnelDossierTimelineCard")
        v = QVBoxLayout(card)
        v.setSpacing(8)
        v.setContentsMargins(14, 12, 14, 12)

        parts = [f"<b>{format_ts_display(entry.ts_utc)}</b>"]
        if entry.sme_mois_context:
            parts.append(f"mois SME <code>{entry.sme_mois_context}</code>")
        if entry.edited_by:
            parts.append(f"par <b>{entry.edited_by}</b>")
        if entry.edit_motif:
            parts.append(f"motif : <i>{entry.edit_motif}</i>")
        head = QLabel(" &nbsp;·&nbsp; ".join(parts))
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

    def _refresh_carriere_tab(self) -> None:
        timeline = build_audit_timeline(self._row.id)
        rows_data: list[tuple[str, str, str, str, str, str]] = []
        for e in timeline:
            if not e.career_changes:
                continue
            date_s = format_ts_display(e.ts_utc)
            mois_s = e.sme_mois_context or "—"
            auteur_parts = []
            if e.edited_by:
                auteur_parts.append(e.edited_by)
            if e.edit_motif:
                auteur_parts.append(f"({e.edit_motif})")
            auteur_s = " ".join(auteur_parts) or "—"
            for field in sorted(e.career_changes.keys()):
                delta = e.career_changes[field]
                rows_data.append((
                    date_s,
                    field_label(field),
                    fmt_val(delta.get("old")),
                    fmt_val(delta.get("new")),
                    mois_s,
                    auteur_s,
                ))
        rows_data.reverse()

        self._carriere_table.clearSpans()
        if not rows_data:
            self._carriere_table.setRowCount(1)
            item = QTableWidgetItem(
                "Aucun mouvement de carrière enregistré — les changements de grade, "
                "structure, fonction, position admin… effectués via cette application "
                "apparaîtront ici."
            )
            self._carriere_table.setItem(0, 0, item)
            self._carriere_table.setSpan(0, 0, 1, 6)
            return

        self._carriere_table.setRowCount(len(rows_data))
        for r, row in enumerate(rows_data):
            for c, text in enumerate(row):
                self._carriere_table.setItem(r, c, QTableWidgetItem(text))
        self._carriere_table.resizeRowsToContents()

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
