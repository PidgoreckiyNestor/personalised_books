import asyncio
import io
import json
import os
import random
import traceback
import uuid
from typing import Dict, Optional
from urllib.parse import urlparse

import boto3
import cv2
import numpy as np
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from .book.manifest_store import load_manifest
from .book.stages import covers_for_stage, page_nums_for_stage, prepay_page_nums
from .config import settings
from .db import AsyncSessionLocal
from .logger import logger
from .models import BookPreview, Job, JobArtifact
from .workers import celery_app

s3 = boto3.client(
    "s3",
    aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
    aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
    region_name=settings.AWS_REGION_NAME,
    endpoint_url=settings.AWS_ENDPOINT_URL,
)


async def _get_job(db: AsyncSession, job_id: str):
    res = await db.execute(select(Job).filter(Job.job_id == job_id))
    return res.scalar_one_or_none()


def _should_randomize_seed(job: Job, stage: str, explicit: bool) -> bool:
    if explicit:
        return True
    if stage != "prepay":
        return False
    data = job.analysis_json if isinstance(job.analysis_json, dict) else {}
    retry_data = data.get("generation_retry")
    if not isinstance(retry_data, dict):
        return False
    return bool(retry_data.get("randomize_seed"))


def _run_face_transfer(
    child_pil: Image.Image,
    base_uri: str,
    prompt: str,
    negative: str,
    randomize_seed: bool = False,
) -> Image.Image:
    """
    Lazy wrapper to avoid importing ComfyUI/InsightFace stack for text-only pages.
    """
    from .inference.comfy_runner import run_face_transfer

    return run_face_transfer(child_pil, base_uri, prompt, negative, randomize_seed=randomize_seed)


def _submit_face_transfer(
    child_pil: Image.Image,
    base_uri: str,
    prompt: str,
    negative: str,
    randomize_seed: bool = False,
) -> str:
    """
    Upload images + queue prompt in ComfyUI, return prompt_id without waiting.
    """
    from .inference.comfy_runner import submit_face_transfer, _build_face_mask
    import random

    s3_client = boto3.client(
        "s3",
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        region_name=settings.AWS_REGION_NAME,
        endpoint_url=settings.AWS_ENDPOINT_URL,
    )

    if base_uri.startswith("s3://"):
        uri_parts = base_uri.replace("s3://", "").split("/", 1)
        bucket = uri_parts[0]
        key = uri_parts[1] if len(uri_parts) > 1 else ""
    else:
        bucket = settings.S3_BUCKET_NAME
        key = base_uri

    # Try loading illustration
    candidate_keys = [key]
    if key.lower().endswith(".png"):
        candidate_keys.append(key[:-4] + ".jpg")
        candidate_keys.append(key[:-4] + ".jpeg")
    elif key.lower().endswith((".jpg", ".jpeg")):
        base = key[:-4] if key.lower().endswith(".jpg") else key[:-5]
        candidate_keys.append(base + ".png")

    illustration_pil = None
    for candidate in candidate_keys:
        try:
            obj = s3_client.get_object(Bucket=bucket, Key=candidate)
            illustration_pil = Image.open(io.BytesIO(obj["Body"].read())).convert("RGB")
            key = candidate
            break
        except Exception:
            pass

    if illustration_pil is None:
        raise RuntimeError(f"Failed to load illustration from {base_uri}")

    # Try loading explicit mask
    explicit_mask_pil = None
    try:
        base_name = os.path.basename(key)
        root = base_name.rsplit(".", 1)[0]
        ext = ".png"
        mask_key = key.replace(base_name, f"{root}_mask{ext}")
        try:
            mobj = s3_client.get_object(Bucket=bucket, Key=mask_key)
            explicit_mask_pil = Image.open(io.BytesIO(mobj["Body"].read())).convert("RGB")
        except Exception:
            pass
    except Exception:
        pass

    seed = random.randint(1, 2**31 - 1) if randomize_seed else None
    return submit_face_transfer(child_pil, illustration_pil, prompt, negative, mask_pil=explicit_mask_pil, seed=seed)


