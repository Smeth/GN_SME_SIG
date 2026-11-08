"""Read-only, canonical-agent reconciliation before enabling a new month cycle."""
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256
import json


EXCLUDED_FIELDS = frozenset({'id','access_join_id','access_profile_fields'})


def _value(value):
    if isinstance(value,(date,datetime)):
        return value.isoformat()
    if isinstance(value,Decimal):
        return str(value)
    return value


def _digest(value):
    return sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,allow_nan=False,
        separators=(',',':'),default=_value).encode('utf-8')).hexdigest()


@dataclass(frozen=True)
class MonthReconciliationSnapshot:
    month: str
    rows: dict
    totals: dict
    fingerprint: str


def snapshot_from_rows(month, rows):
    """Rows are (canonical agent ID, ListeGeneraleRow), never Access identities."""
    normalized={}
    totals={field:Counter() for field in ('structure','grade','position_admin')}
    for agent_id,row in rows:
        key=str(agent_id)
        if key in normalized:
            raise ValueError('Plusieurs situations pour le même agent dans le rapprochement.')
        if not key or not key.isdigit() or int(key)<1:
            raise ValueError('Identifiant agent canonique invalide.')
        values={name:_value(value) for name,value in asdict(row).items() if name not in EXCLUDED_FIELDS}
        normalized[key]=values
        for field,counter in totals.items():counter[str(values.get(field) or '')]+=1
    grouped={field:dict(sorted(counter.items())) for field,counter in totals.items()}
    return MonthReconciliationSnapshot(month,normalized,grouped,_digest(normalized))


def capture_month_reconciliation(settings,month):
    from sme_gend.services.sme_month_closure import canonical_sme_mois_yyyy_mm
    from sme_gend.repositories.mysql_agent_repository import _select_liste,_row_to_liste
    from sme_gend.repositories.mysql_connection import MySQLConnection,mysql_read_snapshot,has_import_transaction,has_read_snapshot
    from contextlib import nullcontext
    context=mysql_read_snapshot(settings)
    if has_import_transaction(settings) and not has_read_snapshot(settings):
        from sme_gend.services.publication_write_barrier import assert_exclusive_publication_barrier
        assert_exclusive_publication_barrier(settings)
        context=nullcontext()
    month=canonical_sme_mois_yyyy_mm(month)
    if not month:
        raise ValueError('Mois de rapprochement invalide.')
    with context:
        with MySQLConnection(settings).connect(read_only=True) as connection:
            with connection.cursor() as cursor:
                sql,params=_select_liste(month,settings=settings,cursor=cursor)
                sql=sql.replace('c.id AS carriere_id','a.id AS canonical_agent_id, c.id AS carriere_id',1)
                cursor.execute(sql,params)
                rows=[(int(row[0]),_row_to_liste(row[1:])) for row in cursor.fetchall()]
    return snapshot_from_rows(month,rows)


def compare_month_reconciliation(before,after):
    if before.month!=after.month:
        raise ValueError('Le rapprochement doit comparer le même mois.')
    old,new=before.rows,after.rows
    added=sorted(new.keys()-old.keys(),key=int)
    removed=sorted(old.keys()-new.keys(),key=int)
    changed=[]
    for agent_id in sorted(old.keys()&new.keys(),key=int):
        fields={name:{'before':old[agent_id].get(name),'after':new[agent_id].get(name)}
            for name in old[agent_id].keys()|new[agent_id].keys()
            if old[agent_id].get(name)!=new[agent_id].get(name)}
        if fields:changed.append({'agent_id':agent_id,'fields':fields})
    return {'month':before.month,'matches':not(added or removed or changed),
        'counts':{'before':len(old),'after':len(new)},'added_agents':added,'removed_agents':removed,
        'changed_agents':changed,'totals_before':before.totals,'totals_after':after.totals,
        'fingerprint_before':before.fingerprint,'fingerprint_after':after.fingerprint}


def encode_snapshot(snapshot):
    return json.dumps({'format':'sgi-dpf-reconciliation-v1',**asdict(snapshot)},
        ensure_ascii=False,sort_keys=True,indent=2).encode('utf-8')


def decode_snapshot(payload):
    raw=json.loads(payload)
    if raw.pop('format',None)!='sgi-dpf-reconciliation-v1':
        raise ValueError('Format de rapprochement non pris en charge.')
    snapshot=MonthReconciliationSnapshot(**raw)
    if snapshot.fingerprint!=_digest(snapshot.rows):
        raise ValueError('L’instantané de rapprochement a été modifié.')
    return snapshot
