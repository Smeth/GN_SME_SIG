"""Synchronisation sexe / matricule militaire depuis les listes Excel de référence."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from sme_gend.config.settings import Settings
from sme_gend.models.personnel_sexe import SEXE_F, SEXE_M, normalize_sexe
from sme_gend.repositories.mysql_connection import MySQLConnection, mysql_database_key
from sme_gend.repositories.reference_write_guard import ReferenceConflictError
from sme_gend.services.shared_reference_write import shared_data_write,mark_shared_data_write

SEX_AGENT_REVISION_SQL = "SHA2(CAST(JSON_ARRAY(id,legacy_access_id,noms,sexe,matricule_militaire) AS CHAR),256)"

MatchMethod = Literal["matricule", "nom_exact", "nom_tokens", ""]


def normalize_person_name(raw: str | None) -> str:
    s = unicodedata.normalize("NFKD", str(raw or ""))
    s = s.encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^A-Za-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip().upper()


def name_token_key(raw: str | None) -> str:
    toks = sorted(t for t in normalize_person_name(raw).split() if t)
    return " ".join(toks)


def normalize_matricule_key(raw: str | None) -> str:
    s = re.sub(r"[^A-Za-z0-9]", "", str(raw or "").upper())
    s = s.lstrip("0") or "0"
    return s


@dataclass(slots=True)
class SexeReferenceRow:
    sexe: str
    matricule_militaire: str
    matricule_key: str
    noms: str
    nom_tokens: str
    grade: str
    source_file: str


@dataclass(slots=True)
class AgentSnapshot:
    agent_id: int
    legacy_access_id: str
    noms: str
    nom_tokens: str
    sexe: str | None
    matricule_militaire: str
    matricule_key: str
    write_token: str | None = field(default=None,repr=False,compare=False)
    _write_database: object | None = field(default=None,repr=False,compare=False)


@dataclass(slots=True)
class SexeSyncAction:
    agent_id: int
    legacy_access_id: str
    noms: str
    match_method: MatchMethod
    ref_sexe: str
    ref_matricule: str
    old_sexe: str | None
    old_matricule: str
    new_sexe: str | None = None
    new_matricule: str | None = None
    update_sexe: bool = False
    update_matricule: bool = False
    status: str = "OK"
    note: str = ""
    write_token: str | None = field(default=None,repr=False,compare=False)
    _write_database: object | None = field(default=None,repr=False,compare=False)


@dataclass(slots=True)
class SexeSyncPlan:
    actions: list[SexeSyncAction] = field(default_factory=list)
    unresolved: list[SexeReferenceRow] = field(default_factory=list)
    duplicate_ref_keys: list[str] = field(default_factory=list)
    stats: dict[str, int] = field(default_factory=dict)


def parse_sexe_reference_xls(path: Path, *, sexe: str) -> list[SexeReferenceRow]:
    """Lit un fichier LISTE_GENERALE_SEXE_*.xls (en-têtes sur ~4 lignes)."""
    import pandas as pd

    sexe_code = normalize_sexe(sexe)
    if sexe_code not in (SEXE_M, SEXE_F):
        raise ValueError(f"Sexe attendu M ou F, reçu : {sexe!r}")

    df = pd.read_excel(path, sheet_name=0, header=None, skiprows=4, engine="calamine")
    if df.shape[1] < 7:
        raise ValueError(f"Format inattendu dans {path.name} ({df.shape[1]} colonnes).")

    out: list[SexeReferenceRow] = []
    for row in df.itertuples(index=False):
        mat_raw = "" if row[2] is None or str(row[2]).strip().lower() == "nan" else str(row[2]).strip()
        grade = "" if row[4] is None or str(row[4]).strip().lower() == "nan" else str(row[4]).strip()
        nom = "" if row[5] is None or str(row[5]).strip().lower() == "nan" else str(row[5]).strip()
        prenom = "" if row[6] is None or str(row[6]).strip().lower() == "nan" else str(row[6]).strip()
        if not nom or nom.upper() in ("NOM(S) ET PRENOM (S)", "NAN") or grade.upper() == "GRADE":
            continue
        noms = normalize_person_name(" ".join(p for p in (nom, prenom) if p))
        if not noms:
            continue
        out.append(
            SexeReferenceRow(
                sexe=sexe_code,
                matricule_militaire=mat_raw,
                matricule_key=normalize_matricule_key(mat_raw),
                noms=noms,
                nom_tokens=name_token_key(noms),
                grade=grade,
                source_file=path.name,
            )
        )
    return out


def load_sexe_reference_bundle(
    feminin_xls: Path,
    masculin_xls: Path,
) -> tuple[list[SexeReferenceRow], list[str]]:
    refs = parse_sexe_reference_xls(feminin_xls, sexe=SEXE_F)
    refs.extend(parse_sexe_reference_xls(masculin_xls, sexe=SEXE_M))

    seen: dict[str, SexeReferenceRow] = {}
    dup_keys: list[str] = []
    for r in refs:
        if not r.matricule_key:
            continue
        prev = seen.get(r.matricule_key)
        if prev is not None and prev.sexe != r.sexe:
            dup_keys.append(r.matricule_key)
        else:
            seen[r.matricule_key] = r
    return refs, dup_keys


def _load_agents(settings: Settings) -> list[AgentSnapshot]:
    mc = MySQLConnection(settings)
    with mc.connect(read_only=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT id, legacy_access_id, noms, sexe, matricule_militaire, {SEX_AGENT_REVISION_SQL}
                FROM agent
                """
            )
            rows = cur.fetchall()
    out: list[AgentSnapshot] = []
    for agent_id, leg, noms, sexe, mat, revision in rows:
        n = normalize_person_name(noms)
        mat_s = (mat or "").strip()
        out.append(
            AgentSnapshot(
                agent_id=int(agent_id),
                legacy_access_id=str(leg or "").strip(),
                noms=n,
                nom_tokens=name_token_key(n),
                sexe=normalize_sexe(sexe),
                matricule_militaire=mat_s,
                matricule_key=normalize_matricule_key(mat_s),
                write_token=f"agent_sex:{int(agent_id)}:{revision}",
                _write_database=mysql_database_key(settings),
            )
        )
    return out


