from dataclasses import fields, replace
from datetime import datetime
import pytest
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.services.migration_reconciliation import (
    snapshot_from_rows,compare_month_reconciliation,encode_snapshot,decode_snapshot)


def row(**changes):
    values={field.name:None for field in fields(ListeGeneraleRow)}
    values.update(id='1',noms='SYNTHETIC',grade='MDL',structure='U1',position_admin='Présent')
    values.update(changes)
    return ListeGeneraleRow(**values)


def test_canonical_identity_survives_monthly_career_ids_and_order():
    old=snapshot_from_rows('2026-09',[(7,row()),(8,row(noms='OTHER'))])
    new=snapshot_from_rows('2026-09',[(8,row(id='22',noms='OTHER')),(7,row(id='23'))])
    result=compare_month_reconciliation(old,new)
    assert result['matches'] and old.fingerprint==new.fingerprint


def test_reconciliation_shows_exact_changed_fields_and_distribution():
    old=snapshot_from_rows('2026-09',[(7,row()),(8,row(noms='OTHER'))])
    new=snapshot_from_rows('2026-09',[(7,row(grade='ADC')),(9,row(noms='NEW'))])
    result=compare_month_reconciliation(old,new)
    assert not result['matches'] and result['added_agents']==['9'] and result['removed_agents']==['8']
    assert result['changed_agents']==[{'agent_id':'7','fields':{'grade':{'before':'MDL','after':'ADC'}}}]
    assert result['totals_after']['grade']=={'ADC':1,'MDL':1}


def test_dated_snapshot_roundtrip_and_integrity():
    snapshot=snapshot_from_rows('2026-09',[(7,row(date_grade=datetime(2026,9,1)))])
    assert decode_snapshot(encode_snapshot(snapshot))==snapshot
    with pytest.raises(ValueError,match='modifié'):
        decode_snapshot(encode_snapshot(snapshot).replace(b'SYNTHETIC',b'CHANGED'))


def test_duplicate_canonical_agents_and_different_months_are_rejected():
    with pytest.raises(ValueError,match='Plusieurs'):
        snapshot_from_rows('2026-09',[(7,row()),(7,row(id='2'))])
    with pytest.raises(ValueError,match='même mois'):
        compare_month_reconciliation(snapshot_from_rows('2026-09',[]),snapshot_from_rows('2026-10',[]))


def test_cycle_activation_metadata_is_not_restored_as_a_preference():
    from sme_gend.services.configuration_restore import operational_key
    from sme_gend.services.backup_restore_service import _app_config_rows_for_restore
    assert operational_key('SME_DOCUMENT_CYCLE')
    rows=[{'config_key':'sme_document_cycle','payload':{'mode':'legacy'}},
          {'config_key':'detaches_config','payload':{}}]
    assert _app_config_rows_for_restore(rows,preserve_local_accounts=False,preserve_runtime_state=True)==rows[1:]
