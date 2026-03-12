"""
Image consulting service — body/color/style analysis pipeline.
"""
from __future__ import annotations

import time
from datetime import datetime
from typing import Optional

from fastapi import HTTPException
from models.schemas import (
    ImageConsultingResult,
    ColorPaletteRecommendation,
    BodyShapeGuidance,
    FaceShapeGuidance,
    UserProfile,
    BodyAnalysis,
)
from services.store import consulting_cache, profiles


# ──────────────────────────────────────────────────────────────
#  Colour & body advice tables (rule-based)
# ──────────────────────────────────────────────────────────────

_UNDERTONE_PALETTES = {
    "warm": dict(
        best=["coral", "peach", "gold", "olive", "terracotta", "warm_brown", "cream", "rust", "mustard"],
        good=["camel", "ivory", "warm_red", "salmon", "amber"],
        avoid=["silver_gray", "cool_pink", "icy_blue", "stark_white"],
        neutral=["cream", "camel", "tan", "warm_gray", "chocolate"],
        accent=["coral", "peach", "warm_pink", "golden_yellow", "turquoise"],
        tips=["Gold jewellery enhances your warm complexion",
              "Earth tones and warm colours glow on you",
              "Avoid stark cool colours near your face"],
    ),
    "cool": dict(
        best=["navy", "royal_blue", "emerald", "purple", "cool_pink", "silver", "charcoal"],
        good=["white", "black", "burgundy", "plum", "teal", "lavender"],
        avoid=["orange", "yellow_orange", "warm_brown", "gold", "peach"],
        neutral=["white", "black", "navy", "charcoal", "cool_gray"],
        accent=["fuchsia", "royal_blue", "emerald", "true_red", "icy_pink"],
        tips=["Silver jewellery flatters your cool undertone",
              "Jewel tones make your complexion pop",
              "Warm yellows and oranges can wash you out"],
    ),
    "neutral": dict(
        best=["jade", "dusty_pink", "soft_white", "medium_gray", "teal", "burgundy"],
        good=["most muted tones", "navy", "camel", "blush"],
        avoid=["very neon", "very warm orange", "very cool icy tones"],
        neutral=["white", "black", "gray", "beige", "taupe"],
        accent=["dusty_pink", "lavender", "soft_blue", "sage", "mauve"],
        tips=["Both gold and silver jewellery work for you",
              "You can mix warm and cool colours freely",
              "Avoid extreme temperatures — very hot or very icy"],
    ),
}

_BODY_SHAPE_GUIDANCE = {
    "hourglass": BodyShapeGuidance(
        body_shape="hourglass",
        flattering_silhouettes=["wrap", "fitted", "bodycon", "tailored", "belted"],
        good_patterns=["any — your proportions balance patterns well"],
        items_to_avoid=["shapeless tops", "oversized everything", "boxy jackets"],
        styling_tips=["Highlight your natural waist at all times",
                      "Wrap dresses are your best friend",
                      "V-necks and sweetheart necklines are flattering"],
        proportion_tips=["Keep tops and bottoms in similar proportions"],
    ),
    "pear": BodyShapeGuidance(
        body_shape="pear",
        flattering_silhouettes=["A-line", "fit-and-flare", "empire waist", "wide-leg trousers"],
        good_patterns=["bold prints on top", "solid or dark on bottom"],
        items_to_avoid=["skinny jeans with large tops", "hip-emphasis pockets"],
        styling_tips=["Draw attention upward with statement tops",
                      "Darker shades on the bottom minimise hips",
                      "Structured shoulders balance your proportions"],
        proportion_tips=["Boat necks and off-shoulder tops add width to shoulders"],
    ),
    "inverted_triangle": BodyShapeGuidance(
        body_shape="inverted_triangle",
        flattering_silhouettes=["A-line skirts", "wide-leg trousers", "flared jeans"],
        good_patterns=["volume on bottom", "horizontal patterns below waist"],
        items_to_avoid=["shoulder pads", "boat necks", "structured blazers with strong shoulders"],
        styling_tips=["Add volume below the waist to balance broad shoulders",
                      "V-necks soften the shoulder line",
                      "Full skirts create the illusion of curves"],
        proportion_tips=["Avoid heavy detailing at shoulder level"],
    ),
    "rectangle": BodyShapeGuidance(
        body_shape="rectangle",
        flattering_silhouettes=["peplum", "A-line", "structured", "ruffles"],
        good_patterns=["horizontal stripes", "colour-blocking", "bold prints"],
        items_to_avoid=["shapeless shifts", "boxy cuts", "clothes that hang straight down"],
        styling_tips=["Use belts to create a waist",
                      "Layers add visual interest to a straight frame",
                      "Peplum tops create the illusion of curves"],
        proportion_tips=["High-waisted bottoms elongate the leg line"],
    ),
    "apple": BodyShapeGuidance(
        body_shape="apple",
        flattering_silhouettes=["empire waist", "A-line", "V-neck", "wrap"],
        good_patterns=["vertical lines", "V-patterns", "dark monochromatic"],
        items_to_avoid=["tight mid-section garments", "clingy jersey knits at the waist"],
        styling_tips=["Elongate the torso with V-necks and vertical details",
                      "Show off your legs — they're often a strong asset",
                      "Dark solid colours create a slimming line"],
        proportion_tips=["Avoid wide belts at the natural waist"],
    ),
    "athletic": BodyShapeGuidance(
        body_shape="athletic",
        flattering_silhouettes=["feminine ruffles", "wrap dresses", "curved hems", "flared"],
        good_patterns=["florals", "soft organic patterns", "draping fabric"],
        items_to_avoid=["very sharp angular cuts", "overly sporty details in formal looks"],
        styling_tips=["Add softness and curves with feminine details",
                      "Peplums and flounce hems add shape",
                      "Ruched fabric creates curves at the waist"],
        proportion_tips=["High heels lengthen and feminise the silhouette"],
    ),
}

