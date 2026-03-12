"""
Try-on service — virtual try-on logic.
"""
import time

from models.schemas import TryOnRequest, TryOnResponse


def virtual_tryon(req: TryOnRequest) -> TryOnResponse:
    """
    Virtual try-on.
    V1: Returns placeholder. Production: calls Layer 6 CatVTON/Replicate.
    """
    start = time.time()
    elapsed = (time.time() - start) * 1000

    return TryOnResponse(
        result_image_url=None,
        result_image_b64=None,
        processing_time_ms=round(elapsed, 1),
    )
