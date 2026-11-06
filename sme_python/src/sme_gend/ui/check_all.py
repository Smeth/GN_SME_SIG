"""Keep bulk-selection actions consistent with the visible rows."""
from PySide6.QtCore import Qt
from shiboken6 import isValid


def sync_check_all(button, keys, checked):
    keys = set(keys)
    active = bool(keys) and keys <= set(checked)
    button.setText('Tout décocher' if active else 'Tout cocher')
    button.setToolTip('Décocher toutes les lignes affichées.' if active else 'Cocher toutes les lignes affichées.')
    return active


def bind_list_check_all(button, widget, search=None):
    """Bind to QListWidget content, manual checks and filtering."""
    def items():
        return [widget.item(i) for i in range(widget.count())
                if not widget.item(i).isHidden() and widget.item(i).flags() & Qt.ItemFlag.ItemIsUserCheckable]
    def refresh(*_):
        if not isValid(widget) or not isValid(button):
            return
        visible = items()
        sync_check_all(button, range(len(visible)),
                       [i for i, item in enumerate(visible) if item.checkState() == Qt.CheckState.Checked])
    def toggle(*_):
        if not isValid(widget) or not isValid(button):
            return
        visible = items()
        state = Qt.CheckState.Unchecked if visible and all(i.checkState() == Qt.CheckState.Checked for i in visible) else Qt.CheckState.Checked
        for item in visible:
            item.setCheckState(state)
        refresh()
    button.clicked.connect(toggle)
    widget.itemChanged.connect(refresh)
    widget.model().rowsInserted.connect(refresh)
    widget.model().modelReset.connect(refresh)
    if search is not None:
        search.textChanged.connect(refresh)
    refresh()
