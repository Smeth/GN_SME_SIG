from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from PySide6.QtWidgets import QApplication,QDialog,QTableWidgetItem,QMessageBox
from PySide6.QtCore import Qt
from sme_gend.config.settings import Settings
from sme_gend.models.subdiv_hierarchy import SubdivHierarchyNode
from sme_gend.repositories.reference_write_guard import ReferenceConflictError
from sme_gend.ui.views import subdivisions_hierarchy_view as hierarchy
from sme_gend.ui.views import referentiel_coherence_view as coherence
from sme_gend.repositories.mysql_coherence_repository import SubdivVariantRow,StructureVariantRow


@pytest.fixture
def ui(monkeypatch,tmp_path):
    app=QApplication.instance() or QApplication([])
    for name in ('critical','warning','information'):monkeypatch.setattr(QMessageBox,name,Mock())
    for module in (hierarchy,coherence):monkeypatch.setattr(module,'evaluate_editing_state',lambda *a,**kw:SimpleNamespace(locked=False,detail=''))
    monkeypatch.setattr(hierarchy,'confirm_yes_no',lambda *a,**kw:True)
    settings=Settings(database_path=tmp_path/'unused.mdb',mysql_database='synthetic_a')
    svc=SimpleNamespace(_settings=settings)
    monkeypatch.setattr(hierarchy,'SubdivHierarchyService',lambda *a:svc)
    monkeypatch.setattr(coherence,'ReferentielCoherenceService',lambda *a:svc)
    return app,settings,svc


def test_hierarchy_dialog_keeps_displayed_version_and_failed_draft(ui,monkeypatch):
    app,settings,svc=ui;view=hierarchy.SubdivisionsHierarchyView(SimpleNamespace(settings=settings))
    node=SubdivHierarchyNode(3,2,'T','ORIGINAL',None,3);view._roots=[node]
    old=object();view._snapshot=old;monkeypatch.setattr(view,'_selected_node',lambda:(3,3))
    calls=[]
    def update(*a,**kw):calls.append(kw['expected']);raise ReferenceConflictError('synthetic conflict')
    svc.update_node=update
    class Dialog:
        def __init__(self,*a,**kw):self.kw=kw
        def exec(self):view._snapshot=object();return QDialog.DialogCode.Accepted
        def values(self):return 'T','MY DRAFT',9.0
    monkeypatch.setattr(hierarchy,'_NodeDialog',Dialog)
    view._on_edit()
    from sme_gend.repositories.mysql_connection import mysql_database_key
    assert calls==[old] and view._node_drafts[(mysql_database_key(settings),3)]==('T','MY DRAFT',9.0)
    assert node.libelle=='ORIGINAL'
    view.deleteLater();app.processEvents()


@pytest.mark.parametrize('text',['nan','inf','-inf','abc'])
def test_node_dialog_invalid_priority_does_not_close(ui,text):
    app,settings,svc=ui
    dialog=hierarchy._NodeDialog(None,title='Synthetic',code='T',libelle='SYNTHETIC',pri_out=text)
    dialog.accept();assert dialog.result()==QDialog.DialogCode.Rejected
    QMessageBox.warning.assert_called_once();dialog.deleteLater();app.processEvents()


@pytest.mark.parametrize('kind',['subdivision','structure'])
def test_merge_confirmation_cannot_upgrade_its_snapshot_and_keeps_selection(ui,monkeypatch,kind):
    app,settings,svc=ui;view=coherence.ReferentielCoherenceView(SimpleNamespace(settings=settings))
    old=object();calls=[]
    def apply(*a,**kw):calls.append(kw['expected']);raise ReferenceConflictError('synthetic conflict')
    if kind=='subdivision':
        view._sub_snapshot=old;view._subdiv_rows=[SubdivVariantRow('A',1,1,True),SubdivVariantRow('B',2,1,True)]
        view._sub_table.setRowCount(2)
        for i in range(2):
            item=QTableWidgetItem();item.setCheckState(Qt.CheckState.Checked);view._sub_table.setItem(i,0,item)
        view._sub_target.setText('MY DRAFT');monkeypatch.setattr(view,'_current_structure',lambda:(1,'KEEP'))
        svc.apply_subdiv_merge=apply
        def confirm(*a,**kw):view._sub_snapshot=object();return True
        monkeypatch.setattr(coherence,'confirm_yes_no',confirm);view._apply_subdiv()
        assert view._sub_target.text()=='MY DRAFT' and view._checked_subdiv_indices()==[0,1]
    else:
        view._struct_snapshot=old;view._struct_rows=[StructureVariantRow(1,'KEEP','KEEP',1,'referentiel'),StructureVariantRow(2,'SOURCE','SOURCE',1,'referentiel')]
        view._struct_keeper.addItem('KEEP',1);view._struct_code.setText('MY DRAFT')
        view._struct_table.setRowCount(2);item=QTableWidgetItem();item.setCheckState(Qt.CheckState.Checked);view._struct_table.setItem(1,0,item)
        svc.apply_structure_merge=apply
        def confirm(*a,**kw):view._struct_snapshot=object();return True
        monkeypatch.setattr(coherence,'confirm_yes_no',confirm);view._apply_structure()
        assert view._struct_code.text()=='MY DRAFT' and view._checked_struct_indices()==[1]
    assert calls==[old]
    view.deleteLater();app.processEvents()