def _collect_face_transfer(prompt_id: str) -> Image.Image:
    """Wait for ComfyUI result by prompt_id."""
    from .inference.comfy_runner import collect_face_transfer
    return collect_face_transfer(prompt_id)


def _has_face(pil_img: Image.Image) -> bool:
    """Fast face presence check using OpenCV Haar cascade."""
    try:
        img_np = np.array(pil_img.convert("RGB"))
        gray = cv2.cvtColor(img_np, cv2.COLOR_RGB2GRAY)
        cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        cascade = cv2.CascadeClassifier(cascade_path)
        faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(48, 48))
        return len(faces) > 0
    except Exception as e:
        logger.warning(f"Face check failed, assuming face present: {e}")
        return True


def _s3_read_private_to_pil(s3_uri: str) -> Image.Image:
    """Read image from S3 and convert to PIL Image"""
    bucket = settings.S3_BUCKET_NAME
    key: Optional[str] = None

    if s3_uri.startswith("s3://"):
        parts = s3_uri.replace("s3://", "").split("/", 1)
        if len(parts) == 2:
            bucket, key = parts
        else:
            bucket = parts[0]
            key = ""
    else:
        key = s3_uri.split("/", 4)[-1] if s3_uri.startswith("http") else s3_uri

    if key is None:
        raise RuntimeError(f"Failed to parse S3 key from uri={s3_uri!r}")

    logger.debug(f"Reading S3 object: bucket={bucket}, key={key}")
    obj = s3.get_object(Bucket=bucket, Key=key)
    img = Image.open(io.BytesIO(obj["Body"].read())).convert("RGB")
    logger.debug(f"Successfully loaded image: size={img.size}")
    return img


def _s3_write_pil(img: Image.Image, key: str, dpi: Optional[int] = None) -> str:
    """Write PIL Image to S3"""
    buf = io.BytesIO()
    save_kwargs: Dict[str, object] = {}
    if dpi:
        save_kwargs["dpi"] = (dpi, dpi)
    img.save(buf, format="PNG", **save_kwargs)
    buf.seek(0)

    logger.debug(
        f"Writing image to S3: bucket={settings.S3_BUCKET_NAME}, key={key}, size={len(buf.getvalue())} bytes"
    )
    s3.put_object(Bucket=settings.S3_BUCKET_NAME, Key=key, Body=buf.getvalue(), ContentType="image/png")

    s3_uri = f"s3://{settings.S3_BUCKET_NAME}/{key}"
    logger.info(f"Successfully wrote image to S3: {s3_uri}")
    return s3_uri


def _page_key(page_num: int) -> str:
    if page_num == -1:
        return "front_cover"
    if page_num == -2:
        return "back_cover"
    return f"page_{page_num:02d}"


def _layout_bg_key(job_id: str, page_num: int) -> str:
    return f"layout/{job_id}/pages/{_page_key(page_num)}_bg.png"


def _layout_final_key(job_id: str, page_num: int) -> str:
    return f"layout/{job_id}/pages/{_page_key(page_num)}.png"


async def _upsert_artifact(
    db: AsyncSession,
    *,
    job_id: str,
    stage: str,
    kind: str,
    s3_uri: str,
    page_num: Optional[int] = None,
    meta: Optional[Dict] = None,
) -> None:
    """
    Insert an artifact record. (We don't enforce uniqueness yet; S3 keys are deterministic anyway.)
    """
    art = JobArtifact(
        id=str(uuid.uuid4()),
        job_id=job_id,
        stage=stage,
        kind=kind,
        page_num=page_num,
        s3_uri=s3_uri,
        meta=meta,
    )
    db.add(art)


