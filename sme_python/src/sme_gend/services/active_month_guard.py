"""Coherent month snapshots and serialized, commit-bound period transitions."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass,field
import json
import weakref
from uuid import uuid4

from sme_gend.repositories.mysql_connection import MySQLConnection,mysql_database_key
from sme_gend.repositories.reference_collection_guard import fingerprint
from sme_gend.repositories.reference_write_guard import ReferenceConflictError
from sme_gend.services.shared_reference_write import shared_data_write,shared_reference_write,mark_shared_data_write

KEY_MONTH_REVISION = "active_month_revision"


@dataclass(frozen=True)
class ActiveMonthSnapshot:
    database: tuple = field(repr=False)
    rows: tuple = field(repr=False)
    revision: str = field(repr=False)

    @property
    def active_month(self):
        from sme_gend.services.sme_workspace_service import _is_ouvert
        from sme_gend.services.sme_month_closure import canonical_sme_mois_yyyy_mm
        months=set()
        for row in self.rows:
            if _is_ouvert(row[2]):
                month=canonical_sme_mois_yyyy_mm(str(row[1]))
                if not month:
                    raise ValueError("Le mois ouvert est invalide. Vérifiez les périodes avant de continuer.")
                months.add(month)
        if len(months)>1:
            raise ValueError("Plusieurs mois sont ouverts dans MySQL. Vérifiez les périodes avant de continuer.")
        return next(iter(months),"")


@dataclass
class PeriodChange:
    database: tuple
    before: ActiveMonthSnapshot
    connection: object = field(repr=False)
    result: ActiveMonthSnapshot | None = None


_period_change=ContextVar("sme_period_change",default=None)


def require_period_scope(settings):
    scope=_period_change.get()
    if scope is None or scope.database!=mysql_database_key(settings):
        raise ValueError("Transition interne sans contrôle de période. Rechargez avant de continuer.")
    return scope


def _json(value):
    return json.loads(value) if isinstance(value,(str,bytes)) else value


def _snapshot(settings,rows,marker):
    rows=tuple(sorted((tuple(row) for row in rows),key=lambda row:row[0]))
    raw_marker=marker
    marker=_json(marker)
    if raw_marker is not None and (not isinstance(marker,dict) or not isinstance(marker.get('token'),str)):
        raise ValueError("Jeton du mois actif invalide. Aucune période n'a été remplacée.")
    snapshot=ActiveMonthSnapshot(mysql_database_key(settings),rows,fingerprint([rows,marker]))
    snapshot.active_month  # Refuse ambiguous state, including at load time.
    return snapshot


def load_active_month_snapshot(settings,*,current=False):
    scope=_period_change.get()
    current=current or scope is not None and scope.database==mysql_database_key(settings)
    try:
        with MySQLConnection(settings).connect(read_only=True) as connection:
            with connection.cursor() as cursor:
                if current:
                    # Only callers owning the common write gate request a
                    # current read; this also bypasses an older RR snapshot.
                    cursor.execute("SELECT id,annee_mois,statut,codsme FROM sme_periode ORDER BY id FOR UPDATE")
                    rows=cursor.fetchall()
                    cursor.execute("SELECT payload FROM app_config WHERE config_key=%s FOR UPDATE",(KEY_MONTH_REVISION,))
                    found=cursor.fetchone();marker=found[0] if found else None
                else:
                    # One statement: period statuses and their transition token
                    # must describe the same committed snapshot.
                    cursor.execute("SELECT (SELECT payload FROM app_config WHERE config_key=%s), "
                        "COALESCE((SELECT JSON_ARRAYAGG(JSON_ARRAY(id,annee_mois,statut,codsme)) FROM sme_periode),JSON_ARRAY())",
                        (KEY_MONTH_REVISION,))
                    marker,rows=cursor.fetchone();rows=_json(rows)
    except Exception as exc:
        raise RuntimeError("Impossible de charger l'état du mois actif. Aucune transition n'a été effectuée.") from exc
    return _snapshot(settings,rows,marker)


def require_month_snapshot(settings,expected):
    if not isinstance(expected,ActiveMonthSnapshot) or expected.database!=mysql_database_key(settings):
        raise ValueError("Version du mois actif indisponible. Rechargez les périodes avant de continuer.")
    return expected


def check_month_snapshot(settings,expected,current):
    require_month_snapshot(settings,expected)
    if expected.revision!=current.revision:
        raise ReferenceConflictError("Le mois actif ou les périodes ont changé depuis leur chargement. "
            "Rechargez les périodes avant de reprendre ce changement de mois.")


@contextmanager
def period_state_write(settings,*,expected=None,reason="active_month",mois=None):
    if expected is not None:require_month_snapshot(settings,expected)
    active=_period_change.get()
    with shared_data_write(settings,reason=reason,mois=mois):
        with shared_reference_write(settings):
            if active is not None and active.database==mysql_database_key(settings):
                if expected is not None:check_month_snapshot(settings,expected,load_active_month_snapshot(settings,current=True))
                yield active
                return
            before=load_active_month_snapshot(settings,current=True)
            if expected is not None:check_month_snapshot(settings,expected,before)
            with MySQLConnection(settings).connect() as connection:
                change=PeriodChange(mysql_database_key(settings),before,weakref.ref(connection))
                token=_period_change.set(change)
                try:
                    yield change
                    after=load_active_month_snapshot(settings,current=True)
                    if after.rows!=before.rows:
                        with connection.cursor() as cursor:
                            cursor.execute("INSERT INTO app_config(config_key,payload) VALUES(%s,%s) "
                                "ON DUPLICATE KEY UPDATE payload=VALUES(payload)",
                                (KEY_MONTH_REVISION,json.dumps({'token':uuid4().hex})))
                        mark_shared_data_write(True)
                        after=load_active_month_snapshot(settings,current=True)
                    change.result=after
                finally:
                    _period_change.reset(token)


def preserved_import_period(conn,mois,*,codsme=None,statut=None,selected_month=None):
    """An archive cannot replace live period statuses (user-approved policy)."""
    from sme_gend.services.mysql_import_period import ensure_sme_periode,normalize_import_mois
    from sme_gend.services.sme_workspace_service import _is_ouvert
    scope=_period_change.get()
    if scope is None:raise RuntimeError("Import de périodes sans transaction de contrôle du mois actif.")
    ym=normalize_import_mois(mois)
    from sme_gend.services.sme_month_closure import canonical_sme_mois_yyyy_mm
    original={canonical_sme_mois_yyyy_mm(str(row[1])):row[2] for row in scope.before.rows}
    if ym in original:
        statut=original[ym]
    elif _is_ouvert(statut) and (scope.before.active_month or selected_month!=ym):
        # New historical periods must not introduce a second active month.
        statut="Fermé"
    return ensure_sme_periode(conn,ym,codsme=codsme,statut=statut)



def list_open_months_for_recovery(settings):
    """Read the open periods without requiring a valid single-active snapshot."""
    from sme_gend.repositories.mysql_connection import MySQLConnection
    from sme_gend.services.sme_month_closure import canonical_sme_mois_yyyy_mm
    with MySQLConnection(settings).connect(read_only=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT annee_mois,codsme,statut FROM sme_periode WHERE LOWER(TRIM(statut)) IN ('ouvert','open','ouverte') ORDER BY annee_mois,codsme")
            rows=cursor.fetchall()
    groups={}
    for month,code,status in rows:
        ym=canonical_sme_mois_yyyy_mm(str(month or ''))
        if not ym:
            raise ValueError("Une période ouverte a un mois invalide. Corrigez-la avec l'administrateur MySQL avant de continuer.")
        group=groups.setdefault(ym,[])
        group.append(str(code or month or '').strip())
    return tuple((month,tuple(codes)) for month,codes in sorted(groups.items()))


def recover_multiple_open_months(settings, target_month, *, expected_open_months, actor=None):
    """Keep one explicitly chosen month open and close the other open periods atomically."""
    from sme_gend.services.auth_guard import session_has_admin_role
    if not session_has_admin_role():
        raise PermissionError("La réparation des mois ouverts est réservée aux administrateurs.")
    from sme_gend.services.sme_month_closure import canonical_sme_mois_yyyy_mm
    target=canonical_sme_mois_yyyy_mm(str(target_month or ''))
    expected={canonical_sme_mois_yyyy_mm(str(month or '')) for month in expected_open_months}
    if None in expected or len(expected)<2:
        raise ValueError("Rechargez la liste des mois ouverts avant de réparer.")
    if not target:
        raise ValueError("Choisissez un mois valide parmi les périodes ouvertes.")
    from sme_gend.repositories.mysql_connection import MySQLConnection
    from sme_gend.services.shared_reference_write import shared_data_write,shared_reference_write,mark_shared_data_write
    from sme_gend.services.auth_context import resolve_write_actor
    from sme_gend.services.mysql_import_period import refresh_est_actuelle_flags,refresh_carriere_segment_dates
    from sme_gend.repositories.mysql_connection import after_mysql_commit
    from uuid import uuid4
    changed_rows=0
    closed_months=[]
    with shared_data_write(settings,reason="active_month_recovery",mois=target):
        with shared_reference_write(settings):
            with MySQLConnection(settings).connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT id,annee_mois,statut,codsme FROM sme_periode ORDER BY id FOR UPDATE")
                    rows=cursor.fetchall()
                    open_by_month={}
                    open_statuses={"ouvert","open","ouverte"}
                    for row_id,month,status,code in rows:
                        if str(status or "").strip().casefold() in open_statuses:
                            ym=canonical_sme_mois_yyyy_mm(str(month or ''))
                            if not ym:
                                raise ValueError("Une période ouverte a un mois invalide. Aucune modification effectuée.")
                            open_by_month.setdefault(ym,[]).append((row_id,code))
                    if set(open_by_month)!=expected:
                        raise ValueError("Les périodes ouvertes ont changé depuis leur affichage. Rechargez avant de réparer.")
                    if target not in open_by_month:
                        raise ValueError("Le mois choisi n'est plus ouvert. Rechargez les périodes.")
                    closed_months=sorted(month for month in open_by_month if month!=target)
                    for month in closed_months:
                        for row_id,_code in open_by_month[month]:
                            cursor.execute("UPDATE sme_periode SET statut='Fermé' WHERE id=%s AND LOWER(TRIM(statut)) IN ('ouvert','open','ouverte')",(row_id,))
                            if cursor.rowcount!=1:
                                raise RuntimeError("Une période a changé pendant la réparation. Toutes les modifications ont été annulées.")
                            changed_rows+=1
                    refresh_est_actuelle_flags(connection,target)
                    refresh_carriere_segment_dates(connection)
                    cursor.execute("INSERT INTO app_config(config_key,payload) VALUES(%s,%s) ON DUPLICATE KEY UPDATE payload=VALUES(payload)",(KEY_MONTH_REVISION,json.dumps({'token':uuid4().hex})))
                    cursor.execute("SELECT annee_mois FROM sme_periode WHERE LOWER(TRIM(statut)) IN ('ouvert','open','ouverte')")
                    verified={canonical_sme_mois_yyyy_mm(str(row[0] or '')) for row in cursor.fetchall()}
                    if verified!={target}:
                        raise RuntimeError("Vérification finale échouée. Toutes les modifications ont été annulées.")
                    from sme_gend.services.audit_log import append_audit_event
                    append_audit_event(action="sme_active_month_recovery",sme_mois_context=target,actor=resolve_write_actor(actor),
                        mois_actif=target,mois_fermes=closed_months,lignes_fermees=changed_rows)
                    mark_shared_data_write(True)
    from sme_gend.services.sme_editing_policy import invalidate_editing_state_cache
    invalidate_editing_state_cache()
    return {"active_month":target,"closed_months":tuple(closed_months),"rows_closed":changed_rows,"already_resolved":False}
