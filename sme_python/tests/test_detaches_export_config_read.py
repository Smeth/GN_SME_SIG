"""Direct spreadsheet exports never initialize or mutate shared layouts."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from sme_gend.models.detaches_file_config import DetachesFileConfig
from sme_gend.services.detaches_config_service import DetachesConfigService


@pytest.mark.parametrize('table_exists,raw',[(False,None),(True,None),(True,{
    'options':{'titre_ligne_feuille':'SYNTHETIC TITLE','unknown':'PRESERVE'},
    'structures':[{'sigle':'SYN','ordre':4,'titres_lignes':['SYNTHETIC STRUCTURE']}],
})])
def test_export_config_is_read_only_even_when_the_layout_is_absent(monkeypatch,table_exists,raw):
    from sme_gend.services import shared_config_write
    metadata=Mock(return_value=table_exists)
    monkeypatch.setattr('sme_gend.repositories.app_config_repository.AppConfigRepository',
                        lambda settings:SimpleNamespace(table_exists=metadata))
    read=Mock(return_value=raw)
    monkeypatch.setattr(shared_config_write,'load_shared_payload',read)
    forbidden=Mock(side_effect=AssertionError('A direct export must not initialize/edit configurations'))
    monkeypatch.setattr(shared_config_write,'load_shared_model',forbidden)
    monkeypatch.setattr(shared_config_write,'save_shared_model',forbidden)
    monkeypatch.setattr(shared_config_write,'may_write_config',forbidden)
    settings=object()
    result=DetachesConfigService(settings).load_for_export()
    expected=DetachesFileConfig.from_dict(raw) if raw else DetachesFileConfig()
    assert result.to_dict()==expected.to_dict()
    assert result.write_token is None and result._write_database is None
    metadata.assert_called_once_with('app_config')
    if table_exists:read.assert_called_once_with(settings,'detaches_config')
    else:read.assert_not_called()
    forbidden.assert_not_called()


def test_existing_unreadable_export_config_is_not_replaced_by_defaults(monkeypatch):
    monkeypatch.setattr('sme_gend.repositories.app_config_repository.AppConfigRepository',
                        lambda settings:SimpleNamespace(table_exists=lambda name:True))
    error=RuntimeError('SYNTHETIC UNREADABLE CONFIG')
    monkeypatch.setattr('sme_gend.services.shared_config_write.load_shared_payload',Mock(side_effect=error))
    with pytest.raises(RuntimeError) as caught:
        DetachesConfigService(object()).load_for_export()
    assert caught.value is error
