"""Historical KPI contract for equivalence and paired benchmarks; never imported by the app.

Function captured unchanged from lot 29 baseline; caller supplies its namespace.
"""
def build_main_kpi_cards(total: int, presents: int, taux_pct: float, rows: list[ListeGeneraleRow], *, settings: Settings | None=None, presences_mode: str='present') -> tuple[DashboardKpiCard, ...]:
    """
    Cartes « indicateurs principaux » : effectifs + répartition Padmin (données ListeGenerale).
    Aligné sur les codes utilisés pour la situation numérique (numerique_padmin).
    """
    c_present = sum((1 for r in rows if padmin_counts_as_present(r.position_admin, presences_mode, settings=settings)))
    c_det = sum((1 for r in rows if _padmin_eq(r.position_admin, npa.PADMIN_DETACHE)))
    c_si = sum((1 for r in rows if _is_stage_interne(r.position_admin)))
    c_miss = sum((1 for r in rows if _padmin_eq(r.position_admin, npa.PADMIN_MISSION)))
    c_mal = sum((1 for r in rows if _padmin_eq(r.position_admin, npa.PADMIN_MALADE)))
    c_des = sum((1 for r in rows if _padmin_eq(r.position_admin, npa.PADMIN_DESERTEUR)))
    c_sl = sum((1 for r in rows if _padmin_eq(r.position_admin, npa.PADMIN_STAGE_LOCAL)))
    c_se = sum((1 for r in rows if _padmin_eq(r.position_admin, npa.PADMIN_STAGE_EX)))
    c_imr = sum((1 for r in rows if _padmin_eq(r.position_admin, npa.PADMIN_INST_MR)))
    c_ca = sum((1 for r in rows if _padmin_eq(r.position_admin, npa.PADMIN_CONGE_ADMIN)))
    c_pun = sum((1 for r in rows if _is_punis_row(r)))
    c_struct_detache = sum((1 for r in rows if is_structure_detache_row(r)))
    sub = f'{_fmt_int(presents)} / {_fmt_int(total)} présents' if total else None
    struct_det_sub = _subtitle_effectif_structure_detache(rows)
    cards: list[DashboardKpiCard] = [DashboardKpiCard('effectifs', 'Effectifs enregistrés', _fmt_int(total), None, 'default'), DashboardKpiCard('present', 'Présent', _fmt_int(c_present), None, 'success'), DashboardKpiCard('detache', 'Détaché', _fmt_int(c_det), None, 'info'), DashboardKpiCard('structure_detache', 'Structure détachement', _fmt_int(c_struct_detache), struct_det_sub, 'info'), DashboardKpiCard('stage_interne', 'Stage interne', _fmt_int(c_si), None, 'brand'), DashboardKpiCard('mission', 'Mission', _fmt_int(c_miss), None, 'info'), DashboardKpiCard('malade', 'Malade', _fmt_int(c_mal), None, 'muted'), DashboardKpiCard('deserteur', 'Déserteur', _fmt_int(c_des), None, 'alert'), DashboardKpiCard('stage_local', 'Stage local', _fmt_int(c_sl), None, 'muted'), DashboardKpiCard('stage_ex', 'Stage ex.', _fmt_int(c_se), None, 'muted'), DashboardKpiCard('inst_mr', 'Inst Mise Route', _fmt_int(c_imr), None, 'muted'), DashboardKpiCard('conge_admin', 'Congé admin.', _fmt_int(c_ca), None, 'info'), DashboardKpiCard('punis', 'Punis', _fmt_int(c_pun), None, 'muted'), DashboardKpiCard('taux', 'Taux de présence', f'{taux_pct:.1f}\xa0%', sub, 'highlight')]
    return tuple(cards)
