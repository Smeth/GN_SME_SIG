"""Accès aux référentiels (structures, subdivisions, grades, fonctions, positions admin)."""

from __future__ import annotations
from functools import wraps
from sme_gend.services.action_permissions import requires_any_permission


def _atomic_reference(method):
    @wraps(method)
    def write(self,*args,**kwargs):
        from sme_gend.services.shared_reference_write import shared_reference_write
        with shared_reference_write(self._settings):
            return method(self,*args,**kwargs)
    return write

from sme_gend.config.settings import Settings
from sme_gend.models.distinction_ref import DistinctionRefRow
from sme_gend.models.fonction_ref import FonctionRefRow
from sme_gend.models.grade_ref import GradeRefRow
from sme_gend.models.padmin_ref import PadminRefRow
from sme_gend.repositories.mysql_distinction_repository import MySQLDistinctionRepository
from sme_gend.models.structure import StructureRow
from sme_gend.repositories.fonctions_repository import FonctionsRepository
from sme_gend.repositories.grade_repository import GradeRepository
from sme_gend.repositories.liste_generale_repository import ListeGeneraleRepository
from sme_gend.repositories.structure_repository import StructureRepository
from sme_gend.repositories.structure_subdivision_repository import StructureSubdivisionRepository
from sme_gend.repositories.subdivision_repository import SubdivisionFlatRow, SubdivisionRepository


def _make_padmin_repo(settings: Settings):
    from sme_gend.repositories.mysql_bundle_repositories import MySQLPadminRepository
    try:
        return MySQLPadminRepository(settings)
    except Exception:
        return None


