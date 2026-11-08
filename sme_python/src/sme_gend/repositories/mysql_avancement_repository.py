"""Barème partagé et dossiers agents utiles au calcul d'avancement."""

from __future__ import annotations

from datetime import date, datetime

from sme_gend.config.settings import Settings
from sme_gend.models.avancement import (
    AgentAvancementInput,
    AvancementRegle,
    default_rules,
)
from sme_gend.repositories.mysql_agent_repository import _row_to_liste, _select_liste
from sme_gend.repositories.mysql_common import BaseMySQLRepository
from sme_gend.services.grade_label_format import normalize_grade_key
from sme_gend.services.statutory_limits import canonical_grade
from sme_gend.repositories.avancement_rule_guard import (
    RULE_COLUMNS, RULE_SELECT, assert_rule_permission, check_rule, load_rules,
    require_rule, require_rules_snapshot,
)
from sme_gend.repositories.reference_write_guard import ReferenceConflictError
from sme_gend.services.shared_reference_write import shared_reference_write, mark_reference_write


def _as_date(value: object) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except ValueError:
            continue
    return None


def _tri_bool(value: object) -> bool | None:
    if value is None:
        return None
    return bool(int(value))


class MySQLAvancementRepository(BaseMySQLRepository):
    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)

    def load_regles_snapshot(self):
        with self._connect(read_only=True) as conn:
            with conn.cursor() as cur:
                return load_rules(cur, self._settings)

    def list_regles(self) -> list[AvancementRegle]:
        return list(self.load_regles_snapshot().rows)

    def save_regle(self, regle: AvancementRegle, *, expected: AvancementRegle | None = None) -> None:
        assert_rule_permission()
        require_rule(self._settings, regle, expected)
        with shared_reference_write(self._settings):
            with self._connect() as conn:
                with conn.cursor() as cur:
                    check_rule(cur, expected)
                    cur.execute(
                        """
                        INSERT INTO avancement_regle (
                          grade_cible, grade_precedent, libelle,
                          annees_grade, annees_grade_ecole, annees_services,
                          age_min, age_max, diplomes, diplome_ou_superieur,
                          inscription_tableau, voie, acte, non_codifie, condition_manuelle
                        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        ON DUPLICATE KEY UPDATE
                          grade_precedent=VALUES(grade_precedent),
                          libelle=VALUES(libelle),
                          annees_grade=VALUES(annees_grade),
                          annees_grade_ecole=VALUES(annees_grade_ecole),
                          annees_services=VALUES(annees_services),
                          age_min=VALUES(age_min),
                          age_max=VALUES(age_max),
                          diplomes=VALUES(diplomes),
                          diplome_ou_superieur=VALUES(diplome_ou_superieur),
                          inscription_tableau=VALUES(inscription_tableau),
                          voie=VALUES(voie),
                          acte=VALUES(acte),
                          non_codifie=VALUES(non_codifie),
                          condition_manuelle=VALUES(condition_manuelle)
                        """,
                        _regle_params(regle),
                    )
                    mark_reference_write(cur.rowcount > 0)

    def reset_regles(self, *, expected=None) -> None:
        assert_rule_permission()
        require_rules_snapshot(self._settings, expected)
        with shared_reference_write(self._settings):
            with self._connect() as conn:
                with conn.cursor() as cur:
                    current = load_rules(cur, self._settings, locking=True)
                    if current.revision != expected.revision:
                        raise ReferenceConflictError("Le barème a été modifié depuis son affichage. "
                            "Aucune règle n'a été remplacée ; rechargez les données.")
                    defaults = tuple(sorted(default_rules(), key=lambda rule: rule.grade_cible))
                    if current.rows == defaults:
                        return
                    cur.execute("DELETE FROM avancement_regle")
                    cur.executemany(
                        "INSERT INTO avancement_regle (" + ",".join(RULE_COLUMNS) + ") "
                        "VALUES (" + ",".join(["%s"] * len(RULE_COLUMNS)) + ")",
                        [_regle_params(rule) for rule in defaults],
                    )
                    mark_reference_write(True)

    def load_agents(self, *, on: date | None = None) -> list[AgentAvancementInput]:
        from sme_gend.services.avancement_reference_date import avancement_reference_date
        on = avancement_reference_date(self._settings, on=on)
        with self._connect(read_only=True) as conn:
            with conn.cursor() as cur:
                sql, params = _select_liste(self._scope_mois(), settings=self._settings, cursor=cur)
                sql = sql.replace("c.id AS carriere_id", "a.id AS agent_id, c.id AS carriere_id", 1)
                cur.execute(f"{sql} ORDER BY a.noms, c.id", params)
                people = list(cur.fetchall())
                if not people:
                    return []
                agent_ids = [int(row[0]) for row in people]
                placeholders = ", ".join(["%s"] * len(agent_ids))
                cur.execute(
                    f"""
                    SELECT d.agent_id,
                           TRIM(CONCAT_WS(' ',
                             NULLIF(TRIM(d.libelle_libre), ''),
                             NULLIF(TRIM(rd.code), ''),
                             NULLIF(TRIM(rd.libelle_court), ''),
                             NULLIF(TRIM(rd.libelle_long), '')
                           ))
                    FROM agent_diplome d
                    LEFT JOIN ref_diplome rd ON rd.id = d.ref_diplome_id
                    WHERE d.agent_id IN ({placeholders})
                      AND (d.date_obtention IS NULL OR d.date_obtention <= %s)
                    """,
                    (*agent_ids, on),
                )
                diplomes: dict[int, list[str]] = {}
                for aid, libelle in cur.fetchall():
                    text = (libelle or "").strip()
                    if text:
                        diplomes.setdefault(int(aid), []).append(text)
                cur.execute(
                    f"""
                    SELECT agent_id, grade_libelle, date_passation, obs
                    FROM agent_passation_grade
                    WHERE agent_id IN ({placeholders})
                      AND (date_passation IS NULL OR date_passation <= %s)
                    ORDER BY date_passation IS NULL, date_passation, id
                    """,
                    (*agent_ids, on),
                )
                passations: dict[int, list[tuple]] = {}
                for row in cur.fetchall():
                    passations.setdefault(int(row[0]), []).append(row)

        from sme_gend.services.avancement_display import grade_abbr

        out: list[AgentAvancementInput] = []
        for row in people:
            agent_id = int(row[0])
            liste = _row_to_liste(row[1:])
            grade = (liste.grade or "").strip()
            code = canonical_grade(grade)
            date_grade = _as_date(liste.date_grade)
            if date_grade is not None and date_grade > on:
                date_grade = None
            issu = False
            for _aid, glib, pdt, obs in passations.get(agent_id, []):
                glibelle = (glib or "").strip()
                if code and canonical_grade(glibelle) == code:
                    parsed = _as_date(pdt)
                    if parsed is not None and (date_grade is None or parsed >= date_grade):
                        date_grade = parsed
                if canonical_grade(glibelle) == "SLT" and "ecole" in normalize_grade_key(obs or ""):
                    issu = True
            labels = list(diplomes.get(agent_id, []))
            diploma_date = _as_date(liste.date_obtention)
            if (liste.diplome or "").strip() and (diploma_date is None or diploma_date <= on):
                labels.append(liste.diplome or "")
            grade_code = grade_abbr(grade, settings=self._settings)
            out.append(
                AgentAvancementInput(
                    agent_id=agent_id,
                    noms=(liste.noms or "").strip() or "—",
                    grade_raw=grade_code,
                    date_grade=date_grade,
                    date_service=_as_date(liste.date_service),
                    date_naissance=_as_date(liste.date_naissance),
                    diplomes=tuple(labels),
                    structure=(liste.structure or "").strip(),
                    matricule=(liste.matricule_militaire or "").strip(),
                    carriere_id=(liste.id or "").strip(),
                    legacy_id=(liste.access_join_id or "").strip(),
                    issu_ecole=issu,
                )
            )
        return out

    def _scope_mois(self) -> str | None:
        from sme_gend.config.liste_generale_scope import resolve_carriere_scope_mois

        return resolve_carriere_scope_mois(self._settings)


def _regle_params(regle: AvancementRegle) -> tuple:
    return (
        regle.grade_cible,
        regle.grade_precedent,
        regle.libelle,
        regle.annees_grade,
        regle.annees_grade_ecole,
        regle.annees_services,
        regle.age_min,
        regle.age_max,
        regle.diplomes_csv(),
        int(regle.diplome_ou_superieur),
        int(regle.inscription_tableau),
        regle.voie,
        regle.acte,
        int(regle.non_codifie),
        regle.condition_manuelle,
    )
