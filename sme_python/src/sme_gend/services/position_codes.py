"""Stable business codes, including labels supplied by the original catalogue."""
import unicodedata

LABELS = {
    'present': ('Présent', 'Présent au corps'),
    'stage_interne': ('StageInterne', 'Stage interne'),
    'detache': ('Détaché',), 'deserteur': ('Déserteur',),
    'conge_admin': ('CongéAdmin',), 'conge_exp': ('CongéExp',),
    'mission': ('Mission', 'En mission'), 'malade': ('Malade', 'Congé maladie'),
    'inst_mr': ('Inst Mise Route',), 'stage_local': ('StageLocal', 'Stage local'),
    'stage_ex': ('StageEx', 'Stage extérieur'), 'punis': ('Punis',), 'prison_ma': ('Prison MA',),
}


def normalized_label(label):
    return ''.join(c for c in unicodedata.normalize('NFKD', ' '.join((label or '').split()).casefold())
                   if not unicodedata.combining(c))


_BY_LABEL = {normalized_label(label): code for code, labels in LABELS.items() for label in labels}


def position_code(label):
    return _BY_LABEL.get(normalized_label(label))
