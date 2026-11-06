"""Printable snapshot of the filtered structure counts, without database reads."""
from html import escape


def structure_effectifs_html(rows, *, mois, search=''):
    month = escape(mois or 'Non défini')
    query = f'<p>Filtre : {escape(search)}</p>' if search else ''
    body = ''.join(
        '<tr><td>{}</td><td>{}</td><td align="right">{}</td></tr>'.format(
            escape(str(code)), escape(str(label)).replace('\n', '<br>'), int(count))
        for code, label, count in rows)
    total = sum(int(count) for _, _, count in rows)
    return (
        '<html><head><style>'
        'body { font-family: Arial; font-size: 9pt; color: #111; }'
        'h2 { text-align: center; font-size: 14pt; }'
        'th { background-color: #eeeeee; }'
        'td, th { padding: 5px; }'
        '</style></head><body><h2>Effectifs par structure</h2>'
        f'<p>Mois SME consulté : <b>{month}</b></p>{query}'
        '<table border="1" cellspacing="0" cellpadding="4" width="100%">'
        '<thead><tr><th width="17%">Code</th><th width="68%">Désignation</th>'
        '<th width="15%">Effectif</th></tr></thead>'
        f'<tbody>{body}<tr><td colspan="2"><b>TOTAL DES EFFECTIFS AFFICHÉS</b></td>'
        f'<td align="right"><b>{total}</b></td></tr></tbody></table>'
        f'<p>{len(rows)} structure(s) affichée(s).</p></body></html>'
    )


def print_structure_effectifs_document(doc, printer):
    """Follow the preview's paper/orientation and repeat table headers per page."""
    from PySide6.QtCore import QSizeF
    from PySide6.QtGui import QPageLayout
    rect = printer.pageLayout().paintRect(QPageLayout.Unit.Point)
    doc.setDocumentMargin(0)
    doc.setPageSize(QSizeF(rect.width(), rect.height()))
    doc.print_(printer)
