"""Formatage des désignations de structure dans les attestations limite d'âge."""

from sme_gend.services.limite_age_service import (
    _fmt_structure_for_attestation,
    _prep_structure,
)


def test_region_lowercase_with_place_capitalization():
    assert _fmt_structure_for_attestation("REGION DE GENDARMERIE DU NIARI") == (
        "région de gendarmerie du Niari"
    )
    assert _fmt_structure_for_attestation("REGION DE GENDARMERIE DES PLATEAUX") == (
        "région de gendarmerie des Plateaux"
    )
    assert _fmt_structure_for_attestation("REGION DE GENDARMERIE DE LA SANGHA") == (
        "région de gendarmerie de la Sangha"
    )
    assert _fmt_structure_for_attestation("REGION DE GENDARMERIE DE POINTE-NOIRE") == (
        "région de gendarmerie de Pointe-Noire"
    )
    assert _prep_structure("REGION DE GENDARMERIE DU NIARI") == "à la"


def test_region_gendarerie_typo():
    assert _fmt_structure_for_attestation("REGION DE GENDARERIE DES PLATEAUX") == (
        "région de gendarmerie des Plateaux"
    )
    assert _prep_structure("REGION DE GENDARERIE DES PLATEAUX") == "à la"


def test_prep_direction_and_cabinet():
    assert _prep_structure("DIRECTION DU PERSONNEL ET DE LA FORMATION") == "à la"
    assert _prep_structure("CABINET DU COMMANDANT DE LA GENDARMERIE") == "au"
    assert _prep_structure("ETAT-MAJOR DE LA GENDARMERIE NATIONALE") == "à l'"
    assert _prep_structure("INSPECTION TECHNIQUE DE LA GENDARMERIE NATIONALE") == "à l'"


def test_ge_gqg_canonical_wording():
    assert _fmt_structure_for_attestation(
        "GROUPEMENT ESCADRONS GRAND QUARTIER GENERAL"
    ) == "groupement des escadrons du grand quartier général"
    assert _prep_structure("GROUPEMENT ESCADRONS GRAND QUARTIER GENERAL") == "au"


def test_ggm_without_de_la_and_ordinal_spelled_out():
    assert _fmt_structure_for_attestation(
        "1ER GROUPEMENT DE LA GENDARMERIE MOBILE"
    ) == "premier groupement de gendarmerie mobile"
    assert _fmt_structure_for_attestation(
        "PREMIER GROUPEMENT GENDARMERIE MOBILE"
    ) == "premier groupement de gendarmerie mobile"
    assert _fmt_structure_for_attestation("1er GGM") == "premier groupement de gendarmerie mobile"
    assert _fmt_structure_for_attestation(
        "DEUXIEME GROUPEMENT DE LA GENDARMERIE MOBILE"
    ) == "deuxième groupement de gendarmerie mobile"
    assert _prep_structure("1ER GROUPEMENT DE LA GENDARMERIE MOBILE") == "au"


def test_generic_structure_lowercase():
    assert _fmt_structure_for_attestation("ESCADRON DE SECURITE ET PROTECTION 1") == (
        "escadron de sécurité et protection 1"
    )


def test_config_override_takes_priority():
    from sme_gend.models.limite_age_config import (
        LimiteAgeConfig,
        LimiteAgeStructureFormat,
        StructureAttestationOverride,
    )
    from sme_gend.services.limite_age_service import structure_overrides_map

    cfg = LimiteAgeConfig(
        structure_format=LimiteAgeStructureFormat(
            overrides=[
                StructureAttestationOverride(
                    "REGION DE GENDARMERIE DU NIARI",
                    "région spéciale du Niari",
                ),
            ]
        )
    )
    assert _fmt_structure_for_attestation(
        "REGION DE GENDARMERIE DU NIARI",
        structure_overrides_map(cfg),
    ) == "région spéciale du Niari"


def test_resolve_structure_text_by_code():
    from sme_gend.config.settings import Settings
    from sme_gend.models.structure import StructureRow
    from sme_gend.services.limite_age_service import (
        attestation_label_for_structure,
        resolve_structure_text,
    )

    class _Repo:
        def fetch_by_code(self, code: str):
            if code == "COMGEND":
                return StructureRow(
                    code="COMGEND",
                    designation="COMMANDEMENT DE LA GENDARMERIE",
                    ordre_tri=1,
                )
            return None

        def fetch_all(self):
            return [
                StructureRow(
                    code="COMGEND",
                    designation="COMMANDEMENT DE LA GENDARMERIE",
                    ordre_tri=1,
                )
            ]

    settings = Settings(database_path=__import__("pathlib").Path("x.mdb"))

    import sme_gend.services.limite_age_service as las

    las.StructureRepository = lambda _s: _Repo()  # type: ignore[misc]
    # Patch at repository import site inside function
    from sme_gend.repositories import structure_repository as sr

    sr.StructureRepository = lambda _s: _Repo()

    code, text = resolve_structure_text(settings, "COMGEND")
    assert code == "COMGEND"
    assert "COMMANDEMENT" in text
    label, _ = attestation_label_for_structure("COMGEND", None, settings)
    assert "commandement" in label.lower()


def test_sync_structure_format_uses_codes():
    from sme_gend.config.settings import Settings
    from sme_gend.models.limite_age_config import LimiteAgeConfig, LimiteAgeStructureFormat
    from sme_gend.models.structure import StructureRow
    from sme_gend.services.limite_age_service import sync_structure_format_from_referentiel

    class _Repo:
        def fetch_all(self):
            return [
                StructureRow(code="COMGEND", designation="COMMANDEMENT DE LA GENDARMERIE", ordre_tri=1),
                StructureRow(code="IT", designation="INSPECTION TECHNIQUE", ordre_tri=2),
            ]

    from sme_gend.repositories import structure_repository as sr

    sr.StructureRepository = lambda _s: _Repo()
    settings = Settings(database_path=__import__("pathlib").Path("x.mdb"))
    cfg = LimiteAgeConfig(
        structure_format=LimiteAgeStructureFormat(
            overrides=[
                __import__(
                    "sme_gend.models.limite_age_config", fromlist=["StructureAttestationOverride"]
                ).StructureAttestationOverride(
                    "COMMANDEMENT DE LA GENDARMERIE",
                    "commandement personnalisé",
                )
            ]
        )
    )
    rows, added = sync_structure_format_from_referentiel(settings, cfg)
    assert len(rows) == 2
    by_code = {r.source: r.attestation for r in rows}
    assert by_code["COMGEND"] == "commandement personnalisé"
    assert added >= 1
