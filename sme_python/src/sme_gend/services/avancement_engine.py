"""Calcul d'éligibilité à l'avancement — sans base ni interface."""

from __future__ import annotations

from datetime import date

from sme_gend.models.avancement import (
    ACTE_LABELS,
    AVIS_DEFAVORABLE,
    AVIS_FAVORABLE,
    DIPLOME_LABELS,
    STATUT_A_PREPARER,
    STATUT_AVIS_DEFAVORABLE,
    STATUT_ELIGIBLE,
    STATUT_FENETRE,
    STATUT_HORS,
    STATUT_INCOMPLET,
    STATUT_SUIVI,
    VOIE_TABLEAU,
    AgentAvancementInput,
    AvancementRegle,
    ConditionResult,
    VigilanceRow,
    default_rules,
)
from sme_gend.services.grade_label_format import normalize_grade_key
from sme_gend.services.statutory_limits import (
    article98_limits,
    canonical_grade,
)

HORIZON_JOURS = 365

# Grade suivant. L'adjudant-chef ouvre deux voies : adjudant-major ou sous-lieutenant.
_NEXT: dict[str, tuple[str, ...]] = {
    "MDL": ("MDC",),
    "MDC": ("ADJ",),
    "ADJ": ("ADC",),
    "ADC": ("ADM", "SLT"),
    "ADM": (),
    "SLT": ("LTN",),
    "LTN": ("CNE",),
    "CNE": ("CDT",),
    "CDT": ("LCL",),
    "LCL": ("COL",),
    "COL": ("CLM",),
    "CLM": ("GDB",),
    "GDB": ("GDD",),
    "GDD": ("GCA",),
    "GCA": ("GDA",),
    "GDA": (),
}

# Du plus élevé au plus court, pour reconnaître un diplôme dans un libellé libre.
_DIPLOME_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("EMS2", ("enseignement militaire superieur 2", "ems 2", "ems2")),
    ("EMS1", ("enseignement militaire superieur 1", "ems 1", "ems1")),
    ("CPOS", ("cours de perfectionnement des officiers", "cours de perfectionnement", "cpos")),
    ("DQSG2", ("diplome de qualification superieur de gendarmerie 2", "dqsg 2", "dqsg n 2", "dqsg2")),
    ("DQSG1", ("diplome de qualification superieur de gendarmerie 1", "dqsg 1", "dqsg n 1", "dqsg1")),
    ("CERT_TECH", ("certificat technique", "certificat de specialite")),
    ("OPJ", ("officier de police judiciaire", "opj")),
    ("BCG", ("brevet de chef de groupe", "bcg")),
)
_DIPLOME_RANK = {"DQSG1": 1, "DQSG2": 2, "CPOS": 3, "EMS1": 4, "EMS2": 5}

_BELOW_MDL: tuple[str, ...] = (
    "gendarme adjoint",
    "gendarme",
    "brigadier chef",
    "brigadier",
    "sergent chef",
    "sergent",
    "soldat",
    "sch",
    "sgt",
    "sdt",
    "brc",
    "brg",
    "gad",
    "gnd",
)


def rules_by_cible(
    regles: tuple[AvancementRegle, ...] | list[AvancementRegle] | None = None,
) -> dict[str, AvancementRegle]:
    source = regles if regles is not None else default_rules()
    return {regle.grade_cible: regle for regle in source}


def add_years(start: date, years: int) -> date:
    try:
        return start.replace(year=start.year + years)
    except ValueError:
        return start.replace(year=start.year + years, day=28)


def decembre_limite(start: date, years: int) -> date:
    """Même convention que les attestations : 31 décembre de l'année anniversaire."""
    return date(start.year + years, 12, 31)


def elapsed_label(start: date | None, on: date) -> str:
    if start is None or on < start:
        return "—"
    months = (on.year - start.year) * 12 + (on.month - start.month)
    if on.day < start.day:
        months -= 1
    months = max(0, months)
    years, rest = divmod(months, 12)
    if years and rest:
        return f"{years} an{'s' if years > 1 else ''} {rest} mois"
    if years:
        return f"{years} an{'s' if years > 1 else ''}"
    return f"{rest} mois"


def detect_diplomes(labels: tuple[str, ...] | list[str]) -> set[str]:
    found: set[str] = set()
    for label in labels:
        nk = normalize_grade_key(label)
        if not nk:
            continue
        tokens = set(nk.split())
        for code, aliases in _DIPLOME_ALIASES:
            for alias in aliases:
                if " " in alias:
                    if alias in nk:
                        found.add(code)
                        break
                elif alias in tokens or nk == alias:
                    found.add(code)
                    break
    return found


