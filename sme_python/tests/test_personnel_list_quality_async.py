"""Le rafraîchissement des indicateurs de qualité ne doit pas bloquer la liste."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from sme_gend.ui.views import personnel_list_view as plv_mod


def test_refresh_row_quality_runs_off_thread_and_ignores_stale_results() -> None:
    view = plv_mod.PersonnelListView.__new__(plv_mod.PersonnelListView)
    view._settings = object()
    view._quality_request_id = 0
    view._model = MagicMock()
    view._apply_filter = MagicMock()

    workers: list[tuple[object, object]] = []

    def start_background(_parent, work, on_success, **kwargs):
        workers.append((work, on_success))

    gap_ids = {"1"}
    fk_ids = {"2"}
    with patch.object(plv_mod, "run_in_background", side_effect=start_background), patch(
        "sme_gend.services.sot_status_service.fetch_row_quality_ids",
        return_value=(gap_ids, fk_ids),
    ) as fetch:
        view._refresh_row_quality()
        view._refresh_row_quality()

        assert fetch.call_count == 0
        assert len(workers) == 2

        stale_work, stale_done = workers[0]
        current_work, current_done = workers[1]
        stale_done(stale_work())
        view._model.set_quality_ids.assert_not_called()

        current_done(current_work())

    # les deux requêtes tournent en tâche de fond, mais seul le résultat courant est appliqué
    assert fetch.call_count == 2
    fetch.assert_called_with(view._settings)
    view._model.set_quality_ids.assert_called_once_with(
        sot_gap_ids=gap_ids,
        fk_unresolved_ids=fk_ids,
    )
    view._apply_filter.assert_called_once_with()
