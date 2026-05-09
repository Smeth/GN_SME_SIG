"""Service — calcul des limites d'âge/service et génération des attestations."""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sme_gend.config.settings import Settings
    from sme_gend.models.liste_generale import ListeGeneraleRow

from sme_gend.models.limite_age_config import (
    GradeLimiteEntry,
    LimiteAgeConfig,
    LimiteAgeDocOptions,
    _DEFAULT_GRADES,
)


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
    "DEPARTEMENT", "GROUPE", "ETAT", "DISTRICT", "ARRONDISSEMENT",
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
    key_words = _grade_key(struct).split()
    for w in key_words:
        if w in _STRUCT_SKIP:
            continue
        # Commence par une voyelle → à l'
        if w and w[0] in "AEIOU":
            return "à l'"
        # Nom masculin connu → au
        if w in _STRUCT_MASC:
            return "au"
        # Premier mot non ignoré, non masculin → à la
        break
    return "à la"


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


# ---------------------------------------------------------------------------
# Génération du code-barres Code 128
# ---------------------------------------------------------------------------

def _barcode_data(person) -> str:
    """Construit la chaîne encodée dans le code-barres : noms, structure, âge."""
    from datetime import date as _date_cls
    noms   = _grade_key(person.noms      or "")[:40]
    struct = _grade_key(person.structure or "")[:30]
    dn     = _to_date(person.date_naissance)
    if dn:
        today = _date_cls.today()
        years = today.year - dn.year - (
            (today.month, today.day) < (dn.month, dn.day)
        )
        age_str = f"{years} ANS"
    else:
        age_str = "?"
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

_DEFAULT_LOOKUP: dict[str, tuple[int, int]] = {
    _grade_key(d["grade"]): (d["limite_age"], d["limite_service"])
    for d in _DEFAULT_GRADES
}


def _find_default_limits(grade_str: str) -> tuple[int, int]:
    """Cherche dans _DEFAULT_GRADES une entrée correspondant au grade donné."""
    g = _grade_key(grade_str)
    if g in _DEFAULT_LOOKUP:
        return _DEFAULT_LOOKUP[g]
    # Correspondance partielle
    for key, val in _DEFAULT_LOOKUP.items():
        if g in key or key in g:
            return val
    # Heuristiques sur mots-clés (sans accents)
    keywords_officers = {"GENERAL", "COLONEL", "COMMANDANT", "LIEUTENANT", "CAPITAINE", "ASPIRANT"}
    keywords_senior_nco = {"ADJUDANT"}
    keywords_mid_nco = {"MARECHAL", "LOGIS", "SERGENT"}
    for kw in keywords_officers:
        if kw in g:
            return (62, 40)
    for kw in keywords_senior_nco:
        if kw in g:
            return (58, 40)
    for kw in keywords_mid_nco:
        if kw in g:
            return (56, 38)
    return (55, 35)


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


