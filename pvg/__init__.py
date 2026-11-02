"""AI Property Video Generator (pvg).

Turns property details + photos into a short, shareable MP4 promo video.
"""
from .errors import PVGError, InputError, RenderError
from .models import PropertyDetails, Feature, RenderOptions
from .pipeline import generate_video, GenerationResult

__all__ = [
    "PVGError", "InputError", "RenderError",
    "PropertyDetails", "Feature", "RenderOptions",
    "generate_video", "GenerationResult",
]
__version__ = "1.0.0"
