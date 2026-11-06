"""Optional profile portability, missing-field semantics and safe Access clearing."""
from dataclasses import fields,replace
from types import SimpleNamespace
from unittest.mock import MagicMock
import pytest
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.services import access_agent_profile as profile
from sme_gend.services import mysql_bundle_export_to_access as export


def row(**values):
    return ListeGeneraleRow(**({field.name:None for field in fields(ListeGeneraleRow)}|dict(id='42',noms='SYNTHETIC')|values))


def test_absent_legacy_fields_do_not_mean_clear_but_explicit_blanks_do():
    old=row()
    assert not any(profile.profile_field_supplied(old,field) for field in profile.FIELDS)
    declared=replace(old,access_profile_fields=profile.FIELDS)
    assert all(profile.profile_field_supplied(declared,field) for field in profile.FIELDS)
    assert profile.profile_field_supplied(replace(old,sexe='F'),'sexe')


def test_old_nineteen_column_archives_still_decode_without_extension():
    values=[42,'SYNTHETIC']+[None]*17
    result=profile.decode_rows([values],(),{},complete=True)[0]
    assert result.id=='42' and result.sexe is None and result.access_profile_fields==()


def test_optional_columns_and_metadata_are_both_supported():
    cur=MagicMock();cur.columns.return_value=[SimpleNamespace(column_name=name) for name in ('Id','noms','Sexe','Telephone')]
    query,available=profile.profile_select(cur,'SELECT Id,noms FROM ListeGenerale')
    assert '[Sexe]' in query and '[Telephone]' in query and available==('sexe','telephone')
    values=[42,'SYNTHETIC']+[None]*17+['F','SYNTHETIC PHONE']
    result=profile.decode_rows([values],available,{},complete=True)[0]
    assert result.sexe=='F' and result.telephone=='SYNTHETIC PHONE'
    assert result.access_profile_fields==available
    overridden=profile.decode_rows([values],available,{'42':('M',None,'SYNTHETIC ADDRESS')},complete=True)[0]
    assert (overridden.sexe,overridden.telephone,overridden.adresse_residence)==('M',None,'SYNTHETIC ADDRESS')


@pytest.mark.parametrize('invalid', ['orphan','duplicate','invalid_sex','phone_too_long','address_too_long','linked'])
def test_corrupt_or_remote_profile_extensions_are_explicitly_rejected(invalid):
    cur=MagicMock();cur.tables.return_value=[SimpleNamespace(table_name=profile.TABLE,table_type='LINK' if invalid=='linked' else 'TABLE')]
    entries=[('42','F','PHONE','ADDRESS')]
    if invalid=='duplicate':entries*=2
    elif invalid=='invalid_sex':entries=[('42','unknown',None,None)]
    elif invalid=='phone_too_long':entries=[('42','F','x'*65,None)]
    elif invalid=='address_too_long':entries=[('42','F',None,'x'*513)]
    cur.fetchall.return_value=entries
    if invalid=='orphan':
        with pytest.raises(ValueError):profile.decode_rows([[1,'SYNTHETIC']+[None]*17],(),{'42':('F',None,None)},complete=True)
    else:
        with pytest.raises(ValueError):profile.read_profiles(SimpleNamespace(cursor=lambda:cur))
    cur.close.assert_called_once() if invalid!='orphan' else None
    if invalid=='linked':cur.execute.assert_not_called()


def test_single_row_fetch_can_ignore_other_valid_profiles_in_the_same_archive():
    result=profile.decode_rows([[42,'SYNTHETIC']+[None]*17],(),{'42':('F',None,None),'43':('M',None,None)})
    assert result[0].sexe=='F'


def test_failed_clear_of_an_existing_table_must_abort_instead_of_keeping_old_records():
    cur=MagicMock();cur.tables.return_value=[SimpleNamespace(table_name='TabMission',table_type='TABLE')]
    cur.execute.side_effect=OSError('synthetic clear refused')
    with pytest.raises(RuntimeError,match='vider|annul'):export._clear_access_tables(cur)


def test_linked_template_table_is_never_deleted():
    cur=MagicMock();cur.tables.return_value=[SimpleNamespace(table_name='TabMission',table_type='LINK')]
    with pytest.raises(ValueError,match='locale'):export._clear_access_tables(cur)
    cur.execute.assert_not_called()
