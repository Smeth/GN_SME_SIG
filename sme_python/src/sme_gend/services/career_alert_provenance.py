"""Éléments vérifiables sur l'origine des fiches ; aucune déduction de cause."""
from dataclasses import dataclass, field
from pathlib import PureWindowsPath
import json

from sme_gend.config.liste_generale_scope import resolve_liste_view_mois, resolve_carriere_scope_mois
from sme_gend.repositories.mysql_agent_repository import _select_liste
from sme_gend.repositories.mysql_connection import MySQLConnection
from sme_gend.services.auth_guard import session_has_permission


@dataclass(frozen=True)
class ProvenanceEvent:
    legacy_id: str
    when: str
    kind: str
    source: str
    actor: str = ""
    detail: str = ""


@dataclass
class FicheProvenance:
    legacy_id: str
    noms: str
    created: str
    updated: str
    months: list[str] = field(default_factory=list)
    visible_in_month: bool | None = None


@dataclass
class CareerProvenance:
    month: str
    fiches: list[FicheProvenance]
    events: list[ProvenanceEvent]
    warnings: list[str] = field(default_factory=list)
    missing_ids: list[str] = field(default_factory=list)

    def presence_text(self):
        if not self.month:
            return "Mois consulté non défini : présence mensuelle non vérifiée."
        visible = [f.legacy_id for f in self.fiches if f.visible_in_month]
        absent = [f.legacy_id for f in self.fiches if f.visible_in_month is False]
        prefix = f"Liste du mois consulté ({self.month}) : "
        if len(visible) == 1:
            text = prefix + f"seule la fiche {visible[0]} y figure."
        elif visible:
            text = prefix + f"{len(visible)} de ces fiches y figurent — identifiants {', '.join(visible)}."
        else:
            text = prefix + "aucune de ces fiches n’y figure."
        if absent:
            text += f" N’y figurent pas : {', '.join(absent)}."
        if len(visible) > 1:
            text += " Ces fiches apparaissent donc ensemble dans la liste de ce mois."
        if self.missing_ids:
            text += f" Fiches introuvables au chargement : {', '.join(self.missing_ids)}."
        return text


def describe_provenance(report: CareerProvenance) -> str:
    lines = [report.presence_text(), "", "QUAND LES FICHES ONT-ELLES ÉTÉ ENREGISTRÉES ?"]
    for fiche in report.fiches:
        lines.extend([
            f"\nFiche {fiche.legacy_id} — {fiche.noms or 'Nom non renseigné'}",
            f"  Enregistrement technique de l’identité en base : {fiche.created}",
            f"  Dernière mise à jour technique : {fiche.updated}",
            f"  Mois de situations rattachés : {', '.join(fiche.months) or 'Aucun mois rattaché'}",
        ])
    lines.extend(["", "Ces dates techniques concernent la base actuelle. Un mois de situation n’est pas une date de création. "
                  "L’absence d’un mois rattaché ne prouve pas à elle seule une absence de l’effectif : le contrôle de présence ci-dessus utilise la liste générale.",
                  "", "ORIGINE DOCUMENTÉE ET LIMITES DE TRAÇABILITÉ",
                  "Les dates et les liens d’import ne suffisent pas à expliquer un doublon. "
                  "Les auteurs et motifs explicitement journalisés sont détaillés ci-dessous ; sans trace suffisante, la cause reste inconnue.",
                  "", "TRACES DISPONIBLES — TOUTES PÉRIODES"])
    for event in sorted(report.events, key=lambda e: (e.when, e.legacy_id, e.source)):
        lines.extend([f"\nFiche {event.legacy_id} — {event.when} — {event.kind}",
                      f"  Source : {event.source}", f"  Auteur : {event.actor or 'Non enregistré dans cette source'}",
                      f"  {event.detail}"])
    if not report.events:
        lines.append("Aucune trace correspondante trouvée dans les sources qui ont pu être lues.")
    if report.warnings:
        lines.extend(["", "LECTURES INCOMPLÈTES", *report.warnings])
    lines.extend(["", "Dates techniques/imports : heure enregistrée en base. Dates du journal : UTC. "
                  "Ce relevé est une consultation ; aucune fiche n’a été modifiée."])
    return "\n".join(lines)


def _payload(value):
    if isinstance(value, (str, bytes)):
        try:
            value = json.loads(value)
        except (ValueError, UnicodeError):
            return {}
    return value if isinstance(value, dict) else {}


def _audit_target(payload, reference, legacy_ids, career_owners):
    """Les anciens journaux peuvent employer un id carrière au lieu de l'id agent."""
    explicit = set()
    for key in ("before", "after"):
        snapshot = payload.get(key)
        if isinstance(snapshot, dict) and snapshot.get("access_join_id"):
            explicit.add(str(snapshot["access_join_id"]).strip())
    if explicit:
        return sorted(explicit & legacy_ids), True
    candidates = set(career_owners.get(str(reference), ()))
    if str(reference) in legacy_ids:
        candidates.add(str(reference))
    # Une référence numérique seule ne prouve pas la liaison : ne pas l'utiliser
    # pour conclure qui a créé la fiche ou pourquoi elle a été créée.
    return sorted(candidates & legacy_ids), False


