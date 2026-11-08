"""Reviewable monthly reference changes, checked again before atomic application."""
from dataclasses import dataclass
from sme_gend.repositories.mysql_connection import mysql_database_key
from sme_gend.config.liste_generale_scope import resolve_liste_view_mois
from sme_gend.services.shared_reference_write import shared_reference_write
from sme_gend.services.fonction_structure_policy import structure_fonctions_non_liees
from sme_gend.services.operation_progress import operation_progress
from sme_gend.services.operation_commit_progress import finish_after_commit


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
    with operation_progress((('read', 'Lecture des fonctions et de leur utilisation'),
                             ('compare', 'Analyse des rattachements manquants'),
                             ('validate', 'Contrôle du plan de reprise'))) as progress:
        progress.start('read', detail='Lecture du référentiel et des fiches du mois…')
        return _prepare_backfill(service, progress)


def _prepare_backfill(service, progress):
    context=_context(service)
    links=service.fonction_links_snapshot_all()
    observed=tuple(sorted(service.fonction_observed_usage()))
    progress.finish('read', detail=f'{len(observed)} utilisation(s) de fonction à examiner.')
    functions={str(r[1]).strip().casefold():int(r[0]) for r in links.owner}
    structures={str(r[1]).strip().casefold():int(r[0]) for r in links.choices}
    linked={(int(r[0]),int(r[1])) for r in links.links}
    items=[];seen=set()
    progress.start('compare', total=len(observed), detail='Recherche des fonctions et rattachements à créer…')
    for index,(code,libelle,count) in enumerate(observed,1):
        if (index-1)%100==0:
            progress.update('compare', current=index-1, total=len(observed),
                            detail=f'{index-1}/{len(observed)} utilisation(s) examinée(s).')
        key=(code.casefold(),libelle.casefold())
        sid=structures.get(key[0]);fid=functions.get(key[1])
        if sid is None or key in seen or structure_fonctions_non_liees(code):continue
        seen.add(key)
        if fid is not None and (sid,fid) in linked:continue
        items.append(BackfillItem(code,libelle,fid is None))
    progress.finish('compare', detail=f'{len(items)} rattachement(s) proposé(s).')
    progress.start('validate', detail='Vérification du contexte avant de présenter le plan…')
    if context!=_context(service):raise ValueError("Le contexte a changé pendant la préparation.")
    progress.finish('validate', detail=f'Plan prêt : {len(items)} rattachement(s) à vérifier.')
    return BackfillPlan(context,links,observed,tuple(items))


def apply_backfill(service,plan):
    with operation_progress((('validate', 'Vérification du plan et des fiches du mois'),
                             ('links', 'Reprise des fonctions et rattachements'),
                             ('commit', 'Validation des rattachements dans MySQL'))) as progress:
        progress.start('validate', detail='Contrôle du contexte et de la révision du référentiel…')
        result = _apply_backfill(service, plan, progress)
        finish_after_commit(service.settings, progress, 'commit', detail=f'{result} rattachement(s) enregistré(s).')
        return result


def _apply_backfill(service,plan,progress):
    if plan.context!=_context(service):raise ValueError("Le contexte a changé. Préparez à nouveau la reprise.")
    with shared_reference_write(service.settings):
        service.validate_links_snapshot(plan.links)
        if tuple(sorted(service.fonction_observed_usage()))!=plan.observed:
            raise ValueError("Les fiches du mois ont changé. Préparez à nouveau la reprise.")
        progress.finish('validate', detail='Plan et fiches du mois contrôlés.')
        progress.start('links', total=len(plan.items), detail='Création des fonctions et des rattachements absents…')
        for index,item in enumerate(plan.items,1):
            service.fonction_ensure_catalog_and_link(item.structure,item.libelle)
            if index%100==0 or index==len(plan.items):
                progress.update('links', current=index, total=len(plan.items),
                                detail=f'{index}/{len(plan.items)} rattachement(s) traité(s).')
        progress.finish('links', detail=f'{len(plan.items)} rattachement(s) traité(s).')
        progress.start('commit', detail='Validation de la transaction et de la révision du référentiel…')
    return len(plan.items)
