"""The rubric preview reads references only and preserves configured ordering."""
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from sme_gend.config.settings import Settings
from sme_gend.models.liste_layout import ListeLayoutConfig, SectionBlock
from sme_gend.models.structure import StructureRow
from sme_gend.services import liste_present_outline as outline


def snapshot(sections, **kwargs):
    return outline.PresentsOutlineSnapshot(
        Settings(database_path=Path('unused'), mysql_view_mois='2026-11'),
        (), '2026-11', ListeLayoutConfig(sections=sections),
        kwargs.get('structures', [StructureRow('DPF', 'Personnel', 1)]), [],
        kwargs.get('subdivisions', {}))


def test_capture_loads_only_reference_data_in_one_snapshot(monkeypatch):
    settings = Settings(database_path=Path('unused'), mysql_view_mois='2026-11')
    layout = ListeLayoutConfig(sections=[
        SectionBlock('one', 0, 'DPF', subdivision_order=['Explicit B', 'Explicit A']),
        SectionBlock('two', 1, 'OTHER'),
        SectionBlock('three', 2, 'CS-DPF'),
        SectionBlock('four', 3, 'DPF', group_by_subdivision=False),
    ])
    active = []
    calls = []
    @contextmanager
    def read_snapshot(pinned):
        assert pinned.mysql_view_mois == '2026-11'
        active.append(True)
        try:
            yield
        finally:
            active.pop()
    def load(*, read_only):
        assert read_only and active
        calls.append('layout')
        return layout
    def structures():
        assert active
        calls.append('structures')
        return [StructureRow('DPF', 'Personnel', 1)]
    def flat():
        assert active
        calls.append('flat')
        return []
    def resolve(section):
        assert active and section.id == 'two'
        calls.append('subdivisions')
        return ['Reference A']
    def forbidden(*args, **kwargs):
        raise AssertionError('Personnel must not be read to display rubrics')
    service = SimpleNamespace(_settings=settings,
        _layout_config=SimpleNamespace(load=load, resolve_subdivision_order_labels=resolve),
        _liste=SimpleNamespace(fetch_all=forbidden, fetch_for_presence_mode=forbidden),
        capture_snapshot=forbidden, build_preview_html=forbidden)
    monkeypatch.setattr(outline, 'mysql_read_snapshot', read_snapshot)
    monkeypatch.setattr(outline, 'StructureRepository', lambda pinned: SimpleNamespace(fetch_all=structures))
    monkeypatch.setattr(outline, 'SubdivisionRepository', lambda pinned: SimpleNamespace(fetch_flat_hierarchy=flat))
    progress = []
    result = outline.capture_outline(service, on_progress=progress.append)
    assert calls == ['layout', 'structures', 'flat', 'subdivisions']
    assert result.subdivisions == {'one': ['Explicit B', 'Explicit A'], 'two': ['Reference A'], 'three': [], 'four': []}
    assert progress[0] == 0 and progress[-1] == 100 and progress == sorted(progress)
    layout.sections[0].subdivision_order.append('LATER CHANGE')
    assert result.layout.sections[0].subdivision_order == ['Explicit B', 'Explicit A']
    assert not active


def test_outline_preserves_order_titles_and_escapes_rubric_links():
    result = snapshot([
        SectionBlock('later', 5, 'DPF', title_main_override='Later'),
        SectionBlock('id: /&', 0, 'DPF', title_main_override='<first>', title_sub_override='Sub & title'),
    ], subdivisions={'id: /&': ['B & <unit>', 'A unit'], 'later': []})
    html = outline.render_outline(result)
    assert html.index('&lt;FIRST&gt;') < html.index('LATER')
    assert html.index('B &amp; &lt;unit&gt;') < html.index('A unit')
    assert "href='section:id%3A%20%2F%26'" in html
    assert "href='subdivision:id%3A%20%2F%26:0'" in html
    assert 'Sub &amp; title' in html and '<first>' not in html
    assert 'Mois SME : 2026-11' in html
    assert 'Matricule' not in html and 'Nom et prénom' not in html


def test_outline_searches_rubrics_with_accents_without_personnel():
    result = snapshot([SectionBlock('one', 0, 'DPF')],
                      subdivisions={'one': ['Équipe spéciale', 'Autre rubrique']})
    filtered = outline.render_outline(result, 'equipe speciale')
    assert 'Équipe spéciale' in filtered and 'Autre rubrique' not in filtered
    assert 'PERSONNEL' in filtered
    assert 'Aucune rubrique correspondante' in outline.render_outline(result, 'UNKNOWN')


def test_unreferenced_structures_are_visible_as_a_layout_warning():
    result = snapshot([SectionBlock('one', 0, 'UNKNOWN')])
    html = outline.render_outline(result)
    assert 'UNKNOWN' in html and 'Structure absente du référentiel.' in html


def test_rg_compagnie_has_its_commandement_rubric_and_no_grouping_is_explicit():
    result = snapshot([SectionBlock('rg', 0, 'RG-BZV'), SectionBlock('merged', 1, 'CS-DPF')],
        structures=[StructureRow('RG-BZV', 'Région', 1), StructureRow('CS-DPF', 'CS-DPF', 2)],
        subdivisions={'rg': ['COMPAGNIE DE GENDARMERIE TERRITORIALE BZV-1'], 'merged': []})
    html = outline.render_outline(result)
    assert 'COMMANDEMENT' in html
    assert 'Sans regroupement par subdivision' in html