@celery_app.task(bind=True, acks_late=True, max_retries=2)
def build_stage_backgrounds_task(self, job_id: str, stage: str, randomize_seed: bool = False, page_nums_filter: list = None):
    """
    GPU-stage task:
    - loads manifest from S3
    - for pages in the given stage:
      - runs face swap if needed
      - otherwise loads base image
      - normalizes to output.page_size_px
      - writes background image to S3 (layout/..._bg.png)
    - enqueues CPU render task (text overlay / finalization)

    If page_nums_filter is provided, only those page numbers are processed.
    """

    async def _run():
        async with AsyncSessionLocal() as db:
            job = await _get_job(db, job_id)
            if not job:
                logger.error(f"Job not found in database: {job_id}")
                return

            manifest = load_manifest(job.slug)
            if stage == "prepay":
                page_nums = prepay_page_nums(manifest)
            else:
                page_nums = page_nums_for_stage(manifest, stage) or []
            if page_nums_filter:
                page_nums = [p for p in page_nums if p in page_nums_filter]
            randomize_seed_flag = _should_randomize_seed(job, stage, randomize_seed)

            if stage == "prepay":
                job.status = "prepay_generating"
            else:
                job.status = "postpay_generating"
            await db.commit()
            await db.refresh(job)

            child_pil: Optional[Image.Image] = None
            if job.child_photo_uri:
                child_pil = _s3_read_private_to_pil(job.child_photo_uri)

            # Phase 1: Submit all face swap prompts to ComfyUI at once
            pending = []  # list of (page_num, prompt_id) for face swap pages
            non_swap_pages = []  # list of (page_num, spec) for pages without face swap
            for page_num in page_nums:
                spec = manifest.page_by_num(page_num)
                if not spec:
                    raise RuntimeError(f"Manifest has no page spec for page_num={page_num}")

                if spec.needs_face_swap and not settings.SKIP_FACE_SWAP:
                    if child_pil is None:
                        raise RuntimeError("child_photo_uri is missing; cannot run face swap")
                    prompt = (spec.prompt or job.common_prompt or "child portrait").strip()
                    negative = (spec.negative_prompt or "low quality, bad face, distorted").strip()
                    prompt_id = _submit_face_transfer(
                        child_pil,
                        spec.base_uri,
                        prompt,
                        negative,
                        randomize_seed=randomize_seed_flag,
                    )
                    pending.append((page_num, prompt_id))
                    logger.info(f"Queued face swap for page {page_num}: {prompt_id}")
                else:
                    non_swap_pages.append((page_num, spec))

            # Phase 2: Process non-face-swap pages immediately
            for page_num, spec in non_swap_pages:
                out_img = _s3_read_private_to_pil(spec.base_uri)
                target = manifest.output.page_size_px
                if out_img.size != (target, target):
                    out_img = out_img.resize((target, target), Image.Resampling.LANCZOS)
                bg_key = _layout_bg_key(job_id, page_num)
                bg_uri = _s3_write_pil(out_img, bg_key, dpi=manifest.output.dpi)
                await _upsert_artifact(db, job_id=job_id, stage=stage, kind="page_bg_png", s3_uri=bg_uri, page_num=page_num)

            # Phase 3: Collect face swap results in order
            for page_num, prompt_id in pending:
                out_img = _collect_face_transfer(prompt_id)
                target = manifest.output.page_size_px
                if out_img.size != (target, target):
                    out_img = out_img.resize((target, target), Image.Resampling.LANCZOS)
                bg_key = _layout_bg_key(job_id, page_num)
                bg_uri = _s3_write_pil(out_img, bg_key, dpi=manifest.output.dpi)
                await _upsert_artifact(db, job_id=job_id, stage=stage, kind="page_bg_png", s3_uri=bg_uri, page_num=page_num)
                logger.info(f"Collected face swap result for page {page_num}")

            # Phase 4: Process covers
            cover_page_nums = {"front": -1, "back": -2}
            covers_to_process = covers_for_stage(manifest, stage)
            cover_pending = []  # (cover_type, prompt_id)
            cover_non_swap = []  # (cover_type, spec)
            for cover_type, cover_spec in covers_to_process:
                if cover_spec.needs_face_swap and not settings.SKIP_FACE_SWAP:
                    if child_pil is None:
                        raise RuntimeError("child_photo_uri is missing; cannot run face swap for cover")
                    prompt = (cover_spec.prompt or job.common_prompt or "child portrait").strip()
                    negative = (cover_spec.negative_prompt or "low quality, bad face, distorted").strip()
                    prompt_id = _submit_face_transfer(
                        child_pil,
                        cover_spec.base_uri,
                        prompt,
                        negative,
                        randomize_seed=randomize_seed_flag,
                    )
                    cover_pending.append((cover_type, prompt_id))
                    logger.info(f"Queued face swap for {cover_type} cover: {prompt_id}")
                else:
                    cover_non_swap.append((cover_type, cover_spec))

            for cover_type, cover_spec in cover_non_swap:
                out_img = _s3_read_private_to_pil(cover_spec.base_uri)
                target = manifest.output.page_size_px
                if out_img.size != (target, target):
                    out_img = out_img.resize((target, target), Image.Resampling.LANCZOS)
                pn = cover_page_nums[cover_type]
                bg_key = _layout_bg_key(job_id, pn)
                bg_uri = _s3_write_pil(out_img, bg_key, dpi=manifest.output.dpi)
                await _upsert_artifact(db, job_id=job_id, stage=stage, kind="page_bg_png", s3_uri=bg_uri, page_num=pn)

            for cover_type, prompt_id in cover_pending:
                out_img = _collect_face_transfer(prompt_id)
                target = manifest.output.page_size_px
                if out_img.size != (target, target):
                    out_img = out_img.resize((target, target), Image.Resampling.LANCZOS)
                pn = cover_page_nums[cover_type]
                bg_key = _layout_bg_key(job_id, pn)
                bg_uri = _s3_write_pil(out_img, bg_key, dpi=manifest.output.dpi)
                await _upsert_artifact(db, job_id=job_id, stage=stage, kind="page_bg_png", s3_uri=bg_uri, page_num=pn)
                logger.info(f"Collected face swap result for {cover_type} cover")

            if randomize_seed_flag and stage == "prepay":
                base_data = job.analysis_json if isinstance(job.analysis_json, dict) else {}
                retry_data = base_data.get("generation_retry")
                if isinstance(retry_data, dict):
                    data = dict(base_data)
                    retry_copy = dict(retry_data)
                    retry_copy["randomize_seed"] = False
                    data["generation_retry"] = retry_copy
                    job.analysis_json = data

            await db.commit()

            try:
                render_stage_pages_task.apply_async(
                    args=(job_id, stage),
                    kwargs={"page_nums_filter": page_nums_filter},
                    queue="render",
                )
            except Exception:
                render_stage_pages_task.delay(job_id, stage)

    try:
        return asyncio.run(_run())
    except Exception as e:
        logger.error(
            f"Stage background build failed for job {job_id}: {e}",
            extra={"job_id": job_id, "stage": stage, "traceback": traceback.format_exc()},
        )
        try:

            async def _mark_failed():
                async with AsyncSessionLocal() as db:
                    job = await _get_job(db, job_id)
                    if job:
                        job.status = "generation_failed"
                        await db.commit()

            asyncio.run(_mark_failed())
        except Exception:
            pass
        raise


