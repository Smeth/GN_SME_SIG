"""Add a diploma to selected agents, with one atomic transaction per agent."""
from dataclasses import dataclass, field
from datetime import date
from sme_gend.models.agent_dossier import DIPLOME_CATEGORIES
from sme_gend.services.access_date_parse import parse_access_date


@dataclass
class BulkDiplomaResult:
    added: int = 0
    existing: int = 0
    duplicate_agents: int = 0
    errors: list[str] = field(default_factory=list)
    details: list[str] = field(default_factory=list)


def add_bulk_diploma(settings, rows, values, report=lambda message: None, *, permission='effectifs.edition'):
    from sme_gend.services.action_permissions import assert_any_permission
    from sme_gend.services.sensitive_data_policy import assert_may_add_diplome
    from sme_gend.services.sme_editing_policy import evaluate_editing_state_for_settings
    from sme_gend.services.personnel_service import _personnel_write
    from sme_gend.services.agent_dossier_service import AgentDossierService
    from sme_gend.repositories.write_guard import lock_agent
    from sme_gend.repositories.mysql_connection import CommittedMirrorError
    from sme_gend.services.stage_completion import diploma_display_result, find_identical_diploma
    from sme_gend.config.liste_generale_scope import reference_date_for_mois
    if permission not in ('effectifs.edition', 'effectifs.sme_dpf'):
        raise ValueError('Permission de saisie inconnue.')
    assert_any_permission(permission)
    assert_may_add_diplome()
    values = dict(values)
    values['date_obtention'] = parse_access_date(values.get('date_obtention'))
    values['libelle'] = str(values.get('libelle') or '').strip()
    if values.get('categorie') not in DIPLOME_CATEGORIES or not values['libelle'] or values['date_obtention'] is None:
        raise ValueError('Catégorie, libellé et date d’obtention du diplôme obligatoires.')
    service = AgentDossierService(settings)
    result = BulkDiplomaResult()
    seen = set()
    for row in rows:
        label = row.noms or row.id
        try:
            assert_any_permission(permission)
            assert_may_add_diplome()
            state = evaluate_editing_state_for_settings(settings)
            if state.locked:
                raise PermissionError(state.detail)
            with _personnel_write(settings, reason='bulk_diploma'):
                aid = service.agent_id_for_row(row)
                if aid in seen:
                    result.duplicate_agents += 1
                    continue
                if lock_agent(settings, agent_id=aid) is None:
                    raise ValueError('Agent introuvable. Actualisez la liste.')
                diplomas = service.list_diplomes(row)
                same = find_identical_diploma(diplomas, values)
                did = same.id if same else service.add_diplome(row, **values)
            seen.add(aid)
            if same:
                result.existing += 1
                result.details.append(f'{label} : diplôme déjà présent, données existantes conservées.')
            else:
                result.added += 1
                try:
                    cutoff = reference_date_for_mois(settings.mysql_view_mois) if settings.mysql_view_mois else date.today()
                    explanation = diploma_display_result(values, service.list_diplomes(row), did, cutoff)
                except Exception:
                    explanation = 'diplôme enregistré ; actualisez pour vérifier son affichage'
                result.details.append(f'{label} : {explanation}.')
        except CommittedMirrorError:
            seen.add(aid)
            if same:
                result.existing += 1
            else:
                result.added += 1
            result.details.append(f'{label} : enregistrement confirmé dans MySQL ; copie locale du journal à vérifier. Ne recommencez pas l’ajout.')
        except Exception as exc:
            result.errors.append(f'{label} : {exc} — aucun ajout pour cet agent.')
        finally:
            report(f'{label} — traité')
    return result
