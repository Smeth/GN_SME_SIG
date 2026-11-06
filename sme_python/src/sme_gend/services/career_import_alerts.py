"""Alertes qualité données carrière (imports mensuels)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import hashlib
import json

from sme_gend.config.settings import Settings
from sme_gend.models.agent_evenement import CarriereSnapshotRow
from sme_gend.repositories.mysql_agent_repository import MySQLAgentRepository
from sme_gend.repositories.mysql_connection import MySQLConnection
from sme_gend.services.mysql_import_period import last_day_of_month

GRADE_BLOCKED_MONTHS = 24

ALERT_LABELS = {
    "MATRICULE_DUPLIQUE": "Matricule partagé entre plusieurs fiches",
    "STRUCTURE_JUMP": "Changement d’affectation à vérifier",
    "STRUCTURE_CLEARED": "Affectation devenue vide",
    "GRADE_CLEARED": "Grade devenu vide",
    "GRADE_LONG_UNCHANGED": "Même grade observé sur une longue période",
    "DATE_INCOHERENTE": "Date de grade antérieure à la naissance",
    "AGENT_SANS_CARRIERE": "Fiche sans situation carrière",
}


@dataclass(frozen=True, slots=True)
class RelatedCareerAgent:
    legacy_id: str
    noms: str | None
    date_naissance: date | None
    matricule_militaire: str | None
    matricule_solde: str | None


@dataclass(slots=True)
class CareerAlert:
    severity: str  # info | warning
    agent_legacy_id: str
    noms: str | None
    mois: str
    code: str
    message: str
    related_agents: tuple[RelatedCareerAgent, ...] = ()
    matched_field: str = ""
    matched_value: str = ""

    @property
    def identity_comparisons(self):
        from sme_gend.services.personnel_identity_assessment import assess_identity
        reference = next((row for row in self.related_agents if row.legacy_id == self.agent_legacy_id), None)
        if reference is None:
            return {}
        return {row.legacy_id: assess_identity(reference, row) for row in self.related_agents
                if row.legacy_id != self.agent_legacy_id}

    @property
    def merge_candidates(self):
        comparisons = self.identity_comparisons
        return tuple(row for row in self.related_agents if row.legacy_id == self.agent_legacy_id
                     or (row.legacy_id in comparisons and comparisons[row.legacy_id].eligible)) if any(
                         item.eligible for item in comparisons.values()) else ()

    @property
    def identity_summary(self):
        comparisons = self.identity_comparisons
        if not comparisons:
            return "Informations de comparaison insuffisantes ; aucun rapprochement proposé."
        eligible = sum(result.eligible for result in comparisons.values())
        if eligible and eligible == len(comparisons):
            return "Doublon potentiel de dossier : concordances avec la fiche de référence, à vérifier sur pièces. Chaque paire est contrôlée séparément."
        if eligible:
            return "Situation mixte : seules certaines fiches ont des identités concordantes. Les autres sont exclues du rapprochement proposé."
        if any(result.status == "divergent" for result in comparisons.values()):
            return "Matricule partagé entre identités divergentes : vérifiez l’attribution du matricule. Aucun rapprochement n’est proposé."
        return "Identités insuffisamment documentées : le matricule partagé ne suffit pas à proposer un rapprochement."

    @property
    def label(self) -> str:
        if self.code == "MATRICULE_DUPLIQUE" and self.matched_field:
            if not self.merge_candidates and any(item.status == "divergent" for item in self.identity_comparisons.values()):
                return f"{self.matched_field.capitalize()} partagé — identités divergentes"
            return f"{self.matched_field.capitalize()} partagé entre plusieurs fiches"
        return ALERT_LABELS.get(self.code, self.code)

    @property
    def resolution_guidance(self) -> str:
        if self.code == "MATRICULE_DUPLIQUE":
            if not self.merge_candidates:
                return (self.identity_summary + "\n\n"
                    "1. Ouvrez les fiches et vérifiez les pièces d’identité et les pièces portant le matricule concerné.\n\n"
                    "2. Si ce sont des personnes différentes, recherchez le matricule correct dans les pièces et corrigez la donnée erronée dans la bonne fiche, "
                    "via Liste générale → Modifier la fiche. Une erreur d’identité ne doit être corrigée que d’après une pièce fiable, avec les droits nécessaires. "
                    "Ne modifiez pas les noms ou les dates de naissance pour rendre un rapprochement possible.\n\n"
                    "3. Si des données sont manquantes, complétez-les seulement d’après les pièces de référence, puis actualisez.\n\n"
                    "4. Si le partage est confirmé et justifié, vous pouvez acquitter ce constat avec un commentaire. "
                    "Acquitter ne corrige ni ne fusionne les données.")
            return (
                "1. Ouvrez les fiches listées ci-dessous et comparez la date de naissance, les deux matricules et le parcours. "
                "Les identifiants de fiche permettent de distinguer deux fiches portant le même nom.\n\n"
                "2. Si ce sont deux personnes différentes, vérifiez les pièces de référence. Corrigez le matricule erroné "
                "dans Liste générale → fiche de la personne → Modifier la fiche, avec les droits nécessaires.\n\n"
                "3. Si les deux fiches représentent la même personne, il s’agit probablement d’un doublon de dossier. "
                "Ne changez pas un matricule exact pour masquer l’alerte et ne supprimez pas une fiche sans rapprochement "
                "de ses historiques. Utilisez « Rapprocher deux fiches et rattacher leurs historiques » : "
                "choisissez la fiche à conserver, vérifiez l’aperçu et résolvez les conflits bloquants avant validation. "
                "Cette opération est réservée aux administrateurs habilités et crée une sauvegarde préalable.\n\n"
                "4. Après correction, revenez dans Alertes parcours et cliquez sur Actualiser. "
                "Si le partage est confirmé et justifié, acquittez chaque constat vérifié avec un commentaire. "
                "Acquitter masque ce constat ; cela ne corrige ni ne fusionne les données."
            )
        return {
            "STRUCTURE_JUMP": "Comparez les deux mois indiqués et les affectations du dossier avec la décision d’affectation. Complétez l’historique si la mutation est réelle, ou corrigez la situation erronée avec les droits nécessaires. Actualisez ensuite les alertes.",
            "STRUCTURE_CLEARED": "Comparez les mois indiqués et la pièce d’affectation. Renseignez la structure manquante dans la situation concernée si c’est une erreur, puis actualisez.",
            "GRADE_CLEARED": "Vérifiez la décision de nomination et la situation du mois indiqué. Corrigez le grade manquant si nécessaire, puis actualisez.",
            "GRADE_LONG_UNCHANGED": "C’est une information, pas un retard d’avancement établi. Vérifiez les décisions de grade et la complétude des mois disponibles. Aucune modification n’est nécessaire si le parcours est exact.",
            "DATE_INCOHERENTE": "Comparez la date de naissance actuellement enregistrée avec la décision de grade du mois indiqué. Faites corriger la donnée erronée par un utilisateur habilité, puis actualisez.",
            "AGENT_SANS_CARRIERE": "Vérifiez si cette fiche doit appartenir à un effectif importé. Contrôlez son identité et l’import source avant de compléter une situation ; ne créez pas de carrière fictive pour faire disparaître l’alerte.",
        }.get(self.code, "Vérifiez le constat dans le dossier et les pièces de référence avant toute correction ou tout acquittement.")

    @property
    def reference_label(self) -> str:
        if self.code in {"MATRICULE_DUPLIQUE", "AGENT_SANS_CARRIERE"}:
            return "Contrôle actuel — hors historique"
        if self.code == "DATE_INCOHERENTE":
            return "Parcours historique / naissance actuellement enregistrée"
        return "Parcours historique"

    def fingerprint(self) -> str:
        """Version exacte du constat présenté à l'utilisateur (hors nom d'affichage)."""
        payload = ["v1", self.agent_legacy_id, self.code, self.mois, self.severity, self.message]
        if self.related_agents:
            payload.append([[r.legacy_id, r.noms, str(r.date_naissance or ""), r.matricule_militaire, r.matricule_solde]
                            for r in sorted(self.related_agents, key=lambda r: r.legacy_id)])
        return hashlib.sha256(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()

    def occurrence_key(self) -> tuple[str, str, str, str]:
        return self.agent_legacy_id, self.code, self.mois, self.fingerprint()


@dataclass(frozen=True, slots=True)
class _AffectationPoint:
    on_date: date
    structure_code: str


def _month_index(ym: str) -> int:
    y, m = ym.split("-")
    return int(y) * 12 + int(m)


def _months_between(a: str, b: str) -> int:
    return abs(_month_index(b) - _month_index(a))


def _structure_at_month_end(
    points: list[_AffectationPoint],
    ym: str,
) -> str | None:
    if not points:
        return None
    ref = last_day_of_month(ym)
    code: str | None = None
    for pt in points:
        if pt.on_date <= ref:
            code = pt.structure_code
        else:
            break
    return code


def _load_affectation_index(settings: Settings) -> dict[int, list[_AffectationPoint]]:
    out: dict[int, list[_AffectationPoint]] = {}
    mc = MySQLConnection(settings)
    with mc.connect(read_only=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT agent_id, date_affectation, structure_code
                FROM agent_affectation
                ORDER BY agent_id, date_affectation, id
                """
            )
            for aid, dt, code in cur.fetchall():
                key = int(aid)
                out.setdefault(key, []).append(
                    _AffectationPoint(
                        on_date=dt if isinstance(dt, date) else date.fromisoformat(str(dt)),
                        structure_code=(code or "").strip(),
                    )
                )
    return out


