from PySide6.QtWidgets import QApplication, QTreeWidget, QTreeWidgetItem
from PySide6.QtCore import QCoreApplication, QEvent
from sme_gend.ui.sidebar_navigation import install_sidebar_accordion


def test_single_group_and_navigation_reveals_selected_page():
    app = QApplication.instance() or QApplication([])
    tree = QTreeWidget()
    first = QTreeWidgetItem(tree, ['Personnel'])
    second = QTreeWidgetItem(tree, ['Administration'])
    page1 = QTreeWidgetItem(first, ['Edition SME'])
    page2 = QTreeWidgetItem(second, ['Utilisateurs'])
    install_sidebar_accordion(tree)
    first.setExpanded(True)
    second.setExpanded(True)
    assert second.isExpanded() and not first.isExpanded()
    tree.setCurrentItem(page1)
    assert first.isExpanded() and not second.isExpanded()
    assert page1.font(0).bold()
    # Clicking a folder changes keyboard focus and may clear selection, while
    # the content page remains the same.
    first.setExpanded(False)
    tree.setCurrentItem(first)
    tree.clearSelection()
    assert page1.font(0).bold()
    assert not first.isExpanded()
    first.setExpanded(True)
    assert page1.isSelected() and page1.font(0).bold()
    tree.setCurrentItem(page2)
    assert second.isExpanded() and not first.isExpanded()
    assert page2.font(0).bold() and not page1.font(0).bold()
    second.setExpanded(False)
    assert not first.isExpanded() and not second.isExpanded()
    tree.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
