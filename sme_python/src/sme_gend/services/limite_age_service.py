"""Service — calcul des limites d'âge/service et génération des attestations."""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from sme_gend.services.person_name_display import format_person_name, set_name_boundary
from sme_gend.services.safe_paths import atomic_destination
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sme_gend.config.settings import Settings
    from sme_gend.models.liste_generale import ListeGeneraleRow

from sme_gend.config.settings import Settings
from sme_gend.models.limite_age_config import (
    GradeLimiteEntry,
    LimiteAgeConfig,
    LimiteAgeDocOptions,
    _DEFAULT_GRADES,
)
from sme_gend.services.matricule_format import format_matricule_solde_display


# ---------------------------------------------------------------------------
# Normalisation des noms de grade pour la comparaison
# ---------------------------------------------------------------------------

def _grade_key(g: str) -> str:
    """Normalise un libellé de grade : majuscules + suppression des accents."""
    upper = g.strip().upper()
    return unicodedata.normalize("NFD", upper).encode("ascii", "ignore").decode("ascii")


# ---------------------------------------------------------------------------
# Préposition de lieu pour les structures militaires (au/à la/à l')
# ---------------------------------------------------------------------------

# Noms masculins courants dans les structures gendarmerie
_STRUCT_MASC: frozenset[str] = frozenset({
    "GROUPEMENT", "QUARTIER", "BATAILLON", "REGIMENT", "ESCADRON",
    "PELOTON", "DETACHEMENT", "SERVICE", "CENTRE", "BUREAU",
    "COMMANDEMENT", "POSTE", "CAMP", "SECTEUR", "CORPS",
    "DEPARTEMENT", "GROUPE", "DISTRICT", "ARRONDISSEMENT",
    "CABINET", "CONTROLE",
})

# Noms féminins (à la …) — sauf voyelle initiale (à l'…)
_STRUCT_FEM: frozenset[str] = frozenset({
    "DIRECTION", "REGION", "POSITION",
})

# Adjectifs / ordinaux à ignorer pour trouver le nom principal
_STRUCT_SKIP: frozenset[str] = frozenset({
    "1ER", "1ERE", "2EME", "2E", "3EME", "3E", "4EME", "4E",
    "5EME", "5E", "6EME", "6E",
    "PREMIER", "DEUXIEME", "TROISIEME", "QUATRIEME", "CINQUIEME",
    "GRAND", "GRANDE", "GENERAL", "NATIONALE", "NATIONAL",
    "PETIT", "PETITE", "SPECIAL", "SPECIALE",
})


def _prep_structure(struct: str) -> str:
    """Retourne la préposition correcte : 'au', 'à la' ou 'à l\\'' selon le genre."""
    key_words = _grade_key(struct).replace("-", " ").split()
    for w in key_words:
        if w in _STRUCT_SKIP:
            continue
        if w and w[0] in "AEIOU":
            return "à l'"
        if w in _STRUCT_FEM:
            return "à la"
        if w in _STRUCT_MASC:
            return "au"
        break
    return "à la"


# Mots courants sans accent dans les désignations héritées (Access / ordre général)
_WORD_ACCENTS: dict[str, str] = {
    "region": "région",
    "securite": "sécurité",
    "generale": "générale",
    "general": "général",
    "interieur": "intérieur",
    "militaire": "militaire",
    "territoriale": "territoriale",
    "judiciaire": "judiciaire",
    "medical": "médical",
    "medicale": "médicale",
    "aerien": "aérien",
    "aeriens": "aériens",
    "ferroviaire": "ferroviaire",
    "maritime": "maritime",
    "fluviale": "fluviale",
    "operations": "opérations",
    "materiels": "matériels",
    "materiel": "matériel",
    "judiciaires": "judiciaires",
    "judiciares": "judiciaires",
    "centrale": "central",
    "ecole": "école",
    "etat": "état",
    "major": "major",
    "controle": "contrôle",
    "special": "spécial",
    "detache": "détaché",
    "routiere": "routière",
    "kintele": "Kintélé",
    "renseignements": "renseignements",
    "documentation": "documentation",
    "organisation": "organisation",
    "emploi": "emploi",
    "transmissions": "transmissions",
    "domaines": "domaines",
    "travaux": "travaux",
    "intervention": "intervention",
    "instruction": "instruction",
    "renseignement": "renseignement",
}

# Réécritures canoniques pour l'attestation (libellés source souvent abrégés)
_STRUCTURE_ATTESTATION_OVERRIDES: dict[str, str] = {
    _grade_key("GROUPEMENT ESCADRONS GRAND QUARTIER GENERAL"):
        "groupement des escadrons du grand quartier général",
    _grade_key("GROUPE D'ESCADRONS DU GRAND QUARTIER GENERAL"):
        "groupement d'escadrons du grand quartier général",
    _grade_key("GROUPEMENT D'ESCADRONS DU GRAND QUARTIER GENERAL"):
        "groupement d'escadrons du grand quartier général",
    _grade_key("GE-GQG"):
        "groupement des escadrons du grand quartier général",
}

_REGION_GEND_RE = re.compile(
    r"^REGION DE GEND(?:ARERIE|ARMERIE) (DE LA|DU|DES|DE) (.+)$",
)

_ORDINAL_FR: dict[str, str] = {
    "1ER": "premier",
    "1ERE": "première",
    "PREMIER": "premier",
    "PREMIERE": "première",
    "2EME": "deuxième",
    "2E": "deuxième",
    "DEUXIEME": "deuxième",
    "3EME": "troisième",
    "3E": "troisième",
    "TROISIEME": "troisième",
    "4EME": "quatrième",
    "4E": "quatrième",
    "QUATRIEME": "quatrième",
    "5EME": "cinquième",
    "5E": "cinquième",
    "CINQUIEME": "cinquième",
}

# GGM : libellé source parfois « … DE LA GENDARMERIE … » — l'attestation dit « … gendarmerie … »
_GGM_RE = re.compile(
    r"^(?:(1ER|1ERE|PREMIER|PREMIERE|2EME|2E|DEUXIEME|3EME|3E|TROISIEME|"
    r"4EME|4E|QUATRIEME|5EME|5E|CINQUIEME) GROUPEMENT(?: DE LA)? GENDARMERIE MOBILE|"
    r"1ER GGM|2E GGM)$",
)

_STRUCT_SMALL_WORDS: frozenset[str] = frozenset({
    "de", "du", "des", "d'", "et", "la", "le", "les", "l'", "en", "au", "à",
})


def _word_lower_with_accents(word: str) -> str:
    """Minuscules + correction d'accents sur un token."""
    low = word.lower()
    return _WORD_ACCENTS.get(low, low)


def _title_token(word: str) -> str:
    """Première lettre majuscule, reste en minuscules avec accents corrigés."""
    if "-" in word:
        return "-".join(_title_token(part) for part in word.split("-"))
    fixed = _word_lower_with_accents(word)
    if not fixed:
        return fixed
    return fixed[0].upper() + fixed[1:]


def _title_place(name: str) -> str:
    """Capitalise un toponyme (ex. NIARI → Niari, POINTE-NOIRE → Pointe-Noire)."""
    return " ".join(_title_token(part) for part in name.split())


def _lookup_in_map(keys: list[str], mapping: dict[str, str]) -> str | None:
    for candidate in keys:
        c = (candidate or "").strip()
        if not c:
            continue
        hit = mapping.get(_grade_key(c))
        if hit:
            return hit
    return None


