"""Shared safe CLI for the legacy maintenance entry points."""
import argparse,json,re,sys
from sme_gend.config import get_settings
from sme_gend.services.automatic_maintenance import active_month,validate_month,prepare_call


def revision(value):
    if re.fullmatch('[0-9a-f]{64}',value or '') is None:raise argparse.ArgumentTypeError('Revision attendue : 64 caracteres hexadecimaux.')
    return value


def _parser(action):
    parser=argparse.ArgumentParser(description='Maintenance Impression : apercu en lecture seule par defaut.')
    mode=parser.add_mutually_exclusive_group()
    mode.add_argument('--apply',action='store_true',help='Appliquer le plan explicitement verifie.')
    mode.add_argument('--dry-run',action='store_true',help='Afficher le plan sans aucune modification (defaut).')
    parser.add_argument('--expected-revision',type=revision,help='Revision du plan correspondant aux memes options.')
    if action!='purge':parser.add_argument('--mois',type=validate_month,required=action=='revert',help='YYYY-MM ; defaut : mois ouvert unique, jamais un ancien mois code en dur.')
    if action=='reference':
        for flag in ('org','rsa','dedupe-rsa','layout','fk','dossier'):parser.add_argument('--skip-'+flag,action='store_true')
        parser.add_argument('--all-archives',action='store_true')
        parser.add_argument('--drop-legacy',action='store_true',help='Etape DDL separee, sans maintenance DML prealable.')
        parser.add_argument('--backup-dir',help='Dossier des sauvegardes et journaux de suppression legacy.')
        parser.add_argument('--maintenance-confirmed',action='store_true',help='Confirmez la fermeture des autres postes pour le DDL.')
    elif action=='coherence':parser.add_argument('--skip-maintenance',action='store_true')
    elif action=='dossier':
        parser.add_argument('--skip-dedupe',action='store_true');parser.add_argument('--skip-resync',action='store_true')
    elif action=='sot':
        group=parser.add_mutually_exclusive_group()
        for flag in ('fk-only','dossier-only','no-dossier'):group.add_argument('--'+flag,action='store_true')
    elif action=='fix':
        parser.add_argument('--policy',choices=['adjacent','all'],default='all')
        parser.add_argument('--no-projection',action='store_true')
    elif action=='revert':parser.add_argument('--keep-from',type=validate_month,required=True,help='Seuil explicite YYYY-MM ; aucune suppression sur un seuil historique implicite.')
    return parser


def _job(action,args,settings):
    from sme_gend.services import sot_maintenance as sot
    from sme_gend.services import referentiel_maintenance as reference
    from sme_gend.services.automatic_jobs import run_dossier_maintenance,run_month_coherence
    if action=='purge':return sot.purge_phantom_carriere_segments,{}
    if action=='reference' and args.all_archives:return reference.run_archives_referentiel_alignment,{}
    mois=args.mois or active_month(settings)
    if action=='reference':return reference.run_referentiel_maintenance,dict(mois=mois,migrate_org=not args.skip_org,backfill_rsa=not args.skip_rsa,dedupe_rsa=not args.skip_dedupe_rsa,rebuild_layout=not args.skip_layout,resync_fk=not args.skip_fk,resync_dossier=not args.skip_dossier)
    if action=='coherence':return run_month_coherence,dict(mois=mois,skip_maintenance=args.skip_maintenance)
    if action=='dossier':return run_dossier_maintenance,dict(mois=mois,skip_dedupe=args.skip_dedupe,skip_resync=args.skip_resync)
    if action=='sot':
        if args.fk_only:return sot.resync_fk_referentiels,dict(mois=mois)
        if args.dossier_only:return sot.project_carriere_from_dossier_batch,dict(mois=mois,only_est_actuelle=False)
        return sot.run_full_sot_resync,dict(mois=mois,project_dossier=not args.no_dossier)
    if action=='fix':return sot.fix_agents_sans_est_actuelle,dict(mois=mois,policy=args.policy,project_dossier=not args.no_projection)
    if action=='revert':return sot.revert_stale_carry_forward,dict(mois=mois,keep_from_mois=args.keep_from)
    raise ValueError('Operation inconnue.')


def run_cli(action,argv=None):
    parser=_parser(action);args=parser.parse_args(argv)
    if args.apply and args.expected_revision is None:parser.error('--apply exige --expected-revision issu de l apercu.')
    if args.expected_revision and not args.apply:parser.error('--expected-revision s utilise avec --apply.')
    if action=='reference':
        skips=any(getattr(args,'skip_'+key) for key in ('org','rsa','dedupe_rsa','layout','fk','dossier'))
        if args.all_archives and (args.mois or skips):parser.error('--all-archives ne se combine pas avec --mois ou les options --skip-*.')
        if args.drop_legacy and (args.all_archives or args.mois or skips):parser.error('--drop-legacy est une etape separee sans --mois, --all-archives ni --skip-*.')
        if (args.backup_dir or args.maintenance_confirmed) and not args.drop_legacy:parser.error('Les options DDL exigent --drop-legacy.')
        if args.apply and args.drop_legacy and (not args.backup_dir or not args.maintenance_confirmed):parser.error('La suppression exige --backup-dir et --maintenance-confirmed.')
    try:
        settings=get_settings()
        if action=='reference' and args.drop_legacy:
            from sme_gend.services.legacy_cleanup import prepare_legacy_cleanup,run_legacy_cleanup
            if not args.apply:out=prepare_legacy_cleanup(settings).summary()
            else:out=run_legacy_cleanup(settings,backup_dir=args.backup_dir,maintenance_confirmed=args.maintenance_confirmed,expected=args.expected_revision)
        else:
            function,options=_job(action,args,settings)
            if not args.apply:
                plan=prepare_call(settings,function,**options)
                if action!='purge' and args.mois is None and 'mois' in options and active_month(settings)!=options['mois']:
                    from sme_gend.repositories.reference_write_guard import ReferenceConflictError
                    raise ReferenceConflictError('Le mois actif a change pendant la preparation du plan. Regenerez le plan.')
                out=plan.summary();out['options']=options
            else:
                result=function(settings,expected=args.expected_revision,**options)
                if hasattr(result,'to_dict'):result=result.to_dict()
                out=dict(mode='applique',writes_applied=True,result=result)
                if isinstance(result,dict) and 'removed_agents' in result:
                    # Do not print personnel names/identifiers in scheduled-task logs.
                    result=dict(result);result['removed_count']=len(result.pop('removed_agents'));out['result']=result
        print(json.dumps(out,ensure_ascii=False,indent=2,default=str))
        if args.apply and action=='fix':return 0 if not (out['result'].get('metrics') or {}).get('agents_sans_actuelle') else 1
        return 0
    except Exception as error:
        from sme_gend.repositories.reference_write_guard import ReferenceConflictError
        from sme_gend.services.legacy_cleanup import LegacyCleanupError
        if isinstance(error,LegacyCleanupError):out=dict(error=str(error),recovery=error.report)
        elif isinstance(error,(ReferenceConflictError,ValueError,PermissionError,RuntimeError)):
            out=dict(error=str(error),exception=type(error).__name__,instruction='Relisez l etat et le plan avant toute nouvelle tentative.')
        else:out=dict(error='Maintenance interrompue. Verifiez connexion et schema, puis relisez l etat avant de reprendre.',exception=type(error).__name__)
        print(json.dumps(out,ensure_ascii=False,indent=2),file=sys.stderr)
        return 2 if isinstance(error,ReferenceConflictError) else 1
