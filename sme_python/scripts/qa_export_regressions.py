"""Generate synthetic exports without accessing operational data."""
from pathlib import Path
import runpy
from types import SimpleNamespace
import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFont, QFontDatabase

project = Path(__file__).resolve().parents[1]
out = project / 'build/qa/exports'
out.mkdir(parents=True, exist_ok=True)
app = QApplication.instance() or QApplication([])
for font in ('arial.ttf', 'segoeui.ttf'):
    QFontDatabase.addApplicationFont('C:/Windows/Fonts/' + font)
app.setFont(QFont('Arial', 10))
with pytest.MonkeyPatch.context() as patch:
    settings, rows, _ = runpy.run_path(str(project / 'tests/test_export_integrity_regressions.py'))['scenario'](patch)
    from sme_gend.services.numerique_excel_export import export_numerique_workbook
    from sme_gend.services.sme_dpf_excel_export import export_sme_dpf_workbook
    from sme_gend.services.sme_dpf_service import SmeDpfBundle
    from sme_gend.services.sme_dpf_scope import filter_dpf_rows
    export_numerique_workbook(settings, project / 'modeles/numerique.xlsx', out / 'SME.xlsx', export_mois='2026-09')
    export_sme_dpf_workbook(settings, out / 'DPF.xlsx', bundle=SmeDpfBundle(mois='2026-09', liste_gnle=filter_dpf_rows(rows)))
    from sme_gend.services import parcours_pdf_export as pdf
    patch.setattr(pdf, 'ReferentielService', lambda st: SimpleNamespace(grades=lambda: [], structures=lambda: []))
    patch.setattr(pdf, 'AgentDossierService', lambda st: SimpleNamespace(list_passations=lambda r: [], list_affectations=lambda r: [], list_diplomes=lambda r: []))
    patch.setattr(pdf, 'person_photo_path', lambda key: None)
    patch.setattr(pdf, 'build_export_mois_context', lambda st: SimpleNamespace(export_mois='2026-09'))
    patch.setattr('sme_gend.services.position_admin_events.list_position_events', lambda *a: [])
    for count, name in ((2, 'fiche'), (75, 'fiche_longue')):
        missions = [SimpleNamespace(nature=f'Mission exemple {i:03d}', lieu='Lieu de destination', date_debut='2026-09-03', date_fin='2026-09-08') for i in range(count)]
        patch.setattr(pdf, 'AgentDossierEventsService', lambda st: SimpleNamespace(missions=lambda key: missions, stages=lambda key: [], malades=lambda key: [], conges=lambda key: [], desertions=lambda key: []))
        pdf.export_parcours_pdf(rows[0], settings, out / (name + '.pdf'))
print(out)
