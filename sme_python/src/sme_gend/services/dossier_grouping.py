"""Présentation regroupée sans supprimer ni modifier les pièces mensuelles."""
from dataclasses import dataclass, replace
from datetime import timedelta
from sme_gend.services.access_date_parse import parse_access_date


def norm(value):
    return " ".join(str(value or "").split()).casefold()


def normalized_label_sql(expr):
    """Identifiants SQL internes uniquement ; les valeurs restent paramétrées."""
    return (f"UPPER(TRIM(REGEXP_REPLACE(REPLACE(COALESCE({expr}, ''), "
            "CONVERT(0xC2A0 USING utf8mb4), ' '), '[[:space:]]+', ' ')))")


@dataclass
class DossierGroup:
    row: object
    members: list
    months: tuple = ()

    @property
    def month_label(self):
        if not self.months:
            return "—"
        if len(self.months) == 1:
            return self.months[0]
        return f"{self.months[0]} → {self.months[-1]} ({len(self.months)} mois attestés)"

    @property
    def detail(self):
        return "Mois attestés : " + (", ".join(self.months) or "non renseignés") + f"\n{len(self.members)} fiche(s) source conservée(s)."


def group_diplomas(rows, snapshots=()):
    dated = {}
    for r in rows:
        dated.setdefault((norm(r.libelle), r.categorie), set()).update([r.date_obtention] if r.date_obtention else [])
    groups = {}
    for r in rows:
        dates = dated[(norm(r.libelle), r.categorie)]
        effective = r.date_obtention or (next(iter(dates)) if len(dates) == 1 else None)
        key = (norm(r.libelle), r.categorie, effective,
               norm(r.lieu_obtention), norm(r.mention), norm(r.etablissement))
        groups.setdefault(key, []).append(r)
    result = []
    for key, members in groups.items():
        rep = next((r for r in members if r.date_obtention), members[0])
        months = tuple(sorted({s.annee_mois for s in snapshots if norm(s.diplome) == norm(rep.libelle)
                              and (not rep.date_obtention or not parse_access_date(s.date_obtention)
                                   or parse_access_date(s.date_obtention) == rep.date_obtention)}))
        result.append(DossierGroup(rep, members, months))
    return result


def group_affectations(rows, snapshots=()):
    groups = []
    for r in sorted(rows, key=lambda r: (r.date_affectation, r.id)):
        interrupted = groups and any(
            groups[-1].row.date_affectation.strftime('%Y-%m') < s.annee_mois
            < r.date_affectation.strftime('%Y-%m') and s.structure
            and norm(s.structure) != norm(r.structure_code) for s in snapshots)
        if groups and not interrupted and norm(groups[-1].row.structure_code) == norm(r.structure_code):
            groups[-1].members.append(r)
        else:
            groups.append(DossierGroup(r, [r]))
    for i, g in enumerate(groups):
        lower = g.row.date_affectation.strftime("%Y-%m")
        upper = groups[i + 1].row.date_affectation.strftime("%Y-%m") if i+1 < len(groups) else "9999-99"
        g.months = tuple(sorted({s.annee_mois for s in snapshots if lower <= s.annee_mois < upper
                                and norm(s.structure) == norm(g.row.structure_code)}))
    return list(reversed(groups))


def group_stages(rows):
    # Les fiches sans période restent individuelles : leur continuité n'est pas prouvée.
    groups = []
    buckets = {}
    for r in rows:
        key = tuple(norm(getattr(r, a)) for a in ('type_stage', 'nature', 'lieu', 'observation')) + (norm(getattr(r, 'reference_dossier', None)), norm(getattr(r, 'session', None)))
        buckets.setdefault(key, []).append(r)
    for bucket in buckets.values():
        current = None
        for r in sorted(bucket, key=lambda r: (r.periode_sme or '', r.date_debut or '', r.id or 0)):
            start, end = parse_access_date(r.date_debut), parse_access_date(r.date_fin)
            previous = current.members[-1] if current else None
            prev_end = parse_access_date(previous.date_fin) if previous else None
            adjacent = (previous and previous.periode_sme and r.periode_sme
                        and month_index(r.periode_sme) - month_index(previous.periode_sme) in (0, 1))
            if current and adjacent and start and prev_end and start <= prev_end + timedelta(days=1):
                current.members.append(r)
            else:
                current = DossierGroup(r, [r]); groups.append(current)
    for g in groups:
        g.months = tuple(sorted({r.periode_sme for r in g.members if r.periode_sme}))
        starts = [parse_access_date(r.date_debut) for r in g.members if parse_access_date(r.date_debut)]
        ends = [parse_access_date(r.date_fin) for r in g.members if parse_access_date(r.date_fin)]
        g.row = replace(g.row, date_debut=min(starts).isoformat() if starts else g.row.date_debut,
                        date_fin=(max(ends).isoformat() if ends else g.row.date_fin)
                        if all(parse_access_date(r.date_fin) for r in g.members) else None,
                        periode_sme=g.month_label if g.months else g.row.periode_sme)
    return sorted(groups, key=lambda g: g.row.date_debut or '', reverse=True)


def month_index(ym):
    try:
        return int(ym[:4])*12 + int(ym[5:7])
    except (ValueError, TypeError):
        return -100000


def function_history(snapshots):
    result = []
    previous = None
    for s in sorted(snapshots, key=lambda s: s.annee_mois):
        state = (norm(s.structure), norm(s.fonction), norm(s.subdivision_arme))
        if previous is None or state != previous:
            result.append((s.annee_mois, s.structure or '—', s.fonction or '—',
                           s.subdivision_arme or '—', 'Situation initiale constatée' if previous is None else 'Changement constaté'))
        previous = state
    return list(reversed(result))


def function_events(rows):
    """Même règle pour l'export fondé sur les fiches de poste du dossier."""
    result, previous = [], None
    for row in sorted(rows, key=lambda r: (r.date_affectation, r.id)):
        state = (norm(row.structure_code), norm(row.fonction), norm(row.subdivision_arme))
        if state != previous:
            result.append(row)
        previous = state
    return list(reversed(result))
