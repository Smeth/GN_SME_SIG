"""Configuration JSON du fichier Excel DETACHES (titres, sigles, ordre, options)."""

from __future__ import annotations

from dataclasses import dataclass, field


def _norm_sigle(s: str) -> str:
    return (s or "").strip().upper()


@dataclass
class DetachesFileOptions:
    """Options globales pour la génération de la feuille DETACHE."""

    titre_ligne_feuille: str = "LISTE DES DETACHES PAR STRUCTURES"
    lieu_defaut: str = "BRAZZAVILLE"
    libelle_bloc_non_reference: str = "— PERSONNEL : SIGLE NON CONFIGURÉ (SUBDIVISION ARME) —"
    sigle_presidence: str = ""
    """Si renseigné (ex. PR), le bloc de ce sigle est toujours placé en tête (avant l’ordre numérique)."""

    @classmethod
    def from_dict(cls, d: dict | None) -> DetachesFileOptions:
        if not d:
            return cls()
        return cls(
            titre_ligne_feuille=str(d.get("titre_ligne_feuille") or cls.titre_ligne_feuille),
            lieu_defaut=str(d.get("lieu_defaut") or cls.lieu_defaut),
            libelle_bloc_non_reference=str(
                d.get("libelle_bloc_non_reference") or cls.libelle_bloc_non_reference
            ),
            sigle_presidence=_norm_sigle(str(d.get("sigle_presidence") or "")),
        )

    def to_dict(self) -> dict:
        return {
            "titre_ligne_feuille": self.titre_ligne_feuille,
            "lieu_defaut": self.lieu_defaut,
            "libelle_bloc_non_reference": self.libelle_bloc_non_reference,
            "sigle_presidence": self.sigle_presidence or "",
        }


@dataclass
class DetachesStructureEntry:
    """
    Un bloc dans la feuille DETACHE : sigle (subdivision arme en base) → titres + ordre.
    `titres_lignes` : une ou plusieurs lignes de titre avant les lignes personnel (ex. grande section puis sous-structure).
    """

    sigle: str
    ordre: int
    titres_lignes: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.sigle = _norm_sigle(self.sigle)
        self.titres_lignes = [str(t).strip() for t in self.titres_lignes if str(t).strip()]

    @classmethod
    def from_dict(cls, d: dict) -> DetachesStructureEntry:
        raw = d.get("titres_lignes")
        if isinstance(raw, list):
            titres = [str(x) for x in raw]
        else:
            titres = []
            for k in ("titre_ligne_1", "titre_ligne_2", "titre_ligne_3"):
                v = d.get(k)
                if v:
                    titres.append(str(v))
        return cls(
            sigle=str(d.get("sigle") or ""),
            ordre=int(d.get("ordre") or 0),
            titres_lignes=titres,
        )

    def to_dict(self) -> dict:
        return {
            "sigle": self.sigle,
            "ordre": self.ordre,
            "titres_lignes": self.titres_lignes,
        }


@dataclass
class DetachesFileConfig:
    """Configuration complète."""

    version: int = 1
    options: DetachesFileOptions = field(default_factory=DetachesFileOptions)
    structures: list[DetachesStructureEntry] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict) -> DetachesFileConfig:
        opts = DetachesFileOptions.from_dict(data.get("options") if isinstance(data.get("options"), dict) else None)
        structs_raw = data.get("structures")
        structures: list[DetachesStructureEntry] = []
        if isinstance(structs_raw, list):
            for item in structs_raw:
                if isinstance(item, dict):
                    structures.append(DetachesStructureEntry.from_dict(item))
        return cls(
            version=int(data.get("version") or 1),
            options=opts,
            structures=structures,
        )

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "options": self.options.to_dict(),
            "structures": [s.to_dict() for s in self.structures],
        }