def _load_legacy_agent_map(settings: Settings) -> dict[str, int]:
    mc = MySQLConnection(settings)
    out: dict[str, int] = {}
    with mc.connect(read_only=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT legacy_access_id, id FROM agent WHERE legacy_access_id IS NOT NULL"
            )
            for leg, aid in cur.fetchall():
                leg_s = str(leg or "").strip()
                if leg_s:
                    out[leg_s] = int(aid)
    return out


def _dossier_confirms_structure_jump(
    *,
    affectations: list[_AffectationPoint],
    prev_mois: str,
    curr_mois: str,
    prev_structure: str,
    curr_structure: str,
) -> bool:
    """True si la timeline affectation explique le changement de structure."""
    ps = (prev_structure or "").strip()
    cs = (curr_structure or "").strip()
    if not ps or not cs or ps == cs:
        return False
    at_prev = _structure_at_month_end(affectations, prev_mois)
    at_curr = _structure_at_month_end(affectations, curr_mois)
    return at_prev == ps and at_curr == cs


def _scan_agent_snaps(
    legacy_id: str,
    noms: str | None,
    snaps: list[CarriereSnapshotRow],
    *,
    affectations: list[_AffectationPoint] | None = None,
) -> list[CareerAlert]:
    out: list[CareerAlert] = []
    if len(snaps) < 2:
        return out

    snaps = sorted(snaps, key=lambda s: s.annee_mois)
    last = snaps[-1]
    first = last
    for previous in reversed(snaps[:-1]):
        if not previous.grade or previous.grade != last.grade:
            break
        first = previous
    if (
        first.grade
        and last.grade
        and first.grade == last.grade
        and _months_between(first.annee_mois, last.annee_mois) >= GRADE_BLOCKED_MONTHS
    ):
        out.append(
            CareerAlert(
                severity="info",
                agent_legacy_id=legacy_id,
                noms=noms,
                mois=last.annee_mois,
                code="GRADE_LONG_UNCHANGED",
                message=(
                    f"Même grade « {last.grade} » dans les situations disponibles de {first.annee_mois} "
                    f"à {last.annee_mois} ({_months_between(first.annee_mois, last.annee_mois)} mois). "
                    "Information à vérifier ; ne constitue pas un retard d’avancement établi."
                ),
            )
        )

    for i in range(1, len(snaps)):
        prev, curr = snaps[i - 1], snaps[i]
        if not prev.annee_mois or not curr.annee_mois:
            continue
        ps, cs = (prev.structure or "").strip(), (curr.structure or "").strip()
        gs, gc = (prev.grade or "").strip(), (curr.grade or "").strip()
        ss, sc = (prev.subdivision_arme or "").strip(), (curr.subdivision_arme or "").strip()

        if ps and not cs:
            out.append(
                CareerAlert(
                    severity="warning",
                    agent_legacy_id=legacy_id,
                    noms=noms,
                    mois=curr.annee_mois,
                    code="STRUCTURE_CLEARED",
                    message=f"Structure effacée ({ps} → vide) entre {prev.annee_mois} et {curr.annee_mois}.",
                )
            )
        elif ps != cs and gs == gc and ss == sc and ps and cs:
            if affectations and _dossier_confirms_structure_jump(
                affectations=affectations,
                prev_mois=prev.annee_mois,
                curr_mois=curr.annee_mois,
                prev_structure=ps,
                curr_structure=cs,
            ):
                pass
            else:
                out.append(
                    CareerAlert(
                        severity="warning",
                        agent_legacy_id=legacy_id,
                        noms=noms,
                        mois=curr.annee_mois,
                        code="STRUCTURE_JUMP",
                        message=(
                            f"Structure « {ps} » → « {cs} » avec le même grade et la même subdivision "
                            f"({prev.annee_mois} → {curr.annee_mois}). "
                            "Changement non expliqué par les affectations enregistrées ; à vérifier."
                        ),
                    )
                )
        if gs != gc and not gc:
            out.append(
                CareerAlert(
                    severity="warning",
                    agent_legacy_id=legacy_id,
                    noms=noms,
                    mois=curr.annee_mois,
                    code="GRADE_CLEARED",
                    message=f"Grade effacé ({gs} → vide) sur {curr.annee_mois}.",
                )
            )

    return out


