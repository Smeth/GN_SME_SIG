"""Reviewable monthly reference changes, checked again before atomic application."""
from dataclasses import dataclass
from sme_gend.repositories.mysql_connection import mysql_database_key
from sme_gend.config.liste_generale_scope import resolve_liste_view_mois
from sme_gend.services.shared_reference_write import shared_reference_write
from sme_gend.services.fonction_structure_policy import structure_fonctions_non_liees


@dataclass(frozen=True)
class BackfillItem:
    structure: str
    libelle: str
    create: bool


@dataclass(frozen=True)
class BackfillPlan:
    context: tuple
    links: object
    observed: tuple
    items: tuple[BackfillItem,...]


def _context(service):
    return mysql_database_key(service.settings), resolve_liste_view_mois(service.settings)


def prepare_backfill(service):
    context=_context(service)
    links=service.fonction_links_snapshot_all()
    observed=tuple(sorted(service.fonction_observed_usage()))
    functions={str(r[1]).strip().casefold():int(r[0]) for r in links.owner}
    structures={str(r[1]).strip().casefold():int(r[0]) for r in links.choices}
    linked={(int(r[0]),int(r[1])) for r in links.links}
    items=[];seen=set()
    for code,libelle,count in observed:
        key=(code.casefold(),libelle.casefold())
        sid=structures.get(key[0]);fid=functions.get(key[1])
        if sid is None or key in seen or structure_fonctions_non_liees(code):continue
        seen.add(key)
        if fid is not None and (sid,fid) in linked:continue
        items.append(BackfillItem(code,libelle,fid is None))
    if context!=_context(service):raise ValueError("Le contexte a changé pendant la préparation.")
    return BackfillPlan(context,links,observed,tuple(items))


def apply_backfill(service,plan):
    if plan.context!=_context(service):raise ValueError("Le contexte a changé. Préparez à nouveau la reprise.")
    with shared_reference_write(service.settings):
        service.validate_links_snapshot(plan.links)
        if tuple(sorted(service.fonction_observed_usage()))!=plan.observed:
            raise ValueError("Les fiches du mois ont changé. Préparez à nouveau la reprise.")
        for item in plan.items:
            service.fonction_ensure_catalog_and_link(item.structure,item.libelle)
    return len(plan.items)
