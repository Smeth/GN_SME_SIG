"""Configuration éditable de la liste des présents (sections, ordre, titres)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SectionBlock:
    """Une section = un bloc structure dans le document."""

    id: str
    order: int
    structure_code: str
    title_main_override: str | None = None
    title_sub_override: str | None = None
    group_by_subdivision: bool = True
    # Ordre des libellés SubArme (legacy) ; préférer subdivision_rsa_order.
    subdivision_order: list[str] | None = None
    subdivision_rsa_order: list[int] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "order": self.order,
            "structure_code": self.structure_code,
            "title_main_override": self.title_main_override,
            "title_sub_override": self.title_sub_override,
            "group_by_subdivision": self.group_by_subdivision,
            "subdivision_order": self.subdivision_order,
            "subdivision_rsa_order": self.subdivision_rsa_order,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SectionBlock:
        raw_order = d.get("subdivision_order")
        sub_order: list[str] | None
        if raw_order is None:
            sub_order = None
        else:
            sub_order = [str(x).strip() for x in raw_order if str(x).strip()]
            if not sub_order:
                sub_order = None
        raw_rsa = d.get("subdivision_rsa_order")
        rsa_order: list[int] | None
        if raw_rsa is None:
            rsa_order = None
        else:
            rsa_order = [int(x) for x in raw_rsa if str(x).strip().isdigit()]
            if not rsa_order:
                rsa_order = None
        return cls(
            id=str(d.get("id") or uuid.uuid4()),
            order=int(d.get("order", 0)),
            structure_code=str(d.get("structure_code", "")).strip(),
            title_main_override=d.get("title_main_override"),
            title_sub_override=d.get("title_sub_override"),
            group_by_subdivision=bool(d.get("group_by_subdivision", True)),
            subdivision_order=sub_order,
            subdivision_rsa_order=rsa_order,
        )


@dataclass
class ListeLayoutConfig:
    """Paramètres globaux + liste ordonnée des sections."""

    version: int = 1
    template_sheet: str = "Liste"
    output_sheet: str = "Liste_SME"
    header_row: int = 3
    style_row_section: int = 4
    style_row_sub: int = 5
    style_row_data: int = 6
    ncols: int = 18
    sections: list[SectionBlock] = field(default_factory=list)
    write_token: str | None = field(default=None,repr=False,compare=False)
    _pending_write: object | None = field(default=None,repr=False,compare=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "template_sheet": self.template_sheet,
            "output_sheet": self.output_sheet,
            "header_row": self.header_row,
            "style_row_section": self.style_row_section,
            "style_row_sub": self.style_row_sub,
            "style_row_data": self.style_row_data,
            "ncols": self.ncols,
            "sections": [s.to_dict() for s in sorted(self.sections, key=lambda x: x.order)],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ListeLayoutConfig:
        secs = [SectionBlock.from_dict(x) for x in d.get("sections", [])]
        return cls(
            version=int(d.get("version", 1)),
            template_sheet=str(d.get("template_sheet", "Liste")),
            output_sheet=str(d.get("output_sheet", "Liste_SME")),
            header_row=int(d.get("header_row", 3)),
            style_row_section=int(d.get("style_row_section", 4)),
            style_row_sub=int(d.get("style_row_sub", 5)),
            style_row_data=int(d.get("style_row_data", 6)),
            ncols=int(d.get("ncols", 18)),
            sections=secs,
        )
