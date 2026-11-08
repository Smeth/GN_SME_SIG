"""Capture/compare a disposable copy's monthly state; never writes the database."""
import argparse
from pathlib import Path
from dataclasses import replace
import json


def main(argv=None):
    parser=argparse.ArgumentParser(description='Rapprochement mensuel en lecture seule sur une copie de migration.')
    parser.add_argument('--mois',required=True)
    parser.add_argument('--database',required=True,help='Nom explicite de la copie à comparer.')
    parser.add_argument('--capture',type=Path,help='Conserver l’état de référence, avec données de personnel.')
    parser.add_argument('--compare',type=Path,help='Instantané de référence déjà conservé.')
    parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args(argv)
    if bool(args.capture)==bool(args.compare):parser.error('Choisir --capture ou --compare.')
    from sme_gend.config import get_settings
    from sme_gend.services.migration_reconciliation import (capture_month_reconciliation,
        encode_snapshot,decode_snapshot,compare_month_reconciliation)
    from sme_gend.services.safe_paths import atomic_destination
    def save(path,payload):
        with atomic_destination(path,replace_existing=False) as temporary:
            temporary.write_bytes(payload)
    base=get_settings()
    settings=replace(base,mysql_database=args.database,mysql_view_mois=args.mois)
    state=capture_month_reconciliation(settings,args.mois)
    if args.capture:
        if args.capture.resolve()==args.report.resolve():parser.error('Instantané et rapport doivent être distincts.')
        save(args.capture,encode_snapshot(state))
        result={'month':state.month,'captured_agents':len(state.rows),'fingerprint':state.fingerprint}
    else:
        result=compare_month_reconciliation(decode_snapshot(args.compare.read_bytes()),state)
    save(args.report,json.dumps(result,ensure_ascii=False,indent=2).encode('utf-8'))
    print(json.dumps({key:value for key,value in result.items() if key in ('month','matches','counts','captured_agents','fingerprint')},ensure_ascii=False))
    return 2 if result.get('matches') is False else 0


if __name__=='__main__':raise SystemExit(main())
