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


@dataclass
class StructureAttestationOverride:
    """Correspondance libellé source → libellé imprimé dans l'attestation."""
    source: str
    attestation: str
    prep: str | None = None  # Préposition forcée : au, à la, à l'

    def to_dict(self) -> dict:
        out: dict = {"source": self.source, "attestation": self.attestation}
        if self.prep:
            out["prep"] = self.prep
        return out

    @classmethod
    def from_dict(cls, d: dict) -> StructureAttestationOverride:
        prep_raw = d.get("prep")
        prep = str(prep_raw).strip() if prep_raw else None
        return cls(
            source=str(d.get("source", "")),
            attestation=str(d.get("attestation", "")),
            prep=prep or None,
        )


def default_structure_overrides() -> list[StructureAttestationOverride]:
    """Valeurs initiales (GE-GQG, GGM…) — modifiables dans l'onglet dédié."""
    return [
        StructureAttestationOverride(
            "GROUPEMENT ESCADRONS GRAND QUARTIER GENERAL",
            "groupement des escadrons du grand quartier général",
        ),
        StructureAttestationOverride(
            "GROUPE D'ESCADRONS DU GRAND QUARTIER GENERAL",
            "groupement d'escadrons du grand quartier général",
        ),
        StructureAttestationOverride(
            "GROUPEMENT D'ESCADRONS DU GRAND QUARTIER GENERAL",
            "groupement d'escadrons du grand quartier général",
        ),
        StructureAttestationOverride(
            "GE-GQG",
            "groupement des escadrons du grand quartier général",
        ),
        StructureAttestationOverride(
            "PREMIER GROUPEMENT GENDARMERIE MOBILE",
            "premier groupement de gendarmerie mobile",
        ),
        StructureAttestationOverride(
            "1ER GROUPEMENT DE LA GENDARMERIE MOBILE",
            "premier groupement de gendarmerie mobile",
        ),
        StructureAttestationOverride(
            "1er GGM",
            "premier groupement de gendarmerie mobile",
        ),
        StructureAttestationOverride(
            "DEUXIEME GROUPEMENT GENDARMERIE MOBILE",
            "deuxième groupement de gendarmerie mobile",
        ),
        StructureAttestationOverride(
            "2E GGM",
            "deuxième groupement de gendarmerie mobile",
        ),
    ]


@dataclass
class LimiteAgeStructureFormat:
    overrides: list[StructureAttestationOverride] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"overrides": [o.to_dict() for o in self.overrides]}

    @classmethod
    def from_dict(cls, d: dict) -> LimiteAgeStructureFormat:
        raw = d.get("overrides", [])
        overrides = (
            [StructureAttestationOverride.from_dict(x) for x in raw]
            if isinstance(raw, list)
            else []
        )
        return cls(overrides=overrides)


def _default_grades() -> list[dict]:
    from sme_gend.services.statutory_limits import default_grade_rows

    return default_grade_rows()


_DEFAULT_GRADES: list[dict] = _default_grades()


@dataclass
class LimiteAgeConfig:
    version: int = 1
    grades: list[GradeLimiteEntry] = field(default_factory=list)
    options: LimiteAgeDocOptions = field(default_factory=LimiteAgeDocOptions)
    structure_format: LimiteAgeStructureFormat = field(
        default_factory=LimiteAgeStructureFormat
    )
    name_formats: dict[str, dict] = field(default_factory=dict)

    write_token: str | None = field(default=None,repr=False,compare=False)
    _pending_write: object | None = field(default=None,repr=False,compare=False)
    _write_key: str | None = field(default=None,repr=False,compare=False)
    _write_database: object | None = field(default=None,repr=False,compare=False)

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "grades": [g.to_dict() for g in self.grades],
            "options": self.options.to_dict(),
            "structure_format": self.structure_format.to_dict(),
            "name_formats": self.name_formats,
        }

    @classmethod
    def from_dict(cls, d: dict) -> LimiteAgeConfig:
        grades_data = d.get("grades", [])
        grades = [GradeLimiteEntry.from_dict(g) for g in grades_data] if grades_data else []
        opts_data = d.get("options", {})
        opts = LimiteAgeDocOptions.from_dict(opts_data) if isinstance(opts_data, dict) else LimiteAgeDocOptions()
        if "structure_format" in d and isinstance(d["structure_format"], dict):
            sf = LimiteAgeStructureFormat.from_dict(d["structure_format"])
        else:
            sf = LimiteAgeStructureFormat(overrides=default_structure_overrides())
        return cls(
            version=int(d.get("version", 1)),
            grades=grades,
            options=opts,
            structure_format=sf,
            name_formats={str(k): dict(v) for k, v in (d.get('name_formats') or {}).items()
                          if isinstance(v, dict)} if isinstance(d.get('name_formats', {}), dict) else {},
        )
