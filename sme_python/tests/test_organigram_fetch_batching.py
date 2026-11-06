"""fetch_organigram() : requêtes en lot au lieu du triple N+1 (sans MySQL réel).

Avec plusieurs dizaines de structures, l'ancienne version (une requête par
structure, puis une requête par libellé orphelin trouvé) pouvait représenter
plusieurs centaines d'allers-retours MySQL à chaque ouverture de l'écran
Structures & subdivisions — comportement structurellement identique au bug
corrigé sur la projection carrière (project_carriere_from_dossier_batch).
"""

from __future__ import annotations

from sme_gend.repositories.mysql_organigram_repository import MySQLOrganigramRepository


class _FakeCursor:
    def __init__(self, responses: list[list[tuple]]) -> None:
        self._responses = responses
        self._idx = 0
        self.executed: list[str] = []

    def execute(self, sql: str, params: tuple = ()) -> None:
        self.executed.append(sql)

    def fetchall(self):
        rows = self._responses[self._idx]
        self._idx += 1
        return rows

    def __enter__(self):
        return self

    def __exit__(self, *_a) -> bool:
        return False


class _FakeConn:
    def __init__(self, cur: _FakeCursor) -> None:
        self._cur = cur

    def cursor(self):
        return self._cur

    def __enter__(self):
        return self

    def __exit__(self, *_a) -> bool:
        return False


def test_fetch_organigram_uses_four_fixed_queries_and_matches_correctly(
    monkeypatch,
) -> None:
    # Structures : 1 = "DPF", 2 = "SEC"
    structure_rows = [
        (1, "DPF", "Direction", 1, 5),
        (2, "SEC", "Section", 2, 3),
    ]
    # ref_subdivision_arme : id, structure_id, libelle, ordre_tri
    rsa_rows = [(10, 1, "Bureau A", 1)]
    # GROUP BY subdivision_arme_id : effectif par subdivision référencée
    rsa_counts_rows = [(10, 4)]
    # GROUP BY structure_id, code_upper, libelle : libellés orphelins + effectif
    orphan_rows = [
        (1, "DPF", "Bureau B", 2),  # correspond à la structure 1 par id
        (None, "SEC", "Bureau C", 1),  # ne correspond qu'au code texte (structure_id NULL)
        (1, "DPF", "bureau a", 1),  # doublon casse du libellé référentiel -> exclu
    ]

    cur = _FakeCursor([structure_rows, rsa_rows, rsa_counts_rows, orphan_rows])
    conn = _FakeConn(cur)

    repo = MySQLOrganigramRepository.__new__(MySQLOrganigramRepository)
    monkeypatch.setattr(repo, "_connect", lambda **kwargs: conn)
    monkeypatch.setattr(repo, "_scope", lambda: ("1=1", ()))

    result = repo.fetch_organigram()

    assert len(cur.executed) == 4, "exactement 4 requêtes, peu importe le nombre de structures"

    by_code = {s.code: s for s in result}
    dpf_labels = {s.libelle: s for s in by_code["DPF"].subdivisions}
    sec_labels = {s.libelle: s for s in by_code["SEC"].subdivisions}

    assert dpf_labels["Bureau A"].agent_count == 4
    assert dpf_labels["Bureau A"].from_referentiel is True
    assert dpf_labels["Bureau B"].agent_count == 2
    assert dpf_labels["Bureau B"].from_referentiel is False
    assert set(dpf_labels) == {"Bureau A", "Bureau B"}  # "bureau a" (doublon casse) exclu

    assert sec_labels["Bureau C"].agent_count == 1
    assert sec_labels["Bureau C"].from_referentiel is False
