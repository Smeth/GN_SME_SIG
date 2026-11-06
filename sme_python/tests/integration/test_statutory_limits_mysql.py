from test_restore_mysql import isolated_mysql
from test_career_alerts_mysql import alert_database


def test_updated_limits_survive_two_clients_and_preserve_shared_configuration(alert_database):
    from sme_gend.repositories.app_config_repository import AppConfigRepository
    from sme_gend.services.app_config_store import KEY_LIMITE_AGE
    from sme_gend.services.limite_age_service import LimiteAgeService
    settings, connect, params = alert_database
    repository = AppConfigRepository(settings)
    raw = {'grades':[{'grade':'Colonel','limite_age':60,'limite_service':42},
                     {'grade':'Grade local','limite_age':59,'limite_service':39}],
           'options':{'ville':'Dolisie','signataire_nom':'SIGNATAIRE LOCAL'},
           'name_formats':{'101':{'source':'nom composé jean','surname_words':2}}}
    repository.set_json(KEY_LIMITE_AGE,raw)
    first,second = LimiteAgeService(),LimiteAgeService()
    for service in (first,second,first):
        cfg = service.load(settings)
        assert (cfg.grades[0].limite_age,cfg.grades[0].limite_service) == (65,47)
        assert cfg.options.ville == 'Dolisie'
        assert cfg.options.signataire_nom == 'SIGNATAIRE LOCAL'
        assert (cfg.grades[1].limite_age,cfg.grades[1].limite_service) == (59,39)
        assert cfg.name_formats == raw['name_formats']
    assert repository.get_json(KEY_LIMITE_AGE) == raw
    first.save(cfg,settings)
    reloaded = second.load(settings)
    assert (reloaded.grades[0].limite_age,reloaded.grades[0].limite_service) == (65,47)
    assert reloaded.options.ville == 'Dolisie'
    assert reloaded.name_formats == raw['name_formats']
