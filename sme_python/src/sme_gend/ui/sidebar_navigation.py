"""Structure et construction de la barre latérale (navigation arborescente)."""



from __future__ import annotations



from dataclasses import dataclass



from PySide6.QtCore import Qt, QSize

from PySide6.QtGui import QColor, QFont, QIcon

from PySide6.QtWidgets import QStyle, QTreeWidget, QTreeWidgetItem



from sme_gend.ui.navigation_pages import (
    PAGE_COUNT,
    PAGE_CAREER_ALERTS,
    PAGE_ARCHIVES_CLOSURE,

    PAGE_BACKUP_RESTORE,


    PAGE_DASHBOARD,

    PAGE_DETACHES,

    PAGE_DETACHES_CONFIG,

    PAGE_DOC_GEN_LOG,

    PAGE_SENSITIVE_AUDIT,
    PAGE_ACTIVITY_AUDIT,

    PAGE_SME_DPF,

    PAGE_EDITION_SME,

    PAGE_FONCTIONS,

    PAGE_GAIN_PERTE,
    PAGE_DELETED_PERSONNEL,

    PAGE_GRADES,

    PAGE_LAYOUT_EDITOR,

    PAGE_LIMITE_AGE,

    PAGE_LISTE_GENERALE,

    PAGE_LISTE_PRESENTS,

    PAGE_MALADE_PUNIS,

    PAGE_STAGES,

    PAGE_MISSIONS,

    PAGE_DESERTION,

    PAGE_CONGES,
    PAGE_DISTINCTIONS,
    PAGE_AVANCEMENT,

    PAGE_NUMERIQUE,

    PAGE_PADMIN,

    PAGE_SETTINGS,

    PAGE_SOIT_TRANSMIS,

    PAGE_STRUCTURES,

    PAGE_STRUCTURES_EFFECTIFS,

    PAGE_USERS_AUTH,

)



ROLE_STACK = Qt.ItemDataRole.UserRole

ROLE_LABEL = Qt.ItemDataRole.UserRole + 1

ROLE_KIND = Qt.ItemDataRole.UserRole + 2  # "leaf" | "folder"

ROLE_SUBMODULE = Qt.ItemDataRole.UserRole + 3  # section Avancement (0…5), sinon -1



_KIND_LEAF = "leaf"

_KIND_FOLDER = "folder"





@dataclass(frozen=True, slots=True)

class NavLeaf:

    page_index: int

    label: str | None = None  # None = libellé register_page

    sub_index: int | None = None  # sous-écran (ex. sections Avancement)





@dataclass(frozen=True, slots=True)

class NavGroup:

    title: str

    icon: QStyle.StandardPixmap

    items: tuple[NavLeaf, ...]

    expanded: bool = True





NAV_ICON_SIZE = 22

# Sur thème sombre + Fusion (Windows), ces pixmap sont vides ou illisibles :
# SP_FileIcon (feuille blanche), SP_TitleBarMenuButton (chrome fenêtre Qt).


def nav_icon(style: QStyle, sp: QStyle.StandardPixmap, *, size: int = NAV_ICON_SIZE) -> QIcon:
    """Icône sidebar avec taille fixe (meilleur rendu dans QTreeWidget)."""
    base = style.standardIcon(sp)
    if base.isNull():
        return base
    return QIcon(
        base.pixmap(size, size, QIcon.Mode.Normal, QIcon.State.Off)
    )