@celery_app.task(bind=True, acks_late=True, max_retries=2)
def render_stage_pages_task(self, job_id: str, stage: str, page_nums_filter: list = None):
    """
    CPU-stage task:
    - loads manifest
    - for pages in stage:
      - loads background image from S3 (layout/..._bg.png) OR derives it directly from base_uri for non-face pages
      - applies text layers if configured
      - writes final page image to S3 (layout/...page_XX.png)

    If page_nums_filter is provided, only those page numbers are processed.
    """

    async def _run():
        from .rendering.html_text import render_text_layers_over_image

        async with AsyncSessionLocal() as db:
            job = await _get_job(db, job_id)
            if not job:
                logger.error(f"Job not found in database: {job_id}")
                return

            manifest = load_manifest(job.slug)
            if stage == "prepay":
                page_nums = prepay_page_nums(manifest)
            else:
                page_nums = page_nums_for_stage(manifest, stage) or []
            if page_nums_filter:
                page_nums = [p for p in page_nums if p in page_nums_filter]

            if stage == "prepay":
                job.status = "prepay_generating"
            else:
                job.status = "postpay_generating"
            await db.commit()
            await db.refresh(job)

            for page_num in page_nums:
                spec = manifest.page_by_num(page_num)
                if not spec:
                    raise RuntimeError(f"Manifest has no page spec for page_num={page_num}")

                target = manifest.output.page_size_px
                bg_key = _layout_bg_key(job_id, page_num)

                if spec.needs_face_swap:
                    bg_uri = f"s3://{settings.S3_BUCKET_NAME}/{bg_key}"
                    bg_img = _s3_read_private_to_pil(bg_uri)
                else:
                    bg_img = _s3_read_private_to_pil(spec.base_uri)
                    if bg_img.size != (target, target):
                        bg_img = bg_img.resize((target, target), Image.Resampling.LANCZOS)

                    bg_uri = _s3_write_pil(bg_img, bg_key, dpi=manifest.output.dpi)
                    await _upsert_artifact(
                        db,
                        job_id=job_id,
                        stage=stage,
                        kind="page_bg_png",
                        s3_uri=bg_uri,
                        page_num=page_num,
                    )

                if spec.text_layers:
                    final_img = await render_text_layers_over_image(
                        bg_img,
                        spec.text_layers,
                        template_vars={
                            "child_name": job.child_name,
                            "child_age": job.child_age,
                            "child_gender": job.child_gender,
                        },
                        output_px=manifest.output.page_size_px,
                        typography=manifest.typography,
                        dpi=manifest.output.dpi,
                        safe_zone_pt=manifest.output.safe_zone_pt,
                    )
                else:
                    final_img = bg_img

                final_key = _layout_final_key(job_id, page_num)
                final_uri = _s3_write_pil(final_img, final_key, dpi=manifest.output.dpi)
                await _upsert_artifact(
                    db,
                    job_id=job_id,
                    stage=stage,
                    kind="page_png",
                    s3_uri=final_uri,
                    page_num=page_num,
                )

            # Render covers (text overlay)
            cover_page_nums = {"front": -1, "back": -2}
            covers_to_render = covers_for_stage(manifest, stage)
            for cover_type, cover_spec in covers_to_render:
                pn = cover_page_nums[cover_type]
                target = manifest.output.page_size_px
                bg_key = _layout_bg_key(job_id, pn)

                if cover_spec.needs_face_swap:
                    bg_uri = f"s3://{settings.S3_BUCKET_NAME}/{bg_key}"
                    bg_img = _s3_read_private_to_pil(bg_uri)
                else:
                    bg_img = _s3_read_private_to_pil(cover_spec.base_uri)
                    if bg_img.size != (target, target):
                        bg_img = bg_img.resize((target, target), Image.Resampling.LANCZOS)
                    bg_uri = _s3_write_pil(bg_img, bg_key, dpi=manifest.output.dpi)
                    await _upsert_artifact(db, job_id=job_id, stage=stage, kind="page_bg_png", s3_uri=bg_uri, page_num=pn)

                if cover_spec.text_layers:
                    cover_typo = (manifest.covers and manifest.covers.typography) or manifest.typography
                    final_img = await render_text_layers_over_image(
                        bg_img,
                        cover_spec.text_layers,
                        template_vars={
                            "child_name": job.child_name,
                            "child_age": job.child_age,
                            "child_gender": job.child_gender,
                        },
                        output_px=manifest.output.page_size_px,
                        typography=cover_typo,
                        dpi=manifest.output.dpi,
                        safe_zone_pt=manifest.output.safe_zone_pt,
                    )
                else:
                    final_img = bg_img

                final_key = _layout_final_key(job_id, pn)
                final_uri = _s3_write_pil(final_img, final_key, dpi=manifest.output.dpi)
                await _upsert_artifact(db, job_id=job_id, stage=stage, kind="page_png", s3_uri=final_uri, page_num=pn)
                logger.info(f"Rendered {cover_type} cover for job {job_id}")

            if stage == "prepay":
                job.status = "prepay_ready"
            else:
                job.status = "completed"
            await db.commit()

    try:
        return asyncio.run(_run())
    except Exception as e:
        logger.error(
            f"Stage render failed for job {job_id}: {e}",
            extra={"job_id": job_id, "stage": stage, "traceback": traceback.format_exc()},
        )
        try:

            async def _mark_failed():
                async with AsyncSessionLocal() as db:
                    job = await _get_job(db, job_id)
                    if job:
                        job.status = "generation_failed"
                        await db.commit()

            asyncio.run(_mark_failed())
        except Exception:
            pass
        raise