def diplome_ok(regle: AvancementRegle, found: set[str]) -> bool:
    if not regle.diplomes:
        return True
    if found.intersection(regle.diplomes):
        return True
    if not regle.diplome_ou_superieur:
        return False
    ranks = [_DIPLOME_RANK[code] for code in regle.diplomes if code in _DIPLOME_RANK]
    if not ranks:
        return False
    minimum = min(ranks)
    return any(_DIPLOME_RANK.get(code, 0) >= minimum for code in found)


def _diplome_requis_label(regle: AvancementRegle) -> str:
    names = [DIPLOME_LABELS.get(code, code) for code in regle.diplomes]
    if not names:
        return ""
    text = " ou ".join(names)
    if regle.diplome_ou_superieur:
        text += " ou diplôme supérieur homologué"
    return text


def cibles_possibles(grade_raw: str) -> list[str]:
    """Grades cibles calculables. Vide si plafond ou grade hors barème."""
    code = canonical_grade(grade_raw)
    if code in _NEXT:
        return list(_NEXT[code])
    if _is_below_mdl(grade_raw):
        return ["MDL"]
    return []


def _is_below_mdl(raw: str) -> bool:
    nk = normalize_grade_key(raw)
    if not nk:
        return False
    for alias in sorted(_BELOW_MDL, key=len, reverse=True):
        if nk == alias or nk.startswith(alias + " "):
            return True
    return False


def _years_required(regle: AvancementRegle, issu_ecole: bool) -> int | None:
    if issu_ecole and regle.annees_grade_ecole is not None:
        return regle.annees_grade_ecole
    return regle.annees_grade


def _limite_date(agent: AgentAvancementInput) -> date | None:
    bornes: list[date] = []
    limits = article98_limits(agent.grade_raw)
    if limits is None or agent.date_naissance is None and agent.date_service is None:
        if limits is None:
            return None
    age_limit, service_limit = limits
    if agent.date_naissance is not None and age_limit > 0:
        bornes.append(decembre_limite(agent.date_naissance, age_limit))
    if agent.date_service is not None and service_limit > 0:
        bornes.append(decembre_limite(agent.date_service, service_limit))
    return min(bornes) if bornes else None


def _row(
    agent: AgentAvancementInput,
    *,
    grade_code: str | None,
    cible: str | None,
    regle: AvancementRegle | None,
    statut: str,
    echeance: date | None,
    limite: date | None,
    message: str,
    conditions: list[ConditionResult],
    on: date,
    found: set[str],
) -> VigilanceRow:
    return VigilanceRow(
        agent=agent,
        grade_code=grade_code,
        grade_cible=cible,
        regle=regle,
        statut=statut,
        echeance=echeance,
        limite=limite,
        message=message,
        conditions=conditions,
        anciennete_label=elapsed_label(agent.date_grade, on),
        services_label=elapsed_label(agent.date_service, on),
        diplome_label=", ".join(sorted(found)) if found else "—",
    )