def page_nav_icons(style: QStyle) -> list[QIcon]:
    """Une icône par index de page (aligné sur navigation_pages / register_page)."""
    sp = QStyle.StandardPixmap
    specs: tuple[QStyle.StandardPixmap, ...] = (
        sp.SP_DirHomeIcon,              # 0  DASHBOARD
        sp.SP_FileDialogListView,       # 1  LISTE_GENERALE
        sp.SP_DirOpenIcon,              # 2  STRUCTURES_EFFECTIFS
        sp.SP_FileDialogDetailedView,   # 3  LAYOUT_EDITOR
        sp.SP_DialogApplyButton,        # 4  LISTE_PRESENTS
        sp.SP_DriveNetIcon,             # 5  DETACHES
        sp.SP_DesktopIcon,              # 6  STRUCTURES (codes)
        sp.SP_ArrowUp,                  # 7  GRADES
        sp.SP_FileDialogContentsView,   # 8  FONCTIONS (catalogue — pas TitleBarMenu)
        sp.SP_DialogOpenButton,         # 9  DETACHES_CONFIG
        sp.SP_ComputerIcon,             # 10 NUMERIQUE
        sp.SP_DirLinkIcon,              # 11 ARCHIVES_CLOSURE
        sp.SP_DialogResetButton,        # 12 SETTINGS
        sp.SP_DialogSaveButton,         # 13 BACKUP_RESTORE
        sp.SP_ArrowForward,             # 14 GAIN_PERTE
        sp.SP_MessageBoxWarning,        # 15 MALADE_PUNIS
        sp.SP_MessageBoxInformation,    # 16 LIMITE_AGE (document / info — pas FileIcon)
        sp.SP_FileLinkIcon,             # 17 SOIT_TRANSMIS
        sp.SP_BrowserReload,            # 18 CSV_IMPORT
        sp.SP_FileDialogListView,       # 19 DOC_GEN_LOG
        sp.SP_CommandLink,              # 20 EDITION_SME
        sp.SP_FileDialogNewFolder,      # 21 PADMIN
        sp.SP_VistaShield,              # 22 USERS_AUTH
        sp.SP_BrowserReload,            # 23 PILOTAGE_MOIS
        sp.SP_MessageBoxCritical,       # 24 SENSITIVE_AUDIT
        sp.SP_FileDialogInfoView,       # 25 SME_DPF
        sp.SP_DialogOkButton,           # 26 STAGES
        sp.SP_ArrowRight,               # 27 MISSIONS
        sp.SP_MessageBoxWarning,        # 28 DESERTION
        sp.SP_FileDialogListView,       # 29 CONGES
        sp.SP_DialogApplyButton,        # 30 DISTINCTIONS
        sp.SP_ArrowUp,                  # 31 AVANCEMENT
        sp.SP_TrashIcon,                # 32 DELETED_PERSONNEL
        sp.SP_MessageBoxWarning,        # 33 CAREER_ALERTS
        sp.SP_FileDialogDetailedView,   # 34 ACTIVITY_AUDIT
    )
    icons = [nav_icon(style, s) for s in specs]
    if len(icons) != PAGE_COUNT:
        raise RuntimeError(
            f"page_nav_icons: {len(icons)} icônes pour PAGE_COUNT={PAGE_COUNT}"
        )
    return icons





def default_nav_groups() -> tuple[NavGroup, ...]:
    """Navigation par activité métier (proposition C)."""
    sp = QStyle.StandardPixmap
    return (
        NavGroup(
            "Consulter le personnel",
            sp.SP_FileDialogListView,
            (
                NavLeaf(PAGE_CAREER_ALERTS),
                NavLeaf(PAGE_LISTE_GENERALE, "Liste générale"),
                NavLeaf(PAGE_STRUCTURES_EFFECTIFS, "Effectifs par structure"),
            ),
            expanded=True,
        ),
        NavGroup(
            "Production SME",
            sp.SP_DialogApplyButton,
            (
                NavLeaf(PAGE_EDITION_SME, "Édition SME"),
                NavLeaf(PAGE_SME_DPF, "SME DPF"),
                NavLeaf(PAGE_LISTE_PRESENTS, "Liste des présents"),
                NavLeaf(PAGE_NUMERIQUE, "Situation numérique"),
                NavLeaf(PAGE_DETACHES, "Détachés"),
            ),
            expanded=True,
        ),
        NavGroup(
            "Suivi administratif et activités",
            sp.SP_ArrowForward,
            (
                NavLeaf(PAGE_GAIN_PERTE, "Gains & pertes"),
                NavLeaf(PAGE_DELETED_PERSONNEL, "Fiches retirées de la liste"),
                NavLeaf(PAGE_MALADE_PUNIS, "Malades & punis"),
                NavLeaf(PAGE_MISSIONS, "Missions"),
                NavLeaf(PAGE_DESERTION, "Désertions"),
                NavLeaf(PAGE_CONGES, "Congés"),
                NavLeaf(PAGE_STAGES, "Stages"),
            ),
        ),
        NavGroup(
            "Piloter le mois SME",
            sp.SP_CommandLink,
            (
                NavLeaf(PAGE_ARCHIVES_CLOSURE, "Gestion des mois et archives"),
            ),
        ),
        NavGroup(
            "Avancement",
            sp.SP_ArrowUp,
            (NavLeaf(PAGE_AVANCEMENT, "Avancement au grade"),),
            expanded=True,
        ),
        NavGroup(
            "Attestations",
            sp.SP_MessageBoxInformation,
            (
                NavLeaf(PAGE_LIMITE_AGE),
                NavLeaf(PAGE_SOIT_TRANSMIS),
            ),
        ),
        NavGroup(
            "Administration et outils",
            sp.SP_DialogResetButton,
            (
                NavLeaf(PAGE_LAYOUT_EDITOR, "Mise en page (présents)"),
                NavLeaf(PAGE_DETACHES_CONFIG, "Config. Excel détachés"),
                NavLeaf(PAGE_STRUCTURES, "Structures & subdivisions"),
                NavLeaf(PAGE_GRADES, "Grades"),
                NavLeaf(PAGE_FONCTIONS, "Fonctions"),
                NavLeaf(PAGE_PADMIN, "Positions administratives"),
                NavLeaf(PAGE_DISTINCTIONS, "Médailles & décorations"),
                NavLeaf(PAGE_USERS_AUTH, "Utilisateurs & droits"),
                NavLeaf(PAGE_DOC_GEN_LOG, "Historique documents"),
                NavLeaf(PAGE_ACTIVITY_AUDIT, "Journal d’activité global"),
                NavLeaf(PAGE_SENSITIVE_AUDIT, "Audit données sensibles"),
                NavLeaf(PAGE_SETTINGS, "Paramètres & données"),
                NavLeaf(PAGE_BACKUP_RESTORE, "Sauvegarde & restauration"),
            ),
            expanded=False,
        ),
    )





