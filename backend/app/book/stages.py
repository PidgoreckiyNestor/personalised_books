from __future__ import annotations

from typing import List, Literal, Tuple

from .manifest import BookManifest, CoverSpec

Stage = Literal["prepay", "postpay"]

FRONT_HIDDEN_PAGE_NUMS = {}


def _exclude_front_hidden_pages(page_nums: List[int]) -> List[int]:
    return [page_num for page_num in page_nums if page_num not in FRONT_HIDDEN_PAGE_NUMS]


def front_visible_page_nums(manifest: BookManifest) -> List[int]:
    nums = sorted({p.page_num for p in manifest.pages})
    return _exclude_front_hidden_pages(nums)


def prepay_page_nums(manifest: BookManifest) -> List[int]:
    return sorted(
        p.page_num
        for p in manifest.pages
        if p.availability.prepay and p.page_num not in FRONT_HIDDEN_PAGE_NUMS
    )


def _prepay_page_nums(manifest: BookManifest) -> List[int]:
    """
    Prepay should generate the first and the last front-visible pages of the book.
    """
    return prepay_page_nums(manifest)


def page_nums_for_stage(manifest: BookManifest, stage: Stage) -> List[int]:
    """
    Resolve the list of page numbers for a stage.

    Product requirement:
    - prepay: first and last front-visible pages from the manifest
    - postpay: everything else that is allowed for postpay
    """
    if stage == "prepay":
        return prepay_page_nums(manifest)

    nums: List[int] = []
    for p in manifest.pages:
        if not p.availability.postpay:
            continue
        nums.append(p.page_num)
    return sorted(set(nums))


def page_nums_for_front_preview(manifest: BookManifest, stage: Stage) -> List[int]:
    """
    Front-facing preview excludes hidden pages (e.g. 1 and 23).
    """
    return _exclude_front_hidden_pages(page_nums_for_stage(manifest, stage))


def covers_for_stage(manifest: BookManifest, stage: Stage) -> List[Tuple[str, CoverSpec]]:
    """Return list of (cover_type, spec) for covers that should be generated in this stage."""
    result: List[Tuple[str, CoverSpec]] = []
    if not manifest.covers:
        return result
    for cover_type in ("front", "back"):
        spec = getattr(manifest.covers, cover_type, None)
        if not spec:
            continue
        avail = getattr(spec.availability, stage, False)
        if avail:
            result.append((cover_type, spec))
    return result


def stage_has_face_swap(manifest: BookManifest, stage: Stage) -> bool:
    """
    Return True if the given stage contains at least one page that requires face swap.

    Used to skip GPU/Comfy stage entirely for text-only / no-op stages.
    """
    page_nums = page_nums_for_stage(manifest, stage)
    for page_num in page_nums:
        spec = manifest.page_by_num(page_num)
        if spec and spec.needs_face_swap:
            return True
    return False