def _match_agent(
    ref: SexeReferenceRow,
    *,
    by_mat: dict[str, list[AgentSnapshot]],
    by_name: dict[str, list[AgentSnapshot]],
    by_tokens: dict[str, list[AgentSnapshot]],
) -> tuple[AgentSnapshot | None, MatchMethod, str]:
    if ref.matricule_key:
        cands = by_mat.get(ref.matricule_key) or []
        if len(cands) == 1:
            return cands[0], "matricule", ""
        if len(cands) > 1:
            return None, "", "plusieurs agents pour le même matricule normalisé"

    cands = by_name.get(ref.noms) or []
    if len(cands) == 1:
        return cands[0], "nom_exact", ""
    if len(cands) > 1:
        return None, "", "nom exact ambigu"

    cands = by_tokens.get(ref.nom_tokens) or []
    if len(cands) == 1:
        return cands[0], "nom_tokens", ""
    if len(cands) > 1:
        return None, "", "nom (tokens) ambigu"

    return None, "", "aucune correspondance"


def build_sexe_sync_plan(
    settings: Settings,
    feminin_xls: Path,
    masculin_xls: Path,
) -> SexeSyncPlan:
    refs, dup_keys = load_sexe_reference_bundle(feminin_xls, masculin_xls)
    agents = _load_agents(settings)

    by_mat: dict[str, list[AgentSnapshot]] = {}
    by_name: dict[str, list[AgentSnapshot]] = {}
    by_tokens: dict[str, list[AgentSnapshot]] = {}
    for ag in agents:
        if ag.matricule_key:
            by_mat.setdefault(ag.matricule_key, []).append(ag)
        by_name.setdefault(ag.noms, []).append(ag)
        by_tokens.setdefault(ag.nom_tokens, []).append(ag)

    plan = SexeSyncPlan(duplicate_ref_keys=dup_keys)
    for ref in refs:
        agent, method, err = _match_agent(
            ref,
            by_mat=by_mat,
            by_name=by_name,
            by_tokens=by_tokens,
        )
        if agent is None:
            plan.unresolved.append(ref)
            continue

        action = SexeSyncAction(
            agent_id=agent.agent_id,
            legacy_access_id=agent.legacy_access_id,
            noms=agent.noms,
            match_method=method,
            ref_sexe=ref.sexe,
            ref_matricule=ref.matricule_militaire,
            old_sexe=agent.sexe,
            old_matricule=agent.matricule_militaire,
            write_token=agent.write_token,
            _write_database=agent._write_database,
        )

        if agent.sexe != ref.sexe:
            action.update_sexe = True
            action.new_sexe = ref.sexe

        if ref.matricule_militaire:
            if not agent.matricule_militaire:
                action.update_matricule = True
                action.new_matricule = ref.matricule_militaire
            elif agent.matricule_key == ref.matricule_key and agent.matricule_militaire != ref.matricule_militaire:
                action.update_matricule = True
                action.new_matricule = ref.matricule_militaire
                action.note = "harmonisation format matricule"
            elif agent.matricule_key != ref.matricule_key:
                action.status = "CONFLIT_MAT"
                action.note = (
                    f"matricule agent={agent.matricule_militaire!r} "
                    f"ref={ref.matricule_militaire!r}"
                )

        if action.update_sexe or action.update_matricule:
            plan.actions.append(action)
        elif action.status != "OK":
            plan.actions.append(action)

    plan.stats = {
        "refs_total": len(refs),
        "refs_f": sum(1 for r in refs if r.sexe == SEXE_F),
        "refs_m": sum(1 for r in refs if r.sexe == SEXE_M),
        "agents_total": len(agents),
        "matched": len(refs) - len(plan.unresolved),
        "unresolved": len(plan.unresolved),
        "to_update": sum(1 for a in plan.actions if a.update_sexe or a.update_matricule),
        "update_sexe": sum(1 for a in plan.actions if a.update_sexe),
        "update_matricule": sum(1 for a in plan.actions if a.update_matricule),
        "conflicts_mat": sum(1 for a in plan.actions if a.status == "CONFLIT_MAT"),
        "duplicate_ref_mat": len(dup_keys),
    }
    return plan


