"""Fiches datées des positions sans registre spécialisé."""
from sme_gend.repositories.mysql_connection import MySQLConnection
from functools import wraps


def _atomic_position(method):
    @wraps(method)
    def write(settings,*args,**kwargs):
        from sme_gend.services.personnel_service import _personnel_write
        with _personnel_write(settings,reason='dated_position'):
            return method(settings,*args,**kwargs)
    return write

POSITION_LABELS = {
    "détaché": "Détaché", "inst mise route": "Inst Mise Route",
    "punis": "Punis", "prison ma": "Prison MA",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_position_event (
 id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
 agent_id BIGINT NOT NULL,
 position_admin VARCHAR(100) NOT NULL,
 date_debut DATE NOT NULL,
 date_fin DATE NULL,
 lieu VARCHAR(255) NULL,
 reference_decision VARCHAR(255) NULL,
 observation TEXT NULL,
 mois_sme VARCHAR(7) NULL,
 INDEX idx_position_agent (agent_id),
 CONSTRAINT fk_position_event_agent FOREIGN KEY (agent_id) REFERENCES agent(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


def apply_position_event_schema(settings):
    with MySQLConnection(settings).connect() as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA)


@_atomic_position
def add_position_event(settings, legacy_id, *, position_admin, date_debut,
                       date_fin, lieu, reference_decision, observation, mois_sme):
    from sme_gend.repositories.mysql_common import BaseMySQLRepository
    from sme_gend.services.sensitive_data_policy import assert_may_add_dated_event
    assert_may_add_dated_event(action="ajouter une fiche de position administrative")
    if position_admin not in POSITION_LABELS.values():
        raise ValueError("Position administrative non prise en charge.")
    if not date_debut or (date_fin and date_fin < date_debut):
        raise ValueError("Dates de position administrative invalides.")
    repo = BaseMySQLRepository(settings)
    with repo._connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM agent WHERE legacy_access_id = %s UNION ALL SELECT canonical_agent_id FROM agent_identity_alias WHERE alias_legacy_id=%s LIMIT 1", (legacy_id, legacy_id))
            agent = cur.fetchone()
            if not agent:
                raise ValueError("Agent introuvable dans le dossier.")
            from sme_gend.services.temporal_events import event_period
            event_period(cur,date_debut)
            if date_fin:event_period(cur,date_fin)
            cur.execute("""INSERT INTO agent_position_event
                (agent_id, position_admin, date_debut, date_fin, lieu,
                 reference_decision, observation, mois_sme)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                (agent[0], position_admin, date_debut, date_fin, lieu,
                 reference_decision, observation, date_debut.strftime('%Y-%m')))
            from sme_gend.services.temporal_events import POSITION_SCOPE,record_position
            scope=POSITION_SCOPE.get()
            baseline=scope.before.position_admin if scope and scope.before else 'Présent'
            record_position(settings,agent[0],position_admin,date_debut,date_fin,
                            reference=f'sgi:auto:position:{cur.lastrowid}',baseline=baseline or 'Présent')


def list_position_events(settings, legacy_id):
    from sme_gend.repositories.mysql_common import BaseMySQLRepository
    repo = BaseMySQLRepository(settings)
    with repo._connect(read_only=True) as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT e.position_admin, e.date_debut, e.date_fin,
                e.lieu, e.reference_decision, e.observation, e.mois_sme
                FROM agent_position_event e JOIN agent a ON a.id=e.agent_id
                WHERE a.legacy_access_id=%s AND (e.reference_decision IS NULL OR e.reference_decision NOT LIKE 'sgi:auto:%%') ORDER BY e.date_debut DESC, e.id DESC""",
                (legacy_id,))
            return list(cur.fetchall())
