"""
Explain service — rule-based outfit explanations.
"""
from __future__ import annotations

import random
from typing import List, Optional

from models.schemas import (
    ExplainOutfitRequest,
    ExplainOutfitResponse,
    OutfitResult,
)


# ─── Templates ───────────────────────────────────────────────

DETAILED_TEMPLATES = [
    (
        "This outfit achieves a {score}% overall score driven largely by its {top_dim} ({top_pct}%). "
        "The {color_palette} palette follows the three-colour rule, ensuring visual coherence without "
        "overwhelming the eye. Formality levels are consistent across all pieces, making the "
        "combination naturally versatile for {occasion} settings. The proportions balance a "
        "structured upper half with a relaxed lower half — a timeless silhouette formula. "
        "For your chosen {profile} style profile, these pieces complement each other with "
        "minimal effort required."
    ),
    (
        "Scoring {score}%, this look excels in {top_dim} and benefits from a thoughtful "
        "{color_palette} colour story. Each garment shares a compatible formality register, "
        "which is the foundation of effortless dressing. The outfit is well-suited to "
        "{occasion} occasions and reflects the {profile} aesthetic clearly. A few deliberate "
        "styling choices — such as tucking the top or adding a minimal accessory — would "
        "elevate it further."
    ),
    (
        "At {score}% overall, the standout dimension here is {top_dim} ({top_pct}%). The "
        "{color_palette} palette creates a sense of editorial calm while maintaining "
        "visual interest. The garment proportions follow a balanced silhouette with no "
        "single piece dominating the look. For {occasion} contexts the outfit reads as "
        "intentional and put-together — exactly what the {profile} profile prioritises."
    ),
]

COLOR_NOTES = {
    "monochromatic": "A monochromatic {color} palette keeps the look cohesive and modern.",
    "two_tone": "The clean {c1} and {c2} pairing creates strong visual contrast without clashing.",
    "multi": "A multi-colour palette of {colors} — balanced by the neutral base tone.",
}

OCCASION_NOTES = {
    "excellent": "Excellent match for {occasion} occasions — all formality cues align perfectly.",
    "good": "Solid choice for {occasion} settings; minor adjustments could sharpen the fit.",
    "fair": "Passable for {occasion} — consider swapping the shoes for a closer formality match.",
}

STYLE_NOTE_TEMPLATES = [
    "Consistent silhouette across all pieces — a cohesive, editorial statement.",
    "Complementary textures add depth without breaking the clean aesthetic.",
    "Formality levels are well-matched — no single item pulls focus negatively.",
    "Proportions follow a classic balanced silhouette: structured top, relaxed bottom.",
    "The colour temperature of all pieces sits in the same warm/cool family.",
    "Pattern usage is minimal — the solid palette lets fit and fabric speak.",
]

STYLING_TIPS = [
    "Half-tuck the shirt for a more relaxed, editorial feel.",
    "A thin leather belt would anchor the outfit and add polish.",
    "Swap the footwear for a minimalist loafer to elevate formality one notch.",
    "Roll the trouser hem one cuff to expose the ankle and lighten the silhouette.",
    "Add a single metal-toned accessory (watch or ring) as a subtle focal point.",
    "Layer a relaxed blazer over the top to adapt the look for business settings.",
]


def _dim_label(key: str) -> str:
    return {
        "color_harmony": "Color Harmony",
        "formality_match": "Formality",
        "occasion_fit": "Occasion Fit",
        "pattern_mixing": "Pattern Mixing",
        "proportion": "Proportion",
        "season_fit": "Season Fit",
        "creativity": "Creativity",
    }.get(key, key)


def _build_explanation(
    outfit: OutfitResult,
    occasion: Optional[str],
    scoring_profile: Optional[str],
    detail_level: str,
) -> ExplainOutfitResponse:
    """Generate a rich, rule-based explanation for the outfit."""
    score_dict = outfit.score.model_dump()
    overall = outfit.score.overall

    dims = {k: v for k, v in score_dict.items() if k != "overall"}
    top_dim_key = max(dims, key=dims.get)
    top_pct = round(dims[top_dim_key] * 100)

    colors = list({
        g.attributes.color_primary
        for g in outfit.garments
        if g.attributes.color_primary
    })
    if len(colors) == 1:
        color_palette = f"monochromatic {colors[0]}"
        color_note = COLOR_NOTES["monochromatic"].format(color=colors[0].title())
    elif len(colors) == 2:
        color_palette = f"{colors[0]}-and-{colors[1]}"
        color_note = COLOR_NOTES["two_tone"].format(c1=colors[0].title(), c2=colors[1].title())
    else:
        color_palette = ", ".join(c.title() for c in colors[:3])
        color_note = COLOR_NOTES["multi"].format(colors=color_palette)

    occ_score = outfit.score.occasion_fit
    occ_label = occasion or "general"
    if occ_score >= 0.80:
        occasion_note = OCCASION_NOTES["excellent"].format(occasion=occ_label)
    elif occ_score >= 0.60:
        occasion_note = OCCASION_NOTES["good"].format(occasion=occ_label)
    else:
        occasion_note = OCCASION_NOTES["fair"].format(occasion=occ_label)

    template = random.choice(DETAILED_TEMPLATES)
    detailed = template.format(
        score=round(overall * 100),
        top_dim=_dim_label(top_dim_key),
        top_pct=top_pct,
        color_palette=color_palette,
        occasion=occ_label,
        profile=scoring_profile or "balanced",
    )

    style_notes = random.sample(STYLE_NOTE_TEMPLATES, k=min(4, len(STYLE_NOTE_TEMPLATES)))

    styling_tips: List[str] = []
    if detail_level == "detailed":
        styling_tips = random.sample(STYLING_TIPS, k=min(3, len(STYLING_TIPS)))

    return ExplainOutfitResponse(
        outfit_id=outfit.id,
        detailed=detailed,
        style_notes=style_notes,
        color_note=color_note,
        occasion_note=occasion_note,
        styling_tips=styling_tips,
    )


async def explain_outfit(request: ExplainOutfitRequest) -> ExplainOutfitResponse:
    """Generate a detailed explanation for a single outfit using rule-based logic."""
    occasion_str = request.occasion.value if request.occasion else None
    profile_str = request.scoring_profile.value if request.scoring_profile else "default"

    return _build_explanation(
        outfit=request.outfit,
        occasion=occasion_str,
        scoring_profile=profile_str,
        detail_level=request.detail_level,
    )
