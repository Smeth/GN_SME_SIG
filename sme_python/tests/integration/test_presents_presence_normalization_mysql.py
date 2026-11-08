"""Presence selection agrees with Python for pasted whitespace and custom flags."""
import pytest
from test_restore_mysql import isolated_mysql

from sme_gend.models.padmin_ref import PadminRefRow
from sme_gend.padmin_present import PadminPresenceIndex, padmin_counts_as_present
from sme_gend.repositories.mysql_agent_repository import _padmin_where_clause


@pytest.mark.parametrize('mode',['present','stage_interne','les_deux'])
@pytest.mark.parametrize('linked',[False,True])
def test_pasted_administrative_positions_have_identical_sql_and_python_presence(isolated_mysql,mode,linked):
    settings,connect,params=isolated_mysql
    flags=[('Présent',False),('StageInterne',False),('Mission',True),('Service spécial',True),('À vérifier',False)]
    # Surrounding and repeated whitespace occur when old monthly sheets are
    # pasted/imported. They must not change an administrative position's code.
    positions=[(None,None),('',None),(' \t\r\n\u00a0 ',None),
        ('\tPrésent\r\n',1),('\u00a0Présent\u00a0',1),
        ('\tStageInterne\n',2),('\u00a0STAGE INTERNE\u00a0',2),
        (' Stage \t interne ',2),('StageInterne',2),
        ('\tMission\n',3),('PrÉsent mission',None),(' Non présent ',None),
        ('\tService spécial\n',4),('\u00a0Service\tspécial\u00a0',4),
        ('À vérifier',5),('Malade',None)]
    index=PadminPresenceIndex.from_rows([PadminRefRow(libelle=label,compte_comme_present=flag,ordre_tri=None) for label,flag in flags])
    with connect(**params) as connection:
        with connection.cursor() as cursor:
            cursor.execute('CREATE TABLE ref_position_admin(id INT PRIMARY KEY,libelle VARCHAR(100),compte_comme_present BOOL) ENGINE=InnoDB')
            cursor.execute('CREATE TABLE carriere_situation(id INT PRIMARY KEY,position_admin_id INT,position_admin_libelle VARCHAR(100)) ENGINE=InnoDB')
            cursor.executemany('INSERT INTO ref_position_admin VALUES(%s,%s,%s)',[(number,label,flag) for number,(label,flag) in enumerate(flags,1)])
            cursor.executemany('INSERT INTO carriere_situation VALUES(%s,%s,%s)',[(number,ref if linked else None,label) for number,(label,ref) in enumerate(positions,1)])
            where,arguments=_padmin_where_clause(mode)
            cursor.execute('SELECT c.id FROM carriere_situation c LEFT JOIN ref_position_admin rpa ON rpa.id=c.position_admin_id WHERE '+where,arguments)
            actual={row[0] for row in cursor.fetchall()}
    # Linked records display the authoritative reference label; unlinked ones
    # retain their imported text, which is normalized before flag lookup.
    expected={number for number,(label,ref) in enumerate(positions,1)
        if padmin_counts_as_present(flags[ref-1][0] if linked and ref else label,mode,index=index)}
    assert actual==expected
