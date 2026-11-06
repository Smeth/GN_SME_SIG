"""Loaded catalogue snapshots, checked under current InnoDB row locks."""
import re
from pymysql.err import IntegrityError

CATALOGUES = {
    "padmin": ("ref_position_admin", "libelle", ("id", "libelle", "compte_comme_present", "ordre_tri")),
    "grade": ("ref_grade", "code", ("id", "code", "libelle", "pref_ord", "effectif_g")),
    "structure": ("ref_structure", "code", ("id", "code", "designation", "ordre_tri", "famille_unite")),
    "fonction": ("ref_fonction", "id", ("id", "libelle")),
}


class ReferenceConflictError(ValueError):
    """The displayed catalogue entry changed, disappeared, or was already created."""


def reference_revision_sql(kind):
    fields=CATALOGUES[kind][2]
    values = ["r." + field for field in fields]
    if kind == 'grade':
        values.append("(SELECT JSON_EXTRACT(payload,CONCAT('$.',JSON_QUOTE(LOWER(r.code)))) "
                      "FROM app_config WHERE config_key='grade_insignia_by_code')")
    return "SHA2(CAST(JSON_ARRAY("+",".join(values)+") AS CHAR),256)"


def require_reference_token(kind,key,expected):
    table,column,_=CATALOGUES[kind]
    key=str(key)
    token=getattr(expected,"write_token",None)
    if (str(getattr(expected,column,""))!=key or not isinstance(token,tuple) or len(token)!=3
            or token[:2]!=(kind,key) or not isinstance(token[2],str)
            or re.fullmatch(r"[0-9a-f]{64}",token[2]) is None):
        raise ValueError("Version du référentiel indisponible. Actualisez les données avant de recommencer.")
    return token[2]


def check_reference_target(cursor,kind,key,revision):
    # Every interpolated identifier is chosen from the closed mapping above.
    table,column,_=CATALOGUES[kind]
    cursor.execute(f"SELECT {reference_revision_sql(kind)} FROM {table} r WHERE r.{column}=%s FOR UPDATE",(key,))
    current=cursor.fetchone()
    if current is None or str(current[0])!=revision:
        raise ReferenceConflictError("Ce référentiel a été modifié ou supprimé depuis son chargement. "
            "Votre opération n'a pas été enregistrée. Actualisez les données avant de recommencer.")


def insert_reference(cursor,kind,sql,params):
    try:
        cursor.execute(sql,params)
    except IntegrityError as exc:
        if exc.args and exc.args[0]==1062:
            raise ReferenceConflictError("Cette entrée de référentiel existe déjà. Actualisez les données avant de recommencer.") from exc
        raise
