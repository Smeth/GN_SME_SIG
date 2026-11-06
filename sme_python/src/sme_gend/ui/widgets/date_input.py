"""Saisie de dates au format français (jj/mm/aaaa) — chiffres uniquement + calendrier."""

from __future__ import annotations

from datetime import date, datetime

from PySide6.QtCore import QDate, Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QCalendarWidget,
    QHBoxLayout,
    QLineEdit,
    QToolButton,
    QWidget,
)


def _digits_only(text: str) -> str:
    return "".join(c for c in (text or "") if c.isdigit())[:8]


def _format_digits(digits: str) -> str:
    n = len(digits)
    if n <= 2:
        return digits
    if n <= 4:
        return f"{digits[:2]}/{digits[2:]}"
    return f"{digits[:2]}/{digits[2:4]}/{digits[4:]}"


def _parse_date_str(s: str | None) -> QDate | None:
    raw = (s or "").strip()
    if not raw:
        return None
    # Priorité au français ; le format américain n'est retenu que si
    # l'interprétation jour/mois est impossible (ex. 07/27/2025).
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%m/%d/%Y"):
        try:
            dt = datetime.strptime(raw[:10], fmt)
            return QDate(dt.year, dt.month, dt.day)
        except ValueError:
            continue
    digits = _digits_only(raw)
    if len(digits) == 8:
        try:
            dt = datetime.strptime(_format_digits(digits), "%d/%m/%Y")
            return QDate(dt.year, dt.month, dt.day)
        except ValueError:
            pass
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return QDate(dt.year, dt.month, dt.day)
    except ValueError:
        return None


class _NumericDateLineEdit(QLineEdit):
    """Champ texte : saisie chiffres uniquement, format jj/mm/aaaa imposé."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setPlaceholderText("jj/mm/aaaa")
        self.setMaxLength(10)
        self.setClearButtonEnabled(True)
        self.setInputMethodHints(Qt.InputMethodHint.ImhDigitsOnly)
        self.setToolTip("Saisir la date au clavier (chiffres uniquement) ou via le calendrier.")

    def digits(self) -> str:
        return _digits_only(self.text())

    def set_digits(self, digits: str) -> None:
        self.setText(_format_digits(_digits_only(digits)))

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        mods = event.modifiers()
        if mods & Qt.KeyboardModifier.ControlModifier:
            super().keyPressEvent(event)
            self._sync_format()
            return
        if key == Qt.Key.Key_Backspace:
            self.set_digits(self.digits()[:-1])
            return
        if key == Qt.Key.Key_Delete:
            self.clear()
            return
        if key in (
            Qt.Key.Key_Left,
            Qt.Key.Key_Right,
            Qt.Key.Key_Home,
            Qt.Key.Key_End,
            Qt.Key.Key_Tab,
            Qt.Key.Key_Return,
            Qt.Key.Key_Enter,
            Qt.Key.Key_Escape,
        ):
            super().keyPressEvent(event)
            return
        if Qt.Key.Key_0 <= key <= Qt.Key.Key_9:
            digit = chr(key)
        else:
            event.ignore()
            return
        if len(self.digits()) >= 8:
            event.ignore()
            return
        self.set_digits(self.digits() + digit)

    def insertFromMimeData(self, source) -> None:
        if source is None or not source.hasText():
            return
        merged = _digits_only(self.digits() + source.text())
        self.set_digits(merged)

    def _sync_format(self) -> None:
        digits = _digits_only(self.text())
        formatted = _format_digits(digits)
        if self.text() != formatted:
            pos = len(formatted)
            self.blockSignals(True)
            self.setText(formatted)
            self.setCursorPosition(pos)
            self.blockSignals(False)


class FrenchDateEdit(QWidget):
    """Champ date jj/mm/aaaa (chiffres seuls) avec calendrier ; vide = non renseigné."""

    changed = Signal()

    def __init__(self, parent=None, *, optional: bool = True) -> None:
        super().__init__(parent)
        self._optional = optional
        self._line = _NumericDateLineEdit(self)
        # textChanged porte le texte : on le filtre pour garder un signal sans argument.
        self._line.textChanged.connect(lambda _texte: self.changed.emit())

        self._btn_cal = QToolButton(self)
        self._btn_cal.setText("📅")
        self._btn_cal.setToolTip("Ouvrir le calendrier")
        self._btn_cal.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_cal.clicked.connect(self._open_calendar)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        lay.addWidget(self._line, stretch=1)
        lay.addWidget(self._btn_cal)

    def date_edit(self) -> QLineEdit:
        """Widget interne (surlignage validation, focus)."""
        return self._line

    def is_empty(self) -> bool:
        return len(self._line.digits()) == 0

    def is_complete(self) -> bool:
        return len(self._line.digits()) == 8

    def set_empty(self) -> None:
        self._line.clear()

    def set_from_datetime(self, value: datetime | None) -> None:
        if value is None:
            self.set_empty()
            return
        if isinstance(value, datetime):
            self._line.set_digits(f"{value.day:02d}{value.month:02d}{value.year:04d}")
        else:
            self.set_empty()

    def set_from_text(self, value: str | None) -> None:
        qd = _parse_date_str(value)
        if qd is None:
            self.set_empty()
        else:
            self._line.set_digits(f"{qd.day():02d}{qd.month():02d}{qd.year():04d}")

    def to_qdate(self) -> QDate | None:
        digits = self._line.digits()
        if len(digits) != 8:
            return None
        try:
            d = int(digits[:2])
            m = int(digits[2:4])
            y = int(digits[4:8])
            qd = QDate(y, m, d)
            if not qd.isValid():
                # Accepter une date mois/jour sans ambiguïté, tout en
                # conservant jour/mois pour les dates comme 07/08/2025.
                if m > 12 and 1 <= d <= 12:
                    qd = QDate(y, d, m)
                if not qd.isValid():
                    return None
                self._line.set_digits(f"{qd.day():02d}{qd.month():02d}{qd.year():04d}")
            return qd
        except ValueError:
            return None

    def to_datetime(self) -> datetime | None:
        qd = self.to_qdate()
        if qd is None:
            return None
        return datetime(qd.year(), qd.month(), qd.day())

    def to_storage_str(self) -> str | None:
        dt = self.to_datetime()
        return dt.strftime("%Y-%m-%d") if dt else None

    def text_for_validation(self) -> str:
        if not self.is_complete():
            return self._line.text().strip()
        qd = self.to_qdate()
        if qd is None:
            return self._line.text().strip()
        return f"{qd.day():02d}/{qd.month():02d}/{qd.year()}"

    def _open_calendar(self) -> None:
        from PySide6.QtWidgets import QDialog, QDialogButtonBox, QVBoxLayout

        dlg = QDialog(self.window())
        dlg.setWindowTitle("Choisir une date")
        cal = QCalendarWidget(dlg)
        cal.setGridVisible(True)
        qd = self.to_qdate()
        if qd is not None:
            cal.setSelectedDate(qd)
        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        lay = QVBoxLayout(dlg)
        lay.addWidget(cal)
        lay.addWidget(bb)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        sel = cal.selectedDate()
        self._line.set_digits(f"{sel.day():02d}{sel.month():02d}{sel.year():04d}")