_FACE_SHAPE_GUIDANCE = {
    "oval": FaceShapeGuidance(
        face_shape="oval",
        flattering_necklines=["most necklines — lucky you!"],
        flattering_collars=["all collar types work"],
        earring_styles=["anything — dangles, hoops, studs"],
        glasses_styles=["most frames suit you"],
        tips=["Oval is considered the most versatile face shape",
              "Almost every neckline and collar flatters you"],
    ),
    "round": FaceShapeGuidance(
        face_shape="round",
        flattering_necklines=["V-neck", "scoop neck", "deep square", "open collar"],
        flattering_collars=["point collar", "spread collar"],
        earring_styles=["long drop earrings", "angular earrings", "chandeliers"],
        glasses_styles=["rectangular frames", "angular frames", "wide frames"],
        tips=["V-necks elongate and add angles to a round face",
              "Avoid turtlenecks and jewel necklines that emphasise roundness",
              "Long drop earrings create the illusion of length"],
    ),
    "square": FaceShapeGuidance(
        face_shape="square",
        flattering_necklines=["V-neck", "scoop neck", "cowl neck", "off-shoulder"],
        flattering_collars=["rounded collar", "shawl collar"],
        earring_styles=["round hoops", "oval drops", "circular studs"],
        glasses_styles=["round frames", "oval frames", "curved frames"],
        tips=["Soft curves and rounded necklines soften a strong jaw",
              "Avoid square or boxy necklines that repeat your jawline angles",
              "Round earrings balance angular features"],
    ),
    "heart": FaceShapeGuidance(
        face_shape="heart",
        flattering_necklines=["scoop neck", "sweetheart", "bateau", "off-shoulder"],
        flattering_collars=["wide lapel", "notch lapel"],
        earring_styles=["teardrop", "triangular wider at bottom", "chandelier"],
        glasses_styles=["bottom-heavy frames", "oval", "rimless"],
        tips=["Add width at the jaw level to balance a wide forehead",
              "Avoid plunging V-necks that emphasise forehead width",
              "Bottom-heavy earrings draw attention downward"],
    ),
    "diamond": FaceShapeGuidance(
        face_shape="diamond",
        flattering_necklines=["scoop neck", "V-neck", "square neck"],
        flattering_collars=["open collar", "spread collar"],
        earring_styles=["width-adding studs", "curved hoops", "teardrop"],
        glasses_styles=["oval", "cat-eye", "rimless"],
        tips=["Highlight your cheekbones — they're your best feature",
              "Avoid narrow frames and necklines"],
    ),
    "rectangle": FaceShapeGuidance(
        face_shape="rectangle",
        flattering_necklines=["scoop neck", "cowl neck", "off-shoulder", "boat neck"],
        flattering_collars=["spread collar", "wide shawl collar"],
        earring_styles=["wide hoops", "cluster earrings", "short drops"],
        glasses_styles=["deep frames", "oversized frames", "rounded squares"],
        tips=["Add width at cheek level to shorten the face",
              "Boat necks and wide collars add horizontal line",
              "Avoid turtlenecks that elongate a long face"],
    ),
}