@dataclass(frozen=True, slots=True)
class _AgentIdentityRow:
    legacy_id: str
    noms: str | None
    matricule_militaire: str | None
    matricule_solde: str | None
    date_naissance: date | None


def _load_agent_identity_index(settings: Settings) -> list[_AgentIdentityRow]:
    """Une requête bulk : identité de tous les agents (matricules, naissance)."""
    mc = MySQLConnection(settings)
    out: list[_AgentIdentityRow] = []
    with mc.connect(read_only=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT legacy_access_id, noms, matricule_militaire, matricule_solde, date_naissance
                FROM agent
                WHERE legacy_access_id IS NOT NULL AND TRIM(legacy_access_id) <> ''
                """
            )
            for leg, noms, mat_mil, mat_solde, dn in cur.fetchall():
                out.append(
                    _AgentIdentityRow(
                        legacy_id=str(leg).strip(),
                        noms=noms,
                        matricule_militaire=(str(mat_mil).strip() if mat_mil else None),
                        matricule_solde=(str(mat_solde).strip() if mat_solde else None),
                        date_naissance=(
                            dn if isinstance(dn, date) else
                            (date.fromisoformat(str(dn)) if dn else None)
                        ),
                    )
                )
    return out


def _load_agents_without_carriere(settings: Settings) -> list[tuple[str, str | None]]:
    """Agents importés dans ``agent`` sans aucune ligne ``carriere_situation``."""
    mc = MySQLConnection(settings)
    out: list[tuple[str, str | None]] = []
    with mc.connect(read_only=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT a.legacy_access_id, a.noms
                FROM agent a
                WHERE a.legacy_access_id IS NOT NULL AND TRIM(a.legacy_access_id) <> ''
                  AND NOT EXISTS (
                      SELECT 1 FROM carriere_situation c WHERE c.agent_id = a.id
                  )
                """
            )
            for leg, noms in cur.fetchall():
                out.append((str(leg).strip(), noms))
    return out