class LimiteAgeService:
    def __init__(self) -> None:
        pass

    # ---- Config I/O --------------------------------------------------------

    def load(self) -> LimiteAgeConfig:
        p = limite_age_config_path()
        if not p.is_file():
            cfg = LimiteAgeConfig()
            self.save(cfg)
            return cfg
        try:
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
            return LimiteAgeConfig.from_dict(data) if isinstance(data, dict) else LimiteAgeConfig()
        except Exception:
            return LimiteAgeConfig()

    def save(self, cfg: LimiteAgeConfig) -> None:
        p = limite_age_config_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(cfg.to_dict(), f, ensure_ascii=False, indent=2)
            f.write("\n")

    # ---- Synchronisation des grades depuis la base de données --------------

    def sync_grades_from_referentiel(
        self, settings: Settings
    ) -> tuple[LimiteAgeConfig, list[str]]:
        """
        Lit les grades depuis TabGrade (Access ou SQLite), ajoute ceux absents de la
        config avec des valeurs par défaut estimées. Sauvegarde si des ajouts ont été faits.
        Retourne (cfg mis à jour, liste des libellés nouvellement ajoutés).
        """
        from sme_gend.repositories.grade_repository import GradeRepository

        cfg = self.load()
        known = {_grade_key(e.grade): e for e in cfg.grades}

        try:
            db_grades = GradeRepository(settings).fetch_all()
        except Exception:
            return cfg, []

        added: list[str] = []
        for gr in sorted(db_grades, key=lambda r: (r.pref_ord or 999, r.libelle)):
            libelle = (gr.libelle or "").strip()
            if not libelle:
                continue
            if _grade_key(libelle) not in known:
                la, ls = _find_default_limits(libelle)
                entry = GradeLimiteEntry(grade=libelle, limite_age=la, limite_service=ls)
                cfg.grades.append(entry)
                known[libelle.upper()] = entry
                added.append(libelle)

        if added:
            self.save(cfg)
        return cfg, added

    # ---- Grade lookup ------------------------------------------------------

    def find_grade_entry(
        self, grade_str: str | None, cfg: LimiteAgeConfig
    ) -> GradeLimiteEntry | None:
        if not grade_str:
            return None
        grade_norm = _grade_key(grade_str)
        # Correspondance exacte (insensible aux accents et à la casse)
        for e in cfg.grades:
            if _grade_key(e.grade) == grade_norm:
                return e
        # Correspondance partielle
        for e in cfg.grades:
            eg = _grade_key(e.grade)
            if grade_norm in eg or eg in grade_norm:
                return e
        return None

    # ---- Date de limite ----------------------------------------------------

    def calculate_limit(
        self, person: ListeGeneraleRow, cfg: LimiteAgeConfig
    ) -> LimiteDateResult:
        entry = self.find_grade_entry(person.grade, cfg)

        age_date: date | None = None
        svc_date: date | None = None

        dn = _to_date(person.date_naissance)
        ds = _to_date(person.date_service)

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

    # ---- Formatage des noms pour les documents --------------------------------

    @staticmethod
    def _fmt_grade(grade: str) -> str:
        """Première lettre en majuscule, reste en minuscule (ex: ADJUDANT → Adjudant)."""
        g = grade.strip()
        return (g[0].upper() + g[1:].lower()) if g else ""

    @staticmethod
    def _fmt_noms(noms: str) -> str:
        """Nom de famille en MAJUSCULES, prénoms en minuscules (ex: DZONDAULT GHISLAIN → DZONDAULT ghislain)."""
        parts = noms.strip().split()
        if not parts:
            return ""
        surname = parts[0].upper()
        given   = " ".join(p.lower() for p in parts[1:])
        return f"{surname} {given}".strip()

    def generate_docx(
        self,
        person: ListeGeneraleRow,
        cfg: LimiteAgeConfig,
        dest_path: Path,
        sign_date: date | None = None,
    ) -> Path:
        from docx import Document
        from docx.oxml.ns import qn
        from sme_gend.config.settings import project_root

        result = self.calculate_limit(person, cfg)

        template_path = project_root() / "limite_age.docx"
        if not template_path.is_file():
            raise FileNotFoundError(f"Modèle introuvable : {template_path}")

        doc   = Document(str(template_path))
        paras = doc.paragraphs

        def _set_bold_value(para, new_text: str) -> None:
            """Replace the bold (value) runs in a paragraph with a single run."""
            from docx.oxml.ns import qn as _qn
            # Cherche les runs avec <w:b/> explicite dans le XML (plus fiable que .bold)
            bold_runs = []
            for r in para.runs:
                rpr = r._element.find(_qn("w:rPr"))
                if rpr is not None and rpr.find(_qn("w:b")) is not None:
                    bold_runs.append(r)
            # Repli python-docx standard si aucun run explicitement bold trouvé
            if not bold_runs:
                bold_runs = [r for r in para.runs if r.bold]
            # Dernier recours : dernier run du paragraphe
            if not bold_runs and para.runs:
                bold_runs = para.runs[-1:]
            if bold_runs:
                bold_runs[0].text = new_text
                for r in bold_runs[1:]:
                    r.text = ""

        # ── Computed values ──────────────────────────────────────────────────
        opts       = cfg.options
        dn         = _to_date(person.date_naissance)
        ds         = _to_date(person.date_service)
        sign_str   = sign_date.strftime("%d/%m/%Y") if sign_date else "___________"
        grade_str  = self._fmt_grade(person.grade      or "")
        noms_str   = self._fmt_noms(person.noms        or "")
        struct_raw = (person.structure  or "").strip()
        struct_str = struct_raw.lower()
        prep_str   = _prep_structure(struct_raw)
        mat_mil    = (person.matricule_militaire or "/").strip()
        mat_sol    = (person.matricule_solde     or "/").strip()
        date_words = (
            date_to_words_fr(result.limit_date)
            if result.limit_date
            else "Date indéterminée (grade non configuré)"
        )

        # ── Variable paragraphs ──────────────────────────────────────────────
        # P12: Atteste que le : [grade noms] — keep leading space (maps to bold Run4 " ")
        _set_bold_value(paras[12], f" {grade_str} {noms_str}".rstrip() or " /")

        # P13: En service\xa0: [au/à la/à l' structure] — structure en minuscules
        _set_bold_value(paras[13], f" {prep_str} {struct_str}" if struct_str else " /")

        # P14: Date de naissance\xa0: [date]
        _set_bold_value(paras[14], dn.strftime('%d/%m/%Y') if dn else '/')

        # P15: Matricule militaire\xa0: [mat_mil]
        _set_bold_value(paras[15], f"    {mat_mil}")

        # P16: Matricule solde\xa0: [mat_sol]
        _set_bold_value(paras[16], f" {mat_sol}")

        # P17: Entrée au service\xa0: [date service]
        _set_bold_value(paras[17], ds.strftime('%d/%m/%Y') if ds else '/')

        # P20: limit date (all bold runs)
        _set_bold_value(paras[20], date_words)

        # ── Nom du signataire — zone de texte P27 ────────────────────────────
        p27_el = paras[27]._element
        txbx27 = p27_el.find('.//' + qn('w:txbxContent'))
        if txbx27 is not None:
            inner27 = txbx27.find(qn('w:p'))
            if inner27 is not None:
                for r_el in inner27.findall(qn('w:r')):
                    t_el = r_el.find(qn('w:t'))
                    if t_el is not None:
                        t_el.text = opts.signataire_nom
                        break

        # ── Code-barres — image PNG ancrée en remplacement de la zone de texte
        bc_data = _barcode_data(person)
        bc_png  = _generate_barcode_png(bc_data)
        if bc_png:
            _embed_barcode_image(doc, paras[0], bc_png)

        dest_path.parent.mkdir(parents=True, exist_ok=True)
        doc.save(str(dest_path))

        from sme_gend.services.doc_gen_log import log_doc_generation
        log_doc_generation(
            doc_type="attestation", format="docx",
            persons=[person.noms or "?"], dest_path=str(dest_path),
        )
        return dest_path

    # ---- Génération HTML (pour PDF via QPrinter) ---------------------------

    def generate_html(
        self,
        person: ListeGeneraleRow,
        cfg: LimiteAgeConfig,
        sign_date: date | None = None,
    ) -> str:
        result = self.calculate_limit(person, cfg)
        opts = cfg.options

        dn = _to_date(person.date_naissance)
        ds = _to_date(person.date_service)
        sign_date_str = sign_date.strftime("%d/%m/%Y") if sign_date else "___________"
        date_words = (
            date_to_words_fr(result.limit_date)
            if result.limit_date
            else "Date indéterminée (grade non configuré)"
        )
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
        noms_str   = self._fmt_noms(person.noms   or "")
        struct_raw = (person.structure or "").strip()
        struct_str = struct_raw.lower()
        prep_str   = _prep_structure(struct_raw)

        lois_html = "".join(lois_lines)
        mat_mil   = (person.matricule_militaire or "/").strip()
        mat_sol   = (person.matricule_solde     or "/").strip()
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
  &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;<span class='v'>{prep_str} {struct_str}</span></p>

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
                tmp = Path(tempfile.mktemp(suffix=".docx"))
                self.generate_docx(person, cfg, tmp, sign_date)
                tmp_paths.append(tmp)

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
        merged.save(str(dest_path))

        from sme_gend.services.doc_gen_log import log_doc_generation
        log_doc_generation(
            doc_type="attestation_batch", format="docx",
            persons=[p.noms or "?" for p in persons[:20]],
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
    ) -> Path:
        """Génère un PDF via LibreOffice si disponible, sinon via QPrinter (HTML)."""
        import shutil, subprocess, tempfile

        soffice = shutil.which("soffice") or shutil.which("libreoffice")
        if soffice:
            tmp_docx = Path(tempfile.mktemp(suffix=".docx"))
            try:
                self.generate_docx(person, cfg, tmp_docx, sign_date)
                subprocess.run(
                    [soffice, "--headless", "--convert-to", "pdf",
                     str(tmp_docx), "--outdir", str(tmp_docx.parent)],
                    check=True, capture_output=True, timeout=90,
                )
                generated = tmp_docx.with_suffix(".pdf")
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                generated.replace(dest_path)
                return dest_path
            except Exception:
                pass
            finally:
                if tmp_docx.exists():
                    tmp_docx.unlink(missing_ok=True)

        # Repli : QPrinter via HTML
        from PySide6.QtGui import QTextDocument, QPageLayout, QPageSize
        from PySide6.QtPrintSupport import QPrinter
        from PySide6.QtCore import QMarginsF

        html = self.generate_html(person, cfg, sign_date)
        dest_path.parent.mkdir(parents=True, exist_ok=True)

        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
        printer.setOutputFileName(str(dest_path))
        printer.setPageLayout(QPageLayout(
            QPageSize(QPageSize.PageSizeId.A4),
            QPageLayout.Orientation.Portrait,
            QMarginsF(20, 20, 20, 20),
        ))

        doc = QTextDocument()
        doc.setHtml(html)
        doc.print_(printer)

        from sme_gend.services.doc_gen_log import log_doc_generation
        log_doc_generation(
            doc_type="attestation", format="pdf",
            persons=[person.noms or "?"], dest_path=str(dest_path),
        )
        return dest_path
