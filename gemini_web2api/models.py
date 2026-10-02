"""Model definitions and mapping from Gemini frontend JS source."""

# MODE_CATEGORY enum from 028-6eb337387583.js:
#   1=FAST, 2=THINKING, 3=PRO, 4=AUTO, 5=FAST_DYNAMIC_THINKING, 6=FLASH_LITE

MODELS = {
    # Gemini 3.8
    "gemini-3.8-flash": {
        "mode": 1, "think": 4,
        "desc": "Latest all-around model (Gemini 3.8 Flash)",
    },
    "gemini-3.8-flash-high": {
        "mode": 2, "think": 0,
        "desc": "Latest all-around model with high thinking depth (Gemini 3.8 Flash High, ~20k chars)",
    },
    "gemini-3.8-flash-thinking": {
        "mode": 2, "think": 0,
        "desc": "Deep thinking mode (Gemini 3.8 Flash), longest output (~20k chars)",
    },
    "gemini-3.8-flash-thinking@think=0": {
        "mode": 2, "think": 0,
        "desc": "Gemini 3.8 Flash thinking mode - deepest reasoning (~20k chars)",
    },
    "gemini-3.8-flash-thinking@think=2": {
        "mode": 2, "think": 2,
        "desc": "Gemini 3.8 Flash thinking mode - medium reasoning (~15k chars)",
    },
    "gemini-3.8-flash-thinking@think=4": {
        "mode": 2, "think": 4,
        "desc": "Gemini 3.8 Flash thinking mode - shallowest reasoning (~12k chars)",
    },
    "gemini-3.8-flash@think=0": {
        "mode": 1, "think": 0,
        "desc": "Gemini 3.8 Flash with deep thinking (think=0)",
    },

    # Gemini 3.7
    "gemini-3.7-flash": {
        "mode": 1, "think": 4,
        "desc": "All-around model (Gemini 3.7 Flash)",
    },
    "gemini-3.7-flash-high": {
        "mode": 2, "think": 0,
        "desc": "Gemini 3.7 Flash with high thinking depth (~20k chars)",
    },
    "gemini-3.7-flash-thinking": {
        "mode": 2, "think": 0,
        "desc": "Deep thinking mode (Gemini 3.7 Flash), longest output (~20k chars)",
    },
    "gemini-3.7-flash-thinking@think=0": {
        "mode": 2, "think": 0,
        "desc": "Gemini 3.7 Flash thinking mode - deepest reasoning (~20k chars)",
    },
    "gemini-3.7-flash-thinking@think=2": {
        "mode": 2, "think": 2,
        "desc": "Gemini 3.7 Flash thinking mode - medium reasoning (~15k chars)",
    },
    "gemini-3.7-flash-thinking@think=4": {
        "mode": 2, "think": 4,
        "desc": "Gemini 3.7 Flash thinking mode - shallowest reasoning (~12k chars)",
    },
    "gemini-3.7-flash@think=0": {
        "mode": 1, "think": 0,
        "desc": "Gemini 3.7 Flash with deep thinking (think=0)",
    },

    # Gemini 3.6
    "gemini-3.6-flash": {
        "mode": 1, "think": 4,
        "desc": "All-around model (Gemini 3.6 Flash)",
    },
    "gemini-3.6-flash-high": {
        "mode": 2, "think": 0,
        "desc": "Gemini 3.6 Flash with high thinking depth (~20k chars)",
    },
    "gemini-3.6-flash-thinking": {
        "mode": 2, "think": 0,
        "desc": "Deep thinking mode (Gemini 3.6 Flash), longest output (~20k chars)",
    },
    "gemini-3.6-flash-thinking@think=0": {
        "mode": 2, "think": 0,
        "desc": "Gemini 3.6 Flash thinking mode - deepest reasoning (~20k chars)",
    },
    "gemini-3.6-flash-thinking@think=2": {
        "mode": 2, "think": 2,
        "desc": "Gemini 3.6 Flash thinking mode - medium reasoning (~15k chars)",
    },
    "gemini-3.6-flash-thinking@think=4": {
        "mode": 2, "think": 4,
        "desc": "Gemini 3.6 Flash thinking mode - shallowest reasoning (~12k chars)",
    },
    "gemini-3.6-flash@think=0": {
        "mode": 1, "think": 0,
        "desc": "Gemini 3.6 Flash with deep thinking (think=0)",
    },

    # Gemini 3.5
    "gemini-3.5-flash": {
        "mode": 1, "think": 4,
        "desc": "Alias for gemini-3.6-flash (backend upgraded)",
    },
    "gemini-3.5-flash-high": {
        "mode": 2, "think": 0,
        "desc": "Gemini 3.5 Flash with high thinking depth (~20k chars)",
    },
    "gemini-3.5-flash-thinking": {
        "mode": 2, "think": 0,
        "desc": "Deep thinking mode, longest output (~20k chars)",
    },
    "gemini-3.5-flash-thinking@think=0": {
        "mode": 2, "think": 0,
        "desc": "Gemini 3.5 Flash thinking mode - deepest reasoning (~20k chars)",
    },
    "gemini-3.5-flash-thinking@think=2": {
        "mode": 2, "think": 2,
        "desc": "Gemini 3.5 Flash thinking mode - medium reasoning (~15k chars)",
    },
    "gemini-3.5-flash-thinking@think=4": {
        "mode": 2, "think": 4,
        "desc": "Gemini 3.5 Flash thinking mode - shallowest reasoning (~12k chars)",
    },
    "gemini-3.5-flash@think=0": {
        "mode": 1, "think": 0,
        "desc": "Gemini 3.5 Flash with deep thinking (think=0)",
    },

    # Pro / Auto / Other
    "gemini-3.1-pro": {
        "mode": 3, "think": 4,
        "desc": "Pro model (requires cookie for real routing)",
    },
    "gemini-3.1-pro-enhanced": {
        "mode": 3, "think": 4, "extra": {31: 2, 80: 3},
        "desc": "Pro with enhanced output (experimental)",
    },
    "gemini-auto": {
        "mode": 4, "think": 4,
        "desc": "Auto model selection",
    },
    "gemini-3.5-flash-thinking-lite": {
        "mode": 5, "think": 0,
        "desc": "Dynamic thinking with adaptive depth",
    },
    "gemini-flash-lite": {
        "mode": 6, "think": 4,
        "desc": "Lightweight fast model",
    },
}

