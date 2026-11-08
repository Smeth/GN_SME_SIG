from contextlib import contextmanager
from unittest.mock import Mock

import pytest
from sme_gend.services import document_month_cycle as cycle


@pytest.mark.parametrize('mode,has_facts,refused', [
    (cycle.LEGACY, False, False), (cycle.LEGACY, True, True), (cycle.DOCUMENTS, False, True),
])
def test_legacy_copy_guards_require_projection_for_real_facts_or_document_cycle(monkeypatch, mode, has_facts, refused):
    monkeypatch.setattr(cycle, 'get_cycle_mode', lambda settings: mode)
    monkeypatch.setattr(cycle, '_has_dated_movements', lambda settings: has_facts)
    for guard in (cycle.assert_legacy_closure, cycle.assert_legacy_month_fill):
        if refused:
            with pytest.raises(cycle.LegacyCycleDisabledError, match='cycle documentaire'):
                guard(object())
        else:
            guard(object())


def test_migrated_legacy_transition_enters_exclusive_barrier_before_caller(monkeypatch):
    trace = []
    monkeypatch.setattr('sme_gend.services.membership_scope.membership_projection_enabled', lambda settings: True)
    @contextmanager
    def barrier(settings, *, exclusive):
        assert exclusive is True
        trace.append('barrier')
        yield
        trace.append('commit')
    monkeypatch.setattr('sme_gend.services.publication_write_barrier.publication_write_barrier', barrier)
    with cycle.legacy_month_transition(object()):
        trace.append('legacy_transition')
    assert trace == ['barrier', 'legacy_transition', 'commit']


def test_unmigrated_legacy_transition_needs_no_new_gate(monkeypatch):
    monkeypatch.setattr('sme_gend.services.membership_scope.membership_projection_enabled', lambda settings: False)
    barrier = Mock(side_effect=AssertionError('No new schema required'))
    monkeypatch.setattr('sme_gend.services.publication_write_barrier.publication_write_barrier', barrier)
    with cycle.legacy_month_transition(object()):
        pass
    barrier.assert_not_called()