def _scan_matricule_duplicates(identities: list[_AgentIdentityRow]) -> list[CareerAlert]:
    from sme_gend.services.personnel_identity_assessment import identity_value
    out: list[CareerAlert] = []
    for field_name, getter in (
        ("matricule militaire", lambda r: r.matricule_militaire),
        ("matricule solde", lambda r: r.matricule_solde),
    ):
        groups: dict[str, list[_AgentIdentityRow]] = {}
        for row in identities:
            val = identity_value("matricule_militaire" if field_name == "matricule militaire" else "matricule_solde", getter(row))
            if not val:
                continue
            groups.setdefault(val, []).append(row)
        for val, rows in groups.items():
            if len(rows) < 2:
                continue
            ordered = sorted(rows, key=lambda r: r.legacy_id)
            related = tuple(RelatedCareerAgent(r.legacy_id, r.noms, r.date_naissance,
                                               r.matricule_militaire, r.matricule_solde) for r in ordered)
            for row in rows:
                others = "; ".join(f"{r.noms or 'Nom non renseigné'} [fiche {r.legacy_id}]"
                                   for r in ordered if r.legacy_id != row.legacy_id)
                out.append(
                    CareerAlert(
                        severity="warning",
                        agent_legacy_id=row.legacy_id,
                        noms=row.noms,
                        mois="",
                        code="MATRICULE_DUPLIQUE",
                        message=f"La fiche {row.legacy_id} partage le {field_name} « {val} » avec : {others}. "
                        "Identités actuellement enregistrées.",
                        related_agents=related,
                        matched_field=field_name,
                        matched_value=val,
                    )
                )
                out[-1].message += " " + out[-1].identity_summary
    return out


