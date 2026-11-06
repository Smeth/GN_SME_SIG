"""Validation of actual completion dates before starting a stage batch."""
from datetime import date
from sme_gend.services.access_date_parse import parse_access_date


def validate_stage_end(value, month, start_dates):
    end = parse_access_date(value)
    if end is None:
        raise ValueError('La date réelle de fin de stage est obligatoire.')
    if end > date.today():
        raise ValueError('Un stage ne peut pas être terminé à une date future.')
    if month and end.strftime('%Y-%m') != month:
        raise ValueError('Choisissez une date de fin dans le mois consulté, ou ouvrez le mois correspondant.')
    for value in start_dates:
        start = parse_access_date(value)
        if start is not None and end < start:
            raise ValueError('La fin de stage ne peut pas précéder le début du stage d’un agent sélectionné.')
    return end.isoformat()


def diploma_display_result(payload, diplomas, diploma_id, cutoff):
    """Explain the existing SME rule; registration and selection are distinct."""
    if payload['categorie'] != 'MILITAIRE':
        return 'diplôme civil conservé au dossier ; la SME affiche uniquement les diplômes militaires'
    obtained = parse_access_date(payload.get('date_obtention'))
    if obtained and obtained > cutoff:
        return 'diplôme conservé au dossier ; sa date d’obtention est postérieure au mois consulté'
    eligible = [d for d in diplomas if d.categorie == 'MILITAIRE'
                and (not d.date_obtention or d.date_obtention <= cutoff)]
    selected = max(eligible, key=lambda d: (d.date_obtention.toordinal() if d.date_obtention else 0, d.id or 0), default=None)
    if selected and selected.id == diploma_id:
        return 'nouveau diplôme affiché dans la SME'
    if selected:
        return f'diplôme conservé au dossier ; la SME conserve « {selected.libelle} », militaire plus récent'
    return 'diplôme enregistré ; actualisez la SME pour vérifier son affichage'


def find_identical_diploma(diplomas, payload):
    from sme_gend.services.fonction_structure_policy import fold_label
    return next((d for d in diplomas if d.categorie == payload['categorie']
        and fold_label(d.libelle) == fold_label(payload['libelle'])
        and d.date_obtention == parse_access_date(payload.get('date_obtention'))), None)