def evaluer(
    agent: AgentAvancementInput,
    regles: dict[str, AvancementRegle] | None = None,
    *,
    on: date | None = None,
    horizon_days: int = HORIZON_JOURS,
    cible_force: str | None = None,
) -> VigilanceRow:
    """Statut d'un agent pour un grade cible (le suivant, ou ``cible_force``)."""
    today = on or date.today()
    book = regles if regles is not None else rules_by_cible()
    found = detect_diplomes(agent.diplomes)
    code = canonical_grade(agent.grade_raw)
    limite = _limite_date(agent)

    cible = (cible_force or "").strip() or None
    if cible is None:
        possibles = cibles_possibles(agent.grade_raw)
        if len(possibles) == 1:
            cible = possibles[0]
        elif len(possibles) > 1:
            cible = possibles[0]
        elif code == "GDA":
            return _row(
                agent,
                grade_code=code,
                cible=None,
                regle=None,
                statut=STATUT_HORS,
                echeance=None,
                limite=limite,
                message="Dernier grade du barème.",
                conditions=[],
                on=today,
                found=found,
            )
        elif code == "ADM":
            return _row(
                agent,
                grade_code=code,
                cible=None,
                regle=None,
                statut=STATUT_HORS,
                echeance=None,
                limite=limite,
                message=(
                    "Pas d'avancement automatique après adjudant-major. "
                    "Le sous-lieutenant se prépare depuis le grade d'adjudant-chef."
                ),
                conditions=[],
                on=today,
                found=found,
            )
        else:
            return _row(
                agent,
                grade_code=code,
                cible=None,
                regle=None,
                statut=STATUT_HORS,
                echeance=None,
                limite=limite,
                message="Grade hors barème d'avancement du décret n° 2023-1764.",
                conditions=[],
                on=today,
                found=found,
            )

    regle = book.get(cible or "")
    if regle is None:
        return _row(
            agent,
            grade_code=code,
            cible=cible,
            regle=None,
            statut=STATUT_HORS,
            echeance=None,
            limite=limite,
            message=f"Aucune règle pour le grade {cible}.",
            conditions=[],
            on=today,
            found=found,
        )

    if regle.non_codifie:
        return _row(
            agent,
            grade_code=code,
            cible=cible,
            regle=regle,
            statut=STATUT_HORS,
            echeance=None,
            limite=limite,
            message=(
                "Le passage vers maréchal des logis n'est pas détaillé dans le "
                "décret n° 2023-1764. La passation se saisit directement au dossier, "
                "sans inscription au tableau."
            ),
            conditions=[],
            on=today,
            found=found,
        )

    years = _years_required(regle, agent.issu_ecole)
    missing: list[str] = []
    if years is not None and agent.date_grade is None:
        missing.append("date de passation du grade actuel")
    if regle.annees_services is not None and agent.date_service is None:
        missing.append("date d'entrée en service")
    if (regle.age_min is not None or regle.age_max is not None) and agent.date_naissance is None:
        missing.append("date de naissance")
    if missing:
        return _row(
            agent,
            grade_code=code,
            cible=cible,
            regle=regle,
            statut=STATUT_INCOMPLET,
            echeance=None,
            limite=limite,
            message="Donnée manquante : " + ", ".join(missing) + ".",
            conditions=[
                ConditionResult("dossier", "Dossier", False, ", ".join(missing)),
            ],
            on=today,
            found=found,
        )

    conditions: list[ConditionResult] = []
    gates: list[date] = []
    statutory_ok = True

    if years is not None and agent.date_grade is not None:
        due = add_years(agent.date_grade, years)
        ok = today >= due
        gates.append(due)
        statutory_ok = statutory_ok and ok
        ecole = " (2 ans, issu d'école)" if agent.issu_ecole and regle.annees_grade_ecole == years else ""
        prec = regle.grade_precedent if regle.grade_precedent else "grade actuel"
        conditions.append(
            ConditionResult(
                "grade",
                f"Temps au grade ({years} ans de {prec}){ecole}",
                ok,
                f"Acquis le {due.strftime('%d/%m/%Y')} — ancienneté {elapsed_label(agent.date_grade, today)}.",
            )
        )

    if regle.annees_services is not None and agent.date_service is not None:
        due = add_years(agent.date_service, regle.annees_services)
        ok = today >= due
        gates.append(due)
        statutory_ok = statutory_ok and ok
        conditions.append(
            ConditionResult(
                "services",
                f"Services effectifs ({regle.annees_services} ans)",
                ok,
                f"Acquis le {due.strftime('%d/%m/%Y')} — {elapsed_label(agent.date_service, today)}.",
            )
        )

    if regle.diplomes:
        ok = diplome_ok(regle, found)
        statutory_ok = statutory_ok and ok
        conditions.append(
            ConditionResult(
                "diplome",
                f"Diplôme ({_diplome_requis_label(regle)})",
                ok,
                "Reconnu dans le dossier." if ok else "Aucun diplôme correspondant dans le dossier.",
            )
        )

    if regle.age_min is not None and agent.date_naissance is not None:
        due = add_years(agent.date_naissance, regle.age_min)
        ok = today >= due
        gates.append(due)
        statutory_ok = statutory_ok and ok
        conditions.append(
            ConditionResult(
                "age_min",
                f"Âge minimum ({regle.age_min} ans)",
                ok,
                f"Atteint le {due.strftime('%d/%m/%Y')}.",
            )
        )

    age_cap: date | None = None
    if regle.age_max is not None and agent.date_naissance is not None:
        age_cap = add_years(agent.date_naissance, regle.age_max + 1)
        ok = today < age_cap
        statutory_ok = statutory_ok and ok
        conditions.append(
            ConditionResult(
                "age_max",
                f"Âge maximum ({regle.age_max} ans, concours)",
                ok,
                f"La limite est dépassée à compter du {age_cap.strftime('%d/%m/%Y')}.",
            )
        )

    if regle.condition_manuelle == "temps_commandement":
        ok = agent.temps_commandement is True
        statutory_ok = statutory_ok and ok
        if agent.temps_commandement is True:
            detail = "Temps de commandement confirmé."
        elif agent.temps_commandement is False:
            detail = "Temps de commandement non validé."
        else:
            detail = "À confirmer : le décret ne fixe pas une durée calculable."
        conditions.append(
            ConditionResult(
                "commandement",
                "Temps de commandement dans le grade précédent",
                ok,
                detail,
            )
        )

    echeance = max(gates) if gates else today
    window_points = [point for point in (limite, age_cap) if point is not None]
    window_end = min(window_points) if window_points else None
    fenetre = False
    fenetre_msg = ""
    if window_end is not None and echeance > window_end:
        fenetre = True
        fenetre_msg = (
            "La limite d'âge, de service ou du concours arrive avant que le temps "
            f"requis soit acquis ({window_end.strftime('%d/%m/%Y')})."
        )
    elif window_end is not None and window_end < today and not statutory_ok:
        fenetre = True
        fenetre_msg = (
            f"Limite atteinte le {window_end.strftime('%d/%m/%Y')} "
            "avant l'ensemble des conditions d'avancement."
        )
    elif limite is not None and limite < today and statutory_ok:
        fenetre = True
        fenetre_msg = (
            f"Limite d'âge ou de service atteinte le {limite.strftime('%d/%m/%Y')} "
            "(article 98)."
        )

    if fenetre:
        return _row(
            agent,
            grade_code=code,
            cible=cible,
            regle=regle,
            statut=STATUT_FENETRE,
            echeance=echeance,
            limite=window_end,
            message=fenetre_msg,
            conditions=conditions,
            on=today,
            found=found,
        )

    if not statutory_ok:
        proche = (echeance - today).days <= horizon_days
        diploma_blocking = any(c.code == "diplome" and c.ok is False for c in conditions)
        if proche or (diploma_blocking and echeance <= today):
            message = (
                "Échéance dans l'horizon de préparation."
                if proche
                else "Le temps est acquis, il reste une condition à réunir."
            )
            return _row(
                agent,
                grade_code=code,
                cible=cible,
                regle=regle,
                statut=STATUT_A_PREPARER,
                echeance=echeance,
                limite=window_end,
                message=message,
                conditions=conditions,
                on=today,
                found=found,
            )
        return _row(
            agent,
            grade_code=code,
            cible=cible,
            regle=regle,
            statut=STATUT_SUIVI,
            echeance=echeance,
            limite=window_end,
            message="Conditions non encore réunies, échéance au-delà de l'horizon.",
            conditions=conditions,
            on=today,
            found=found,
        )

    if agent.maniere_servir == AVIS_DEFAVORABLE:
        conditions.append(
            ConditionResult("avis", "Manière de servir", False, "Avis défavorable.")
        )
        return _row(
            agent,
            grade_code=code,
            cible=cible,
            regle=regle,
            statut=STATUT_AVIS_DEFAVORABLE,
            echeance=echeance,
            limite=window_end,
            message="Les conditions de temps sont réunies, l'avis est défavorable.",
            conditions=conditions,
            on=today,
            found=found,
        )

    if agent.maniere_servir != AVIS_FAVORABLE:
        detail = "Avis non saisi." if not agent.maniere_servir else "Avis réservé."
        conditions.append(ConditionResult("avis", "Manière de servir", False, detail))
        return _row(
            agent,
            grade_code=code,
            cible=cible,
            regle=regle,
            statut=STATUT_A_PREPARER,
            echeance=echeance,
            limite=window_end,
            message="Conditions statutaires réunies. Il reste à recueillir un avis favorable.",
            conditions=conditions,
            on=today,
            found=found,
        )

    conditions.append(
        ConditionResult("avis", "Manière de servir", True, "Avis favorable.")
    )
    if regle.inscription_tableau and regle.voie == VOIE_TABLEAU:
        # Keep the administrative requirement visible without using retired
        # campaigns as proof of inscription, eligibility or actual nomination.
        conditions.append(ConditionResult("tableau", "Inscription au tableau", None,
            "Vérification administrative hors de ce module ; aucune inscription n'est attestée ici."))
    acte = ACTE_LABELS.get(regle.acte, "")
    message = "Toutes les conditions connues sont réunies."
    if acte:
        message += f" Nomination par {acte[0].lower() + acte[1:]}."
    return _row(
        agent,
        grade_code=code,
        cible=cible,
        regle=regle,
        statut=STATUT_ELIGIBLE,
        echeance=echeance,
        limite=window_end,
        message=message,
        conditions=conditions,
        on=today,
        found=found,
    )


def evaluer_agent(
    agent: AgentAvancementInput,
    regles: dict[str, AvancementRegle] | None = None,
    *,
    on: date | None = None,
    horizon_days: int = HORIZON_JOURS,
    cibles: list[str] | None = None,
) -> list[VigilanceRow]:
    """Une ligne par voie (deux pour un adjudant-chef : ADM et SLT)."""
    choisis = cibles if cibles is not None else cibles_possibles(agent.grade_raw)
    if not choisis:
        return [evaluer(agent, regles, on=on, horizon_days=horizon_days)]
    return [
        evaluer(agent, regles, on=on, horizon_days=horizon_days, cible_force=cible)
        for cible in choisis
    ]