def _scan_date_incoherente(
    identities: list[_AgentIdentityRow],
    bulk: dict[str, tuple[str | None, list[CarriereSnapshotRow]]],
) -> list[CareerAlert]:
    out: list[CareerAlert] = []
    for row in identities:
        if not row.date_naissance:
            continue
        _, snaps = bulk.get(row.legacy_id, (None, []))
        if not snaps:
            continue
        for first in snaps:
            if not first.date_grade:
                continue
            try:
                dg = date.fromisoformat(str(first.date_grade)[:10])
            except ValueError:
                continue
            if dg >= row.date_naissance:
                continue
            out.append(
                CareerAlert(
                    severity="warning",
                    agent_legacy_id=row.legacy_id,
                    noms=row.noms,
                    mois=first.annee_mois or "",
                    code="DATE_INCOHERENTE",
                    message=(
                        f"Date de grade ({first.date_grade}) antérieure à la date de "
                        f"naissance ({row.date_naissance.isoformat()})."
                    ),
                )
            )
    return out


def _scan_agents_sans_carriere(
    orphans: list[tuple[str, str | None]]
) -> list[CareerAlert]:
    return [
        CareerAlert(
            severity="warning",
            agent_legacy_id=leg,
            noms=noms,
            mois="",
            code="AGENT_SANS_CARRIERE",
            message="Agent importé sans aucune situation carrière (mois manquant).",
        )
        for leg, noms in orphans
    ]


def fetch_acknowledged_keys(settings: Settings) -> set[tuple[str, str, str, str]]:
    """Acquittements portant sur un constat précis ; les anciens restent archivés."""
    mc = MySQLConnection(settings)
    out: set[tuple[str, str, str, str]] = set()
    with mc.connect(read_only=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT agent_legacy_id, alert_code, mois, fingerprint FROM career_alert_ack_occurrence"
            )
            for leg, code, mois, fingerprint in cur.fetchall():
                out.add((str(leg or ""), str(code or ""), str(mois or ""), str(fingerprint)))
    return out