def apply_sexe_sync_plan(settings: Settings, plan: SexeSyncPlan) -> dict[str, int]:
    """Validate all planned identities under locks before applying any row."""
    applied={"sexe":0,"matricule":0,"skipped_conflict":0}
    prepared={}
    for action in plan.actions:
        if action.status=="CONFLIT_MAT" and not action.update_sexe:
            applied["skipped_conflict"]+=1
            continue
        if not action.update_sexe and not action.update_matricule:
            continue
        identifier=int(action.agent_id)
        if identifier in prepared:
            raise ValueError("Plusieurs propositions visent le même agent. Corrigez les références puis régénérez le plan.")
        prefix=f"agent_sex:{identifier}:"
        if (action._write_database!=mysql_database_key(settings) or not isinstance(action.write_token,str)
            or re.fullmatch(re.escape(prefix)+r"[0-9a-f]{64}",action.write_token) is None):
            raise ValueError("Version du rapprochement indisponible. Régénérez le plan avant de l'appliquer.")
        updates={}
        if action.update_sexe:
            value=normalize_sexe(action.new_sexe)
            if value not in (SEXE_M,SEXE_F):raise ValueError("Sexe proposé invalide dans le plan.")
            updates['sexe']=value
        if action.update_matricule:
            value=str(action.new_matricule or '').strip()
            if not value:raise ValueError("Matricule proposé vide dans le plan.")
            updates['matricule_militaire']=value
        prepared[identifier]=(action,updates)
    if not prepared:return applied
    with shared_data_write(settings,reason="sex_reference_sync",mois=settings.mysql_view_mois):
        with MySQLConnection(settings).connect() as conn:
            with conn.cursor() as cur:
                identifiers=sorted(prepared)
                for start in range(0,len(identifiers),250):
                    batch=identifiers[start:start+250]
                    cur.execute("SELECT id,"+SEX_AGENT_REVISION_SQL+" FROM agent WHERE id IN ("+','.join(['%s']*len(batch))+") ORDER BY id FOR UPDATE",tuple(batch))
                    current={int(row[0]):str(row[1]) for row in cur.fetchall()}
                    for identifier in batch:
                        action,_=prepared[identifier]
                        if action.write_token!=f"agent_sex:{identifier}:{current.get(identifier,'')}":
                            raise ReferenceConflictError("Un agent a été modifié ou supprimé depuis le rapprochement. Régénérez le plan ; aucune modification enregistrée.")
                for identifier in identifiers:
                    _,updates=prepared[identifier]
                    cur.execute("UPDATE agent SET "+','.join(key+'=%s' for key in updates)+" WHERE id=%s",(*updates.values(),identifier))
                    changed=cur.rowcount>0
                    mark_shared_data_write(changed)
                    if changed:
                        applied['sexe']+=int('sexe' in updates)
                        applied['matricule']+=int('matricule_militaire' in updates)
    return applied