@pytest.mark.parametrize('module',[hierarchy,coherence])
def test_maintenance_dialog_refuses_old_database_after_rebind(ui,module):
    app,settings,svc=ui;reference=SimpleNamespace(settings=settings)
    view=hierarchy.SubdivisionsHierarchyView(reference) if module is hierarchy else coherence.ReferentielCoherenceView(reference)
    if module is hierarchy:view._snapshot=object()
    reference.settings=replace(settings,mysql_database='synthetic_b')
    assert not (view._check_editable() if module is hierarchy else view._ensure_editable())
    view.deleteLater();app.processEvents()


def test_failed_tree_reload_disables_old_writes_without_substituting_defaults(ui):
    app,settings,svc=ui;view=hierarchy.SubdivisionsHierarchyView(SimpleNamespace(settings=settings))
    old=[SubdivHierarchyNode(3,2,'T','ORIGINAL',None,3)];view._roots=old;view._snapshot=object()
    def fail():raise RuntimeError('synthetic read failure')
    svc.fetch_tree=fail;view.reload(force=True)
    assert view._roots is old and view._snapshot is None and not view._check_editable()
    view.deleteLater();app.processEvents()


def test_harmonization_initial_empty_load_finishes_without_invalid_lifecycle_call(ui):
    from sme_gend.repositories.hierarchy_merge_guard import VersionedRows
    app,settings,svc=ui;svc.structure_variants=lambda:VersionedRows([],object())
    view=coherence.ReferentielCoherenceView(SimpleNamespace(settings=settings,structures=lambda:[]))
    view.on_page_activated()
    assert view._view_data.is_ready()
    assert view._struct_snapshot is not None
    view.deleteLater();app.processEvents()


def test_harmonization_failed_structure_choices_are_explicit_and_remain_stale(ui):
    from sme_gend.repositories.hierarchy_merge_guard import VersionedRows
    app,settings,svc=ui;svc.structure_variants=lambda:VersionedRows([],object())
    def fail():raise RuntimeError('synthetic choice failure')
    view=coherence.ReferentielCoherenceView(SimpleNamespace(settings=settings,structures=fail))
    view._sub_snapshot=object();view.on_page_activated()
    assert view._view_data.needs_load() and view._sub_snapshot is None
    QMessageBox.critical.assert_called_once()
    view.deleteLater();app.processEvents()



@pytest.mark.parametrize('kind',['node','subdivision','structure','propagation'])
def test_backend_maintenance_permission_is_checked_before_sql(ui,monkeypatch,kind):
    from sme_gend.repositories.mysql_subdiv_hierarchy_repository import MySQLSubdivHierarchyRepository
    from sme_gend.repositories.mysql_coherence_repository import MySQLCoherenceRepository
    from sme_gend.services.subdiv_hierarchy_service import SubdivHierarchyService
    from sme_gend.repositories.mysql_connection import MySQLConnection
    app,settings,svc=ui
    monkeypatch.setattr('sme_gend.services.auth_guard.session_has_permission',lambda permission:False)
    def forbidden(*a,**kw):raise AssertionError('SQL must not run')
    monkeypatch.setattr(MySQLConnection,'connect',forbidden)
    repo=MySQLSubdivHierarchyRepository.__new__(MySQLSubdivHierarchyRepository);repo._settings=settings
    merge=MySQLCoherenceRepository.__new__(MySQLCoherenceRepository);merge._settings=settings
    service=SubdivHierarchyService.__new__(SubdivHierarchyService);service._settings=settings
    with pytest.raises(PermissionError,match='structures.codes'):
        if kind=='node':repo.update_node(4,'T','T')
        elif kind=='subdivision':merge.apply_subdiv_merge(1,'S',keeper_rsa_id=1,target_libelle='X',merge_rsa_ids=[1,2],merge_libelles=['A','B'])
        elif kind=='structure':merge.apply_structure_merge(keeper_structure_id=1,target_code='S',target_designation=None,absorb_structure_ids=[2],absorb_text_codes=[])
        else:service.propagate_leaf_rsa(4)
