"""Tâches workflow partagées (MySQL workflow_task)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any

from sme_gend.config.settings import Settings
from sme_gend.repositories.app_config_repository import AppConfigRepository
from sme_gend.services.auth_context import resolve_audit_actor
from sme_gend.repositories.mysql_connection import has_import_transaction, mysql_initialization_lock
from sme_gend.services.shared_reference_write import shared_data_write, mark_shared_data_write
from sme_gend.services.data_sync import KEY_DATA_REVISION
from sme_gend.repositories.mysql_connection import mysql_database_key
from sme_gend.repositories.reference_write_guard import ReferenceConflictError
import re

WORKFLOW_REVISION_SQL = "SHA2(CAST(JSON_ARRAY(id,mois,task_kind,status,priority,title,detail,assigned_role,created_by,created_at,resolved_by,resolved_at) AS CHAR),256)"


class WorkflowTaskConflictError(ReferenceConflictError):
    """The task was edited since its visible snapshot."""


_log = logging.getLogger(__name__)


class WorkflowTaskError(RuntimeError):
    """Explicit storage failure; False/empty collections are real business results."""


@dataclass(slots=True)
class WorkflowTaskRow:
    id: int
    mois: str | None
    task_kind: str
    status: str
    priority: str
    title: str
    detail: str
    assigned_role: str | None
    created_by: str | None
    created_at: str | None = None
    write_token: str | None = field(default=None,repr=False,compare=False)
    _write_database: object | None = field(default=None,repr=False,compare=False)
    resolved_by: str | None = None
    resolved_at: str | None = None


class WorkflowTaskService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._repo = AppConfigRepository(settings)
        self._table_ready = False

    def _ensure_table(self) -> bool:
        from sme_gend.config.settings import project_root
        from sme_gend.repositories.mysql_common import _split_sql_statements
        from pymysql.constants.SERVER_STATUS import SERVER_STATUS_IN_TRANS

        if self._table_ready:
            return True
        def exists():
            # A read must not commit an unrelated untracked write connection.
            with self._repo._mc.connect(read_only=True) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1 FROM information_schema.tables WHERE table_schema=DATABASE() AND table_name='workflow_task' LIMIT 1")
                    return cur.fetchone() is not None
        try:
            if exists():
                self._table_ready = True
                return True
            if has_import_transaction(self._settings):
                raise WorkflowTaskError("La table des tâches est absente ; initialisez-la hors de la transaction en cours.")
            schema = project_root() / "data" / "schema" / "mysql_workflow.sql"
            if not schema.is_file():
                raise WorkflowTaskError("Le schéma des tâches workflow est indisponible.")
            with mysql_initialization_lock(self._settings):
                if not exists():
                    with self._repo._mc.connect() as conn:
                        if conn.server_status & SERVER_STATUS_IN_TRANS:
                            raise WorkflowTaskError("Initialisation des tâches refusée pendant une transaction en cours.")
                        with conn.cursor() as cur:
                            for statement in _split_sql_statements(schema.read_text(encoding="utf-8")):
                                cur.execute(statement)
            if not exists():
                raise WorkflowTaskError("La table des tâches workflow reste indisponible.")
            self._table_ready = True
            return True
        except WorkflowTaskError:
            raise
        except Exception as exc:
            _log.warning("Accès au stockage des tâches workflow impossible.")
            raise WorkflowTaskError("Impossible d'accéder aux tâches workflow. Vérifiez la connexion et les droits.") from exc

    def list_open(
        self,
        *,
        mois: str | None = None,
        assigned_role: str | None = None,
        limit: int | None = 50,
        status: str | None = "open",
    ) -> list[WorkflowTaskRow]:
        if not self._ensure_table():
            raise WorkflowTaskError("Les tâches workflow sont indisponibles.")
        lim = max(1, min(limit, 200)) if limit is not None else None
        sql = f"""
            SELECT id, mois, task_kind, status, priority, title, detail,
                   assigned_role, created_by, created_at, {WORKFLOW_REVISION_SQL}, resolved_by, resolved_at
            FROM workflow_task
            WHERE 1=1
        """
        params: list[Any] = []
        if status not in (None, "open", "done", "review"):
            raise ValueError("Statut de tâche invalide.")
        if status == "open":
            sql += " AND status IN ('open','review')"
        elif status:
            sql += " AND status = %s"
            params.append(status)
        if mois:
            sql += " AND mois = %s"
            params.append(mois)
        if assigned_role:
            sql += " AND (assigned_role IS NULL OR assigned_role = %s)"
            params.append(assigned_role)
        sql += " ORDER BY FIELD(priority,'high','normal','low'), id DESC"
        if lim is not None:
            sql += " LIMIT %s"
            params.append(lim)
        try:
            with self._repo._mc.connect(read_only=True) as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, tuple(params))
                    rows = cur.fetchall()
        except Exception as exc:
            self._table_ready = False
            _log.warning("Lecture des tâches workflow impossible.")
            raise WorkflowTaskError("Impossible de charger les tâches workflow. Réessayez l'actualisation.") from exc
        out: list[WorkflowTaskRow] = []
        for r in rows:
            ts = r[9]
            ts_s = ts.isoformat() if hasattr(ts, "isoformat") else str(ts or "")
            out.append(
                WorkflowTaskRow(
                    id=int(r[0]),
                    mois=r[1],
                    task_kind=str(r[2]),
                    status=str(r[3]),
                    priority=str(r[4]),
                    title=str(r[5]),
                    detail=str(r[6] or ""),
                    assigned_role=r[7],
                    created_by=r[8],
                    created_at=ts_s,
                    write_token=f"workflow_task:{int(r[0])}:{r[10]}",
                    _write_database=mysql_database_key(self._settings),
                    resolved_by=r[11] if len(r) > 11 else None,
                    resolved_at=str(r[12] or "") if len(r) > 12 else None,
                )
            )
        return out

    def create_task(
        self, *, title: str, detail: str = "", mois: str | None = None,
        task_kind: str = "manual", priority: str = "normal", assigned_role: str | None = None,
    ) -> int | None:
        if not self._ensure_table():
            raise WorkflowTaskError("Les tâches workflow sont indisponibles.")
        actor = resolve_audit_actor()
        pr = priority if priority in ("high", "normal", "low") else "normal"
        try:
            with shared_data_write(self._settings, reason="workflow_task", mois=mois):
                self._lock_revision()
                with self._repo._mc.connect() as conn:
                    with conn.cursor() as cur:
                        cur.execute("""
                            INSERT INTO workflow_task
                              (mois,task_kind,status,priority,title,detail,assigned_role,created_by)
                            VALUES (%s,%s,'open',%s,%s,%s,%s,%s)
                            """, (mois,task_kind,pr,title[:255],detail,assigned_role,actor))
                        identifier = int(cur.lastrowid or 0)
                        if identifier <= 0:
                            raise WorkflowTaskError("L'identifiant de la tâche enregistrée est invalide.")
                        mark_shared_data_write(cur.rowcount > 0)
                        from sme_gend.services.business_audit import record_business_action
                        record_business_action(self._settings,action="workflow_task_create",module="workflow",entity_id=identifier,
                            after=dict(title=title[:255],detail=detail,priority=pr,assigned_role=assigned_role),actor=actor,mois=mois)
                        return identifier
        except WorkflowTaskError:
            raise
        except Exception as exc:
            self._table_ready = False
            _log.warning("Enregistrement d'une tâche workflow impossible.")
            raise WorkflowTaskError("Impossible d'enregistrer les tâches workflow. Vérifiez les données et la connexion.") from exc

    def resolve_task(self, task_id: int, *, expected: WorkflowTaskRow | None = None,
                     comment: str = "", reopen: bool = False) -> bool:
        if reopen and not comment.strip():
            raise ValueError("Un motif est requis pour réactiver une tâche.")
        token=getattr(expected,"write_token",None)
        if (not isinstance(expected,WorkflowTaskRow) or expected.id!=int(task_id)
            or expected._write_database!=mysql_database_key(self._settings)
            or not isinstance(token,str) or re.fullmatch(r"workflow_task:"+str(int(task_id))+r":[0-9a-f]{64}",token) is None):
            raise WorkflowTaskConflictError("Version de tâche indisponible. Actualisez la liste avant de la traiter.")
        if not self._ensure_table():
            raise WorkflowTaskError("Les tâches workflow sont indisponibles.")
        actor = resolve_audit_actor()
        try:
            with shared_data_write(self._settings, reason="workflow_task"):
                self._lock_revision()
                with self._repo._mc.connect() as conn:
                    with conn.cursor() as cur:
                        cur.execute("SELECT status,"+WORKFLOW_REVISION_SQL+" FROM workflow_task WHERE id=%s FOR UPDATE",(task_id,))
                        current=cur.fetchone()
                        if reopen and expected.status != "done" or not reopen and expected.status not in ("open", "review"):
                            return False
                        source_status = "done" if reopen else expected.status
                        if source_status not in ("done", "open", "review"):
                            raise WorkflowTaskConflictError("Statut de tâche invalide. Actualisez la liste.")
                        target_status = "open" if reopen else "done"
                        if current is None or current[0] != source_status:
                            return False
                        if token!=f"workflow_task:{int(task_id)}:{current[1]}":
                            raise WorkflowTaskConflictError("Cette tâche a été modifiée depuis son affichage. Actualisez avant de la traiter.")
                        cur.execute("""
                            UPDATE workflow_task SET status=%s,resolved_by=%s,resolved_at=%s
                            WHERE id=%s AND status=%s
                            """, (target_status, None if reopen else actor,
                                  None if reopen else datetime.now(timezone.utc).replace(tzinfo=None),task_id,source_status))
                        changed = cur.rowcount > 0
                        mark_shared_data_write(changed)
                        if changed:
                            from sme_gend.services.business_audit import record_business_action
                            record_business_action(self._settings,action="workflow_task_reopen" if reopen else "workflow_task_resolve",module="workflow",entity_id=task_id,
                                before=expected,after={"status":target_status,"comment":comment.strip()},actor=actor,mois=expected.mois)
                        return changed
        except (WorkflowTaskError,WorkflowTaskConflictError):
            raise
        except Exception as exc:
            self._table_ready = False
            _log.warning("Traitement d'une tâche workflow impossible.")
            raise WorkflowTaskError("Impossible de traiter les tâches workflow. Vérifiez la connexion et réessayez.") from exc

    def sync_from_pre_closure(self, report: object) -> None:
        """One transactional, serialized pre-closure batch, without truncated dedup."""
        checks = [check for check in report.checks if check.level != "ok"]
        if not self._ensure_table():
            raise WorkflowTaskError("Les tâches workflow sont indisponibles.")
        kinds = {f"pre_closure_{check.check_id}" for check in checks}
        try:
            with shared_data_write(self._settings, reason="workflow_task", mois=report.mois_cloture):
                self._lock_revision()
                with self._repo._mc.connect() as conn:
                    with conn.cursor() as cur:
                        cur.execute("SELECT task_kind,title,id,detail,priority,assigned_role,status FROM workflow_task WHERE status IN ('open','review') AND mois <=> %s "
                                    "AND LEFT(task_kind,12)='pre_closure_' FOR UPDATE", (report.mois_cloture,))
                        existing = {(r[0],r[1]): r for r in cur.fetchall()}
                        # Une lecture indisponible ne permet pas d'inférer la disparition
                        # d'un constat. Ne jamais classer automatiquement une tâche traitée.
                        complete = not any(c.check_id.endswith("_error") or c.check_id in ("mysql", "format") for c in report.checks)
                        if complete:
                            for row in existing.values():
                                if len(row) >= 7 and row[0] not in kinds and row[6] == "open":
                                    cur.execute("UPDATE workflow_task SET status='review' WHERE id=%s AND status='open'", (row[2],))
                                    mark_shared_data_write(cur.rowcount > 0)
                                    from sme_gend.services.business_audit import record_business_action
                                    record_business_action(self._settings,action="workflow_task_revalidate",module="workflow",entity_id=row[2],
                                        before={"status":"open"},after={"status":"review"},actor=resolve_audit_actor(),mois=report.mois_cloture)
                for check in checks:
                    kind = f"pre_closure_{check.check_id}"
                    key = (kind,check.title[:255])
                    if key in existing:
                        row = existing[key]
                        priority = "high" if check.level == "block" else "normal"
                        role = "operateur" if check.level == "warn" else "admin"
                        if len(row) >= 6 and (tuple(row[3:6]) != (check.detail, priority, role) or len(row) >= 7 and row[6] == "review"):
                            with self._repo._mc.connect() as conn:
                                with conn.cursor() as cur:
                                    cur.execute("UPDATE workflow_task SET detail=%s,priority=%s,assigned_role=%s,status='open' WHERE id=%s AND status IN ('open','review')",
                                                (check.detail,priority,role,row[2]))
                                    mark_shared_data_write(cur.rowcount > 0)
                                    from sme_gend.services.business_audit import record_business_action
                                    record_business_action(self._settings,action="workflow_task_update",module="workflow",entity_id=row[2],
                                        before=dict(detail=row[3],priority=row[4],assigned_role=row[5]),
                                        after=dict(detail=check.detail,priority=priority,assigned_role=role),
                                        actor=resolve_audit_actor(),mois=report.mois_cloture)
                        continue
                    self.create_task(title=check.title,detail=check.detail,mois=report.mois_cloture,
                        task_kind=kind,priority="high" if check.level == "block" else "normal",
                        assigned_role="operateur" if check.level == "warn" else "admin")
                    existing[key] = (kind, check.title[:255])
                # Le rapport et les tâches sont confirmés dans la même transaction.
                self._repo.append_audit(action="sme_preclosure_report", module="workflow",
                    ts_utc=datetime.now(timezone.utc), actor=resolve_audit_actor(),
                    sme_mois_context=report.mois_cloture, payload={"report":asdict(report)})
        except WorkflowTaskError:
            raise
        except Exception as exc:
            self._table_ready = False
            _log.warning("Synchronisation des tâches de pré-clôture impossible.")
            raise WorkflowTaskError("L'enregistrement des tâches de pré-clôture n'a pas pu être confirmé. Vérifiez leur état avant de recommencer.") from exc

    def _lock_revision(self) -> None:
        """Existing revision row serializes workflow writes until the real commit.

        Empty token only initializes a missing row; an existing token is unchanged.
        This is a transaction row lock, with no new persistent lock table or key.
        """
        with self._repo._mc.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO app_config(config_key,payload) VALUES(%s,%s) "
                            "ON DUPLICATE KEY UPDATE config_key=VALUES(config_key)",
                            (KEY_DATA_REVISION,'{"token":""}'))


    def count_open(self, *, mois: str | None = None) -> int:
        if not self._ensure_table():
            raise WorkflowTaskError("Les tâches workflow sont indisponibles.")
        sql = "SELECT COUNT(*) FROM workflow_task WHERE status IN ('open','review')"
        params = ()
        if mois:
            sql += " AND mois=%s"
            params = (mois,)
        try:
            with self._repo._mc.connect(read_only=True) as conn:
                with conn.cursor() as cur:
                    cur.execute(sql,params)
                    return int(cur.fetchone()[0])
        except Exception as exc:
            self._table_ready = False
            _log.warning("Comptage des tâches workflow impossible.")
            raise WorkflowTaskError("Impossible de compter les tâches workflow.") from exc