def load_career_provenance(settings, legacy_ids) -> CareerProvenance:
    if not session_has_permission("effectifs.liste"):
        raise PermissionError("Consultation des fiches non autorisée.")
    ids = sorted({str(value).strip() for value in legacy_ids if str(value).strip()})
    month = resolve_liste_view_mois(settings) or ""
    if not ids:
        return CareerProvenance(month, [], [])
    marks = ",".join(["%s"] * len(ids))
    connection = MySQLConnection(settings)
    fiches, events, warnings = {}, [], []
    career_owners = {}
    with connection.connect(read_only=True) as conn:
        with conn.cursor() as cursor:
            cursor.execute(f"SELECT legacy_access_id,noms,cree_le,updated_le FROM agent "
                           f"WHERE legacy_access_id IN ({marks}) ORDER BY legacy_access_id", tuple(ids))
            for legacy, name, created, updated in cursor.fetchall():
                key = str(legacy).strip()
                fiches[key] = FicheProvenance(key, name or "", str(created or "Non enregistrée"), str(updated or "Non enregistrée"))
            cursor.execute("SELECT a.legacy_access_id,c.id,p.annee_mois,c.cree_le,c.import_lot_id "
                           "FROM agent a JOIN carriere_situation c ON c.agent_id=a.id "
                           "LEFT JOIN sme_periode p ON p.id=c.sme_periode_id "
                           f"WHERE a.legacy_access_id IN ({marks}) ORDER BY p.annee_mois,c.id", tuple(ids))
            lot_links = []
            for legacy, career_id, ym, created, lot in cursor.fetchall():
                key = str(legacy).strip()
                career_owners.setdefault(str(career_id), set()).add(key)
                if key in fiches and ym and str(ym) not in fiches[key].months:
                    fiches[key].months.append(str(ym))
                if lot is not None:
                    lot_links.append((key, career_id, str(ym or "Sans mois"), int(lot)))
            if month:
                sql, params = _select_liste(resolve_carriere_scope_mois(settings))
                cursor.execute(f"{sql} WHERE a.legacy_access_id IN ({marks})", params + tuple(ids))
                visible = {str(row[1]).strip() for row in cursor.fetchall()}
                for key, fiche in fiches.items():
                    fiche.visible_in_month = key in visible
            if lot_links:
                lots = sorted({link[3] for link in lot_links})
                try:
                    cursor.execute("SELECT id,chemin_fichier,importe_le FROM import_lot WHERE id IN ("
                                   + ",".join(["%s"] * len(lots)) + ")", tuple(lots))
                    imports = {int(row[0]): row[1:] for row in cursor.fetchall()}
                    for key, career_id, ym, lot in lot_links:
                        record = imports.get(lot)
                        if record:
                            filename, timestamp = record
                            events.append(ProvenanceEvent(key, str(timestamp or "Non enregistrée"), "Situation liée à un import",
                                f"import_lot #{lot} — {PureWindowsPath(filename).name if filename else 'Fichier non renseigné'}",
                                detail=f"Situation {career_id}, mois {ym}. Ce lien ne prouve pas que cet import a créé l’identité."))
                        else:
                            warnings.append(f"Fiche {key} : lot {lot} référencé mais introuvable.")
                except Exception:
                    warnings.append("Lecture des lots d’import indisponible ; l’origine des imports n’a pas pu être vérifiée.")
            references = sorted(set(ids) | set(career_owners))
            try:
                cursor.execute("SELECT id,ts_utc,action,actor,personnel_id,sme_mois_context,payload FROM audit_journal "
                               "WHERE module='personnel' AND (personnel_id IN (" + ",".join(["%s"] * len(references)) + ") "
                               "OR JSON_UNQUOTE(JSON_EXTRACT(payload,'$.after.access_join_id')) IN (" + marks + ") "
                               "OR JSON_UNQUOTE(JSON_EXTRACT(payload,'$.before.access_join_id')) IN (" + marks + ")) ORDER BY ts_utc,id",
                               tuple(references) + tuple(ids) + tuple(ids))
                for event_id, ts, action, actor, reference, ym, raw in cursor.fetchall():
                    payload = _payload(raw)
                    targets, confirmed = _audit_target(payload, reference, set(ids), career_owners)
                    motif = str(payload.get("edit_motif") or "Motif non renseigné")
                    for key in targets:
                        nature = {"create": "Création journalisée", "update": "Modification journalisée", "delete": "Suppression journalisée"}.get(action, str(action))
                        if not confirmed:
                            nature += " — liaison à confirmer"
                        events.append(ProvenanceEvent(key, f"{ts} UTC", nature, f"audit_journal #{event_id}", str(actor or "Auteur non renseigné"),
                            f"Mois : {ym or 'non renseigné'}. {motif}. " +
                            ("L’identité est explicitement référencée dans la trace." if confirmed else "Ancienne référence numérique seule ; ne prouve pas l’origine de cette fiche.")))
            except Exception:
                warnings.append("Journal d’audit indisponible ; aucune conclusion sur l’auteur ou le motif de création n’est possible.")
    return CareerProvenance(month, [fiches[key] for key in ids if key in fiches], events, warnings,
                            [key for key in ids if key not in fiches])