def resolve_structure_text(
    settings: "Settings | None",
    struct_raw: str,
) -> tuple[str, str]:
    """Retourne (code référentiel, libellé long pour les règles automatiques)."""
    raw = (struct_raw or "").strip()
    if not raw:
        return "", ""
    if settings is None:
        return raw, raw
    try:
        from sme_gend.repositories.structure_repository import StructureRepository

        repo = StructureRepository(settings)
        row = repo.fetch_by_code(raw)
        if row is not None:
            return row.code, (row.designation or row.code)
        raw_key = _grade_key(raw)
        for s in repo.fetch_all():
            if _grade_key(s.code) == raw_key:
                return s.code, (s.designation or s.code)
            if s.designation and _grade_key(s.designation) == raw_key:
                return s.code, s.designation
    except Exception:
        pass
    return raw, raw


def structure_lookup_keys(
    struct_raw: str,
    settings: "Settings | None" = None,
) -> list[str]:
    code, text = resolve_structure_text(settings, struct_raw)
    keys: list[str] = []
    for candidate in (code, struct_raw, text):
        c = (candidate or "").strip()
        if c and c not in keys:
            keys.append(c)
    return keys


def default_attestation_label_for_structure_text(text: str) -> str:
    return _fmt_structure_for_attestation(text, dict(_STRUCTURE_ATTESTATION_OVERRIDES))


def attestation_label_for_structure(
    struct_raw: str,
    cfg: "LimiteAgeConfig | None",
    settings: "Settings | None" = None,
) -> tuple[str, bool]:
    """Retourne (libellé attestation, True si override explicite en config)."""
    _code, text = resolve_structure_text(settings, struct_raw)
    omap = structure_overrides_map(cfg)
    hit = _lookup_in_map(structure_lookup_keys(struct_raw, settings), omap)
    if hit:
        return hit, True
    label = _fmt_structure_for_attestation(text or struct_raw, omap)
    return label, False


def sync_structure_format_from_referentiel(
    settings: "Settings",
    cfg: LimiteAgeConfig,
) -> tuple[list, int]:
    """Aligne la config sur le référentiel structures (une ligne par code)."""
    from sme_gend.models.limite_age_config import StructureAttestationOverride
    from sme_gend.repositories.structure_repository import StructureRepository

    structures = StructureRepository(settings).fetch_all()
    att_by_key: dict[str, str] = {}
    prep_by_key: dict[str, str] = {}
    for entry in cfg.structure_format.overrides:
        src = (entry.source or "").strip()
        if not src:
            continue
        k = _grade_key(src)
        if entry.attestation:
            att_by_key[k] = entry.attestation
        if entry.prep:
            prep_by_key[k] = entry.prep

    known_codes = {_grade_key(o.source) for o in cfg.structure_format.overrides if o.source}
    added = 0
    out: list[StructureAttestationOverride] = []

    for row in sorted(
        structures,
        key=lambda s: (s.ordre_tri if s.ordre_tri is not None else 9999, s.code),
    ):
        code = (row.code or "").strip()
        if not code:
            continue
        designation = (row.designation or "").strip()
        custom = _lookup_in_map([code, designation], att_by_key)
        if _grade_key(code) not in known_codes:
            added += 1
        att = custom or default_attestation_label_for_structure_text(designation or code)
        prep = _lookup_in_map([code, designation], prep_by_key)
        out.append(
            StructureAttestationOverride(
                source=code,
                attestation=att,
                prep=prep,
            )
        )
    return out, added


def structure_prep_overrides_map(cfg: "LimiteAgeConfig | None" = None) -> dict[str, str]:
    """Mappe clé normalisée → préposition forcée (config utilisateur)."""
    if cfg is None:
        return {}
    out: dict[str, str] = {}
    for entry in cfg.structure_format.overrides:
        src = (entry.source or "").strip()
        prep = (entry.prep or "").strip()
        if src and prep:
            out[_grade_key(src)] = prep
    return out


def resolve_structure_prep(
    raw: str,
    cfg: "LimiteAgeConfig | None" = None,
    *,
    attestation: str | None = None,
    attestation_is_override: bool = False,
) -> str:
    """Préposition au/à la/à l' (override config prioritaire)."""
    text = (raw or "").strip()
    if not text:
        return "à la"
    key = _grade_key(text)
    prep = structure_prep_overrides_map(cfg).get(key)
    if prep:
        return prep
    if attestation_is_override and attestation:
        return _prep_structure(attestation)
    return _prep_structure(text)


def structure_overrides_map(cfg: "LimiteAgeConfig | None" = None) -> dict[str, str]:
    """Mappe clé normalisée → libellé attestation (config utilisateur prioritaire)."""
    from sme_gend.models.limite_age_config import LimiteAgeConfig

    merged = dict(_STRUCTURE_ATTESTATION_OVERRIDES)
    if cfg is None:
        return merged
    for entry in cfg.structure_format.overrides:
        src = (entry.source or "").strip()
        att = (entry.attestation or "").strip()
        if src and att:
            merged[_grade_key(src)] = att
    return merged


def _fmt_structure_for_attestation(
    raw: str,
    overrides: dict[str, str] | None = None,
) -> str:
    """Formate la désignation de structure pour l'attestation (accents + casse française)."""
    text = (raw or "").strip()
    if not text:
        return ""

    key = _grade_key(text)
    omap = overrides if overrides is not None else _STRUCTURE_ATTESTATION_OVERRIDES
    override = omap.get(key)
    if override:
        return override

    region = _REGION_GEND_RE.match(key)
    if region:
        prep_map = {"DE LA": "de la", "DU": "du", "DES": "des", "DE": "de"}
        prep = prep_map.get(region.group(1), region.group(1).lower())
        place = _title_place(region.group(2))
        return f"région de gendarmerie {prep} {place}"

    ggm = _GGM_RE.match(key)
    if ggm:
        if key == "1ER GGM":
            ordinal = "premier"
        elif key == "2E GGM":
            ordinal = "deuxième"
        else:
            ordinal = _ORDINAL_FR.get(ggm.group(1), ggm.group(1).lower())
        return f"{ordinal} groupement de gendarmerie mobile"

    words = text.split()
    out: list[str] = []
    for i, word in enumerate(words):
        core = word.lower().strip("'")
        if i == 0:
            expanded = _ORDINAL_FR.get(_grade_key(word), "")
            out.append(expanded or _word_lower_with_accents(word))
        elif core in _STRUCT_SMALL_WORDS or word.lower() in _STRUCT_SMALL_WORDS:
            out.append(_word_lower_with_accents(word))
        else:
            out.append(_word_lower_with_accents(word))
    return " ".join(out)


def _join_prep_structure(prep: str, struct: str) -> str:
    """Assemble préposition + libellé (sans espace après « à l' »)."""
    if not struct:
        return prep
    if prep.endswith("'"):
        return f"{prep}{struct}"
    return f"{prep} {struct}"


def preview_structure_attestation(
    raw: str,
    cfg: "LimiteAgeConfig | None" = None,
    settings: "Settings | None" = None,
) -> str:
    """Ligne complète « préposition + structure » pour aperçu ou test."""
    text = (raw or "").strip()
    if not text:
        return ""
    struct_str, is_override = attestation_label_for_structure(text, cfg, settings)
    lookup = structure_lookup_keys(text, settings)
    prep = _lookup_in_map(lookup, structure_prep_overrides_map(cfg))
    if not prep:
        _code, resolved = resolve_structure_text(settings, text)
        prep = resolve_structure_prep(
            resolved or text,
            cfg,
            attestation=struct_str,
            attestation_is_override=is_override,
        )
    if not struct_str:
        return prep
    return _join_prep_structure(prep, struct_str)


