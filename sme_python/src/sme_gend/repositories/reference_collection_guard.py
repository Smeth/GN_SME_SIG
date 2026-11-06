"""Consistent displayed collections and current checks inside reference writes."""
from dataclasses import dataclass,field
import hashlib,json,re
from sme_gend.repositories.mysql_connection import MySQLConnection
from sme_gend.repositories.reference_write_guard import ReferenceConflictError


def fingerprint(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",",":"),allow_nan=False).encode("utf-8")).hexdigest()


@dataclass(frozen=True,slots=True)
class CollectionSnapshot:
    kind:str
    key:str
    revision:str=field(repr=False)
    owner:tuple=field(repr=False)
    choices:tuple=field(repr=False)
    links:tuple=field(repr=False)
    observed:tuple=field(default=(),repr=False)


def _plans(kind,key):
    structures=("r.id,r.code,r.designation,r.ordre_tri,r.famille_unite","FROM ref_structure r",())
    fonctions=("r.id,r.libelle","FROM ref_fonction r",())
    if kind=="links_fonction":
        return [(fonctions[0],fonctions[1]+" WHERE r.id=%s",(key,)),structures,
                ("l.structure_id,l.fonction_id,l.ordre_tri","FROM ref_fonction_structure l WHERE l.fonction_id=%s",(key,))]
    if kind=="links_structure":
        return [(structures[0],structures[1]+" WHERE r.code=%s",(key,)),fonctions,
                ("l.structure_id,l.fonction_id,l.ordre_tri","FROM ref_fonction_structure l INNER JOIN ref_structure s ON s.id=l.structure_id WHERE s.code=%s",(key,))]
    if kind=="links_all":
        return [fonctions,structures,("l.structure_id,l.fonction_id,l.ordre_tri","FROM ref_fonction_structure l",())]
    if kind=="order_structures":return [structures,None,None]
    if kind=="order_subdivisions":
        return [(structures[0],structures[1]+" WHERE r.id=%s",(key,)),
                ("r.id,r.structure_id,r.libelle,r.ordre_tri,r.actif","FROM ref_subdivision_arme r WHERE r.structure_id=%s",(key,)),None]
    raise ValueError("Collection de référentiel non autorisée.")


def _rows(values):
    return tuple(sorted((tuple(row) for row in (values or [])),key=lambda row:json.dumps(row,ensure_ascii=False)))


def _snapshot(kind,key,raw,observed=()):
    rows=tuple(_rows(part) for part in raw)
    if kind not in {"links_all","order_structures"} and not rows[0]:
        raise ReferenceConflictError("Ce référentiel a été modifié ou supprimé. Actualisez les données.")
    return CollectionSnapshot(kind,str(key),fingerprint(rows),*rows,observed=tuple(observed))


def load_collection(settings,kind,key=""):
    plans=_plans(kind,str(key));parts=[];params=[]
    for plan in plans:
        if plan is None:parts.append("JSON_ARRAY()")
        else:
            fields,source,arguments=plan
            parts.append(f"(SELECT JSON_ARRAYAGG(JSON_ARRAY({fields})) {source})")
            params.extend(arguments)
    # One SQL statement: parent labels, choices and link rows cannot be loaded
    # from different committed states, even at READ COMMITTED.
    with MySQLConnection(settings).connect(read_only=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT "+",".join(parts),tuple(params))
            values=cursor.fetchone()
            raw=[json.loads(value) if isinstance(value,(str,bytes)) else value for value in values]
            observed=[]
            if kind=="links_structure" and not raw[2]:
                cursor.execute("SELECT DISTINCT TRIM(COALESCE(rf.libelle,c.fonction_libelle)) FROM carriere_situation c LEFT JOIN ref_structure rs ON rs.id=c.structure_id LEFT JOIN ref_fonction rf ON rf.id=c.fonction_id WHERE UPPER(TRIM(COALESCE(rs.code,c.structure_code)))=UPPER(TRIM(%s)) AND TRIM(COALESCE(rf.libelle,c.fonction_libelle,''))<>''",(str(key),))
                observed=[str(row[0]).strip() for row in cursor.fetchall()]
    return _snapshot(kind,str(key),raw,observed)


def require_collection(expected,kind,key=""):
    _plans(kind,str(key))
    if (not isinstance(expected,CollectionSnapshot) or expected.kind!=kind or expected.key!=str(key)
            or re.fullmatch(r"[0-9a-f]{64}",expected.revision or "") is None):
        raise ValueError("Version de l'ensemble indisponible. Actualisez les données avant de recommencer.")


def check_collection(cursor,expected):
    # The early reference revision lock is owned by shared_reference_write.
    # Current shared locking reads also bypass an outer RR snapshot.
    raw=[]
    for plan in _plans(expected.kind,expected.key):
        if plan is None:raw.append([])
        else:
            fields,source,params=plan
            cursor.execute(f"SELECT {fields} {source} FOR SHARE",params)
            raw.append(cursor.fetchall())
    current=_snapshot(expected.kind,expected.key,raw)
    if current.revision!=expected.revision:
        raise ReferenceConflictError("Cet ensemble a été modifié depuis son chargement. Votre opération n'a pas été enregistrée. Actualisez les données avant de recommencer.")
    return current
