"""Avancement au grade — barème et lignes de vigilance."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

STATUT_ELIGIBLE = "eligible"
STATUT_A_PREPARER = "a_preparer"
STATUT_FENETRE = "fenetre_courte"
STATUT_INCOMPLET = "incomplet"
STATUT_HORS = "hors_calcul"
STATUT_SUIVI = "en_suivi"
STATUT_AVIS_DEFAVORABLE = "avis_defavorable"
STATUT_NOMME = "nomme"
STATUT_ECARTE = "ecarte"

STATUT_LABELS: dict[str, str] = {
    STATUT_ELIGIBLE: "Éligible",
    STATUT_A_PREPARER: "À préparer",
    STATUT_FENETRE: "Fenêtre courte",
    STATUT_INCOMPLET: "Incomplet",
    STATUT_HORS: "Hors calcul",
    STATUT_SUIVI: "En suivi",
    STATUT_AVIS_DEFAVORABLE: "Avis défavorable",
    STATUT_NOMME: "Nommé",
    STATUT_ECARTE: "Écarté",
}

STATUT_SORT: dict[str, int] = {
    STATUT_FENETRE: 0,
    STATUT_INCOMPLET: 1,
    STATUT_A_PREPARER: 2,
    STATUT_ELIGIBLE: 3,
    STATUT_AVIS_DEFAVORABLE: 4,
    STATUT_SUIVI: 5,
    STATUT_NOMME: 6,
    STATUT_ECARTE: 7,
    STATUT_HORS: 8,
}

TRAITER_STATUTS: frozenset[str] = frozenset(
    {
        STATUT_ELIGIBLE,
        STATUT_A_PREPARER,
        STATUT_FENETRE,
        STATUT_INCOMPLET,
        STATUT_AVIS_DEFAVORABLE,
    }
)

VOIE_TABLEAU = "tableau"
VOIE_ECOLE = "ecole"
VOIE_EXCEPTIONNEL = "exceptionnel"
VOIE_HORS = "hors_tableau"

VOIE_LABELS: dict[str, str] = {
    VOIE_TABLEAU: "Tableau d'avancement",
    VOIE_ECOLE: "Avancement école",
    VOIE_EXCEPTIONNEL: "Avancement exceptionnel",
    VOIE_HORS: "Hors tableau",
}

ACTE_ORDRE = "ordre_general"
ACTE_ARRETE = "arrete"
ACTE_DECRET = "decret"

ACTE_LABELS: dict[str, str] = {
    ACTE_ORDRE: "Ordre général du commandant de la gendarmerie",
    ACTE_ARRETE: "Arrêté ministériel",
    ACTE_DECRET: "Décret présidentiel",
}

AVIS_FAVORABLE = "favorable"
AVIS_RESERVE = "reserve"
AVIS_DEFAVORABLE = "defavorable"

AVIS_LABELS: dict[str, str] = {
    AVIS_FAVORABLE: "Favorable",
    AVIS_RESERVE: "Réservé",
    AVIS_DEFAVORABLE: "Défavorable",
}

DIPLOME_LABELS: dict[str, str] = {
    "OPJ": "OPJ",
    "BCG": "BCG",
    "CERT_TECH": "certificat technique homologué",
    "DQSG1": "DQSG1",
    "DQSG2": "DQSG2",
    "CPOS": "CPOS",
    "EMS1": "EMS1",
    "EMS2": "EMS2",
}


@dataclass(frozen=True, slots=True)
class AvancementRegle:
    grade_cible: str
    grade_precedent: str
    libelle: str
    annees_grade: int | None = None
    annees_grade_ecole: int | None = None
    annees_services: int | None = None
    age_min: int | None = None
    age_max: int | None = None
    diplomes: tuple[str, ...] = ()
    diplome_ou_superieur: bool = False
    inscription_tableau: bool = True
    voie: str = VOIE_TABLEAU
    acte: str = ""
    non_codifie: bool = False
    condition_manuelle: str = ""

    # Persistence metadata never appears in the rules table or comparison.
    write_token: str | None = field(default=None, repr=False, compare=False)
    _write_database: tuple | None = field(default=None, repr=False, compare=False)

    def diplomes_csv(self) -> str:
        return ",".join(self.diplomes)

    @classmethod
    def from_db(
        cls,
        grade_cible: str,
        grade_precedent: str,
        libelle: str,
        annees_grade: int | None,
        annees_grade_ecole: int | None,
        annees_services: int | None,
        age_min: int | None,
        age_max: int | None,
        diplomes: str | None,
        diplome_ou_superieur: int | bool,
        inscription_tableau: int | bool,
        voie: str,
        acte: str | None,
        non_codifie: int | bool,
        condition_manuelle: str | None,
    ) -> AvancementRegle:
        raw = (diplomes or "").replace(";", ",")
        codes = tuple(part.strip() for part in raw.split(",") if part.strip())
        return cls(
            grade_cible=grade_cible,
            grade_precedent=grade_precedent or "",
            libelle=libelle or grade_cible,
            annees_grade=annees_grade,
            annees_grade_ecole=annees_grade_ecole,
            annees_services=annees_services,
            age_min=age_min,
            age_max=age_max,
            diplomes=codes,
            diplome_ou_superieur=bool(diplome_ou_superieur),
            inscription_tableau=bool(inscription_tableau),
            voie=(voie or VOIE_TABLEAU).strip() or VOIE_TABLEAU,
            acte=(acte or "").strip(),
            non_codifie=bool(non_codifie),
            condition_manuelle=(condition_manuelle or "").strip(),
        )


def default_rules() -> tuple[AvancementRegle, ...]:
    """Décret n° 2023-1764. MDC = maréchal des logis-chef."""
    return (
        AvancementRegle(
            "MDL",
            "",
            "Maréchal des logis",
            inscription_tableau=False,
            voie=VOIE_HORS,
            non_codifie=True,
        ),
        AvancementRegle(
            "MDC",
            "MDL",
            "Maréchal des logis-chef",
            annees_grade=4,
            annees_services=6,
            diplomes=("OPJ", "BCG", "CERT_TECH"),
            acte=ACTE_ORDRE,
        ),
        AvancementRegle(
            "ADJ",
            "MDC",
            "Adjudant",
            annees_grade=4,
            annees_services=10,
            diplomes=("DQSG1",),
            diplome_ou_superieur=True,
            acte=ACTE_ORDRE,
        ),
        AvancementRegle(
            "ADC",
            "ADJ",
            "Adjudant-chef",
            annees_grade=3,
            annees_services=13,
            diplomes=("DQSG2",),
            diplome_ou_superieur=True,
            acte=ACTE_ORDRE,
        ),
        AvancementRegle(
            "ADM",
            "ADC",
            "Adjudant-major",
            annees_grade=5,
            age_min=45,
            acte=ACTE_ORDRE,
        ),
        AvancementRegle(
            "SLT",
            "ADC",
            "Sous-lieutenant",
            annees_grade=2,
            annees_services=15,
            age_max=45,
            diplomes=("DQSG2",),
            diplome_ou_superieur=True,
            acte=ACTE_ARRETE,
        ),
        AvancementRegle(
            "LTN",
            "SLT",
            "Lieutenant",
            annees_grade=3,
            annees_grade_ecole=2,
            acte=ACTE_ARRETE,
        ),
        AvancementRegle(
            "CNE",
            "LTN",
            "Capitaine",
            annees_grade=5,
            annees_services=9,
            diplomes=("CPOS",),
            diplome_ou_superieur=True,
            acte=ACTE_ARRETE,
        ),
        AvancementRegle(
            "CDT",
            "CNE",
            "Commandant",
            annees_grade=6,
            annees_services=15,
            diplomes=("EMS1",),
            diplome_ou_superieur=True,
            acte=ACTE_DECRET,
        ),
        AvancementRegle(
            "LCL",
            "CDT",
            "Lieutenant-colonel",
            annees_grade=5,
            annees_services=20,
            diplomes=("EMS1",),
            diplome_ou_superieur=True,
            acte=ACTE_DECRET,
        ),
        AvancementRegle(
            "COL",
            "LCL",
            "Colonel",
            annees_grade=4,
            annees_services=24,
            diplomes=("EMS2",),
            diplome_ou_superieur=True,
            acte=ACTE_DECRET,
        ),
        AvancementRegle(
            "CLM",
            "COL",
            "Colonel-major",
            annees_grade=5,
            annees_services=29,
            diplomes=("EMS2",),
            diplome_ou_superieur=True,
            acte=ACTE_DECRET,
        ),
        AvancementRegle(
            "GDB",
            "CLM",
            "Général de brigade",
            annees_grade=3,
            annees_services=32,
            diplomes=("EMS2",),
            diplome_ou_superieur=True,
            acte=ACTE_DECRET,
        ),
        AvancementRegle(
            "GDD",
            "GDB",
            "Général de division",
            acte=ACTE_DECRET,
            condition_manuelle="temps_commandement",
        ),
        AvancementRegle(
            "GCA",
            "GDD",
            "Général de corps d'armée",
            acte=ACTE_DECRET,
            condition_manuelle="temps_commandement",
        ),
        AvancementRegle(
            "GDA",
            "GCA",
            "Général d'armée",
            acte=ACTE_DECRET,
            condition_manuelle="temps_commandement",
        ),
    )


@dataclass(frozen=True, slots=True)
class AgentAvancementInput:
    agent_id: int
    noms: str
    grade_raw: str
    date_grade: date | None
    date_service: date | None
    date_naissance: date | None
    diplomes: tuple[str, ...] = ()
    structure: str = ""
    matricule: str = ""
    carriere_id: str = ""
    legacy_id: str = ""
    issu_ecole: bool = False
    maniere_servir: str | None = None
    temps_commandement: bool | None = None
    # Accepted for old DTO callers; never used by the active calculation.
    campagne_statut: str | None = None
    ligne_id: int | None = None
    voie_campagne: str | None = None


@dataclass(slots=True)
class ConditionResult:
    code: str
    libelle: str
    ok: bool | None
    detail: str


@dataclass(slots=True)
class VigilanceRow:
    agent: AgentAvancementInput
    grade_code: str | None
    grade_cible: str | None
    regle: AvancementRegle | None
    statut: str
    echeance: date | None
    limite: date | None
    message: str
    conditions: list[ConditionResult] = field(default_factory=list)
    anciennete_label: str = "—"
    services_label: str = "—"
    diplome_label: str = "—"

    @property
    def statut_label(self) -> str:
        return STATUT_LABELS.get(self.statut, self.statut)
