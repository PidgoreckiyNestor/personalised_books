"""Grid-based text positioning calculator."""

from __future__ import annotations

VALID_POSITIONS = {
    "top-left", "top-center", "top-right",
    "middle-left", "middle-center", "middle-right",
    "bottom-left", "bottom-center", "bottom-right",
}


def resolve_text_box(
    position: str,
    box_width: float,
    offset_x_pt: float,
    offset_y_pt: float,
    page_size_px: int,
    safe_zone_pt: float,
    dpi: int,
) -> dict:
    """
    Convert a grid position to pixel coordinates.

    Returns dict with keys: top, margin_left, box_w, box_h (all in px).
    """
    if position not in VALID_POSITIONS:
        raise ValueError(f"Invalid position {position!r}, must be one of {VALID_POSITIONS}")

    pt_to_px = dpi / 72
    safe_px = round(safe_zone_pt * pt_to_px)

    safe_w = page_size_px - 2 * safe_px
    safe_h = page_size_px - 2 * safe_px

    box_w = round(safe_w * box_width)

    v, h = position.split("-")

    # Horizontal anchor
    if h == "left":
        x = safe_px
    elif h == "center":
        x = safe_px + (safe_w - box_w) // 2
    else:
        x = safe_px + safe_w - box_w

    # Vertical anchor + box_h
    if v == "top":
        y = safe_px
        box_h = safe_h
    elif v == "middle":
        y = safe_px + safe_h // 4
        box_h = safe_h // 2
    else:
        y = safe_px + safe_h // 2
        box_h = safe_h // 2

    # Fine-tune offsets (pt → px)
    x += round(offset_x_pt * pt_to_px)
    y += round(offset_y_pt * pt_to_px)

    return {"top": y, "margin_left": x, "box_w": box_w, "box_h": box_h}