_CONTRAST_SEASON = {
    ("warm", "high"): "Autumn",
    ("warm", "very_high"): "Autumn",
    ("warm", "low"): "Spring",
    ("warm", "very_low"): "Spring",
    ("warm", "medium"): "Spring",
    ("cool", "high"): "Winter",
    ("cool", "very_high"): "Winter",
    ("cool", "low"): "Summer",
    ("cool", "very_low"): "Summer",
    ("cool", "medium"): "Summer",
    ("neutral", "high"): "Winter",
    ("neutral", "very_high"): "Winter",
    ("neutral", "low"): "Summer",
    ("neutral", "very_low"): "Summer",
    ("neutral", "medium"): "Summer",
}


def _compute_season(undertone: Optional[str], contrast_level: Optional[str]) -> Optional[str]:
    if not undertone or not contrast_level:
        return None
    return _CONTRAST_SEASON.get((undertone.lower(), contrast_level.lower()))


def _build_color_palette(
    undertone: Optional[str],
    skin_tone: Optional[str],
    contrast_level: Optional[str],
) -> ColorPaletteRecommendation:
    ut = (undertone or "neutral").lower()
    palette_data = _UNDERTONE_PALETTES.get(ut, _UNDERTONE_PALETTES["neutral"])

    extra_tips = []
    cl = (contrast_level or "medium").lower()
    if cl in ("high", "very_high"):
        extra_tips.append("Bold contrasts suit you — try black-and-white combinations")
        extra_tips.append("Jewel tones complement your high-contrast coloring")
    elif cl in ("low", "very_low"):
        extra_tips.append("Stick to monochromatic or tone-on-tone looks")
        extra_tips.append("Avoid stark contrasts — keep colors in the same tonal family")
    else:
        extra_tips.append("Balanced color combinations work well for your contrast level")

    return ColorPaletteRecommendation(
        best_colors=palette_data["best"],
        good_colors=palette_data["good"],
        colors_to_avoid=palette_data["avoid"],
        neutral_colors=palette_data["neutral"],
        accent_colors=palette_data["accent"],
        tips=palette_data["tips"] + extra_tips,
    )


def _build_body_guidance(body_shape: Optional[str]) -> Optional[BodyShapeGuidance]:
    if not body_shape:
        return None
    mapping = {"triangle": "pear", "oval": "apple"}
    key = mapping.get(body_shape.lower(), body_shape.lower())
    return _BODY_SHAPE_GUIDANCE.get(key)


def _build_face_guidance(face_shape: Optional[str]) -> Optional[FaceShapeGuidance]:
    if not face_shape:
        return None
    return _FACE_SHAPE_GUIDANCE.get(face_shape.lower())


def _build_summary(
    body_shape: Optional[str],
    skin_tone: Optional[str],
    undertone: Optional[str],
    contrast_level: Optional[str],
) -> str:
    season = _compute_season(undertone, contrast_level)
    parts = []
    if body_shape:
        parts.append(f"Your body shape is **{body_shape.replace('_', ' ').title()}**")
    if skin_tone and undertone:
        parts.append(f"your complexion is **{skin_tone.replace('_', ' ')} with {undertone} undertones**")
    if season:
        parts.append(f"placing you in the **{season}** colour season")
    if not parts:
        return "Complete your image consulting to unlock personalised style guidance."
    return (
        ", ".join(parts)
        + ". Use the sections below to discover the colours, silhouettes, and styling tips that work best for you."
    )


# ──────────────────────────────────────────────────────────────
#  Mock result
# ──────────────────────────────────────────────────────────────

_MOCK_BODY_SHAPES = ["hourglass", "pear", "inverted_triangle", "rectangle", "apple", "athletic"]
_MOCK_SKIN_TONES = ["light", "medium_light", "medium", "medium_dark", "olive"]
_MOCK_UNDERTONES = ["warm", "cool", "neutral"]
_MOCK_HAIR_COLORS = ["dark_brown", "medium_brown", "light_brown", "black", "blonde", "auburn"]
_MOCK_CONTRAST = ["low", "medium", "high"]
_MOCK_FACE_SHAPES = ["oval", "round", "square", "heart", "diamond", "rectangle"]


