"""
Try-on service — virtual try-on via LLM_project Layer 6 (CatVTON / Replicate).

Delegates all AI work to the LLM_project API through llm_client.
Falls back to a placeholder response if the LLM project is unreachable.
"""
import time
import logging

from models.schemas import TryOnRequest, TryOnResponse
from services import llm_client as _llm

logger = logging.getLogger(__name__)


def virtual_tryon(req: TryOnRequest) -> TryOnResponse:
    """
    Virtual try-on endpoint.

    Sends the person photo + garment image(s) to LLM_project Layer 6
    (CatVTON or Replicate backend) via the pipeline endpoint.

    Falls back gracefully (placeholder response) if:
    - The LLM project is unreachable
    - No garment images are available (garment_ids only, no bytes)
    """
    start = time.time()

    # The mobile client sends garment_ids and an optional user_photo_url.
    # We currently support a base64 person photo via user_photo_url if it is
    # a data-URI (data:image/jpeg;base64,...) or a raw b64 string.
    # In production this would fetch the garment images from the DB.
    person_b64: str | None = None
    if req.user_photo_url and req.user_photo_url.startswith("data:"):
        # Strip the data-URI prefix
        header, _, person_b64 = req.user_photo_url.partition(",")
    elif req.user_photo_url:
        person_b64 = req.user_photo_url  # assume raw b64

    # For now we need actual image bytes for the garments.
    # Without fetching from storage, we skip and return a placeholder.
    if not person_b64:
        logger.info("No person photo provided — returning placeholder try-on response")
        elapsed = (time.time() - start) * 1000
        return TryOnResponse(
            result_image_url=None,
            result_image_b64=None,
            processing_time_ms=round(elapsed, 1),
        )

    import base64
    try:
        person_bytes = base64.b64decode(person_b64)
    except Exception:
        person_bytes = None

    if not person_bytes:
        elapsed = (time.time() - start) * 1000
        return TryOnResponse(result_image_url=None, result_image_b64=None,
                             processing_time_ms=round(elapsed, 1))

    # Use the person image as a proxy for the garment image too when no
    # actual garment image bytes are available (V1 limitation).
    result = _llm.virtual_tryon(
        person_image_bytes=person_bytes,
        garment_image_bytes=person_bytes,  # placeholder until garment fetch is wired up
        backend=req.backend,
    )

    elapsed = (time.time() - start) * 1000

    if result and result.get("result_image_b64"):
        return TryOnResponse(
            result_image_url=None,
            result_image_b64=result["result_image_b64"],
            processing_time_ms=round(result.get("processing_time_ms") or elapsed, 1),
        )

    # Fallback — LLM project unreachable or returned no image
    logger.warning("Try-on returned no image — LLM project unreachable or backend not ready")
    return TryOnResponse(
        result_image_url=None,
        result_image_b64=None,
        processing_time_ms=round(elapsed, 1),
    )