def _resolve_service_line(
    struct_raw: str,
    cfg: "LimiteAgeConfig | None",
    settings: "Settings | None" = None,
) -> tuple[str, str, str]:
    """Retourne (prep, libellé attestation, ligne complète En service)."""
    struct_str, is_override = attestation_label_for_structure(struct_raw, cfg, settings)
    lookup = structure_lookup_keys(struct_raw, settings)
    prep_str = _lookup_in_map(lookup, structure_prep_overrides_map(cfg))
    if not prep_str:
        _code, text = resolve_structure_text(settings, struct_raw)
        prep_str = resolve_structure_prep(
            text or struct_raw,
            cfg,
            attestation=struct_str,
            attestation_is_override=is_override,
        )
    line = _join_prep_structure(prep_str, struct_str) if struct_str else prep_str
    return prep_str, struct_str, line


# ---------------------------------------------------------------------------
# Chemin du fichier de configuration
# ---------------------------------------------------------------------------

def limite_age_config_path() -> Path:
    from sme_gend.config.settings import project_root
    return project_root() / "data" / "limite_age_config.json"


# ---------------------------------------------------------------------------
# Utilitaires : conversion de nombres en lettres (français)
# ---------------------------------------------------------------------------

_UNITS = [
    "", "un", "deux", "trois", "quatre", "cinq", "six", "sept", "huit", "neuf",
    "dix", "onze", "douze", "treize", "quatorze", "quinze", "seize",
    "dix-sept", "dix-huit", "dix-neuf",
]

_TENS_BASE = [
    "", "dix", "vingt", "trente", "quarante", "cinquante", "soixante",
]

_MOIS_FR = [
    "", "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
]


def _small_int_fr(n: int) -> str:
    """Convertit un entier 0-99 en lettres (français)."""
    if n < 0 or n > 99:
        return str(n)
    if n < 20:
        return _UNITS[n]
    if n < 70:
        tens, units = divmod(n, 10)
        base = _TENS_BASE[tens]
        if units == 0:
            return base
        if units == 1:
            return base + " et un"
        return base + "-" + _UNITS[units]
    if n < 80:
        units = n - 60
        if units == 11:
            return "soixante et onze"
        return "soixante-" + _UNITS[units] if units < 20 else "soixante-" + _small_int_fr(units)
    if n == 80:
        return "quatre-vingts"
    if n < 90:
        return "quatre-vingt-" + _UNITS[n - 80]
    return "quatre-vingt-" + _small_int_fr(n - 80)


def _int_to_fr(n: int) -> str:
    """Entier 0-9999 → lettres français."""
    if n < 0:
        return "moins " + _int_to_fr(-n)
    if n < 100:
        return _small_int_fr(n)
    if n < 200:
        rest = n - 100
        return "cent" if rest == 0 else "cent " + _small_int_fr(rest)
    if n < 1000:
        hundreds, rest = divmod(n, 100)
        h = _UNITS[hundreds] + " cent"
        return h + "s" if rest == 0 else h + " " + _small_int_fr(rest)
    if n < 2000:
        rest = n - 1000
        return "mille" if rest == 0 else "mille " + _int_to_fr(rest)
    thousands, rest = divmod(n, 1000)
    prefix = _small_int_fr(thousands) + " mille"
    return prefix if rest == 0 else prefix + " " + _int_to_fr(rest)


def _day_fr(day: int) -> str:
    """Numéro de jour → lettres (1 = Premier, 2 = deux, …)."""
    if day == 1:
        return "Premier"
    return _int_to_fr(day).capitalize()


def date_to_words_fr(d: date) -> str:
    """date → 'Trente et un décembre deux mille trente-quatre (31-12-2034)'."""
    day_w = _day_fr(d.day)
    month_w = _MOIS_FR[d.month]
    year_w = _int_to_fr(d.year)
    date_num = f"{d.day:02d}-{d.month:02d}-{d.year}"
    return f"{day_w} {month_w} {year_w} ({date_num})"


# ---------------------------------------------------------------------------
# Parsing des dates
# ---------------------------------------------------------------------------

_DATE_PATTERNS = [
    re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$"),   # DD/MM/YYYY
    re.compile(r"^(\d{4})-(\d{2})-(\d{2})$"),         # YYYY-MM-DD
    re.compile(r"^(\d{1,2})-(\d{1,2})-(\d{4})$"),     # DD-MM-YYYY
]


def _parse_date_str(s: str | None) -> date | None:
    """Tente de parser DD/MM/YYYY, YYYY-MM-DD ou DD-MM-YYYY."""
    if not s:
        return None
    s = s.strip()
    for pat in _DATE_PATTERNS:
        m = pat.match(s)
        if m:
            g = m.groups()
            try:
                if len(g[0]) == 4:
                    return date(int(g[0]), int(g[1]), int(g[2]))
                return date(int(g[2]), int(g[1]), int(g[0]))
            except ValueError:
                continue
    return None


def _to_date(v: datetime | date | str | None) -> date | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return _parse_date_str(str(v))


def current_age_years(
    birth: date | datetime | str | None,
    *,
    on: date | None = None,
) -> int | None:
    """Âge révolu en années à la date ``on`` (aujourd'hui par défaut)."""
    dn = _to_date(birth)
    if dn is None:
        return None
    ref = on or date.today()
    return ref.year - dn.year - ((ref.month, ref.day) < (dn.month, dn.day))


def match_age_filter(
    age: int | None,
    operator: str,
    threshold: int,
) -> bool:
    """Teste l'âge révolu contre un seuil (opérateurs >, <, =)."""
    if age is None:
        return False
    op = (operator or "").strip()
    if op == ">":
        return age > threshold
    if op == "<":
        return age < threshold
    if op == "=":
        return age == threshold
    return True


# ---------------------------------------------------------------------------
# Génération du code-barres Code 128
# ---------------------------------------------------------------------------

def _barcode_data(person) -> str:
    """Construit la chaîne encodée dans le code-barres : noms, structure, âge."""
    noms   = _grade_key(person.noms      or "")[:40]
    struct = _grade_key(person.structure or "")[:30]
    years = current_age_years(person.date_naissance)
    age_str = f"{years} ANS" if years is not None else "?"
    return "|".join([noms or "?", struct or "?", age_str])


def _generate_barcode_png(data: str, target_width: int = 520) -> bytes | None:
    """Génère un code-barres Code128 en PNG redimensionné à target_width pixels."""
    try:
        import barcode
        from barcode.writer import ImageWriter
        from PIL import Image as _PILImage
        import io
        safe = "".join(c if 32 <= ord(c) <= 127 else "_" for c in data)
        code = barcode.get("code128", safe, writer=ImageWriter())
        buf = io.BytesIO()
        code.write(buf, options={
            "module_height":  36.0,
            "module_width":   0.38,
            "quiet_zone":     6.0,
            "write_text":     False,
            "background":     "white",
            "foreground":     "black",
        })
        buf.seek(0)
        img = _PILImage.open(buf)
        ratio = target_width / img.width
        new_h  = int(img.height * ratio)
        img    = img.resize((target_width, new_h), _PILImage.LANCZOS)
        out = io.BytesIO()
        img.save(out, format="PNG")
        return out.getvalue()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Intégration du code-barres comme image ancrée dans le document Word
# ---------------------------------------------------------------------------

