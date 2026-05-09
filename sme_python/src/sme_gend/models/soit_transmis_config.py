"""Modèle pour la configuration du document 'Soit transmis'."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SoitTransmisConfig:
    version: int = 1
    destinataire_titre: str = "Monsieur le commissaire colonel-major,"
    destinataire_role: str = (
        "directeur des services financiers de la gendarmerie nationale"
    )
    ville: str = "Brazzaville"
    reference_num: str = ""
    reference_suffix: str = "/GN/EM/DPF/DGP.-"

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "destinataire_titre": self.destinataire_titre,
            "destinataire_role": self.destinataire_role,
            "ville": self.ville,
            "reference_num": self.reference_num,
            "reference_suffix": self.reference_suffix,
        }

    @classmethod
    def from_dict(cls, d: dict) -> SoitTransmisConfig:
        obj = cls()
        for key in (
            "destinataire_titre",
            "destinataire_role",
            "ville",
            "reference_num",
            "reference_suffix",
        ):
            if key in d:
                setattr(obj, key, str(d[key]))
        if "version" in d:
            obj.version = int(d["version"])
        return obj
