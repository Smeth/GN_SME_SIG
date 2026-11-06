from datetime import date
from unittest.mock import Mock

import pytest

from sme_gend.services.mysql_access_numerique_import import import_stages_from_access


@pytest.mark.parametrize('text,expected', [
    ('07/09/2026 \u2014 17/09/2026', (date(2026,9,7),date(2026,9,17))),
    ('28/08/2026 \u2014 04/10/2026', (date(2026,8,28),date(2026,10,4))),
    ('ANCIEN TEXTE LIBRE', (date(2026,9,1),date(2026,9,30))),
    ('07/09/2026', (date(2026,9,1),date(2026,9,30))),
    (None, (date(2026,9,1),date(2026,9,30))),
])
def test_stage_dates_roundtrip_and_legacy_fallback(text,expected):
    cur=Mock();cur.fetchone.return_value=(42,)
    counts=import_stages_from_access(cur,lambda query: ([(42,'STAGE','LIEU',text)],None),
        mois='2026-09',sme_periode_id=9,import_lot_id=None)
    inserts=[call.args[1] for call in cur.execute.call_args_list if 'INSERT INTO parcours_stage' in call.args[0]]
    assert len(inserts)==3
    assert {row[2] for row in inserts}=={'LOCAL','EXTERIEUR','INTERNE'}
    assert all(row[3:5]==expected for row in inserts)
    assert all(row[7]==text for row in inserts)
    assert sum(counts.values())==3


@pytest.mark.parametrize('text',['31/02/2026 \u2014 17/09/2026','17/09/2026 \u2014 07/09/2026'])
def test_invalid_structured_stage_dates_are_explicit(text):
    cur=Mock();cur.fetchone.return_value=(42,)
    with pytest.raises(ValueError,match='Dates de stage Access invalides'):
        import_stages_from_access(cur,lambda query: ([(42,'STAGE','LIEU',text)],None),
            mois='2026-09',sme_periode_id=9,import_lot_id=None)
    assert not any('INSERT INTO parcours_stage' in call.args[0] for call in cur.execute.call_args_list)
