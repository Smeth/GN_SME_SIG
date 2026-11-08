"""The presents model's grouped dimensions and display settings survive export."""
from copy import copy

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font

from sme_gend.models.liste_layout import ListeLayoutConfig
from sme_gend.services.excel_template_exporter import export_liste_fidele


def test_presents_export_preserves_grouped_columns_defaults_and_page_layout(tmp_path):
    template = tmp_path / 'synthetic_template.xlsx'
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = 'Liste'
    sheet['A1'] = 'SYNTHETIC PRESENTS'
    sheet.merge_cells('A1:R1')
    sheet['A3'] = 'N°'
    sheet.sheet_format.baseColWidth = 10
    sheet.sheet_format.defaultColWidth = 8.88671875
    sheet.sheet_format.defaultRowHeight = 14.4
    for letter, first, last, width in (('A', 1, 2, 4.44140625),
                                        ('M', 13, 14, 10.44140625),
                                        ('O', 15, 16, 6.44140625)):
        dimension = sheet.column_dimensions[letter]
        dimension.min, dimension.max, dimension.width = first, last, width
        dimension.font = Font(name='Arial', bold=True, color='123456')
        dimension.alignment = Alignment(horizontal='center')
        dimension.number_format = '0.00'
        dimension.bestFit = True
    sheet.column_dimensions['M'].outlineLevel = 1
    sheet.column_dimensions['M'].hidden = True
    sheet.sheet_view.view = 'pageLayout'
    sheet.sheet_view.zoomScale = 108
    sheet.sheet_view.zoomScaleNormal = 100
    sheet.sheet_view.zoomScalePageLayoutView = 108
    sheet.sheet_view.showGridLines = False
    sheet.sheet_view.topLeftCell = 'A4560'
    sheet.sheet_view.selection[0].activeCell = 'A4560'
    sheet.sheet_view.selection[0].sqref = 'A4560'
    workbook.save(template)
    workbook.close()
    original_bytes = template.read_bytes()

    destination = tmp_path / 'presents.xlsx'
    export_liste_fidele(template, ListeLayoutConfig(), [], destination)
    original = load_workbook(template)
    output = load_workbook(destination)
    try:
        source, result = original.active, output.active
        assert result.sheet_format == source.sheet_format
        for letter in ('A', 'M', 'O'):
            before, after = source.column_dimensions[letter], result.column_dimensions[letter]
            assert (after.min, after.max, after.width, after.bestFit, after.hidden, after.outlineLevel) == (
                before.min, before.max, before.width, before.bestFit, before.hidden, before.outlineLevel)
            for component in ('font', 'alignment', 'number_format'):
                assert copy(getattr(after, component)) == copy(getattr(before, component))
        assert result.sheet_view.view == 'pageLayout'
        assert (result.sheet_view.zoomScale, result.sheet_view.zoomScaleNormal,
                result.sheet_view.zoomScalePageLayoutView) == (108, 100, 108)
        assert result.sheet_view.showGridLines is False
        assert result.sheet_view.topLeftCell is None
        assert result.sheet_view.selection[0].activeCell == 'A1'
        assert result.sheet_view.selection[0].sqref == 'A1'
        assert template.read_bytes() == original_bytes
    finally:
        original.close()
        output.close()
