"""Official workbook fidelity, fresh monthly data and calculated totals."""
from dataclasses import fields
from pathlib import Path
from hashlib import sha256

import pytest
from openpyxl import load_workbook

from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.models.detaches_file_config import DetachesFileConfig
from sme_gend.models.gain_perte import GainPerteRow
from sme_gend.models.numerique_satellite import MissionDetail, StageDetail
from sme_gend.services.numerique_model_export import build_from_model

MODEL = Path(__file__).resolve().parents[2] / 'SME_GEND_model.xlsx'


def person(key, position, **overrides):
    values = {f.name: None for f in fields(ListeGeneraleRow)}
    values.update(id=key, noms=f'PERSONNE TEST {key}', grade='CNE', position_admin=position,
                  structure='DPF', subdivision_arme='Direction')
    values.update(overrides)
    return ListeGeneraleRow(**values)


def details():
    return {key: {} for key in ('missions', 'malades', 'deserteurs', 'stages_loc', 'stages_ex',
                                'stages_internal', 'conges', 'conges_by_type', 'positions')}


def test_model_fidelity_monthly_data_and_formula_results(tmp_path):
    fingerprint = sha256(MODEL.read_bytes()).hexdigest()
    original = load_workbook(MODEL)
    slot = next(row for row in original['PRESENT'].iter_rows(min_row=4)
                if isinstance(row[0].value, int))
    rows = [person('P', 'Présent', structure=slot[16].value, subdivision_arme=slot[13].value),
            person('S', 'Stage interne'), person('M', 'Mission', noms='=FORMULE INTERDITE'),
            person('N', 'Inst Mise Route'), person('D', 'Détaché', subdivision_arme='SIGLE TEST')]
    data = details()
    data['missions']['M'] = MissionDetail('Renfort', 'Lieu test', 'NOTE TEST', '05/11/2026')
    data['stages_internal']['S'] = StageDetail('Formation test', 'École test', '01–30/11/2026')
    moves = [GainPerteRow(str(i), 'CNE', f'GAIN TEST {i}', 'GAIN', '112026', 'AJOUT') for i in range(6)]
    moves += [GainPerteRow('P', 'CNE', 'PERTE TEST', 'PERTE', '112026', 'MUTATION')]
    output = tmp_path / 'model_export.xlsx'
    build_from_model(MODEL, output, mois='2026-11', rows=rows, grades=[], details=data,
                     detaches=[rows[-1]], previous_counts=[0] * 10, movements=moves,
                     detaches_config=DetachesFileConfig())
    wb = load_workbook(output)
    cached = load_workbook(output, data_only=True)
    assert sha256(MODEL.read_bytes()).hexdigest() == fingerprint
    assert wb.sheetnames == original.sheetnames
    for name in wb.sheetnames:
        assert wb[name].page_setup == original[name].page_setup
        dimension = lambda ws: {key: (d.width, d.hidden, d.min, d.max, d.outlineLevel, d._style)
                                for key, d in ws.column_dimensions.items()}
        assert dimension(wb[name]) == dimension(original[name])
    for name in ('NUMERIQUE', 'INDISPO PAR STRUCTURE bis', 'PAGE INTER', 'Feuil4'):
        assert set(map(str, wb[name].merged_cells.ranges)) == set(map(str, original[name].merged_cells.ranges))
    assert set(map(str, wb['MISSION'].merged_cells.ranges)) == set(map(str, original['MISSION'].merged_cells.ranges)) | {'A2:H2'}
    assert wb['MISSION']['A3'].value == original['MISSION']['A3'].value
    assert wb['MISSION']['A3']._style == original['MISSION']['A3']._style
    assert wb['MISSION']['C4'].value == '=FORMULE INTERDITE'
    assert wb['MISSION']['C4'].data_type == 's'
    assert wb['MISSION']['G4'].value == 'NOTE TEST'
    assert wb['MISSION']['C5'].value is None
    assert wb['Feuil4']['C5'].value == 'PERSONNE TEST S'
    assert wb['Feuil4']['E5'].value == 'Formation test'
    assert wb['DETACHE']['A4'].value == original['DETACHE']['A4'].value
    assert wb['DETACHE']['C5'].value == 'PERSONNE TEST D'
    assert '2026' in wb['PAGE INTER']['A10'].value and 'novembre' in wb['PAGE INTER']['A10'].value.casefold()
    assert cached['NUMERIQUE']['T8'].value == 5
    assert cached['NUMERIQUE']['T9'].value == 2
    assert cached['NUMERIQUE']['T10'].value == 1
    assert cached['NUMERIQUE']['T14'].value == 1
    assert cached['NUMERIQUE']['T16'].value == 1
    assert cached['NUMERIQUE']['CF14'].value == 3
    assert cached['NUMERIQUE']['G48'].value == 3  # CNE column, ten unavailable categories.
    cne_column = next(c.column for c in wb['NON-REJOINT'][1] if c.value == 'CNE')
    assert cached['NON-REJOINT'].cell(2, cne_column).value == 1
    assert wb['NUMERIQUE']['B8'].data_type == 'f'
    assert wb['PRESENT'].cell(slot[0].row, 3).value == 'PERSONNE TEST P'
    assert all(c.value is None for row in wb['PRESENT'].iter_rows(min_row=4, min_col=19) for c in row)
    assert len([c for row in wb['GAIN PERTE'] for c in row if str(c.value or '').startswith('GAIN TEST')]) == 6
    assert any(c.value == 'PERTE TEST' for row in wb['GAIN PERTE'] for c in row)
    assert cached['DETACHE PAR STRUCTURE']['C110'].value == 1
    wb.close(); cached.close(); original.close()


def test_missing_previous_month_has_no_invented_variation(tmp_path):
    output = tmp_path / 'empty.xlsx'
    build_from_model(MODEL, output, mois='2026-01', rows=[], grades=[], details=details(),
                     detaches=[], previous_counts=[None] * 10, movements=[],
                     detaches_config=DetachesFileConfig())
    wb = load_workbook(output, data_only=True)
    assert wb['NUMERIQUE']['CD5'].value is None
    assert wb['NUMERIQUE']['CF5'].value in ('', None)
    assert wb['NUMERIQUE']['CF14'].value in ('', None)
    assert wb['NUMERIQUE']['T8'].value == 0
    assert wb['LISTE GENERALE']['B4'].value is None
    assert wb['PRESENT']['C4'].value is None
    wb.close()


def test_unsupported_grade_does_not_publish_incomplete_counts(tmp_path):
    output = tmp_path / 'keep.xlsx'
    output.write_bytes(b'previous document')
    with pytest.raises(ValueError, match='Grades absents'):
        build_from_model(MODEL, output, mois='2026-11', rows=[person('X', 'Présent', grade='INCONNU')],
                         grades=[], details=details(), detaches=[], previous_counts=[], movements=[],
                         detaches_config=DetachesFileConfig())
    assert output.read_bytes() == b'previous document'


def test_legacy_default_is_migrated_to_official_model(monkeypatch):
    from sme_gend.config import settings
    from sme_gend.config.user_paths import UserPathsOverrides
    monkeypatch.delenv('SME_NUMERIQUE_TEMPLATE_PATH', raising=False)
    chosen = settings._resolve_numerique_template_path(UserPathsOverrides(template_numerique_path=str(MODEL.parent / 'sme_python/modeles/numerique.xlsx')))
    assert chosen.resolve() == MODEL.resolve()
