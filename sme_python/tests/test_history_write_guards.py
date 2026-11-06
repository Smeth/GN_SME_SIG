"""Observable safety contracts, independently of the database test fixture."""
from types import SimpleNamespace
from unittest.mock import MagicMock
import pytest


def test_history_lock_refuses_an_unregistered_transaction():
    from sme_gend.config.settings import Settings
    from sme_gend.repositories.write_guard import lock_agent
    with pytest.raises(RuntimeError, match="transaction partagée"):
        lock_agent(Settings(database_path=None), agent_id=1)


@pytest.mark.parametrize("table", ["agent; DROP TABLE agent", "../agent", "app_config"])
def test_monthly_revision_only_accepts_known_history_tables(table):
    from sme_gend.repositories.monthly_write_guard import revision_sql
    with pytest.raises(ValueError, match="autorisé"):
        revision_sql(table)


def test_stage_preparation_loads_personnel_once_and_preserves_lookup_fallbacks():
    from sme_gend.ui.views.stages_view import StagesView
    class Row:
        def __init__(self, key, legacy): self.id, self.legacy = key, legacy
        def satellite_join_key(self): return self.legacy
    originals = [Row(str(i), f"legacy-{i}") for i in range(100)]
    current = [Row(str(i), f"legacy-{i}") for i in range(100)]
    # Missing career id still resolves by legacy; a genuinely absent row is
    # passed to the existing service CAS, which will reject its removal.
    originals[1].id = "old-id"
    current = current[:-1]
    repo = MagicMock()
    repo.fetch_all.return_value = current
    view = SimpleNamespace(_liste=repo)
    prepared = StagesView._prepare_stage_rows(view, originals)
    assert prepared["old-id"] is current[1]
    assert prepared["0"] is current[0]
    assert prepared["99"] is originals[99]
    repo.fetch_all.assert_called_once_with()



def test_large_parallel_local_audits_remain_complete_json_lines(tmp_path):
    import json
    from concurrent.futures import ThreadPoolExecutor
    from sme_gend.services.local_audit_mirror import append_json_mirror
    target = tmp_path / "audit.jsonl"
    entries = [{"id":i, "text":"é\n"*9000} for i in range(80)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda entry: append_json_mirror(target, [entry]), entries))
    parsed = [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines()]
    assert len(parsed) == len(entries)
    assert {entry["id"] for entry in parsed} == set(range(80))
    assert all(entry["text"] == "é\n"*9000 for entry in parsed)
