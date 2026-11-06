"""Explicit, read-only preview before applying reference changes or exporting."""
from PySide6.QtWidgets import QDialog,QVBoxLayout,QLabel,QTableWidget,QTableWidgetItem,QDialogButtonBox,QAbstractItemView


def confirm_reference_plan(parent,title,headers,rows,explanation):
    dialog=QDialog(parent);dialog.setWindowTitle(title);dialog.resize(850,520)
    layout=QVBoxLayout(dialog)
    hint=QLabel(explanation);hint.setWordWrap(True);layout.addWidget(hint)
    table=QTableWidget(len(rows),len(headers));table.setHorizontalHeaderLabels(headers)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    for ri,row in enumerate(rows):
        for ci,value in enumerate(row):table.setItem(ri,ci,QTableWidgetItem(str(value)))
    table.resizeColumnsToContents();layout.addWidget(table)
    buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
    buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Confirmer")
    buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject);layout.addWidget(buttons)
    return dialog.exec()==QDialog.DialogCode.Accepted