def build_sidebar_nav(

    tree: QTreeWidget,

    *,

    style: QStyle,

    nav_labels: list[str],

    page_icons: list[QIcon],

    page_to_item: dict[int, QTreeWidgetItem],

    avancement_nav_items: dict[int, QTreeWidgetItem] | None = None,

) -> None:

    """

    Remplit ``tree`` : tableau de bord puis groupes repliables.

    Remplit ``page_to_item`` pour chaque page.

    """

    tree.clear()

    page_to_item.clear()

    if avancement_nav_items is not None:
        avancement_nav_items.clear()



    def _label_for(leaf: NavLeaf) -> str:

        if leaf.label:

            return leaf.label

        return nav_labels[leaf.page_index]



    def _style_folder(item: QTreeWidgetItem) -> None:

        item.setData(0, ROLE_KIND, _KIND_FOLDER)

        f = QFont(item.font(0))

        f.setPointSize(max(9, f.pointSize() - 1))

        f.setWeight(QFont.Weight.DemiBold)

        f.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 102)

        item.setFont(0, f)

        item.setForeground(0, QColor("#b0bec5"))



    def add_leaf(

        parent: QTreeWidgetItem | None,

        label: str,

        stack_idx: int,

        sub_index: int | None = None,

    ) -> QTreeWidgetItem:

        if parent is None:

            it = QTreeWidgetItem(tree, [label])

        else:

            it = QTreeWidgetItem(parent, [label])

        it.setIcon(0, page_icons[stack_idx])

        it.setData(0, ROLE_STACK, stack_idx)

        it.setData(0, ROLE_LABEL, label)

        it.setData(0, ROLE_KIND, _KIND_LEAF)

        it.setData(
            0,
            ROLE_SUBMODULE,
            sub_index if sub_index is not None else -1,
        )

        it.setToolTip(0, label)

        it.setTextAlignment(0, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter))

        page_to_item.setdefault(stack_idx, it)

        if avancement_nav_items is not None and sub_index is not None:
            avancement_nav_items[sub_index] = it

        return it



    def add_folder(title: str, folder_icon: QIcon, *, expanded: bool) -> QTreeWidgetItem:

        it = QTreeWidgetItem(tree, [title])

        it.setToolTip(0, title)

        it.setIcon(0, folder_icon)

        it.setData(0, ROLE_STACK, -1)

        it.setData(0, ROLE_LABEL, title)

        _style_folder(it)

        it.setExpanded(expanded)

        it.setChildIndicatorPolicy(QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator)

        it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsSelectable)

        return it



    dash_label = nav_labels[PAGE_DASHBOARD]

    add_leaf(None, "Tableau de bord" if "Synthèse" in dash_label else dash_label, PAGE_DASHBOARD)



    for group in default_nav_groups():

        folder = add_folder(

            group.title,

            nav_icon(style, group.icon),

            expanded=group.expanded,

        )

        for leaf in group.items:

            add_leaf(
                folder,
                _label_for(leaf),
                leaf.page_index,
                leaf.sub_index,
            )



    install_sidebar_accordion(tree)
    tree.setCurrentItem(page_to_item[PAGE_DASHBOARD])


def install_sidebar_accordion(tree: QTreeWidget) -> None:
    """Keep one sibling group open and reveal the current page on navigation."""
    active = [None]

    def expanded(item):
        parent = item.parent() or tree.invisibleRootItem()
        for index in range(parent.childCount()):
            sibling = parent.child(index)
            if sibling is not item and sibling.childCount():
                sibling.setExpanded(False)
        # Folder clicks can clear Qt's selection without changing the page.
        # Restore its highlight without reopening a deliberately closed group.
        if active[0] is not None:
            active[0].setSelected(True)

    def selected(current, previous):
        if current is None or current.childCount():
            return
        previous = active[0]
        active[0] = current
        for item, bold in ((previous, False), (current, True)):
            if item is not None and not item.childCount():
                font = QFont(item.font(0))
                font.setBold(bold)
                item.setFont(0, font)
        parent = current.parent() if current is not None else None
        while parent is not None:
            parent.setExpanded(True)
            parent = parent.parent()

    tree.itemExpanded.connect(expanded)
    tree.currentItemChanged.connect(selected)
    tree.collapseAll()

