"""Model definitions and mapping from Gemini frontend JS source."""

# MODE_CATEGORY enum from 028-6eb337387583.js:
#   1=FAST, 2=THINKING, 3=PRO, 4=AUTO, 5=FAST_DYNAMIC_THINKING, 6=FLASH_LITE

MODELS = {
    "gemini-3.8-flash": {
        "mode": 1, "think": 4,
        "desc": "Latest all-around model (Gemini 3.8 Flash)",
    },
    "gemini-3.8": {
        "mode": 1, "think": 4,
        "desc": "Latest all-around model (Gemini 3.8)",
    },
    "gemini-3.7-flash": {
        "mode": 1, "think": 4,
        "desc": "Latest all-around model (Gemini 3.7 Flash)",
    },
    "gemini-3.6-flash": {
        "mode": 1, "think": 4,
        "desc": "All-around model (Gemini 3.6 Flash)",
    },
    "gemini-3.5-flash": {
        "mode": 1, "think": 4,
        "desc": "Alias for gemini-3.6-flash (backend upgraded)",
    },
    "gemini-3.5-flash-thinking": {
        "mode": 2, "think": 0,
        "desc": "Deep thinking mode, longest output (~20k chars)",
    },
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

# Client-facing aliases (OpenCode / OpenAI-compatible clients) → canonical MODELS keys.
# Do not remove or replace entries in MODELS; aliases only remap request names.
MODEL_ALIASES = {
    "gemini-pro": "gemini-3.1-pro",
    "gemini-local-pro": "gemini-3.1-pro",
    "gemini-local-flash": "gemini-3.7-flash",
    "gemini-local-38": "gemini-3.8-flash",
    "gemini-3.8": "gemini-3.8-flash",
    "gemini-local-thinking": "gemini-3.5-flash-thinking",
}


def _normalize_requested_model(model_name: str) -> str:
    """Strip provider prefixes (e.g. openai/gemini-pro → gemini-pro)."""
    name = (model_name or "").strip()
    if "/" in name:
        name = name.rsplit("/", 1)[-1]
    return name


def public_models():
    """Yield (id, cfg) for /v1/models, including aliases not already in MODELS."""
    for name, cfg in MODELS.items():
        yield name, cfg
    for alias, target in MODEL_ALIASES.items():
        if alias in MODELS:
            continue
        target_cfg = MODELS.get(target)
        if not target_cfg:
            continue
        yield alias, {
            **target_cfg,
            "desc": f"Alias for {target} ({target_cfg.get('desc', '')})".strip(),
        }


def resolve_model(model_name: str, default: str = "gemini-3.8-flash"):
    """Resolve model name to (name, mode_id, think_mode, error, extra_fields).

    Unknown model names fall back to default rather than erroring,
    since upstream clients may request arbitrary model identifiers.
    """
    from .gemini import log

    requested = model_name if model_name is not None else default
    name = _normalize_requested_model(requested)

    think_override = None
    if "@think=" in name:
        name, think_str = name.rsplit("@think=", 1)
        try:
            think_override = int(think_str)
        except ValueError:
            return None, None, None, f"Invalid think level: {think_str}", None

    resolved = MODEL_ALIASES.get(name, name)
    log(f"[Model Router] Requested: {requested}")
    log(f"[Model Router] Resolved: {resolved}")

    cfg = MODELS.get(resolved)
    if not cfg:
        log(f"Unknown model '{resolved}', falling back to '{default}'")
        resolved = default
        cfg = MODELS[default]
        log(f"[Model Router] Resolved: {resolved}")
    mode_id = cfg["mode"]
    think_mode = think_override if think_override is not None else cfg["think"]
    extra = cfg.get("extra")
    return resolved, mode_id, think_mode, None, extra
