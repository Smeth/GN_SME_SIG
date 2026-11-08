"""Percent, actual work counters and a visible checklist for a running operation."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QProgressBar, QPushButton, QStyle, QVBoxLayout)

from sme_gend.services.operation_progress import ProgressSnapshot, ProgressStep


class OperationProgressDialog(QDialog):
    def __init__(self, title, text):
        super().__init__()
        self.setObjectName('operationProgressDialog')
        self.setWindowTitle(title)
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setMinimumWidth(560)
        self.resize(650, 440)
        self._finished = False
        self._failed = False
        self._snapshot = None
        self._plan = ()
        self._legacy_labels = []
        self._heading = QLabel(text)
        self._heading.setWordWrap(True)
        self._heading.setObjectName('dashSubtitle')
        self._bar = QProgressBar()
        self._bar.setRange(0, 100)
        self._bar.setValue(0)
        self._bar.setTextVisible(True)
        self._bar.setFormat('%p %')
        self._bar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._bar.setMinimumHeight(28)
        self._percentage = QLabel('0 %')
        percent_font = self._percentage.font()
        percent_font.setBold(True)
        percent_font.setPointSize(max(12, percent_font.pointSize()+2))
        self._percentage.setFont(percent_font)
        self._percentage.setMinimumWidth(58)
        progress_row = QHBoxLayout()
        progress_row.addWidget(self._bar, 1)
        progress_row.addWidget(self._percentage)
        self._detail = QLabel('Préparation…')
        self._detail.setWordWrap(True)
        self._detail.setTextFormat(Qt.TextFormat.PlainText)
        self._counter = QLabel()
        self._counter.setObjectName('settingsPathsHint')
        self._steps = QListWidget()
        self._steps.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self._steps.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._steps.setMinimumHeight(190)
        self._close = QPushButton('Fermer')
        self._close.clicked.connect(self.accept)
        self._close.hide()
        buttons = QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(self._close)
        layout = QVBoxLayout(self)
        layout.addWidget(self._heading)
        layout.addLayout(progress_row)
        layout.addWidget(self._detail)
        layout.addWidget(self._counter)
        layout.addWidget(self._steps, 1)
        layout.addLayout(buttons)
        self.apply_snapshot(ProgressSnapshot((ProgressStep('prepare', text, 'running'),),
                                             'prepare', 0, 'Préparation…'))

    def reject(self):
        if self._finished:
            super().reject()

    def closeEvent(self, event):
        if self._finished:
            super().closeEvent(event)
        else:
            event.ignore()

    def apply_snapshot(self, snapshot):
        if self._finished:
            return
        self._snapshot = snapshot
        plan = tuple((step.key, step.label) for step in snapshot.steps)
        if plan != self._plan:
            self._steps.clear()
            for step in snapshot.steps:
                self._steps.addItem(QListWidgetItem(step.label))
            self._plan = plan
        active = next((index for index, step in enumerate(snapshot.steps) if step.key == snapshot.active_key), 0)
        all_done = all(step.state == 'done' for step in snapshot.steps)
        for index, step in enumerate(snapshot.steps):
            state = 'running' if all_done and index == active else step.state
            self._set_step(index, step.label, state)
        self._heading.setText(f'Étape {active+1} / {len(snapshot.steps)} — {snapshot.steps[active].label}')
        self._detail.setText(snapshot.detail or ('Finalisation de l’opération…' if all_done else 'Traitement en cours…'))
        self._bar.setValue(max(self._bar.value(), min(99, snapshot.percent)))
        self._percentage.setText(f'{self._bar.value()} %')
        if snapshot.current is not None and snapshot.total:
            def number(value):
                return f'{value:,.0f}'.replace(',', ' ') if float(value).is_integer() else f'{value:.2f}'
            self._counter.setText(number(snapshot.current)+' / '+number(snapshot.total)+' unités traitées')
        else:
            self._counter.setText('Progression des étapes — 100 % après confirmation de la fin.')

    def _set_step(self, index, label, state):
        text, icon = {
            'pending': ('À venir', QStyle.StandardPixmap.SP_DialogResetButton),
            'running': ('En cours', QStyle.StandardPixmap.SP_ArrowRight),
            'done': ('Terminée', QStyle.StandardPixmap.SP_DialogApplyButton),
            'failed': ('Interrompue', QStyle.StandardPixmap.SP_MessageBoxCritical),
        }[state]
        item = self._steps.item(index)
        item.setText(text+' — '+label)
        item.setIcon(self.style().standardIcon(icon))

    def success(self):
        self._finished = True
        self._bar.setValue(100)
        self._percentage.setText('100 %')
        for index, (_, label) in enumerate(self._plan):
            self._set_step(index, label, 'done')
        self._detail.setText('Opération terminée.')
        self._bar.repaint()

    def failure(self, message):
        self._finished = True
        self._failed = True
        if self._snapshot:
            for index, step in enumerate(self._snapshot.steps):
                if step.key == self._snapshot.active_key:
                    self._set_step(index, step.label, 'failed')
        self._heading.setText('Opération interrompue')
        self._detail.setText(message)
        self._counter.setText('Opération non terminée — consultez le détail ci-dessus.')
        self._close.show()
        self._close.setFocus()

    def after_worker_finished(self):
        if not self._failed:
            self.accept()

    def legacy_step(self, current, total, message):
        total = max(1, total)
        if len(self._legacy_labels) != total:
            self._legacy_labels = [f'Étape {index+1}' for index in range(total)]
        current = min(max(1, current), total)
        self._legacy_labels[current-1] = message
        steps = tuple(ProgressStep(str(index+1), label,
            'done' if index < current-1 else 'running' if index == current-1 else 'pending')
            for index, label in enumerate(self._legacy_labels))
        self.apply_snapshot(ProgressSnapshot(steps, str(current),
            int(100*(current-1)/max(1,total)), message))

    def legacy_detail(self, message):
        self._detail.setText(message)