def _embed_barcode_image(doc, para, bc_png_bytes: bytes) -> bool:
    """
    Remplace la zone de texte Code-128 (P00) par une image PNG ancrée
    à la même position dans le document.
    """
    import io as _io
    from docx.oxml.ns import qn
    from docx.shared import Emu
    import lxml.etree as etree

    WP  = 'http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing'
    A   = 'http://schemas.openxmlformats.org/drawingml/2006/main'
    PIC = 'http://schemas.openxmlformats.org/drawingml/2006/picture'
    R   = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
    MC  = 'http://schemas.openxmlformats.org/markup-compatibility/2006'
    W   = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'

    # Lire la position/taille de l'ancre existante
    existing_anchor = para._element.find('.//{%s}anchor' % WP)
    pos_h, pos_v, cx, cy = 335915, 167005, 1946275, 399415
    if existing_anchor is not None:
        for child in existing_anchor:
            ln = etree.QName(child.tag).localname
            if ln == 'positionH':
                for c in child:
                    if etree.QName(c.tag).localname == 'posOffset':
                        try: pos_h = int(c.text)
                        except (ValueError, TypeError): pass
            elif ln == 'positionV':
                for c in child:
                    if etree.QName(c.tag).localname == 'posOffset':
                        try: pos_v = int(c.text)
                        except (ValueError, TypeError): pass
            elif ln == 'extent':
                try: cx = int(child.get('cx', cx))
                except (ValueError, TypeError): pass
                try: cy = int(child.get('cy', cy))
                except (ValueError, TypeError): pass

    # Calculer la hauteur réelle en conservant les proportions de l'image
    try:
        from PIL import Image as _PILImage
        with _PILImage.open(_io.BytesIO(bc_png_bytes)) as _img:
            iw, ih = _img.size
        if iw > 0:
            cy = int(cx * ih / iw)
    except Exception:
        pass

    # Ajouter l'image au document via un paragraphe temporaire
    temp_p = doc.add_paragraph()
    try:
        temp_p.add_run().add_picture(_io.BytesIO(bc_png_bytes), width=Emu(cx))
    except Exception:
        temp_p._element.getparent().remove(temp_p._element)
        return False

    blip = temp_p._element.find('.//{%s}blip' % A)
    if blip is None:
        temp_p._element.getparent().remove(temp_p._element)
        return False
    rId = blip.get('{%s}embed' % R, '')
    temp_p._element.getparent().remove(temp_p._element)
    if not rId:
        return False

    # Construire l'ancre XML pour une image
    anchor_str = (
        f'<wp:anchor xmlns:wp="{WP}" xmlns:a="{A}" xmlns:pic="{PIC}" xmlns:r="{R}" '
        f'distT="0" distB="0" distL="114300" distR="114300" simplePos="0" '
        f'relativeHeight="251657216" behindDoc="0" locked="0" layoutInCell="1" allowOverlap="1">'
        f'<wp:simplePos x="0" y="0"/>'
        f'<wp:positionH relativeFrom="column"><wp:posOffset>{pos_h}</wp:posOffset></wp:positionH>'
        f'<wp:positionV relativeFrom="paragraph"><wp:posOffset>{pos_v}</wp:posOffset></wp:positionV>'
        f'<wp:extent cx="{cx}" cy="{cy}"/>'
        f'<wp:effectExtent l="0" t="0" r="0" b="0"/>'
        f'<wp:wrapNone/>'
        f'<wp:docPr id="2" name="BarcodeImg"/>'
        f'<wp:cNvGraphicFramePr>'
        f'<a:graphicFrameLocks noChangeAspect="1"/>'
        f'</wp:cNvGraphicFramePr>'
        f'<a:graphic>'
        f'<a:graphicData uri="{PIC}">'
        f'<pic:pic xmlns:pic="{PIC}">'
        f'<pic:nvPicPr><pic:cNvPr id="0" name="Barcode"/><pic:cNvPicPr/></pic:nvPicPr>'
        f'<pic:blipFill>'
        f'<a:blip r:embed="{rId}"/>'
        f'<a:stretch><a:fillRect/></a:stretch>'
        f'</pic:blipFill>'
        f'<pic:spPr>'
        f'<a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
        f'<a:prstGeom prst="rect"><a:avLst/></a:prstGeom>'
        f'</pic:spPr>'
        f'</pic:pic>'
        f'</a:graphicData>'
        f'</a:graphic>'
        f'</wp:anchor>'
    )
    new_anchor = etree.fromstring(anchor_str)

    # Remplacer AlternateContent (zone de texte) par le nouveau dessin
    for r_el in para._element.findall('{%s}r' % W):
        alt = r_el.find('{%s}AlternateContent' % MC)
        if alt is not None:
            new_drawing = etree.Element('{%s}drawing' % W)
            new_drawing.append(new_anchor)
            r_el.remove(alt)
            r_el.append(new_drawing)
            return True
    return False


# ---------------------------------------------------------------------------
# Recherche de limites par défaut pour un grade inconnu
# ---------------------------------------------------------------------------

def _grade_key_loose(g: str) -> str:
    """Même clé que ``_grade_key``, tirets et apostrophes traités comme des espaces."""
    raw = _grade_key(g).replace("-", " ").replace("'", " ")
    return " ".join(raw.split())


_DEFAULT_LOOKUP: dict[str, tuple[int, int]] = {
    _grade_key_loose(d["grade"]): (d["limite_age"], d["limite_service"])
    for d in _DEFAULT_GRADES
}


def _align_article98(cfg: LimiteAgeConfig) -> bool:
    """Aligne les grades de l'article 98 sur la table unique, si la config diverge."""
    from sme_gend.services.statutory_limits import article98_limits, exact_canonical_grade

    changed = False
    for entry in cfg.grades:
        code = exact_canonical_grade(entry.grade)
        limits = article98_limits(code) if code else None
        if limits is None:
            continue
        age, service = limits
        if entry.limite_age != age or entry.limite_service != service:
            entry.limite_age = age
            entry.limite_service = service
            changed = True
    return changed


def statutory_default_limits(grade_str: str) -> tuple[int, int] | None:
    """Barème central : article 98, puis grades historiques hors décret."""
    from sme_gend.services.statutory_limits import limits_for_grade, exact_canonical_grade

    code = exact_canonical_grade(grade_str)
    return limits_for_grade(code) if code else None


def _find_default_limits(grade_str: str) -> tuple[int, int] | None:
    """Cherche dans _DEFAULT_GRADES une entrée correspondant au grade donné."""
    g = _grade_key_loose(grade_str)
    if g in _DEFAULT_LOOKUP:
        return _DEFAULT_LOOKUP[g]
    return statutory_default_limits(grade_str)


# ---------------------------------------------------------------------------
# Service principal
# ---------------------------------------------------------------------------

@dataclass
class LimiteDateResult:
    limit_date: date | None
    limit_type: str   # "âge", "service", "âge+service", "—"
    age_limit_date: date | None
    service_limit_date: date | None
    grade_entry: GradeLimiteEntry | None
    issues: tuple[str, ...] = ()


def _attestation_template_fields(doc):
    """Valider les repères sémantiques avant de modifier le document."""
    from sme_gend.services.grade_label_format import normalize_grade_key
    labels = {"person":"atteste que le", "service":"en service", "birth":"date de naissance",
              "military":"matricule militaire", "pay":"matricule solde", "entry":"entree au service"}
    result = {}
    for field,label in labels.items():
        found = [p for p in doc.paragraphs if normalize_grade_key(p.text).startswith(label+" ") or normalize_grade_key(p.text) == label]
        if len(found) != 1 or ":" not in found[0].text:
            raise ValueError(f"Modèle d’attestation incompatible : repère « {label} » absent ou ambigu. Aucun document n’a été remplacé.")
        result[field] = found[0]
    limit_labels = [i for i,p in enumerate(doc.paragraphs) if normalize_grade_key(p.text).startswith("atteindra la limite")]
    if len(limit_labels) != 1:
        raise ValueError("Modèle d’attestation incompatible : repère de date limite absent ou ambigu.")
    following = next((p for p in doc.paragraphs[limit_labels[0]+1:] if p.text.strip()),None)
    if following is None or not (re.search(r"\b\d{2}[-/]\d{2}[-/]\d{4}\b",following.text) or "{{date_limite}}" in following.text):
        raise ValueError("Modèle d’attestation incompatible : emplacement de date limite non reconnu.")
    result["limit"] = following
    signature, barcode = [], []
    for p in doc.paragraphs:
        boxes = p._element.xpath('.//w:txbxContent')
        if not boxes: continue
        text = normalize_grade_key(" ".join(p._element.xpath('.//w:txbxContent//w:t/text()')))
        if "code a barre" in text or "code barre" in text: barcode.append(p)
        elif text: signature.append(p)
    if len(signature) != 1 or len(barcode) > 1:
        raise ValueError("Modèle d’attestation incompatible : zones de signature ou de code-barres ambiguës.")
    result["signature"] = signature[0]
    result["barcode"] = barcode[0] if barcode else None
    return result