@celery_app.task(bind=True, acks_late=True, max_retries=3)
def analyze_photo_task(self, job_id: str, child_photo_uri: str, illustration_id: str, child_gender: str):
    """
    Celery task to analyze child photo (lightweight placeholder).
    """

    async def _run():
        logger.info(f"Starting photo analysis for job: {job_id}")

        async with AsyncSessionLocal() as db:
            job = await _get_job(db, job_id)
            if not job:
                logger.error(f"Job not found in database: {job_id}")
                return

            job.status = "analyzing"
            await db.commit()
            await db.refresh(job)

            try:
                from .config import settings

                if settings.MOCK_ML:
                    logger.info(f"MOCK_ML enabled — skipping Qwen2-VL for job: {job_id}")
                    analysis_result = {
                        "face_detected": True,
                        "gender": child_gender or "girl",
                        "age_estimate": "5-7",
                        "hair_color": "brown",
                        "hair_style": "straight",
                        "skin_tone": "light",
                        "eye_color": "brown",
                    }
                else:
                    from .inference.vision_qwen import analyze_image_pil

                    pil = _s3_read_private_to_pil(job.child_photo_uri)

                    # Analyze with Qwen2-VL
                    logger.info(f"Analyzing photo with Qwen2-VL for job: {job_id}")
                    analysis_result = analyze_image_pil(pil, settings.QWEN_MODEL_ID)

                job.analysis_json = analysis_result

                # # Build prompt from analysis
                # if analysis_result.get("face_detected"):
                #     prompt_parts = ["child portrait"]
                #     if analysis_result.get("gender"):
                #         prompt_parts.append(analysis_result["gender"])
                #     if analysis_result.get("hair_color"):
                #         prompt_parts.append(f"{analysis_result['hair_color']} hair")
                #     if analysis_result.get("hair_style"):
                #         prompt_parts.append(f"{analysis_result['hair_style']} hairstyle")
                #     prompt_parts.append("high quality")
                #     job.common_prompt = ", ".join(prompt_parts)
                # else:
                job.common_prompt = "5 year old girl, long dark brown tightly curly voluminous hair falling past shoulders, dark-black brown expressive eyes, warm olive skin, joyful bright smile showing teeth, sparkling happy eyes, excited expression"

                job.status = "analyzing_completed"
                await db.commit()
                await db.refresh(job)
            except Exception as e:
                logger.error(
                    f"Analysis failed for job {job_id}: {str(e)}",
                    extra={
                        "job_id": job_id,
                        "error": str(e),
                        "traceback": traceback.format_exc(),
                    },
                )
                job.status = "analysis_failed"
                await db.commit()
                raise

    return asyncio.run(_run())