def acknowledge_alert(
    settings: Settings,
    agent_legacy_id: str,
    alert_code: str,
    mois: str,
    *,
    fingerprint: str,
    by: str | None = None,
    note: str | None = None,
    alert: CareerAlert | None = None,
) -> None:
    """Marque une alerte comme reconnue (n'apparaît plus tant que la même occurrence persiste)."""
    from sme_gend.services.auth_guard import session_has_permission
    from sme_gend.services.auth_context import resolve_audit_actor
    from sme_gend.services.shared_reference_write import shared_data_write, mark_shared_data_write
    from sme_gend.services.business_audit import record_business_action

    if not session_has_permission("workflow.resolve_task"):
        raise PermissionError("Acquittement non autoris\u00e9.")
    if len(fingerprint) != 64 or any(c not in "0123456789abcdef" for c in fingerprint):
        raise ValueError("Empreinte de l’alerte invalide ; actualisez la liste.")
    by = resolve_audit_actor()
    note = (note or "").strip() or None
    if note and len(note) > 255:
        raise ValueError("Le commentaire est limité à 255 caractères.")
    if alert is not None and alert.occurrence_key() != (agent_legacy_id, alert_code, mois, fingerprint):
        raise ValueError("Le constat ne correspond pas à l’alerte sélectionnée.")
    with shared_data_write(settings, reason="career_alert_ack", mois=mois or None):
        mc = MySQLConnection(settings)
        with mc.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO career_alert_ack_occurrence
                        (agent_legacy_id, alert_code, mois, fingerprint, acknowledged_by, note)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE fingerprint = fingerprint
                    """,
                    (
                        (agent_legacy_id or "").strip(),
                        (alert_code or "").strip(),
                        (mois or "").strip(),
                        fingerprint,
                        by,
                        note,
                    ),
                )
                if cur.rowcount == 0:
                    raise RuntimeError("Cette alerte a déjà été acquittée par un autre utilisateur. Actualisez.")
        record_business_action(
            settings, action="career_alert_ack", module="career_alerts",
            entity_id=agent_legacy_id, actor=by, mois=mois or None,
            after={"code": alert_code, "mois": mois, "fingerprint": fingerprint, "note": note,
                   "message": alert.message if alert else None,
                   "severity": alert.severity if alert else None},
        )
        mark_shared_data_write(True)
        from sme_gend.repositories.mysql_connection import after_mysql_commit
        from sme_gend.services.career_alerts_cache import invalidate_career_alerts_cache
        after_mysql_commit(invalidate_career_alerts_cache)


@dataclass(frozen=True)
class CareerAcknowledgement:
    agent_legacy_id: str
    code: str
    mois: str
    fingerprint: str | None
    by: str
    at: str
    note: str
    token: str
    noms: str = ""


_ACK_TOKEN_SQL = "SHA2(CAST(JSON_ARRAY(acknowledged_by,acknowledged_at,note) AS CHAR),256)"


def list_career_acknowledgements(settings: Settings) -> list[CareerAcknowledgement]:
    """Historique actuel et anciens acquittements sans empreinte, sans les convertir."""
    from sme_gend.services.auth_guard import session_has_permission
    if not session_has_permission("effectifs.liste"):
        raise PermissionError("Consultation des alertes non autorisée.")
    records = []
    with MySQLConnection(settings).connect(read_only=True) as conn:
        with conn.cursor() as cur:
            for table, fingerprint in (("career_alert_ack_occurrence", "fingerprint"), ("career_alert_ack", "NULL")):
                cur.execute(f"SELECT agent_legacy_id, alert_code, mois, {fingerprint}, "
                            f"acknowledged_by, acknowledged_at, note, {_ACK_TOKEN_SQL}, "
                            "COALESCE((SELECT noms FROM agent WHERE legacy_access_id=ack.agent_legacy_id LIMIT 1),'') "
                            f"FROM {table} ack ORDER BY acknowledged_at DESC")
                for row in cur.fetchall():
                    leg, code, mois, version, actor, timestamp, note, token, name = row
                    records.append(CareerAcknowledgement(
                        str(leg or ""), str(code or ""), str(mois or ""), version,
                        str(actor or ""), str(timestamp or ""), str(note or ""),
                        str(token or ""), str(name or "")))
    return sorted(records, key=lambda record: record.at, reverse=True)


def reopen_career_alert(settings: Settings, record: CareerAcknowledgement) -> None:
    """Réactivation conditionnelle : aucun écrasement d’un acquittement modifié ailleurs."""
    from sme_gend.services.auth_guard import session_has_permission
    from sme_gend.services.auth_context import resolve_audit_actor
    from sme_gend.services.shared_reference_write import shared_data_write, mark_shared_data_write
    from sme_gend.services.business_audit import record_business_action
    if not session_has_permission("workflow.resolve_task"):
        raise PermissionError("Réactivation non autorisée.")
    if not record.fingerprint:
        raise ValueError("Cet ancien acquittement ne masque aucune alerte actuelle.")
    key = (record.agent_legacy_id, record.code, record.mois, record.fingerprint)
    with shared_data_write(settings, reason="career_alert_reopen", mois=record.mois or None):
        with MySQLConnection(settings).connect() as conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT {_ACK_TOKEN_SQL} FROM career_alert_ack_occurrence "
                            "WHERE agent_legacy_id=%s AND alert_code=%s AND mois=%s AND fingerprint=%s FOR UPDATE", key)
                row = cur.fetchone()
                if row is None or str(row[0]) != record.token:
                    raise RuntimeError("Cet acquittement a changé ou a déjà été réactivé. Actualisez.")
                cur.execute("DELETE FROM career_alert_ack_occurrence "
                            "WHERE agent_legacy_id=%s AND alert_code=%s AND mois=%s AND fingerprint=%s", key)
        record_business_action(settings, action="career_alert_reopen", module="career_alerts",
            entity_id=record.agent_legacy_id, actor=resolve_audit_actor(), mois=record.mois or None,
            before={"code": record.code, "fingerprint": record.fingerprint, "by": record.by,
                    "at": record.at, "note": record.note}, after={"acknowledged": False})
        mark_shared_data_write(True)
        from sme_gend.repositories.mysql_connection import after_mysql_commit
        from sme_gend.services.career_alerts_cache import invalidate_career_alerts_cache
        after_mysql_commit(invalidate_career_alerts_cache)


def scan_all_career_alerts(
    settings: Settings,
    *,
    mois_focus: str | None = None,
    limit: int | None = None,
    include_acknowledged: bool = False,
) -> list[CareerAlert]:
    """Parcourt les agents et détecte les anomalies de parcours (historique bulk)."""
    if mois_focus is None:
        from sme_gend.config.liste_generale_scope import resolve_liste_view_mois
        mois_focus = resolve_liste_view_mois(settings)
    repo = MySQLAgentRepository(settings)
    bulk = repo.load_carriere_history_bulk()
    if mois_focus:
        bulk = {leg: (name, [s for s in snaps if s.annee_mois and s.annee_mois <= mois_focus])
                for leg, (name, snaps) in bulk.items()}
    aff_index = _load_affectation_index(settings)
    legacy_map = _load_legacy_agent_map(settings)
    alerts: list[CareerAlert] = []

    for leg, (noms, snaps) in bulk.items():
        if not leg or len(snaps) < 2:
            continue
        filtered = snaps
        if mois_focus:
            filtered = [s for s in snaps if s.annee_mois <= mois_focus]
        if len(filtered) < 2:
            continue
        agent_id = legacy_map.get(str(leg).strip())
        aff = aff_index.get(agent_id or -1) if agent_id else None
        alerts.extend(
            _scan_agent_snaps(leg, noms, filtered, affectations=aff)
        )

    identities = _load_agent_identity_index(settings)
    alerts.extend(_scan_matricule_duplicates(identities))
    alerts.extend(_scan_date_incoherente(identities, bulk))
    alerts.extend(_scan_agents_sans_carriere(_load_agents_without_carriere(settings)))

    if not include_acknowledged:
        acked = fetch_acknowledged_keys(settings)
        if acked:
            alerts = [
                a for a in alerts
                if a.occurrence_key() not in acked
            ]

    alerts.sort(key=lambda a: (a.severity != "warning", a.mois, a.agent_legacy_id))
    return alerts if limit is None else alerts[:limit]


def alerts_for_agent(
    settings: Settings,
    legacy_id: str,
    noms: str | None = None,
) -> list[CareerAlert]:
    """Même moteur, période, contrôles et acquittements que l’écran central."""
    leg = str(legacy_id or "").strip()
    if not leg:
        return []
    return [a for a in scan_all_career_alerts(settings, limit=None) if a.agent_legacy_id == leg]
