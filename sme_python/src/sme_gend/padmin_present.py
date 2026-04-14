"""Critère « compte comme présent » — isolé pour éviter les imports circulaires services ↔ repositories."""


def padmin_counts_as_present(padmin: str | None) -> bool:
    """
    Compte dans les effectifs « présents » (NUMERIQUE, liste des présents, tableau de bord) :
    position contenant « présent » (accents neutralisés) ou libellé équivalent « stage interne ».
    """
    if padmin is None:
        return False
    raw = str(padmin).strip()
    if not raw:
        return False
    norm = raw.lower().replace("é", "e").replace("è", "e")
    if "present" in norm:
        return True
    collapsed = " ".join(raw.split()).casefold().replace("é", "e")
    return collapsed == "stageinterne" or collapsed == "stage interne"
