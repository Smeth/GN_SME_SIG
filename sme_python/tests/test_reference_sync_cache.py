"""Reference cache invalidation and pre-SQL validation contracts."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading

import pytest
from sme_gend.config.settings import Settings
from sme_gend.models.padmin_ref import PadminRefRow
from sme_gend import padmin_present


@pytest.mark.parametrize('all_profiles', [False, True])
def test_presence_read_started_before_invalidation_cannot_reinstall_stale_flags(monkeypatch, all_profiles):
    started, release = threading.Event(), threading.Event()
    reads = []
    class Repository:
        def __init__(self, settings):
            pass
        def fetch_all(self):
            old = not reads
            reads.append(old)
            if old:
                started.set()
                assert release.wait(3)
            return [PadminRefRow(libelle='SYNTHETIC', compte_comme_present=old, ordre_tri=1)]
    monkeypatch.setattr('sme_gend.repositories.mysql_bundle_repositories.MySQLPadminRepository', Repository)
    monkeypatch.setattr(padmin_present, '_index_cache', {})
    settings = Settings(database_path=Path('unused.mdb'))
    with ThreadPoolExecutor(max_workers=1) as workers:
        pending = workers.submit(padmin_present.load_padmin_presence_index, settings)
        try:
            assert started.wait(3)
            padmin_present.invalidate_padmin_presence_cache(None if all_profiles else settings)
        finally:
            release.set()
        old = pending.result(timeout=3)
    assert old is not None and old.compte_comme_present('SYNTHETIC') is True
    fresh = padmin_present.load_padmin_presence_index(settings)
    assert fresh is not None and fresh.compte_comme_present('SYNTHETIC') is False
    assert reads == [True, False]


@pytest.mark.parametrize('operation', ['empty_function', 'empty_function_update', 'empty_destination'])
def test_invalid_reference_inputs_still_fail_before_connecting(monkeypatch, operation):
    from sme_gend.repositories.mysql_bundle_repositories import MySQLFonctionsRepository
    from sme_gend.repositories.mysql_organigram_repository import MySQLOrganigramRepository
    from sme_gend.services import shared_reference_write
    def unexpected(*args, **kwargs):
        pytest.fail('Invalid input must not open a transaction')
    monkeypatch.setattr(shared_reference_write, 'mysql_import_transaction', unexpected)
    cls = MySQLOrganigramRepository if operation == 'empty_destination' else MySQLFonctionsRepository
    repository = cls.__new__(cls)
    repository._settings = Settings(database_path=Path('unused.mdb'))
    with pytest.raises(ValueError):
        if operation == 'empty_function': repository.insert(' ')
        elif operation == 'empty_function_update': repository.update(1, 'OLD', ' ')
        else: repository.move_subdivision_to_structure(1, 2, ' ')