def _replace_attestation_value(paragraph, value):
    """Conserver le libellé et son format ; remplacer seulement la valeur."""
    boundary = paragraph.text.index(":")+1
    position, inserted = 0, False
    for run in list(paragraph.runs):
        original = run.text
        end = position + len(original)
        if end > boundary:
            if not inserted:
                prefix = original[:max(0,boundary-position)]
                if prefix:
                    run.text = prefix
                    added = paragraph.add_run(" " + value)
                    added.bold = True
                    run._element.addnext(added._element)
                else:
                    run.text = " " + value
                    run.bold = True
                inserted = True
            else:
                run.text = ""
        position = end
    if not inserted:
        paragraph.add_run(" " + value).bold = True


class LimiteAgeService:
    def __init__(self) -> None:
        pass

    def save_name_format(self, cfg, person, family_name, given_names, settings):
        from copy import deepcopy
        from sme_gend.services.auth_guard import session_has_permission
        if not session_has_permission('attestations.limite_age'):
            raise PermissionError('Modification de la présentation des attestations non autorisée.')
        updated = deepcopy(cfg)
        set_name_boundary(updated, person, family_name, given_names)
        key = str(person.satellite_join_key())
        def patch(payload):
            names = deepcopy(payload.get('name_formats') or {})
            names[key] = deepcopy(updated.name_formats[key])
            payload['name_formats'] = names
        self._save_patch(updated, settings, patch)
        return updated

    # ---- Config I/O --------------------------------------------------------

    def load(self, settings: Settings | None = None) -> LimiteAgeConfig:
        from sme_gend.config import get_settings
        from sme_gend.services.app_config_store import KEY_LIMITE_AGE, load_config_model

        st = settings or get_settings()

        def _factory(d: dict) -> LimiteAgeConfig:
            return LimiteAgeConfig.from_dict(d) if d else LimiteAgeConfig()

        cfg = load_config_model(st, KEY_LIMITE_AGE, default_factory=_factory)
        # Appliquer le barème imposé en mémoire sans modifier la configuration
        # pendant une lecture. L'éditeur rend ces cellules explicitement fixes.
        adjusted = [(e.grade,e.limite_age,e.limite_service) for e in cfg.grades]
        _align_article98(cfg)
        cfg._limits_adjusted = tuple(old[0] for old,e in zip(adjusted,cfg.grades)
                                    if old[1:] != (e.limite_age,e.limite_service))
        return cfg

    def save(self, cfg: LimiteAgeConfig, settings: Settings | None = None) -> None:
        from sme_gend.config import get_settings
        from sme_gend.services.app_config_store import KEY_LIMITE_AGE, save_config_model

        self._validate_grades(cfg)
        save_config_model(settings or get_settings(), KEY_LIMITE_AGE, cfg)

    @staticmethod
    def _validate_grades(cfg: LimiteAgeConfig) -> None:
        from sme_gend.services.statutory_limits import exact_canonical_grade, article98_limits
        from sme_gend.services.grade_label_format import normalize_grade_key
        seen = set()
        for entry in cfg.grades:
            code = exact_canonical_grade(entry.grade)
            key = code or normalize_grade_key(entry.grade)
            if not key or key in seen:
                raise ValueError("Chaque grade doit être renseigné une seule fois, y compris ses alias.")
            seen.add(key)
            if not (0 <= entry.limite_age <= 120 and 0 <= entry.limite_service <= 120):
                raise ValueError("Les limites doivent être comprises entre 0 et 120 ans.")
            imposed = article98_limits(code) if code else None
            if imposed and imposed != (entry.limite_age,entry.limite_service):
                raise ValueError(f"{entry.grade} : barème imposé par le logiciel ({imposed[0]} ans d’âge, {imposed[1]} ans de service). Ces valeurs ne sont pas modifiables ici.")

    def save_grades(self, cfg: LimiteAgeConfig, settings: Settings) -> None:
        self._validate_grades(cfg)
        self._save_patch(cfg, settings, lambda payload: payload.update(grades=[entry.to_dict() for entry in cfg.grades]))

    def save_doc_options(self, cfg: LimiteAgeConfig, settings: Settings | None = None) -> None:
        from sme_gend.config import get_settings
        validity = cfg.options.validite_mois
        if type(validity) is not int or not 1 <= validity <= 12:
            raise ValueError('La validité du document doit être comprise entre 1 et 12 mois.')
        def patch(payload):
            options = payload.get('options')
            payload['options'] = {**(options if isinstance(options, dict) else {}), **cfg.options.to_dict()}
        self._save_patch(cfg, settings or get_settings(), patch)

    def save_structure_format(self, cfg: LimiteAgeConfig, settings: Settings) -> None:
        """Save only the displayed structure labels, preserving other sections.

        Legacy grade aliases must not prevent an unrelated presentation edit.
        Their validation remains mandatory when saving the grade configuration.
        """
        sources = set()
        for entry in cfg.structure_format.overrides:
            key = _grade_key(entry.source)
            if not key or key in sources or not entry.attestation.strip():
                raise ValueError('Chaque structure doit avoir un code unique et un libellé attestation renseigné.')
            sources.add(key)
        self._save_patch(cfg, settings, lambda payload: payload.update(structure_format=cfg.structure_format.to_dict()))

    def _save_patch(self, cfg, settings, transform):
        """Apply one section's intent with the editor's shared version token."""
        from copy import deepcopy
        from sme_gend.services.app_config_store import KEY_LIMITE_AGE
        from sme_gend.services.shared_config_write import (
            VersionedConfigDict, copy_config_version, load_shared_payload, save_shared_model,
        )
        from sme_gend.services.shared_reference_write import shared_reference_write
        from sme_gend.repositories.mysql_connection import after_mysql_commit, mysql_database_key
        token = getattr(cfg, 'write_token', None)
        if (getattr(cfg, '_write_key', None) != KEY_LIMITE_AGE
                or getattr(cfg, '_write_database', None) != mysql_database_key(settings)
                or not isinstance(token, str) or re.fullmatch('[0-9a-f]{64}', token) is None):
            raise ValueError("Version de configuration indisponible. Rechargez les données avant d'enregistrer.")
        with shared_reference_write(settings):
            raw = load_shared_payload(settings, KEY_LIMITE_AGE)
            payload = deepcopy(raw if raw is not None else LimiteAgeConfig().to_dict())
            transform(payload)
            updated = VersionedConfigDict(payload)
            copy_config_version(cfg, updated)
            save_shared_model(settings, KEY_LIMITE_AGE, updated)
            copy_config_version(updated, cfg)
            after_mysql_commit(lambda: copy_config_version(updated, cfg))

    # ---- Synchronisation des grades depuis la base de données --------------

    def sync_grades_from_referentiel(
        self, settings: Settings
    ) -> tuple[LimiteAgeConfig, list[str]]:
        """
        Lit les grades depuis TabGrade (MySQL / Access), ajoute ceux absents de la
        config avec des valeurs par défaut estimées. Sauvegarde si des ajouts ont été faits.
        Retourne (cfg mis à jour, liste des libellés nouvellement ajoutés).
        """
        from sme_gend.repositories.grade_repository import GradeRepository

        from copy import deepcopy
        from sme_gend.services.grade_label_format import normalize_grade_key
        cfg = deepcopy(self.load(settings))
        from sme_gend.services.statutory_limits import exact_canonical_grade
        # A single entry serves all known aliases. Coalesce only identical
        # effective limits; conflicting custom limits require an explicit edit.
        unique, canonical, cleanups = [], {}, []
        for number, entry in enumerate(cfg.grades, 1):
            key = exact_canonical_grade(entry.grade) or normalize_grade_key(entry.grade)
            if not key:
                if (entry.limite_age, entry.limite_service) == (0, 0):
                    cleanups.append(f'Ligne vide {number} retirée.')
                    continue
                raise ValueError(f'Ligne {number} : renseignez le grade dans « Configuration des limites » avant de synchroniser.')
            previous = canonical.get(key)
            if previous is not None:
                if (entry.limite_age, entry.limite_service) != (previous.limite_age, previous.limite_service):
                    raise ValueError(f'Limites contradictoires pour « {previous.grade} » et « {entry.grade} ». Corrigez ces lignes dans « Configuration des limites » ; aucune modification enregistrée.')
                cleanups.append(f'« {entry.grade} » regroupé avec « {previous.grade} ».')
                continue
            canonical[key] = entry
            unique.append(entry)
        cfg.grades = unique
        cfg._sync_cleanups = tuple(cleanups)
        known = {_grade_key(e.grade): e for e in cfg.grades}
        known_codes = {exact_canonical_grade(e.grade) for e in cfg.grades} - {None}

        db_grades = GradeRepository(settings).fetch_all()

        added: list[str] = []
        for gr in sorted(db_grades, key=lambda r: (r.pref_ord or 999, r.libelle)):
            libelle = (gr.libelle or "").strip()
            if not libelle:
                continue
            gkey = _grade_key(libelle)
            code = exact_canonical_grade(libelle)
            if gkey not in known and (code is None or code not in known_codes):
                defaults = _find_default_limits(libelle)
                la, ls = defaults if defaults is not None else (0, 0)
                entry = GradeLimiteEntry(grade=libelle, limite_age=la, limite_service=ls)
                cfg.grades.append(entry)
                known[gkey] = entry
                if code: known_codes.add(code)
                added.append(libelle)

        if added or cleanups:
            self.save_grades(cfg, settings)
        return cfg, added

    # ---- Grade lookup ------------------------------------------------------

    def find_grade_entry(
        self, grade_str: str | None, cfg: LimiteAgeConfig
    ) -> GradeLimiteEntry | None:
        if not grade_str:
            return None
        from sme_gend.services.grade_label_format import normalize_grade_key
        from sme_gend.services.statutory_limits import exact_canonical_grade
        key = normalize_grade_key(grade_str)
        if not key:
            return None
        code = exact_canonical_grade(grade_str)
        matches = [e for e in cfg.grades if normalize_grade_key(e.grade) == key
                   or code is not None and exact_canonical_grade(e.grade) == code]
        return matches[0] if len(matches) == 1 else None

    # ---- Date de limite ----------------------------------------------------

    def calculate_limit(
        self, person: ListeGeneraleRow, cfg: LimiteAgeConfig
    ) -> LimiteDateResult:
        entry = self.find_grade_entry(person.grade, cfg)

        age_date: date | None = None
        svc_date: date | None = None

        dn = _to_date(person.date_naissance)
        ds = _to_date(person.date_service)

        issues = []
        if entry is None:
            issues.append("Grade non configuré ou correspondance ambiguë.")
        elif entry.limite_age <= 0 and entry.limite_service <= 0:
            issues.append("Limites non configurées pour ce grade.")
        elif not (0 <= entry.limite_age <= 120 and 0 <= entry.limite_service <= 120):
            issues.append("Barème invalide : les limites doivent être comprises entre 0 et 120 ans.")
        if dn is None: issues.append("Date de naissance manquante ou invalide.")
        if ds is None: issues.append("Date d’entrée au service manquante ou invalide.")
        today = date.today()
        if dn and dn > today: issues.append("La date de naissance est dans le futur.")
        if ds and ds > today: issues.append("La date d’entrée au service est dans le futur.")
        if dn and ds and ds <= dn: issues.append("L’entrée au service doit être postérieure à la naissance.")
        if issues:
            return LimiteDateResult(None,"À vérifier",None,None,entry,tuple(issues))

        if entry:
            if dn and entry.limite_age > 0:
                try:
                    age_date = date(dn.year + entry.limite_age, 12, 31)
                except ValueError:
                    pass
            if ds and entry.limite_service > 0:
                try:
                    svc_date = date(ds.year + entry.limite_service, 12, 31)
                except ValueError:
                    pass

        candidates = [d for d in (age_date, svc_date) if d is not None]
        if not candidates:
            return LimiteDateResult(None, "—", None, None, entry)

        limit = min(candidates)
        if age_date and svc_date:
            ltype = "âge+service" if age_date == svc_date else ("âge" if limit == age_date else "service")
        elif age_date:
            ltype = "âge"
        else:
            ltype = "service"

        return LimiteDateResult(limit, ltype, age_date, svc_date, entry)

    # ---- Génération Word (.docx) -------------------------------------------

    @staticmethod
    def _fmt_grade(grade: str) -> str:
        """Première lettre en majuscule, reste en minuscule (ex: ADJUDANT → Adjudant)."""
        g = grade.strip()
        return (g[0].upper() + g[1:].lower()) if g else ""

    def generate_docx(
        self,
        person: ListeGeneraleRow,
        cfg: LimiteAgeConfig,
        dest_path: Path,
        sign_date: date | None = None,
        settings: "Settings | None" = None,
        *,
        log_generation: bool = True,
    ) -> Path:
        from docx import Document
        from docx.oxml.ns import qn
        from sme_gend.config.settings import project_root

        result = self.calculate_limit(person, cfg)
        if result.limit_date is None:
            raise ValueError(
                "Attestation impossible. Configurez la limite du grade et vérifiez les dates. " + " ".join(result.issues)
            )

        from sme_gend.config.settings import default_modele_path, resolve_modele_file

        template_path = resolve_modele_file("limite_age.docx") or default_modele_path(
            "limite_age.docx"
        )
        if not template_path.is_file():
            raise FileNotFoundError(f"Modèle introuvable : {template_path}")

        doc   = Document(str(template_path))
        template_fields = _attestation_template_fields(doc)

        def _set_bold_value(para, new_text: str) -> None:
            """Le paragraphe de date validé contient uniquement sa valeur."""
            runs = para.runs
            first = runs[0] if runs else para.add_run()
            first.text = new_text
            first.bold = True
            for run in runs[1:]:
                run.text = ""

        # ── Computed values ──────────────────────────────────────────────────
        opts       = cfg.options
        dn         = _to_date(person.date_naissance)
        ds         = _to_date(person.date_service)
        grade_str  = self._fmt_grade(person.grade      or "")
        noms_str   = format_person_name(person, cfg)
        struct_raw = (person.structure  or "").strip()
        prep_str, struct_str, _service_line = _resolve_service_line(struct_raw, cfg, settings)
        mat_mil    = (person.matricule_militaire or "/").strip()
        mat_sol    = format_matricule_solde_display(person.matricule_solde, empty="/")
        date_words = (
            date_to_words_fr(result.limit_date)
            if result.limit_date
            else "Date indéterminée (grade non configuré)"
        )

        # ── Variable paragraphs ──────────────────────────────────────────────
        # P12: Atteste que le : [grade noms] — keep leading space (maps to bold Run4 " ")
        _replace_attestation_value(template_fields["person"], f"{grade_str} {noms_str}".strip() or "/")

        # P13: En service\xa0: [au/à la/à l' structure]
        _replace_attestation_value(template_fields["service"], _service_line if struct_str else "/")

        # P14: Date de naissance\xa0: [date]
        _replace_attestation_value(template_fields["birth"], dn.strftime('%d/%m/%Y') if dn else '/')

        # P15: Matricule militaire\xa0: [mat_mil]
        _replace_attestation_value(template_fields["military"], mat_mil)

        # P16: Matricule solde\xa0: [mat_sol]
        _replace_attestation_value(template_fields["pay"], mat_sol)

        # P17: Entrée au service\xa0: [date service]
        _replace_attestation_value(template_fields["entry"], ds.strftime('%d/%m/%Y') if ds else '/')

        # P20: limit date (all bold runs)
        _set_bold_value(template_fields["limit"], date_words)

        # ── Nom du signataire — zone de texte P27 ────────────────────────────
        for box in template_fields["signature"]._element.xpath('.//w:txbxContent'):
            texts = list(box.iter(qn('w:t')))
            for index,text in enumerate(texts):
                text.text = opts.signataire_nom if index == 0 else ""

        # ── Code-barres — image PNG ancrée en remplacement de la zone de texte
        bc_data = _barcode_data(person)
        bc_png  = _generate_barcode_png(bc_data)
        if bc_png:
            if template_fields["barcode"] is None or not _embed_barcode_image(doc, template_fields["barcode"], bc_png):
                raise ValueError("Modèle d’attestation incompatible : le code-barres ne peut pas être placé.")

        dest_path.parent.mkdir(parents=True, exist_ok=True)
        with atomic_destination(dest_path, temporary_suffix=".docx") as staged:
            doc.save(str(staged))

        from sme_gend.services.doc_gen_log import log_doc_generation
        if log_generation:
            log_doc_generation(
                settings=settings, doc_type="attestation", format="docx",
                persons=[person.noms or "?"], dest_path=str(dest_path),
            )
        return dest_path

    # ---- Génération HTML (pour PDF via QPrinter) ---------------------------

    def generate_html(
        self,
        person: ListeGeneraleRow,
        cfg: LimiteAgeConfig,
        sign_date: date | None = None,
        settings: "Settings | None" = None,
    ) -> str:
        result = self.calculate_limit(person, cfg)
        if result.limit_date is None:
            raise ValueError(
                "Attestation impossible. Configurez la limite du grade et vérifiez les dates. " + " ".join(result.issues)
            )
        opts = cfg.options

        dn = _to_date(person.date_naissance)
        ds = _to_date(person.date_service)
        sign_date_str = ""
        date_words = date_to_words_fr(result.limit_date)
        val_str = (
            "trois (03)" if opts.validite_mois == 3
            else f"{_int_to_fr(opts.validite_mois)} ({opts.validite_mois:02d})"
        )
        n_lois = len(opts.lois_refs)
        lois_lines = []
        for i, loi in enumerate(opts.lois_refs):
            txt = loi.rstrip(";").rstrip(".").rstrip()
            term = "." if i == n_lois - 1 else "&nbsp;;"
            if i == 0:
                lois_lines.append(
                    f"<span style='font-style:italic'>     - {txt}{term}</span>"
                )
            else:
                lois_lines.append(
                    f"<p style='margin:0 0 2px 0;font-style:italic'>"
                    f"&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;"
                    f"&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;"
                    f"&nbsp;&nbsp;&nbsp;&nbsp; - {txt}{term}</p>"
                )

        # Code-barres en base64 pour l'HTML
        bc_data = _barcode_data(person)
        bc_png  = _generate_barcode_png(bc_data)
        if bc_png:
            import base64 as _b64
            import io as _io2
            from PIL import Image as _PILImg
            _im = _PILImg.open(_io2.BytesIO(bc_png))
            _bw, _bh = _im.size
            _disp_w, _disp_h = 220, int(220 * _bh / _bw) if _bw else 80
            bc_b64  = _b64.b64encode(bc_png).decode()
            bc_html = (
                f"<center>"
                f"<img src='data:image/png;base64,{bc_b64}'"
                f" width='{_disp_w}' height='{_disp_h}'/>"
                f"</center>"
            )
        else:
            bc_html = f"<center><small>{bc_data}</small></center>"

        grade_str  = self._fmt_grade(person.grade  or "")
        noms_str   = format_person_name(person, cfg)
        struct_raw = (person.structure or "").strip()
        _prep, struct_str, service_line = _resolve_service_line(struct_raw, cfg, settings)

        lois_html = "".join(lois_lines)
        mat_mil   = (person.matricule_militaire or "/").strip()
        mat_sol   = format_matricule_solde_display(person.matricule_solde, empty="/")
        dn_str    = dn.strftime("%d/%m/%Y") if dn else "/"
        ds_str    = ds.strftime("%d/%m/%Y") if ds else "/"

        return f"""<!DOCTYPE html>
<html><head><meta charset='utf-8'/>
<style>
  body  {{ font-family:'Times New Roman',serif; font-size:12pt;
           margin:0; padding:0; }}
  .page {{ padding:18mm 22mm 14mm 22mm; }}

  /* ── En-tête ─────────────────────────────────────── */
  table.hdr {{ width:100%; border-collapse:collapse; margin-bottom:0; }}
  td.hleft  {{ width:46%; vertical-align:top; text-align:center;
               font-size:9pt; line-height:1.45; }}
  td.hmid   {{ width:8%; }}
  td.hright {{ width:46%; vertical-align:top; text-align:center;
               font-size:9pt; line-height:1.45; }}

  /* ── Titre ───────────────────────────────────────── */
  p.t1 {{ text-align:center; font-weight:bold; font-size:14pt;
          margin:10px 0 0 0; letter-spacing:1px; }}
  p.t2 {{ text-align:center; font-weight:bold; font-size:14pt;
          text-decoration:underline; margin:0 0 10px 0;
          letter-spacing:1px; }}

  /* ── Corps ───────────────────────────────────────── */
  p.ref  {{ font-size:11pt; margin:4px 0; }}
  p.body {{ font-size:11pt; margin:3px 0; line-height:1.5; }}
  span.v {{ font-weight:bold; }}
  span.rl{{ font-weight:bold; font-style:italic;
            text-decoration:underline; }}

  /* ── Date limite ─────────────────────────────────── */
  p.limlbl  {{ text-align:center; font-size:11pt; margin:14px 0 0 0; }}
  p.limdate {{ text-align:center; font-weight:bold;
               text-decoration:underline; font-size:13pt;
               margin:4px 0 14px 0; }}

  /* ── Clôture ─────────────────────────────────────── */
  p.close {{ text-align:justify; font-size:11pt; margin:4px 0 20px 0; }}
  p.sigr  {{ text-align:right; font-size:11pt; margin:2px 0; }}
  p.sign  {{ text-align:right; font-weight:bold; font-size:11pt;
             margin:28px 0 0 0; }}
</style></head>
<body><div class='page'>

<table class='hdr'><tr>
  <td class='hleft'>
    <b>MINISTERE DE L'INTERIEUR<br>ET DE LA DECENTRALISATION</b><br>
    -=-=-=-=-<br>
    GENDARMERIE NATIONALE<br>
    -=-=-=-=-<br>
    <b>DIRECTION DU PERSONNEL<br>ET DE LA FORMATION</b><br>
    -=-=-=-=-=<br>
    DIVISION GESTION DU PERSONNEL<br><br>
    N°&nbsp;___________/MID/GN/DPF/DGP.-<br>
    {bc_html}
  </td>
  <td class='hmid'></td>
  <td class='hright'>
    REPUBLIQUE DU CONGO<br>
    <i>Unité &ndash; Travail &ndash; Progrès</i><br>
    -=-=-=-=-=-<br><br><br><br>
    {opts.ville}, le {sign_date_str}
  </td>
</tr></table>

<p class='t1'>ATTESTATION DE LIMITE</p>
<p class='t2'>D&rsquo;&#194;GE ET DE SERVICE</p>

<p class='ref'><span class='rl'>Références</span>&nbsp;:
  {lois_html}</p>

<p class='body'>{opts.signataire_titre}, soussigné,</p>

<p class='body'>Atteste que le&nbsp;:
  &nbsp;&nbsp;&nbsp;<span class='v'>{grade_str} {noms_str}</span></p>

<p class='body'>En service&nbsp;:
  &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;<span class='v'>{service_line}</span></p>

<p class='body'>Date de naissance&nbsp;:
  &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;<span class='v'>{dn_str}</span></p>

<p class='body'>Matricule militaire&nbsp;:
  &nbsp;&nbsp;&nbsp;&nbsp;<span class='v'>{mat_mil}</span></p>

<p class='body'>Matricule solde&nbsp;:
  &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;<span class='v'>{mat_sol}</span></p>

<p class='body'>Entrée au service&nbsp;:
  &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;<span class='v'>{ds_str}</span></p>

<p class='limlbl'>Atteindra la limite d&rsquo;&acirc;ge de son grade actuel
  ou de service le&nbsp;:</p>
<p class='limdate'>{date_words}</p>

<p class='close'>En foi de quoi, la présente attestation, valable pour une durée
  de {val_str} mois à compter de la date de signature, lui est délivrée pour servir
  et valoir ce que de droit.&nbsp;/-</p>

<p class='sigr'>{opts.signataire_titre},</p>
<p class='sign'>{opts.signataire_nom}</p>

</div></body></html>"""

    # ---- Génération d'un document Word fusionné (plusieurs personnes) --------

    def generate_docx_batch(
        self,
        persons: list,
        cfg: LimiteAgeConfig,
        dest_path: Path,
        sign_date: date | None = None,
        grade_code_map: dict[str, str] | None = None,
        grades: list | None = None,
        settings: "Settings | None" = None,
    ) -> Path:
        """Génère un seul .docx avec une attestation par page pour chaque personne."""
        from docx import Document
        from docx.oxml.ns import qn
        from docx.oxml import OxmlElement
        from docx.opc.part import Part
        from docx.opc.packuri import PackURI
        import copy, tempfile

        if not persons:
            raise ValueError("Aucune personne fournie")

        from sme_gend.services.soit_transmis_service import sort_persons_by_grade
        persons = sort_persons_by_grade(
            persons, grade_code_map=grade_code_map, grades=grades
        )

        R_IMG = ("http://schemas.openxmlformats.org/officeDocument/"
                 "2006/relationships/image")
        img_counter = [0]

        def _copy_images(src_doc, dst_doc) -> dict:
            rid_map: dict[str, str] = {}
            for src_rid, src_rel in src_doc.part.rels.items():
                if src_rel.reltype != R_IMG:
                    continue
                img_counter[0] += 1
                sp = src_rel.target_part
                new_pn  = PackURI(f"/word/media/m{img_counter[0]}.png")
                new_part = Part(new_pn, sp.content_type, sp.blob,
                                dst_doc.part.package)
                rid_map[src_rid] = dst_doc.part.relate_to(new_part, R_IMG)
            return rid_map

        def _patch_rids(elem, rid_map: dict) -> None:
            R = ("http://schemas.openxmlformats.org/officeDocument/"
                 "2006/relationships")
            for node in elem.iter():
                for attr in (f"{{{R}}}embed", f"{{{R}}}link"):
                    v = node.get(attr)
                    if v and v in rid_map:
                        node.set(attr, rid_map[v])

        def _page_break_el():
            pg = OxmlElement("w:p")
            r  = OxmlElement("w:r")
            br = OxmlElement("w:br")
            br.set(qn("w:type"), "page")
            r.append(br); pg.append(r)
            return pg

        # Génère chaque personne dans un fichier temp, puis fusionne
        tmp_paths: list[Path] = []
        try:
            for person in persons:
                import os
                fd, name = tempfile.mkstemp(suffix=".docx")
                os.close(fd)
                tmp = Path(name)
                tmp_paths.append(tmp)
                self.generate_docx(person, cfg, tmp, sign_date, settings, log_generation=False)

            merged = Document(str(tmp_paths[0]))

            for src_path in tmp_paths[1:]:
                src = Document(str(src_path))
                rid_map = _copy_images(src, merged)

                body   = merged.element.body
                sect   = body.find(qn("w:sectPr"))
                pg_el  = _page_break_el()

                def _insert(el):
                    if sect is not None:
                        sect.addprevious(el)
                    else:
                        body.append(el)

                _insert(pg_el)
                for elem in src.element.body:
                    if elem.tag == qn("w:sectPr"):
                        continue
                    ec = copy.deepcopy(elem)
                    _patch_rids(ec, rid_map)
                    _insert(ec)

        finally:
            for p in tmp_paths:
                try:
                    p.unlink()
                except OSError:
                    pass

        dest_path.parent.mkdir(parents=True, exist_ok=True)
        with atomic_destination(dest_path, temporary_suffix=".docx") as staged:
            merged.save(str(staged))

        from sme_gend.services.doc_gen_log import log_doc_generation
        log_doc_generation(
            settings=settings, doc_type="attestation_batch", format="docx",
            persons=[p.noms or "?" for p in list(persons)],
            dest_path=str(dest_path), count=len(persons),
        )
        return dest_path

    # ---- Export PDF ---------------------------------------------------------

    def generate_pdf(
        self,
        person: ListeGeneraleRow,
        cfg: LimiteAgeConfig,
        dest_path: Path,
        sign_date: date | None = None,
        settings: "Settings | None" = None,
    ) -> Path:
        """Génère un PDF via LibreOffice si disponible, sinon via QPrinter (HTML)."""
        import shutil, subprocess, tempfile

        soffice = shutil.which("soffice") or shutil.which("libreoffice")
        if soffice:
            try:
                with tempfile.TemporaryDirectory(prefix="sgi-dpf-pdf-") as directory:
                    tmp_docx = Path(directory) / "attestation.docx"
                    self.generate_docx(person, cfg, tmp_docx, sign_date, settings, log_generation=False)
                    subprocess.run(
                        [soffice, "--headless", "--convert-to", "pdf",
                         str(tmp_docx), "--outdir", str(tmp_docx.parent)],
                        check=True, capture_output=True, timeout=90,
                    )
                    from sme_gend.services.safe_paths import atomic_copy
                    atomic_copy(tmp_docx.with_suffix(".pdf"), dest_path)
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
                import logging
                logging.getLogger(__name__).warning("Conversion LibreOffice indisponible ; repli PDF Qt.", exc_info=True)
            else:
                from sme_gend.services.doc_gen_log import log_doc_generation
                log_doc_generation(settings=settings, doc_type="attestation", format="pdf", persons=[person.noms or "?"], dest_path=str(dest_path))
                return dest_path

        # Repli : QPrinter via HTML
        from PySide6.QtGui import QTextDocument, QPageLayout, QPageSize
        from PySide6.QtPrintSupport import QPrinter
        from PySide6.QtCore import QMarginsF

        html = self.generate_html(person, cfg, sign_date)
        dest_path.parent.mkdir(parents=True, exist_ok=True)

        def configure(document, printer):
            printer.setPageLayout(QPageLayout(
                QPageSize(QPageSize.PageSizeId.A4),
                QPageLayout.Orientation.Portrait,
                QMarginsF(20, 20, 20, 20),
            ))
        doc = QTextDocument()
        doc.setHtml(html)
        from sme_gend.services.document_pdf_output import save_document_pdf
        save_document_pdf(doc, dest_path, configure=configure, doc_type="attestation",
            count=1, persons=[person.noms or "?"], settings=settings)
        return dest_path
