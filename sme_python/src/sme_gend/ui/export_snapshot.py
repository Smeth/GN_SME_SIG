"""Capture only the rows actually visible, in the table's current order."""
from collections import defaultdict,deque
from copy import deepcopy
from PySide6.QtCore import Qt

def visible_export_rows(table, rows, *, key=None, key_column=0):
    rows = list(rows)
    buckets = defaultdict(deque)
    if key is not None:
        for row in rows:
            buckets[str(key(row))].append(row)
    selected = []
    for index in range(table.rowCount()):
        if table.isRowHidden(index):
            continue
        item = table.item(index, key_column)
        identity = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        if key is None:
            if not isinstance(identity, int) or isinstance(identity, bool) or not 0 <= identity < len(rows):
                raise ValueError("La liste affichée a changé. Actualisez-la avant l’export.")
            selected.append(rows[identity])
        else:
            candidates = buckets.get(str(identity))
            if identity is None or not candidates:
                raise ValueError("La liste affichée a changé. Actualisez-la avant l’export.")
            selected.append(candidates.popleft())
    return deepcopy(selected)