class ReferentielService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._structures = StructureRepository(settings)
        self._subdivisions = SubdivisionRepository(settings)
        self._struct_sub = StructureSubdivisionRepository(settings)
        self._liste_generale = ListeGeneraleRepository(settings)
        self._grades = GradeRepository(settings)
        self._fonctions = FonctionsRepository(settings)
        self._padmin = _make_padmin_repo(settings)
        self._distinctions = MySQLDistinctionRepository(settings)

    @property
    def settings(self) -> Settings:
        return self._settings

    def rebind_settings(self, settings: Settings) -> None:
        self._settings = settings
        self._structures = StructureRepository(settings)
        self._subdivisions = SubdivisionRepository(settings)
        self._struct_sub = StructureSubdivisionRepository(settings)
        self._liste_generale = ListeGeneraleRepository(settings)
        self._grades = GradeRepository(settings)
        self._fonctions = FonctionsRepository(settings)
        self._padmin = _make_padmin_repo(settings)
        self._distinctions = MySQLDistinctionRepository(settings)

    def structures(self) -> list[StructureRow]:
        return self._structures.fetch_all()

    def subdivisions_flat(self) -> list[SubdivisionFlatRow]:
        return self._subdivisions.fetch_flat_hierarchy()

    def grades(self) -> list[GradeRefRow]:
        return self._grades.fetch_all()

    def grade_usage_counts(self):
        from sme_gend.repositories.mysql_organigram_repository import MySQLOrganigramRepository
        return MySQLOrganigramRepository(self._settings).grade_usage_counts()

    def fonctions_distinctes(self) -> list[str]:
        return self._fonctions.fetch_distinct()

    def fonctions_all(self) -> list[FonctionRefRow]:
        return self._fonctions.fetch_all()

    @requires_any_permission('referentiels.fonctions')
    def fonction_insert(self, libelle: str) -> int:
        lb = (libelle or "").strip()
        if not lb:
            raise ValueError("Le libellé de fonction est obligatoire.")
        for row in self._fonctions.fetch_all():
            if row.libelle.casefold() == lb.casefold():
                raise ValueError(f"La fonction « {row.libelle} » existe déjà.")
        return self._fonctions.insert(lb)

    @requires_any_permission('referentiels.fonctions')
    def fonction_update(self, fonction_id: int, old_libelle: str, new_libelle: str, *, expected=None) -> None:
        new = (new_libelle or "").strip()
        if not new:
            raise ValueError("Le libellé de fonction est obligatoire.")
        if new.casefold() != (old_libelle or "").strip().casefold():
            for row in self._fonctions.fetch_all():
                if row.id != int(fonction_id) and row.libelle.casefold() == new.casefold():
                    raise ValueError(f"La fonction « {row.libelle} » existe déjà.")
        self._fonctions.update(fonction_id, old_libelle, new, expected=expected)

    @requires_any_permission('referentiels.fonctions')
    def fonction_delete(self, fonction_id: int, *, expected=None) -> None:
        from sme_gend.services.fonction_priority_service import prune_fonction_from_priority
        from sme_gend.services.shared_reference_write import shared_reference_write
        with shared_reference_write(self._settings):
            self._fonctions.delete(fonction_id, expected=expected)
            prune_fonction_from_priority(self._settings, fonction_id)

    def fonction_rename_impact(self, row):
        return self._fonctions.rename_impact(row.libelle,row.id)

    @requires_any_permission('referentiels.fonctions')
    @_atomic_reference
    def fonction_rename_confirmed(self,row,new_libelle,impact):
        from sme_gend.repositories.reference_write_guard import ReferenceConflictError
        if tuple(impact) != tuple(self.fonction_rename_impact(row)):
            raise ReferenceConflictError("Le périmètre du renommage a changé. Rechargez et vérifiez à nouveau son impact.")
        self.fonction_update(row.id,row.libelle,new_libelle,expected=row)

    def fonction_count_usage(self, libelle: str, fonction_id: int | None = None) -> int:
        return self._fonctions.count_usage(libelle, fonction_id)

    def fonction_usage_counts(self) -> dict[int, int]:
        return self._fonctions.fetch_usage_counts()

    def fonction_counts_by_structure(self) -> dict[str, int]:
        """Rattachements explicites uniquement, sans repli sur la carrière."""
        counts = {}
        for codes in self._fonctions.links_summary().values():
            for code in {c.strip().casefold() for c in codes if c.strip()}:
                counts[code] = counts.get(code, 0) + 1
        return counts

    def fonction_linked_choices_for_structure(self, structure_code: str) -> list[str]:
        return self._fonctions.fetch_linked_for_structure(structure_code)

    def fonction_linked_labels_by_structure(self):
        return self._fonctions.fetch_linked_labels_by_structure()

    def fonction_choices_for_structure(self, structure_code: str) -> list[str]:
        """Fonctions autorisées pour une structure (ref_fonction_structure)."""
        code = (structure_code or "").strip()
        if not code:
            return []
        return self._fonctions.fetch_for_structure(code)

    def fonction_structure_codes(self, fonction_id: int) -> list[str]:
        return self._fonctions.structure_codes_for(fonction_id)

    def fonction_links_snapshot(self,fonction_id):
        return self._fonctions.links_snapshot_for_fonction(fonction_id)

    def structure_links_snapshot(self,code):
        return self._fonctions.links_snapshot_for_structure(code)

    def fonction_links_snapshot_all(self):
        return self._fonctions.links_snapshot_all()

    def validate_links_snapshot(self,expected):
        self._fonctions.validate_links_snapshot(expected)

    @requires_any_permission('structures.codes', 'referentiels.fonctions')
    def fonction_set_structure_links(self, fonction_id: int, structure_codes: list[str], *, expected=None) -> None:
        self._fonctions.set_structure_links(fonction_id, structure_codes, expected=expected)

    @requires_any_permission('referentiels.fonctions', 'structures.codes', 'effectifs.edition', 'effectifs.sme_dpf')
    def fonction_ensure_structure_link(self, structure_code: str, fonction_libelle: str) -> None:
        """Crée le lien manquant après saisie (si fonction existe dans ref_fonction)."""
        self._fonctions.ensure_structure_link(structure_code, fonction_libelle)

    @requires_any_permission('referentiels.fonctions', 'structures.codes', 'effectifs.edition', 'effectifs.sme_dpf')
    @_atomic_reference
    def fonction_ensure_catalog_and_link(self, structure_code: str, fonction_libelle: str) -> int:
        """Crée la fonction au catalogue si besoin, puis rattache la structure."""
        sc = (structure_code or "").strip()
        raw = (fonction_libelle or "").strip()
        if not sc or not raw:
            raise ValueError("Structure et libellé de fonction requis.")
        canonical = raw
        fonction_id: int | None = None
        for row in self._fonctions.fetch_all():
            if row.libelle.casefold() == raw.casefold():
                fonction_id = row.id
                canonical = row.libelle
                break
        if fonction_id is None:
            fonction_id = self._fonctions.insert(raw)
            canonical = raw
        self._fonctions.ensure_structure_link(sc, canonical)
        from sme_gend.services.postes_tete_exclusions import clear_expected_poste

        clear_expected_poste(self._settings, sc, canonical)
        return int(fonction_id)

    @requires_any_permission('referentiels.fonctions')
    def fonction_merge_structure_links(self, fonction_id: int, structure_codes: list[str]) -> None:
        """Atomic additive intent, preserving other clients' links and order."""
        from sme_gend.services.shared_reference_write import shared_reference_write
        with shared_reference_write(self._settings):
            self._fonctions.merge_structure_links(fonction_id,structure_codes)
            merged=self.fonction_structure_codes(fonction_id)
            libelle=next((row.libelle for row in self._fonctions.fetch_all() if row.id==int(fonction_id)),"")
            if libelle:
                from sme_gend.services.postes_tete_exclusions import clear_expected_poste
                for code in merged:clear_expected_poste(self._settings,code,libelle)

    @requires_any_permission('referentiels.fonctions')
    def fonction_backfill_structure_links(self) -> int:
        return self._fonctions.backfill_structure_links_from_carriere()

    def fonction_observed_usage(self) -> list[tuple[str, str, int]]:
        """Libellés portés sur les fiches, par structure : (code, libellé, effectif)."""
        return self._fonctions.fetch_observed_usage()

    def fonction_links_summary(self) -> dict[int, list[str]]:
        return self._fonctions.links_summary()

    def structure_subdivision_pairs(self) -> list[tuple[str, str]]:
        """Couples (code structure, subdivision d'arme) présents dans ListeGenerale."""
        return self._struct_sub.fetch_distinct_pairs()

    def subdivisions_arme_for_structure(self, structure_code: str) -> list[str]:
        """Subdivisions d'arme rattachées à la structure (ref_subdivision_arme)."""
        return self._struct_sub.fetch_subdiv_labels_for_structure(structure_code)

    def subdivision_arme_choices_for_structure(self, structure_code: str) -> list[str]:
        """
        Choix SubArme pour une structure : RSA référentiel + libellés carrière du mois
        pour cette structure uniquement (pas la hiérarchie U→D→T globale).
        """
        seen: set[str] = set()
        out: list[str] = []

        def add(raw: str | None) -> None:
            x = (raw or "").strip()
            if not x:
                return
            k = x.casefold()
            if k in seen:
                return
            seen.add(k)
            out.append(x)

        for x in self.subdivisions_arme_for_structure(structure_code):
            add(x)
        for x in self._liste_generale.fetch_distinct_subarme_for_structure(structure_code):
            add(x)
        out.sort(key=str.casefold)
        return out

    def subdivision_arme_combo_choices(self) -> list[str]:
        """Libellés SubArme : hiérarchie référentiel + ref_subdivision_arme."""
        from_flat = [r.design_t for r in self._subdivisions.fetch_flat_hierarchy() if r.design_t]
        from_ref = self._struct_sub.fetch_distinct_subdiv_arme_labels()
        seen: set[str] = set()
        out: list[str] = []
        for x in from_flat + from_ref:
            x = (x or "").strip()
            if x and x not in seen:
                seen.add(x)
                out.append(x)
        out.sort(key=str.lower)
        return out

    def padmins(self) -> list[PadminRefRow]:
        """Positions administratives (ref_position_admin MySQL)."""
        if self._padmin:
            return self._padmin.fetch_all()
        from sme_gend.padmin_present import load_padmin_presence_index, padmin_counts_as_present

        idx = load_padmin_presence_index(self._settings)
        return [
            PadminRefRow(
                libelle=v,
                compte_comme_present=padmin_counts_as_present(
                    v, "present", index=idx, settings=self._settings
                ),
                ordre_tri=None,
            )
            for v in self._liste_generale.fetch_distinct_padmin()
        ]

    def position_admin_choices(self) -> list[str]:
        """Libellés position admin — ref_position_admin (MySQL) ; repli liste si MySQL indisponible."""
        if self._padmin:
            return [r.libelle for r in self._padmin.fetch_all()]
        return self._liste_generale.fetch_distinct_padmin()

    def padmin_insert(self, libelle: str, compte: bool, ordre: int | None) -> None:
        self._require_reference_access("padmin")
        if not self._padmin:
            raise ValueError("Le référentiel padmin nécessite MySQL (table ref_position_admin).")
        self._padmin.insert(libelle, compte, ordre)
        from sme_gend.padmin_present import invalidate_padmin_presence_cache

        invalidate_padmin_presence_cache(self._settings)

    def padmin_update(
        self, old_libelle: str, new_libelle: str, compte: bool, ordre: int | None, *, expected=None
    ) -> None:
        self._require_reference_access("padmin")
        if not self._padmin:
            raise ValueError("Le référentiel padmin nécessite MySQL (table ref_position_admin).")
        self._padmin.update(old_libelle, new_libelle, compte, ordre, expected=expected)
        from sme_gend.padmin_present import invalidate_padmin_presence_cache

        invalidate_padmin_presence_cache(self._settings)

    def padmin_delete(self, libelle: str, *, expected=None) -> None:
        self._require_reference_access("padmin")
        if not self._padmin:
            raise ValueError("Le référentiel padmin nécessite MySQL (table ref_position_admin).")
        self._padmin.delete(libelle, expected=expected)
        from sme_gend.padmin_present import invalidate_padmin_presence_cache

        invalidate_padmin_presence_cache(self._settings)

    def padmin_usage_counts(self):
        return self._padmin.usage_counts() if self._padmin else {}

    @staticmethod
    def _require_reference_access(kind):
        from sme_gend.services.auth_guard import session_has_permission
        if not session_has_permission(f"referentiels.{kind}"):
            raise PermissionError("Votre rôle ne permet pas de modifier ce référentiel global.")

    @requires_any_permission('structures.codes')
    def structure_insert(
        self,
        code: str,
        designation: str,
        ordre_tri: int | None,
        *,
        famille_unite: str | None = None,
    ) -> None:
        from sme_gend.services.structure_organigram_service import StructureOrganigramService

        StructureOrganigramService(self._settings).structure_insert(
            code, designation, ordre_tri, famille_unite=famille_unite
        )

    @requires_any_permission('structures.codes')
    def structure_update(
        self,
        code: str,
        designation: str,
        ordre_tri: int | None,
        *,
        famille_unite: str | None = None,
        expected=None,
    ) -> None:
        self._structures.update(
            code, designation, ordre_tri, famille_unite=famille_unite, expected=expected
        )

    @requires_any_permission('structures.codes')
    def structure_bulk_set_famille(
        self, structure_codes: list[str], famille_unite: str | None, *, expected=None,
    ) -> int:
        from sme_gend.repositories.reference_write_guard import require_reference_token
        from sme_gend.services.shared_reference_write import shared_reference_write

        codes=sorted({raw.strip() for raw in structure_codes if raw and raw.strip()})
        if not codes:
            return 0
        rows={row.code:row for row in (expected or [])}
        if set(rows)!=set(codes) or len(rows)!=len(expected or []):
            raise ValueError("Versions des structures indisponibles. Actualisez les données avant de recommencer.")
        for code in codes:
            require_reference_token("structure",code,rows[code])
        with shared_reference_write(self._settings):
            for code in codes:
                row=rows[code]
                self._structures.update(code,row.designation,row.ordre_tri,
                    famille_unite=famille_unite,expected=row)
        return len(codes)

    @requires_any_permission('structures.codes', 'referentiels.fonctions')
    def fonction_set_links_for_structure(
        self, structure_code: str, fonction_libelles: list[str], *, expected=None
    ) -> None:
        if not self._fonctions:
            raise ValueError("MySQL requis pour les liaisons fonction ↔ structure.")
        self._fonctions.set_fonction_links_for_structure(structure_code, fonction_libelles, expected=expected)

    @requires_any_permission('structures.codes')
    def structure_delete(self, code: str, *, expected=None) -> None:
        from sme_gend.services.structure_organigram_service import StructureOrganigramService

        StructureOrganigramService(self._settings).delete_structure(code, expected=expected)

    def grade_insert(
        self,
        code: str,
        libelle: str,
        pref_ord: int | None,
        effectif_g: int | None,
    ) -> None:
        self._require_reference_access("grades")
        self._grades.insert(code, libelle, pref_ord, effectif_g)

    def grade_update(
        self,
        code: str,
        libelle: str,
        pref_ord: int | None,
        effectif_g: int | None,
        *, expected=None,
    ) -> None:
        self._require_reference_access("grades")
        return self.save_grade(code, libelle, pref_ord, effectif_g, expected=expected)

    def grade_delete(self, code: str, *, expected=None) -> None:
        self._require_reference_access("grades")
        from sme_gend.services.structure_organigram_service import StructureOrganigramService

        StructureOrganigramService(self._settings).delete_grade(code, expected=expected)

    def save_grade(self, code, libelle, pref_ord, effectif_g, *, expected=None,
                   insignia_source=None, create=False):
        self._require_reference_access("grades")
        from sme_gend.services.reference_policy import grade_values
        from sme_gend.services.personnel_media import grade_insignia_path
        from sme_gend.services.grade_media import staged_insignia, associate
        from sme_gend.services.shared_reference_write import shared_reference_write
        code, libelle, pref_ord, effectif_g = grade_values(code, libelle, pref_ord, effectif_g)
        source = insignia_source
        if source is None and expected is not None and expected.libelle != libelle:
            source = grade_insignia_path(expected.code, [expected])
        with staged_insignia(code, source) as entry:
            with shared_reference_write(self._settings):
                if create:
                    self._grades.insert(code, libelle, pref_ord, effectif_g)
                else:
                    self._grades.update(code, libelle, pref_ord, effectif_g, expected=expected)
                associate(self._settings, code, libelle, entry)
        return True

    def distinctions_tables_ready(self) -> bool:
        return self._distinctions.table_ready()

    def ensure_distinctions_initialized(self) -> bool:
        """Crée les tables (migration) et le jeu minimal de référentiels si besoin."""
        from sme_gend.services.mysql_import_period import apply_agent_distinction_schema

        try:
            apply_agent_distinction_schema(self._settings)
        except Exception:
            pass
        if not self.distinctions_tables_ready():
            return False
        try:
            self._distinctions.seed_defaults_if_empty()
        except Exception:
            pass
        return True

    def medailles(self, *, actif_only: bool = False) -> list[DistinctionRefRow]:
        if not self.distinctions_tables_ready():
            return []
        return self._distinctions.list_medailles(actif_only=actif_only)

    def decorations(self, *, actif_only: bool = False) -> list[DistinctionRefRow]:
        if not self.distinctions_tables_ready():
            return []
        return self._distinctions.list_decorations(actif_only=actif_only)

    @requires_any_permission('referentiels.distinctions')
    def medaille_insert(
        self,
        *,
        code: str,
        libelle_court: str | None,
        libelle_long: str | None = None,
        categorie: str | None = None,
        ordre_preseance: int | None = None,
        actif: bool = True,
    ) -> int:
        if not self.distinctions_tables_ready():
            raise ValueError("Tables médailles / décorations absentes — lancez les migrations MySQL.")
        return self._distinctions.insert_medaille(
            code=code,
            libelle_court=libelle_court,
            libelle_long=libelle_long,
            categorie=categorie,
            ordre_preseance=ordre_preseance,
            actif=actif,
        )

    @requires_any_permission('referentiels.distinctions')
    def medaille_update(self, ref_id: int, **kwargs) -> None:
        if not self.distinctions_tables_ready():
            raise ValueError("Tables médailles / décorations absentes.")
        self._distinctions.update_medaille(int(ref_id), **kwargs)

    @requires_any_permission('referentiels.distinctions')
    def medaille_delete(self, ref_id: int) -> None:
        if not self.distinctions_tables_ready():
            raise ValueError("Tables médailles / décorations absentes.")
        self._distinctions.delete_medaille(int(ref_id))

    @requires_any_permission('referentiels.distinctions')
    def decoration_insert(
        self,
        *,
        code: str,
        libelle_court: str | None,
        libelle_long: str | None = None,
        categorie: str | None = None,
        ordre_preseance: int | None = None,
        actif: bool = True,
    ) -> int:
        if not self.distinctions_tables_ready():
            raise ValueError("Tables médailles / décorations absentes — lancez les migrations MySQL.")
        return self._distinctions.insert_decoration(
            code=code,
            libelle_court=libelle_court,
            libelle_long=libelle_long,
            categorie=categorie,
            ordre_preseance=ordre_preseance,
            actif=actif,
        )

    @requires_any_permission('referentiels.distinctions')
    def decoration_update(self, ref_id: int, **kwargs) -> None:
        if not self.distinctions_tables_ready():
            raise ValueError("Tables médailles / décorations absentes.")
        self._distinctions.update_decoration(int(ref_id), **kwargs)

    @requires_any_permission('referentiels.distinctions')
    def decoration_delete(self, ref_id: int) -> None:
        if not self.distinctions_tables_ready():
            raise ValueError("Tables médailles / décorations absentes.")
        self._distinctions.delete_decoration(int(ref_id))

