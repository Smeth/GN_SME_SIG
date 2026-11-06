from contextlib import contextmanager
from dataclasses import fields
from types import SimpleNamespace
from unittest.mock import MagicMock
import pytest
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.services import access_identity_map as identities
from sme_gend.services.mysql_bundle_export_to_access import ExportListeRow,_write_liste_generale


def row(key):
    return ListeGeneraleRow(**({field.name:None for field in fields(ListeGeneraleRow)}|dict(id="1",noms="SYNTHETIC",access_join_id=key)))


def test_export_assigns_numeric_ids_without_replacing_external_ids_or_opening_other_connections():
    items=[ExportListeRow(2,row("GEN-APP-"+"a"*32),"GEN-APP-"+"a"*32),ExportListeRow(1,row("42"),"42")]
    cursor=MagicMock()
    repo=MagicMock()
    written,generated,mapping=_write_liste_generale(cursor,items,access_repo=repo)
    assert (written,generated)==(2,1)
    assert mapping=={1:"42",2:"43"}
    assert [call.args[1][0] for call in cursor.execute.call_args_list]==[42,43]
    assert items[0].row.access_join_id=="GEN-APP-"+"a"*32
    repo.insert_row.assert_not_called()


def test_real_access_insert_failure_is_propagated_without_reassigning_its_id():
    cursor=MagicMock();cursor.execute.side_effect=RuntimeError("synthetic write failure")
    repo=MagicMock()
    with pytest.raises(RuntimeError,match="write failure"):
        _write_liste_generale(cursor,[ExportListeRow(1,row("42"),"42")],access_repo=repo)
    repo.insert_row.assert_not_called()


@pytest.mark.parametrize("pairs", [[("1","../unsafe")],[("","GEN-valid")],[("1","GEN-valid"),("1","GEN-other")],[("1","GEN-valid"),("2","GEN-valid")]])
def test_malformed_or_ambiguous_identity_map_is_rejected(pairs):
    with pytest.raises(ValueError):identities.validate_identity_pairs(pairs)


@pytest.mark.parametrize("table",sorted(identities.PERSON_TABLES))
def test_every_person_satellite_uses_the_internal_key(table):
    mapping={"43":"GEN-APP-"+"a"*32}
    raw=[(43,"payload"),(42,"external")]
    assert identities.map_person_query(f"SELECT ID, Field FROM [{table}]",raw,mapping)==[(mapping["43"],"payload"),(42,"external")]
    assert identities.map_person_query(f"SELECT TOP 1 * FROM [{table}]",raw,mapping)==raw


def test_old_archives_without_metadata_keep_original_ids():
    cursor=MagicMock();cursor.tables.return_value=[]
    conn=SimpleNamespace(cursor=lambda:cursor)
    assert identities.read_identity_map(conn)=={}
    cursor.execute.assert_not_called();cursor.close.assert_called_once()


def test_linked_identity_table_is_not_read():
    cursor=MagicMock();cursor.tables.return_value=[SimpleNamespace(table_name=identities.TABLE,table_type="LINK")]
    with pytest.raises(ValueError,match="locale"):
        identities.read_identity_map(SimpleNamespace(cursor=lambda:cursor))
    cursor.execute.assert_not_called()


def test_exclusive_file_publication_preserves_a_concurrent_destination(tmp_path):
    from sme_gend.services.safe_paths import atomic_destination
    destination=tmp_path/"export.mdb"
    with pytest.raises(FileExistsError):
        with atomic_destination(destination,temporary_suffix=".mdb",replace_existing=False) as staged:
            staged.write_bytes(b"NEW COMPLETE EXPORT")
            destination.write_bytes(b"OTHER VALID EXPORT")
    assert destination.read_bytes()==b"OTHER VALID EXPORT"
    assert not list(tmp_path.glob(".sme-*"))
