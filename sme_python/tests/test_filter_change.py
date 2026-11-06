"""Filter updates declare changed rows after modifying parameters."""
from types import SimpleNamespace
from sme_gend.ui.filter_change import row_filter_change


def test_modern_filter_updates_have_correct_order(monkeypatch):
    from sme_gend.ui import filter_change
    rows = object()
    monkeypatch.setattr(filter_change, "QSortFilterProxyModel", SimpleNamespace(Direction=SimpleNamespace(Rows=rows)))
    calls=[];proxy=SimpleNamespace(value='old')
    proxy.beginFilterChange=lambda:calls.append(('begin',proxy.value))
    proxy.endFilterChange=lambda direction:calls.append(('end',proxy.value,direction))
    @row_filter_change
    def set_value(self,value):self.value=value
    set_value(proxy,'new')
    assert calls==[('begin','old'),('end','new',rows)]


def test_older_supported_qt_uses_legacy_invalidation():
    calls=[];proxy=SimpleNamespace(value='old',invalidateFilter=lambda:calls.append('invalidate'))
    @row_filter_change
    def set_value(self,value):self.value=value
    set_value(proxy,'new')
    assert proxy.value=='new' and calls==['invalidate']
