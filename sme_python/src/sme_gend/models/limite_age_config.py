"""Modèle pour la configuration des limites d'âge et de service par grade."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class GradeLimiteEntry:
    grade: str
    limite_age: int      # Limite d'âge en années (ex : 58)
    limite_service: int  # Limite de service en années (ex : 40)

    def to_dict(self) -> dict:
        return {
            "grade": self.grade,
            "limite_age": self.limite_age,
            "limite_service": self.limite_service,
        }

    @classmethod
    def from_dict(cls, d: dict) -> GradeLimiteEntry:
        return cls(
            grade=str(d.get("grade", "")),
            limite_age=int(d.get("limite_age", 0)),
            limite_service=int(d.get("limite_service", 0)),
        )


@dataclass
class LimiteAgeDocOptions:
    signataire_titre: str = (
        "Le directeur du personnel et de la formation de la gendarmerie nationale"
    )
    signataire_nom: str = "Colonel-major Arthur Fernand KIBA"
    ville: str = "Brazzaville"
    lois_refs: list[str] = field(default_factory=lambda: [
        "Loi n°10-2021 du 27 janvier 2021, telle que modifiée et complétée"
        " par la loi n°17-2023 du 27 mai 2023",
        "Loi n°5-2022 du 26 janvier 2022",
    ])
    validite_mois: int = 3

    def to_dict(self) -> dict:
        return {
            "signataire_titre": self.signataire_titre,
            "signataire_nom":   self.signataire_nom,
            "ville": self.ville,
            "lois_refs": self.lois_refs,
            "validite_mois": self.validite_mois,
        }

    @classmethod
    def from_dict(cls, d: dict) -> LimiteAgeDocOptions:
        obj = cls()
        if "signataire_titre" in d:
            obj.signataire_titre = str(d["signataire_titre"])
        if "signataire_nom" in d:
            obj.signataire_nom = str(d["signataire_nom"])
        if "ville" in d:
            obj.ville = str(d["ville"])
        if isinstance(d.get("lois_refs"), list):
            obj.lois_refs = [str(x) for x in d["lois_refs"]]
        if "validite_mois" in d:
            obj.validite_mois = int(d["validite_mois"])
        return obj


_DEFAULT_GRADES: list[dict] = [
    {"grade": "Gendarme Adjoint",          "limite_age": 55, "limite_service": 35},
    {"grade": "Gendarme",                  "limite_age": 55, "limite_service": 35},
    {"grade": "Brigadier",                 "limite_age": 55, "limite_service": 37},
    {"grade": "Brigadier-Chef",            "limite_age": 55, "limite_service": 37},
    {"grade": "Maréchal des Logis",        "limite_age": 56, "limite_service": 37},
    {"grade": "Maréchal des Logis Chef",   "limite_age": 56, "limite_service": 38},
    {"grade": "Adjudant",                  "limite_age": 58, "limite_service": 40},
    {"grade": "Adjudant-Chef",             "limite_age": 58, "limite_service": 40},
    {"grade": "Major",                     "limite_age": 58, "limite_service": 40},
    {"grade": "Aspirant",                  "limite_age": 60, "limite_service": 40},
    {"grade": "Sous-Lieutenant",           "limite_age": 60, "limite_service": 40},
    {"grade": "Lieutenant",                "limite_age": 60, "limite_service": 40},
    {"grade": "Capitaine",                 "limite_age": 60, "limite_service": 40},
    {"grade": "Commandant",               "limite_age": 62, "limite_service": 40},
    {"grade": "Lieutenant-Colonel",        "limite_age": 62, "limite_service": 40},
    {"grade": "Colonel",                   "limite_age": 65, "limite_service": 40},
    {"grade": "Colonel-Major",             "limite_age": 65, "limite_service": 40},
    {"grade": "Général de Brigade",        "limite_age": 67, "limite_service": 40},
    {"grade": "Général de Division",       "limite_age": 67, "limite_service": 40},
]


@dataclass
class LimiteAgeConfig:
    version: int = 1
    grades: list[GradeLimiteEntry] = field(default_factory=list)
    options: LimiteAgeDocOptions = field(default_factory=LimiteAgeDocOptions)

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "grades": [g.to_dict() for g in self.grades],
            "options": self.options.to_dict(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> LimiteAgeConfig:
        grades_data = d.get("grades", [])
        grades = [GradeLimiteEntry.from_dict(g) for g in grades_data] if grades_data else []
        opts_data = d.get("options", {})
        opts = LimiteAgeDocOptions.from_dict(opts_data) if isinstance(opts_data, dict) else LimiteAgeDocOptions()
        return cls(
            version=int(d.get("version", 1)),
            grades=grades,
            options=opts,
        )