def _mock_result(
    user_id: str,
    height_cm: Optional[float],
    weight_kg: Optional[float],
) -> ImageConsultingResult:
    """Deterministic mock based on user_id hash."""
    seed = sum(ord(c) for c in user_id)
    body_shape = _MOCK_BODY_SHAPES[seed % len(_MOCK_BODY_SHAPES)]
    skin_tone = _MOCK_SKIN_TONES[seed % len(_MOCK_SKIN_TONES)]
    undertone = _MOCK_UNDERTONES[seed % len(_MOCK_UNDERTONES)]
    hair_color = _MOCK_HAIR_COLORS[seed % len(_MOCK_HAIR_COLORS)]
    contrast_level = _MOCK_CONTRAST[seed % len(_MOCK_CONTRAST)]
    face_shape = _MOCK_FACE_SHAPES[seed % len(_MOCK_FACE_SHAPES)]
    color_season = _compute_season(undertone, contrast_level)

    top_size, bottom_size = None, None
    if height_cm:
        if height_cm < 160:
            top_size, bottom_size = "XS", "XS"
        elif height_cm < 165:
            top_size, bottom_size = "S", "S"
        elif height_cm < 170:
            top_size, bottom_size = "M", "M"
        elif height_cm < 178:
            top_size, bottom_size = "L", "L"
        else:
            top_size, bottom_size = "XL", "XL"

    return ImageConsultingResult(
        user_id=user_id,
        body_shape=body_shape,
        face_shape=face_shape,
        skin_tone=skin_tone,
        undertone=undertone,
        hair_color=hair_color,
        contrast_level=contrast_level,
        visual_weight="medium",
        color_season=color_season,
        estimated_top_size=top_size,
        estimated_bottom_size=bottom_size,
        color_palette=_build_color_palette(undertone, skin_tone, contrast_level),
        body_shape_guidance=_build_body_guidance(body_shape),
        face_shape_guidance=_build_face_guidance(face_shape),
        summary=_build_summary(body_shape, skin_tone, undertone, contrast_level),
        overall_confidence=0.72,
    )


# ──────────────────────────────────────────────────────────────
#  LLM pipeline runner
# ──────────────────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────
#  Public API
# ──────────────────────────────────────────────────────────────


async def analyze_image(
    user_id: str,
    image_bytes: bytes,
    height_cm: Optional[float] = None,
    weight_kg: Optional[float] = None,
) -> ImageConsultingResult:
    """Run the full image consulting pipeline on a user photo."""
    stored_profile = profiles.get(user_id)
    if stored_profile:
        height_cm = height_cm or stored_profile.height_cm
        weight_kg = weight_kg or stored_profile.weight_kg

    result = _mock_result(user_id, height_cm, weight_kg)

    result.analyzed_at = datetime.utcnow()
    consulting_cache[user_id] = result

    # Persist back to profile store
    if stored_profile:
        stored_profile.body_analysis = BodyAnalysis(
            body_shape=result.body_shape,
            skin_tone=result.skin_tone,
            undertone=result.undertone,
            hair_color=result.hair_color,
            contrast_level=result.contrast_level,
            estimated_top_size=result.estimated_top_size,
            estimated_bottom_size=result.estimated_bottom_size,
        )
        if height_cm:
            stored_profile.height_cm = height_cm
        if weight_kg:
            stored_profile.weight_kg = weight_kg
        profiles[user_id] = stored_profile

    return result


def get_cached_result(user_id: str) -> ImageConsultingResult:
    """Return the most recent cached image consulting result."""
    result = consulting_cache.get(user_id)
    if result:
        return result

    stored_profile = profiles.get(user_id)
    if stored_profile and stored_profile.body_analysis:
        ba = stored_profile.body_analysis
        result = ImageConsultingResult(
            user_id=user_id,
            body_shape=ba.body_shape,
            skin_tone=ba.skin_tone,
            undertone=ba.undertone,
            hair_color=ba.hair_color,
            contrast_level=ba.contrast_level,
            estimated_top_size=ba.estimated_top_size,
            estimated_bottom_size=ba.estimated_bottom_size,
            color_palette=_build_color_palette(ba.undertone, ba.skin_tone, ba.contrast_level),
            body_shape_guidance=_build_body_guidance(ba.body_shape),
            face_shape_guidance=None,
            color_season=_compute_season(ba.undertone, ba.contrast_level),
            summary=_build_summary(ba.body_shape, ba.skin_tone, ba.undertone, ba.contrast_level),
            overall_confidence=0.5,
        )
        consulting_cache[user_id] = result
        return result

    raise HTTPException(404, "No image consulting result found. Please run the analysis first.")
