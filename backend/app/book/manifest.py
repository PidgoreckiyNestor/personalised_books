from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, model_validator


class Availability(BaseModel):
    prepay: bool = False
    postpay: bool = True


class ShadowSpec(BaseModel):
    color: str = "0,0,0"
    opacity: float = 0.5
    offset: int = 4
    offset_x: Optional[int] = None  # if set, overrides offset for horizontal
    offset_y: Optional[int] = None  # if set, overrides offset for vertical
    blur: List[int] = Field(default_factory=lambda: [0, 4])


class TypographySpec(BaseModel):
    font_uri: str
    font_bold_uri: Optional[str] = None
    body: Dict[str, Any] = Field(default_factory=lambda: {
        "font_size": "14pt",
        "line_height": "19pt",
        "color": "#ffffff",
    })
    accent: Dict[str, Any] = Field(default_factory=lambda: {
        "font_size": "19pt",
    })
    shadow: ShadowSpec = Field(default_factory=ShadowSpec)


class TextLayer(BaseModel):
    text_key: Optional[str] = None
    text_template: Optional[str] = None

    template_engine: str = "format"
    template_vars: List[str] = Field(default_factory=lambda: ["child_name"])

    position: str  # "top-left", "bottom-center", etc.
    box_width: float = 0.8
    text_align: str = "left"
    offset_x: float = 0.0
    offset_y: float = 0.0

    font_uri: Optional[str] = None  # per-layer override (fallback to typography)
    style: Dict[str, Any] = Field(default_factory=dict)  # per-layer overrides

    @model_validator(mode='after')
    def _validate_text_source(self) -> 'TextLayer':
        if not self.text_key and not self.text_template:
            raise ValueError("TextLayer requires either text_key or text_template")
        return self


class PageSpec(BaseModel):
    page_num: int
    base_uri: str

    needs_face_swap: bool = False
    text_layers: List[TextLayer] = Field(default_factory=list)

    availability: Availability = Field(default_factory=Availability)

    prompt: Optional[str] = None
    negative_prompt: Optional[str] = None


class CoverSpec(BaseModel):
    base_uri: str
    needs_face_swap: bool = False
    text_layers: List[TextLayer] = Field(default_factory=list)
    availability: Availability = Field(default_factory=Availability)
    prompt: Optional[str] = None
    negative_prompt: Optional[str] = None
    typography: Optional[TypographySpec] = None  # per-cover override


class CoversSpec(BaseModel):
    front: Optional[CoverSpec] = None
    back: Optional[CoverSpec] = None
    typography: Optional[TypographySpec] = None


class OutputSpec(BaseModel):
    dpi: int = 300
    page_size_px: int = 2551
    safe_zone_pt: float = 24.0


class BookManifest(BaseModel):
    slug: str
    typography: TypographySpec
    pages: List[PageSpec]
    covers: Optional[CoversSpec] = None
    output: OutputSpec = Field(default_factory=OutputSpec)

    def page_by_num(self, page_num: int) -> Optional[PageSpec]:
        for p in self.pages:
            if p.page_num == page_num:
                return p
        return None
