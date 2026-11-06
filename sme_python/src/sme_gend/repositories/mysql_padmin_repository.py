"""Position catalogue: versioned writes, global scope and complete business audit."""
from sme_gend.models.padmin_ref import PadminRefRow
from sme_gend.repositories.mysql_common import BaseMySQLRepository
from sme_gend.repositories.reference_write_guard import (
    reference_revision_sql, require_reference_token, check_reference_target, insert_reference,
)
from sme_gend.services.shared_reference_write import shared_reference_write, mark_reference_write
from sme_gend.services.reference_policy import position_values, system_position
from sme_gend.services.business_audit import record_business_action


def _same_label(left, right):
    # Internal SQL expressions only; values are always bound parameters.
    def normalized(column):
        return f"REGEXP_REPLACE(TRIM({column}),'[[:space:]]+',' ') COLLATE utf8mb4_unicode_ci"
    return normalized(left) + '=' + normalized(right)


class MySQLPadminRepository(BaseMySQLRepository):
    def fetch_all(self):
        with self._connect(read_only=True) as conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT libelle, compte_comme_present, ordre_tri, "
                            f"{reference_revision_sql('padmin')}, id FROM ref_position_admin r "
                            "ORDER BY ordre_tri IS NULL, ordre_tri, libelle")
                return [PadminRefRow.from_sequence(r) for r in cur.fetchall()]

    def usage_counts(self):
        """Distinct agents across all months, including legacy text-only links."""
        with self._connect(read_only=True) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT r.libelle, COUNT(DISTINCT c.agent_id) FROM ref_position_admin r "
                            "LEFT JOIN carriere_situation c ON c.position_admin_id=r.id OR "
                            "(c.position_admin_id IS NULL AND " + _same_label('c.position_admin_libelle', 'r.libelle') + ") "
                            "GROUP BY r.id, r.libelle")
                return {str(label): int(count) for label, count in cur.fetchall()}

    def insert(self, libelle, compte_comme_present, ordre_tri):
        label, present, order = position_values(libelle, compte_comme_present, ordre_tri)
        with shared_reference_write(self._settings):
            with self._connect() as conn:
                with conn.cursor() as cur:
                    insert_reference(cur, 'padmin',
                        "INSERT INTO ref_position_admin(libelle,compte_comme_present,ordre_tri) VALUES(%s,%s,%s)",
                        (label, int(present), order))
                    mark_reference_write(cur.rowcount > 0)
                    record_business_action(self._settings, action='position_admin_create', module='reference',
                        entity_id=label, after=dict(libelle=label, compte_comme_present=present, ordre_tri=order))

    def update(self, old_libelle, new_libelle, compte_comme_present, ordre_tri, *, expected=None):
        old = old_libelle.strip()
        label, present, order = position_values(new_libelle, compte_comme_present, ordre_tri, old_label=old)
        revision = require_reference_token('padmin', old, expected)
        with shared_reference_write(self._settings):
            with self._connect() as conn:
                with conn.cursor() as cur:
                    check_reference_target(cur, 'padmin', old, revision)
                    cur.execute('SELECT id FROM ref_position_admin WHERE libelle=%s', (old,))
                    ref_id = int(cur.fetchone()[0])
                    insert_reference(cur, 'padmin',
                        'UPDATE ref_position_admin SET libelle=%s,compte_comme_present=%s,ordre_tri=%s WHERE id=%s',
                        (label, int(present), order, ref_id))
                    changed = cur.rowcount > 0
                    if old != label:
                        # This catalogue is global. Preserve the existing stable id and
                        # migrate both linked and legacy text-only historical segments.
                        cur.execute('UPDATE carriere_situation SET position_admin_libelle=%s, position_admin_id=%s '
                                    'WHERE position_admin_id=%s OR '
                                    '(position_admin_id IS NULL AND ' + _same_label('position_admin_libelle', '%s') + ')',
                                    (label, ref_id, ref_id, old))
                        cur.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=DATABASE() "
                                    "AND table_name='agent_position_event'")
                        if cur.fetchone()[0]:
                            cur.execute('UPDATE agent_position_event SET position_admin=%s WHERE ' +
                                        _same_label('position_admin', '%s'), (label, old))
                    mark_reference_write(changed)
                    if changed:
                        record_business_action(self._settings, action='position_admin_update', module='reference',
                            entity_id=str(ref_id), before=expected,
                            after=dict(libelle=label, compte_comme_present=present, ordre_tri=order))

    def delete(self, libelle, *, expected=None):
        label = libelle.strip()
        revision = require_reference_token('padmin', label, expected)
        if system_position(label):
            raise ValueError('Cette position système est utilisée par les règles métier et ne peut pas être supprimée.')
        with shared_reference_write(self._settings):
            with self._connect() as conn:
                with conn.cursor() as cur:
                    check_reference_target(cur, 'padmin', label, revision)
                    cur.execute('SELECT id FROM ref_position_admin WHERE libelle=%s', (label,))
                    ref_id = int(cur.fetchone()[0])
                    cur.execute('SELECT COUNT(DISTINCT agent_id) FROM carriere_situation '
                                'WHERE position_admin_id=%s OR ' + _same_label('position_admin_libelle', '%s'), (ref_id, label))
                    count = int(cur.fetchone()[0])
                    if count:
                        raise ValueError(f'Position utilisée par {count} agent(s), archives comprises. Suppression refusée pour conserver leur historique.')
                    cur.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=DATABASE() "
                                "AND table_name='agent_position_event'")
                    if cur.fetchone()[0]:
                        cur.execute('SELECT COUNT(*) FROM agent_position_event WHERE ' + _same_label('position_admin', '%s'), (label,))
                        if cur.fetchone()[0]:
                            raise ValueError('Position utilisée dans un historique de dossier. Suppression refusée.')
                    cur.execute('DELETE FROM ref_position_admin WHERE id=%s', (ref_id,))
                    mark_reference_write(cur.rowcount > 0)
                    record_business_action(self._settings, action='position_admin_delete', module='reference',
                                           entity_id=str(ref_id), before=expected)