MODEL_ALIASES = {
    # 3.8 aliases
    "gemini-3.8-flash:high": "gemini-3.8-flash-high",
    "gemini-3.8-flash (high)": "gemini-3.8-flash-high",
    "gemini-3.8-flash-thinking-high": "gemini-3.8-flash-high",
    "gemini-3.8-flash-thinking-medium": "gemini-3.8-flash-thinking@think=2",
    "gemini-3.8-flash-thinking-low": "gemini-3.8-flash-thinking@think=4",
    "3.8-flash-high": "gemini-3.8-flash-high",
    "3.8 flash high": "gemini-3.8-flash-high",
    "3.8-flash": "gemini-3.8-flash",
    "3.8-flash-thinking": "gemini-3.8-flash-thinking",

    # 3.7 aliases
    "gemini-3.7-flash:high": "gemini-3.7-flash-high",
    "gemini-3.7-flash (high)": "gemini-3.7-flash-high",
    "gemini-3.7-flash-thinking-high": "gemini-3.7-flash-high",
    "gemini-3.7-flash-thinking-medium": "gemini-3.7-flash-thinking@think=2",
    "gemini-3.7-flash-thinking-low": "gemini-3.7-flash-thinking@think=4",
    "3.7-flash-high": "gemini-3.7-flash-high",
    "3.7 flash high": "gemini-3.7-flash-high",
    "3.7-flash": "gemini-3.7-flash",
    "3.7-flash-thinking": "gemini-3.7-flash-thinking",

    # 3.6 aliases
    "gemini-3.6-flash:high": "gemini-3.6-flash-high",
    "gemini-3.6-flash (high)": "gemini-3.6-flash-high",
    "gemini-3.6-flash-thinking-high": "gemini-3.6-flash-high",
    "gemini-3.6-flash-thinking-medium": "gemini-3.6-flash-thinking@think=2",
    "gemini-3.6-flash-thinking-low": "gemini-3.6-flash-thinking@think=4",
    "3.6-flash-high": "gemini-3.6-flash-high",
    "3.6 flash high": "gemini-3.6-flash-high",
    "3.6-flash": "gemini-3.6-flash",
    "3.6-flash-thinking": "gemini-3.6-flash-thinking",

    # 3.5 aliases
    "gemini-3.5-flash:high": "gemini-3.5-flash-high",
    "gemini-3.5-flash (high)": "gemini-3.5-flash-high",
    "gemini-3.5-flash-thinking-high": "gemini-3.5-flash-high",
    "gemini-3.5-flash-thinking-medium": "gemini-3.5-flash-thinking@think=2",
    "gemini-3.5-flash-thinking-low": "gemini-3.5-flash-thinking@think=4",
    "3.5-flash-high": "gemini-3.5-flash-high",
    "3.5 flash high": "gemini-3.5-flash-high",
    "3.5-flash": "gemini-3.5-flash",
}


def resolve_model(model_name: str, default: str = "gemini-3.6-flash"):
    """Resolve model name to (name, mode_id, think_mode, error, extra_fields).

    Unknown model names fall back to default rather than erroring,
    since upstream clients may request arbitrary model identifiers.
    """
    original_name = model_name
    think_override = None
    if "@think=" in model_name:
        base_name, think_str = model_name.rsplit("@think=", 1)
        try:
            think_override = int(think_str)
            model_name = base_name
        except ValueError:
            return None, None, None, f"Invalid think level: {think_str}", None

    resolved_name = model_name
    if resolved_name not in MODELS:
        resolved_name = MODEL_ALIASES.get(model_name.lower().strip(), model_name)

    cfg = MODELS.get(original_name) or MODELS.get(resolved_name)
    if not cfg:
        from .gemini import log
        log(f"Unknown model '{original_name}', falling back to '{default}'")
        resolved_name = default
        cfg = MODELS[default]
    else:
        if original_name in MODELS or "@think=" in original_name:
            resolved_name = original_name

    mode_id = cfg["mode"]
    think_mode = think_override if think_override is not None else cfg["think"]
    extra = cfg.get("extra")
    return resolved_name, mode_id, think_mode, None, extra
