"""Supported row-filter updates, including clients on older Qt 6 versions."""
from functools import wraps
from contextlib import contextmanager
from PySide6.QtCore import QSortFilterProxyModel


def row_filter_change(method):
    @wraps(method)
    def change(self,*args,**kwargs):
        if getattr(self, '_row_filter_batch_depth', 0):
            self._row_filter_batch_dirty = True
            return method(self,*args,**kwargs)
        begin=getattr(self,"beginFilterChange",None)
        end=getattr(self,"endFilterChange",None)
        modern=callable(begin) and callable(end)
        if modern:begin()
        try:
            return method(self,*args,**kwargs)
        finally:
            if modern:
                end(QSortFilterProxyModel.Direction.Rows)
            else:
                # Qt 6.6..6.9, supported by the project's declared dependency.
                self.invalidateFilter()
    return change


@contextmanager
def batch_row_filters(proxy):
    """Publish related criteria together, with one supported Qt refilter."""
    outer = not getattr(proxy, '_row_filter_batch_depth', 0)
    modern = callable(getattr(proxy, 'beginFilterChange', None)) and callable(getattr(proxy, 'endFilterChange', None))
    if outer:
        proxy._row_filter_batch_dirty = False
        if modern:
            proxy.beginFilterChange()
    proxy._row_filter_batch_depth = getattr(proxy, '_row_filter_batch_depth', 0) + 1
    try:
        yield
    finally:
        proxy._row_filter_batch_depth -= 1
        if outer:
            if modern:
                proxy.endFilterChange(QSortFilterProxyModel.Direction.Rows)
            elif proxy._row_filter_batch_dirty:
                proxy.invalidateFilter()
