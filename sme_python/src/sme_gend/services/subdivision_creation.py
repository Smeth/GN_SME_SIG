"""Validate and create a U/D/T path inside the editor's versioned transaction."""
from sme_gend.repositories.hierarchy_merge_guard import hierarchy_rows
from sme_gend.repositories.mysql_subdiv_hierarchy_repository import _values
from sme_gend.services.fonction_structure_policy import fold_label


def validate_creation_path(snapshot, path):
    if not isinstance(path, (list, tuple)) or len(path) != 3:
        raise ValueError('Renseignez les trois niveaux U, D et T.')
    nodes, levels = hierarchy_rows(snapshot)
    parent = 1
    clean = []
    for level, item in enumerate(path, 1):
        if isinstance(item, int) and not isinstance(item, bool):
            if item not in nodes or levels[item] != level or parent is None or nodes[item][1] != parent:
                raise ValueError('Le niveau choisi ne dépend pas du parent sélectionné.')
            clean.append(item)
            parent = item
        elif isinstance(item, dict):
            code, label, _ = _values(item.get('code'), item.get('libelle'), None)
            if parent is not None and any(row[1] == parent and fold_label(row[2]) == fold_label(code)
                                          for row in nodes.values()):
                raise ValueError('Ce code existe déjà sous ce parent. Sélectionnez le niveau existant.')
            clean.append(dict(code=code, libelle=label))
            parent = None
        else:
            raise ValueError('Choisissez un niveau existant ou renseignez son code et son libellé.')
    return clean


def create_path(cursor, snapshot, path):
    clean = validate_creation_path(snapshot, path)
    parent = 1
    if not snapshot.parts[0]:
        from sme_gend.services.mysql_ref_etl import _ensure_subdiv_root
        _ensure_subdiv_root(cursor)
    for item in clean:
        if isinstance(item, int):
            parent = item
        else:
            cursor.execute('INSERT INTO ref_subdivision_noeud(parent_id,code,libelle,pri_out) '
                           'VALUES(%s,%s,%s,NULL)', (parent, item['code'], item['libelle']))
            parent = int(cursor.lastrowid)
    return parent
