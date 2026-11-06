from sme_gend.services.access_date_parse import parse_access_date


def grade_save_notice(requested, saved):
    effective = parse_access_date(requested.date_grade)
    if effective and effective != parse_access_date(saved.date_grade):
        return (
            f"Passation enregistrée : {requested.grade} au {effective.strftime('%d/%m/%Y')} "
            f"(mois {effective.strftime('%Y-%m')}). "
            "Les situations existantes sont recalculées à partir de cette date. "
            "Le mois consulté affiche le grade correspondant à son historique."
        )
    return None
