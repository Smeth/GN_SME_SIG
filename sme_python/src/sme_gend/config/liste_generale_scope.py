"""Périmètre temporel de la liste générale (mois actif ou archive consultée)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sme_gend.config.settings import Settings
from sme_gend.config.sme_context import load_sme_context


def resolve_liste_view_mois(settings: Settings, *, context=None) -> str | None:
    """
    Mois SME utilisé pour filtrer la liste générale.

    - ``settings.mysql_view_mois`` si défini (consultation d'une archive),
    - sinon le mois actif du contexte SME (``sme_context.json``).
    """
    explicit = (settings.mysql_view_mois or "").strip()
    if explicit:
        return explicit
    from sme_gend.services.sme_month_closure import canonical_sme_mois_yyyy_mm

    ctx = context if context is not None else load_sme_context()
    return canonical_sme_mois_yyyy_mm(ctx.mois_actif or "") or None


def should_filter_liste_for_display_mois(
    display_mois: str,
    *,
    operational_mois: str,
    consultation_mois: str | None,
    month_is_closed: bool,
) -> bool:
    """
    True si la liste / le tableau de bord doivent lire ``carriere_situation`` du mois affiché
    (``mysql_view_mois``), et non la situation « actuelle » (``est_actuelle = 1``).
    """
    ym = (display_mois or "").strip()
    op = (operational_mois or "").strip()
    if not ym:
        return False
    if month_is_closed:
        return True
    cons = (consultation_mois or "").strip()
    return bool(cons and op and ym != op)


def resolve_export_mois(settings: Settings, *, context=None) -> str | None:
    """
    Mois SME utilisé pour les exports (numérique, titres Excel, récap, satellites).

    Aligné sur le mois **consulté** (archive ou actif), pas sur le seul mois actif du contexte.
    """
    return resolve_liste_view_mois(settings,context=context)


@dataclass(frozen=True, slots=True)
class ExportMoisContext:
    """Contexte export — mois des données vs mois actif de session."""

    export_mois: str | None
    mois_actif: str | None
    is_archive_view: bool

    def label_short(self) -> str:
        ym = (self.export_mois or "").strip()
        return ym or "—"

    def banner_html(self) -> str:
        """Bandeau informatif pour les vues d'export."""
        exp = (self.export_mois or "").strip()
        act = (self.mois_actif or "").strip()
        if not exp:
            return (
                "<span style='color:#e65100;'>"
                "<b>Mois SME non défini</b> — configurez la session SME avant d'exporter."
                "</span>"
            )
        if self.is_archive_view and act and exp != act:
            return (
                f"<span style='color:#1565c0;'>"
                f"<b>Export / aperçu pour le mois consulté : {exp}</b> "
                f"(mois actif de session : {act})."
                f"</span>"
            )
        return (
            f"<span style='color:#2e7d32;'>"
            f"<b>Mois SME : {exp}</b> — données, titres et récap alignés sur ce mois."
            f"</span>"
        )


def build_export_mois_context(settings: Settings, *, context=None) -> ExportMoisContext:
    from sme_gend.services.sme_month_closure import canonical_sme_mois_yyyy_mm

    ctx = context if context is not None else load_sme_context()
    mois_actif = canonical_sme_mois_yyyy_mm(ctx.mois_actif or "") or None
    export_mois = resolve_export_mois(settings,context=ctx)
    explicit = (settings.mysql_view_mois or "").strip()
    is_archive = bool(explicit) or (
        export_mois is not None
        and mois_actif is not None
        and export_mois != mois_actif
    )
    return ExportMoisContext(
        export_mois=export_mois,
        mois_actif=mois_actif,
        is_archive_view=is_archive,
    )


def reference_date_for_mois(view_mois: str) -> date:
    """Dernier jour du mois SME — date de référence pour les requêtes par intervalle."""
    from sme_gend.services.mysql_import_period import last_day_of_month

    return last_day_of_month(view_mois)


def carriere_scope_sql(view_mois: str | None, *, alias: str = "c", membership: bool = False) -> tuple[str, tuple]:
    """Select the registered month snapshot, including an empty snapshot.

    Date intervals are a compatibility fallback only for unregistered months.
    Without a month, select the current segments.
    """
    ym = (view_mois or "").strip()
    a = (alias or "").strip()
    col = f"{a}." if a else ""
    outer = col or 'carriere_situation.'
    member = (f" AND NOT EXISTS (SELECT 1 FROM sme_month_membership workforce_member "
              f"WHERE workforce_member.agent_id={outer}agent_id "
              f"AND workforce_member.sme_periode_id={outer}sme_periode_id "
              "AND workforce_member.is_member=0)") if membership else ''
    if ym:
        ref = reference_date_for_mois(ym)
        token = ym.replace('-', '')
        reverse_token = token[4:] + token[:4]
        period = "TRIM(REPLACE(annee_mois, '-', '')) IN (%s,%s)"
        period_match = f"{col}sme_periode_id IN (SELECT id FROM sme_periode WHERE {period})"
        # A registered month owns its snapshot, including an intentionally empty
        # snapshot. An older open-ended segment must never repopulate that month.
        legacy_interval = (
            f"{col}date_debut IS NOT NULL AND {col}date_debut <= %s "
            f"AND ({col}date_fin IS NULL OR {col}date_fin >= %s) "
            f"AND ({col}sme_periode_id IS NOT NULL OR {col}est_actuelle = 1) "
            f"AND NOT EXISTS (SELECT 1 FROM sme_periode WHERE {period})"
        )
        return f"((({period_match}) OR ({legacy_interval})){member})", (token, reverse_token, ref, ref, token, reverse_token)
    return (f"({col}est_actuelle = 1{member})", ())


def resolve_carriere_scope_mois(settings: Settings) -> str | None:
    """Resolve the displayed month for both operational and archive views."""
    view_mois = resolve_liste_view_mois(settings)
    if not view_mois:
        return None

    # The active month is also monthly data, not a cross-month fallback.
    return view_mois


def carriere_scope_for_settings(settings: Settings, *, alias: str = "c", membership: bool = False, cursor=None) -> tuple[str, tuple]:
    """Périmètre carrière aligné liste / exports / organigramme."""
    if cursor is not None:
        from sme_gend.services.membership_scope import membership_projection_enabled
        membership = membership_projection_enabled(settings, cursor=cursor)
    return carriere_scope_sql(resolve_carriere_scope_mois(settings), alias=alias, membership=membership)


def carriere_scope_for_numerique(settings: Settings, *, alias: str = "c", membership: bool = False) -> tuple[str, tuple]:
    """
    Périmètre carrière pour matrice / indispo / export numérique.

    Toujours le snapshot du mois SME consulté (intervalle / ``sme_periode_id``),
    jamais le repli ``est_actuelle = 1`` du mois opérationnel ouvert.
    """
    ym = resolve_export_mois(settings)
    if not ym:
        return carriere_scope_sql(None, alias=alias, membership=membership)
    return carriere_scope_for_explicit_export_mois(ym, alias=alias, membership=membership)


def settings_for_numerique_data(base: Settings) -> Settings:
    """
    Clone ``Settings`` pour lecture situation numérique (liste + satellites).

    Force ``mysql_view_mois`` sur le mois SME exporté/consulté afin que missions,
    malades, désertions et stages utilisent la même période que la matrice.
    """
    from dataclasses import replace

    ym = resolve_export_mois(base)
    if not ym:
        return base
    explicit = (base.mysql_view_mois or "").strip()
    if explicit == ym:
        return base
    return replace(base, mysql_view_mois=ym)


def carriere_scope_for_explicit_export_mois(mois_ym: str, *, alias: str = "c", membership: bool = False) -> tuple[str, tuple]:
    """
    Périmètre carrière pour un export où l'utilisateur a choisi un mois précis.

    Force le filtre intervalle / ``sme_periode_id`` sur ce mois, sans repli
    ``est_actuelle = 1`` lorsque le mois choisi coïncide avec le mois actif de session.
    """
    from sme_gend.services.mysql_import_period import normalize_import_mois
    from sme_gend.services.sme_month_closure import canonical_sme_mois_yyyy_mm

    raw = (mois_ym or "").strip()
    ym = canonical_sme_mois_yyyy_mm(raw) or normalize_import_mois(raw) if raw else ""
    if not ym:
        return carriere_scope_sql(None, alias=alias, membership=membership)
    return carriere_scope_sql(ym, alias=alias, membership=membership)


def resolve_mutation_view_mois(settings: Settings) -> str:
    """Mois SME cible pour insert/update carrière (lève si absent)."""
    ym = resolve_liste_view_mois(settings)
    if not ym:
        raise ValueError(
            "Mois SME non défini — configurez le mois actif (Paramètres > Session SME) "
            "avant d'ajouter ou modifier du personnel."
        )
    return ym


def settings_for_chosen_export_mois(base: Settings, mois_ym: str | None) -> Settings:
    """
    Clone ``Settings`` pour exporter un mois SME précis.

    Si ``mois_ym`` est absent ou égal au mois actif opérationnel, conserve le comportement
    courant (situation actuelle ou archive déjà consultée).
    """
    from dataclasses import replace

    from sme_gend.services.sme_month_closure import canonical_sme_mois_yyyy_mm

    raw = (mois_ym or "").strip()
    if not raw:
        return base
    chosen = canonical_sme_mois_yyyy_mm(raw)
    if not chosen:
        return base
    ctx = load_sme_context()
    op = canonical_sme_mois_yyyy_mm(ctx.mois_actif or "") or ""
    current_view = (base.mysql_view_mois or "").strip()
    if chosen == op and not current_view:
        return replace(base, mysql_view_mois=None)
    if chosen == current_view:
        return base
    return replace(base, mysql_view_mois=chosen)


def list_available_export_mois(settings: Settings) -> list[str]:
    """Catalogue YYYY-MM pour le sélecteur d'export (périodes SME + mouvements)."""
    from sme_gend.repositories.gain_perte_repository import GainPerteRepository

    try:
        months = GainPerteRepository(settings).fetch_distinct_months()
    except Exception:
        months = []
    current = resolve_export_mois(settings)
    if current and current not in months:
        months = [current, *months]
    return sorted({m for m in months if m}, reverse=True)


def format_export_mois_choice_label(mois_ym: str) -> str:
    """Libellé combo export : « 2026-05 — Mois de mai 2026 »."""
    from sme_gend.services.numerique_excel_export import month_title_de_lowercase

    ym = (mois_ym or "").strip()
    if not ym:
        return "—"
    de = month_title_de_lowercase(ym)
    return f"{ym} — {de}" if de else ym



def carriere_write_scope_for_settings(settings: Settings, *, membership: bool = False, cursor=None) -> tuple[str, tuple]:
    """Keep the read scope while allowing MySQL to update the same table.

    DISTINCT prevents derived-table merging (including with derived_merge=on),
    avoiding error 1093 for the existing self-referencing period/dedup predicates.
    No optimizer/session setting, separate ID read or Python-side ID list.
    The UPDATE target must use alias c.
    """
    if cursor is not None:
        from sme_gend.services.membership_scope import membership_projection_enabled
        membership = membership_projection_enabled(settings, cursor=cursor)
    month = resolve_carriere_scope_mois(settings)
    if not month:
        return carriere_scope_sql(None, membership=membership)
    selected, params = carriere_scope_sql(month, alias="cs_write", membership=membership)
    return (
        "c.id IN (SELECT scoped.id FROM ("
        "SELECT DISTINCT cs_write.id FROM carriere_situation cs_write "
        f"WHERE {selected}) AS scoped)",
        params,
    )