@celery_app.task(bind=True, acks_late=True, max_retries=2)
def generate_image_task(self, job_id: str, child_name: str = None, child_age: int = None):
    """
    Legacy task (kept for backward compatibility).
    """

    async def _run():
        logger.info(f"Starting image generation for job: {job_id}")

        async with AsyncSessionLocal() as db:
            job = await _get_job(db, job_id)
            if not job:
                logger.error(f"Job not found in database: {job_id}")
                return

            if job.status not in ("pending_generation", "generating"):
                logger.warning(f"Job {job_id} not in pending_generation/generating state: {job.status}")
                return

            if job.status != "generating":
                job.status = "generating"
                await db.commit()
                await db.refresh(job)

            try:
                child_pil = _s3_read_private_to_pil(job.child_photo_uri)

                illustrations_path = os.path.join(os.path.dirname(__file__), "illustrations.json")
                with open(illustrations_path, "r", encoding="utf-8") as f:
                    ill_data = json.load(f)
                    illustrations = ill_data.get("illustrations", [])

                preview_res = await db.execute(
                    select(BookPreview).filter(BookPreview.slug == job.slug).order_by(BookPreview.page_index)
                )
                preview_pages_all = preview_res.scalars().all()
                preview_pages = [p for p in preview_pages_all if p.image_url and "/thumbnails/" not in p.image_url]

                required_ill_ids = []
                for p in preview_pages:
                    try:
                        base = os.path.basename(urlparse(p.image_url).path)
                        ill_id, _ext = os.path.splitext(base)
                        if ill_id and ill_id not in required_ill_ids:
                            required_ill_ids.append(ill_id)
                    except Exception:
                        continue

                if not required_ill_ids:
                    required_ill_ids = [i.get("id") for i in illustrations if i.get("id")]

                ill_by_id = {i.get("id"): i for i in illustrations if i.get("id")}

                resolved_child_name = child_name or job.child_name
                resolved_child_age = child_age if child_age is not None else job.child_age
                common_prompt_base = (job.common_prompt or "child portrait").strip(", ")
                personal_bits = []
                if resolved_child_name:
                    personal_bits.append(str(resolved_child_name))
                if resolved_child_age is not None:
                    personal_bits.append(f"{resolved_child_age} years old")
                if job.child_gender:
                    personal_bits.append(job.child_gender)
                if personal_bits:
                    common_prompt = f"{common_prompt_base}, " + ", ".join(personal_bits)
                    common_prompt = common_prompt.strip(", ")
                else:
                    common_prompt = common_prompt_base
                base_negative = "low quality, bad face, distorted"

                saved_results = []
                failed_ids = []
                for ill_id in required_ill_ids:
                    ill = ill_by_id.get(ill_id)
                    if not ill:
                        failed_ids.append(ill_id)
                        continue

                    illustration_uri = ill.get("full_uri") or ill.get("thumbnail_uri")
                    if not illustration_uri:
                        failed_ids.append(ill_id)
                        continue

                    ill_prompt = ill.get("prompt")
                    ill_negative = ill.get("negative_prompt")
                    prompt = f"{ill_prompt}, {common_prompt}" if ill_prompt else common_prompt
                    negative = f"{ill_negative}, {base_negative}" if ill_negative else base_negative

                    try:
                        out_img = _run_face_transfer(child_pil, illustration_uri, prompt, negative)
                        result_key = f"results/{job_id}/{ill_id}.png"
                        s3_uri = _s3_write_pil(out_img, result_key)
                        saved_results.append((ill_id, s3_uri))
                    except Exception:
                        failed_ids.append(ill_id)

                if not saved_results or failed_ids:
                    job.status = "generation_failed"
                    analysis = job.analysis_json or {}
                    analysis["generation_failed_ids"] = failed_ids
                    job.analysis_json = analysis
                    await db.commit()
                    raise RuntimeError(f"Generation incomplete: {failed_ids}")

                job.result_uri = saved_results[0][1]
                job.status = "completed"
                await db.commit()
                await db.refresh(job)

            except Exception as e:
                logger.error(
                    f"Generation failed for job {job_id}: {str(e)}",
                    extra={
                        "job_id": job_id,
                        "error": str(e),
                        "traceback": traceback.format_exc(),
                    },
                )
                job.status = "generation_failed"
                await db.commit()
                raise

    return asyncio.run(_run())

