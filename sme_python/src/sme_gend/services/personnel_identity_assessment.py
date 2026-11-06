"""Critères communs à l'affichage et au rapprochement ; aucune correspondance floue."""
from dataclasses import dataclass
from datetime import date
import unicodedata


FIELDS = ("noms", "date_naissance", "matricule_militaire", "matricule_solde")
LABELS = {"noms": "Nom complet", "date_naissance": "Date de naissance",
          "matricule_militaire": "Matricule militaire", "matricule_solde": "Matricule solde"}
MISSING = {"", "-", "/", ".", "?", "0", "N/A", "NA", "NEANT", "INCONNU", "NON RENSEIGNE", "SANS",
           "NC", "N.C.", "NON COMMUNIQUE", "UNKNOWN", "UNDEFINED", "NULL", "NONE"}


def identity_value(field, value):
    if value is None:
        return ""
    if field == "date_naissance" and hasattr(value, "isoformat"):
        return value.isoformat()[:10]
    if field == "date_naissance":
        try:
            return date.fromisoformat(str(value).strip()[:10]).isoformat()
        except ValueError:
            return ""
    text = " ".join(str(value).split()).upper()
    folded = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))
    if folded in MISSING:
        return ""
    if field.startswith("matricule"):
        meaningful = "".join(c for c in folded if c.isalnum())
        if meaningful and set(meaningful) == {"0"}:
            return ""
    if field == "noms":
        # Tolérance de présentation uniquement, sans réordonner les noms/prénoms.
        return " ".join("".join(c if c.isalnum() else " " for c in folded).split())
    return text


def _get(record, field):
    return record.get(field) if isinstance(record, dict) else getattr(record, field, None)


@dataclass(frozen=True)
class IdentityAssessment:
    status: str  # compatible | divergent | incomplete
    agreements: tuple[str, ...]
    contradictions: tuple[str, ...]
    missing: tuple[str, ...]

    @property
    def eligible(self):
        return self.status == "compatible"

    @property
    def label(self):
        return {"compatible": "Doublon potentiel — à vérifier sur pièces",
                "divergent": "Identités divergentes — matricule à vérifier",
                "incomplete": "Identité incomplète — rapprochement non proposé"}[self.status]

    @property
    def explanation(self):
        if self.contradictions:
            return "Divergences : " + ", ".join(self.contradictions) + ". Le matricule partagé ne justifie pas un rapprochement."
        if not self.eligible:
            return ("Concordances insuffisantes. Il faut le même nom complet et au moins deux éléments concordants "
                    "parmi naissance, matricule militaire et matricule solde, dont au moins un matricule renseigné. "
                    + ("Données absentes ou non exploitables : " + ", ".join(self.missing) + "." if self.missing else ""))
        return "Concordances : " + ", ".join(self.agreements) + ". Elles permettent seulement de proposer un contrôle sur pièces, pas de conclure automatiquement à une identité unique."


def assess_identity(left, right):
    agreements, contradictions, missing = [], [], []
    for field in FIELDS:
        a, b = identity_value(field, _get(left, field)), identity_value(field, _get(right, field))
        if not a or not b:
            missing.append(LABELS[field])
        elif a == b:
            agreements.append(LABELS[field])
        else:
            contradictions.append(LABELS[field])
    if contradictions:
        status = "divergent"
    else:
        evidence = len([field for field in FIELDS[1:] if LABELS[field] in agreements])
        has_matricule = any(LABELS[field] in agreements for field in FIELDS[2:])
        status = "compatible" if LABELS["noms"] in agreements and evidence >= 2 and has_matricule else "incomplete"
    return IdentityAssessment(status, tuple(agreements), tuple(contradictions), tuple(missing))
