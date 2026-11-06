"""Validation shared by catalogue dialogs and SQL writers."""
import re
from sme_gend.padmin_present import (
    _collapsed, _non_present_exact_codes, _is_explicit_non_present,
    _is_present_text, _is_stage_interne,
)
from sme_gend.services.position_codes import position_code


def optional_order(value, label="Ordre"):
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError(f"{label} : indiquez un entier positif ou nul.")
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f"{label} : indiquez un entier positif ou nul.") from None
    if re.fullmatch(r"[0-9]+", str(value).strip()) is None or not 0 <= number <= 2147483647:
        raise ValueError(f"{label} : entier attendu entre 0 et 2147483647.")
    return number


def grade_values(code, label, precedence, workforce):
    code, label = (code or "").strip(), (label or "").strip()
    if not code or len(code) > 64 or not label or len(label) > 512:
        raise ValueError("Code (1–64 caractères) et libellé (1–512 caractères) obligatoires.")
    return code, label, optional_order(precedence, "Préséance"), optional_order(workforce, "Effectif renseigné")


def fixed_presence(label):
    code = position_code(label)
    if code is not None:
        return code == 'present'
    if _is_explicit_non_present(label) or _is_stage_interne(label):
        return False
    if _is_present_text(label):
        return True
    return None


def system_position(label):
    return position_code(label) is not None


def position_values(label, present, order, *, old_label=None):
    label = (label or "").strip()
    if not label or len(label) > 256:
        raise ValueError("Libellé obligatoire (256 caractères maximum).")
    if old_label is not None and old_label != label and system_position(old_label):
        raise ValueError("Cette position système utilise un code métier fixe et ne peut pas être renommée.")
    required = fixed_presence(label)
    if required is not None and bool(present) != required:
        raise ValueError("L'indicateur de présence est imposé par la règle métier de cette position.")
    return label, bool(present), optional_order(order)
