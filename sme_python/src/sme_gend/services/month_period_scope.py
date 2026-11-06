"""Find all database periods representing one canonical month."""


def month_period_ids(cur, month, *, lock=False):
    cur.execute(
        "SELECT id FROM sme_periode WHERE TRIM(REPLACE(annee_mois, '-', ''))=%s ORDER BY id"
        + (' FOR UPDATE' if lock else ''),
        (month.replace('-', ''),),
    )
    return tuple(int(row[0]) for row in cur.fetchall())
