"""Image utility functions for parsers."""

import base64
import io
import logging
from typing import Optional, Union

logger = logging.getLogger(__name__)

# Try to import PIL for image handling (optional)
try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False


def resize_image(img, max_dim: Optional[int] = 500):
    """Resize image to max dimension while maintaining aspect ratio.
    
    Args:
        img: PIL Image object
        max_dim: Maximum dimension (width or height). None to skip resizing.
        
    Returns:
        Resized PIL Image
    """
    if not HAS_PIL:
        logger.warning("PIL/Pillow not installed. Cannot resize images.")
        return img
        
    if max_dim is None:
        return img

    width, height = img.size
    if max(width, height) <= max_dim:
        return img

    if width > height:
        new_width = max_dim
        new_height = int(height * max_dim / width)
    else:
        new_height = max_dim
        new_width = int(width * max_dim / height)

    return img.resize((new_width, new_height), Image.Resampling.LANCZOS)


def shrink_for_vision(image_bytes: bytes, max_dim: int = 1280, max_bytes: int = 800_000,
                      quality: int = 80) -> bytes:
    """Return image bytes small enough to send to a vision endpoint.

    Hosted vision endpoints reject or fail on very large payloads (ALCF answers
    HTTP 500/413 for 8-20 MB figures), and a description does not need the
    original resolution. Images that are already small are returned untouched.
    Larger ones are scaled so the long side is at most ``max_dim`` and
    re-encoded as JPEG, then shrunk further until they fit ``max_bytes``.

    Never raises: if the bytes cannot be decoded or Pillow is missing, the
    original bytes are returned and the endpoint decides.

    Args:
        image_bytes: Original image bytes.
        max_dim: Longest allowed side in pixels. 0 or None disables shrinking.
        max_bytes: Target upper bound for the encoded size. 0 or None disables.
        quality: Starting JPEG quality.
    """
    if not image_bytes or not HAS_PIL or not (max_dim or max_bytes):
        return image_bytes
    try:
        img = Image.open(io.BytesIO(image_bytes))
        img.load()
        long_side = max(img.size)
        too_big_dim = bool(max_dim) and long_side > max_dim
        too_big_bytes = bool(max_bytes) and len(image_bytes) > max_bytes
        if not (too_big_dim or too_big_bytes):
            return image_bytes

        if img.mode in ("RGBA", "LA", "P"):
            rgba = img.convert("RGBA")
            background = Image.new("RGB", rgba.size, (255, 255, 255))
            background.paste(rgba, mask=rgba.split()[-1])
            img = background
        elif img.mode != "RGB":
            img = img.convert("RGB")

        dim = min(max_dim, long_side) if max_dim else long_side
        q = quality
        out = image_bytes
        for _ in range(5):
            resized = resize_image(img, dim)
            buf = io.BytesIO()
            resized.save(buf, format="JPEG", quality=q, optimize=True)
            out = buf.getvalue()
            if not max_bytes or len(out) <= max_bytes:
                break
            dim = int(dim * 0.75)
            q = max(55, q - 8)
        logger.debug("Shrunk image for vision: %d -> %d bytes", len(image_bytes), len(out))
        return out
    except Exception as e:  # undecodable, truncated, unsupported mode ...
        logger.warning("Could not shrink image for vision (%s); sending original", e)
        return image_bytes


def image_to_base64(img, format: Optional[str] = None) -> str:
    """Convert PIL image to base64 string, preserving format when possible.
    
    Args:
        img: PIL Image object
        format: Image format (JPEG, PNG, etc.). If None, defaults to PNG.
        
    Returns:
        Base64-encoded image string
    """
    if not HAS_PIL:
        raise ImportError("PIL/Pillow required for image conversion")
        
    buffered = io.BytesIO()

    # Try to preserve original format, fallback to PNG
    if format and format.upper() in ['JPEG', 'JPG', 'PNG', 'WEBP', 'GIF']:
        save_format = 'JPEG' if format.upper() == 'JPG' else format.upper()
    else:
        save_format = 'PNG'

    img.save(buffered, format=save_format)
    return base64.b64encode(buffered.getvalue()).decode()


def detect_image_format(image_base64: str) -> str:
    """Detect image format from base64 data.
    
    Args:
        image_base64: Base64-encoded image string
        
    Returns:
        Image format string (jpeg, png, webp, gif)
    """
    if image_base64.startswith('/9j/'):
        return 'jpeg'
    elif image_base64.startswith('iVBORw0KGgo'):
        return 'png'
    elif image_base64.startswith('UklGR'):
        return 'webp'
    elif image_base64.startswith('R0lGOD'):
        return 'gif'
    else:
        return 'jpeg'  # Default fallback


def display_image(image_data: Union[bytes, dict, 'Document']) -> None:
    """Display an image from bytes, dict, or Document object.
    
    Works in both scripts (opens default image viewer) and Jupyter notebooks (displays inline).
    
    Args:
        image_data: Can be:
            - bytes: Raw image binary data
            - dict: Image dictionary with 'binary' field (from parser)
            - Document: Document object with binary field (from kb)
    
    Returns:
        PIL Image object
    
    Raises:
        ImportError: If PIL/Pillow is not installed
        ValueError: If image_data doesn't contain binary data
    
    Example:
        ```
        # From bytes
        display_image(image_bytes)
        
        # From parser dict
        doc_dicts = parse("document.pdf", {...})
        display_image(doc_dicts[1])  # Show first image
        
        # From Document object
        from kb_mcp.kb import get
        doc = get("image-doc-id")
        ```
        display_image(doc)
    """
    if not HAS_PIL:
        raise ImportError("PIL/Pillow is required to display images. Install with: pip install pillow")
    
    # Extract bytes from different input types
    if isinstance(image_data, bytes):
        image_bytes = image_data
    elif isinstance(image_data, dict):
        if "binary" not in image_data:
            raise ValueError("Image dict must have 'binary' field")
        image_bytes = image_data["binary"]
    else:
        # Assume it's a Document object (avoid circular import)
        if not hasattr(image_data, 'binary') or image_data.binary is None:
            raise ValueError("Document object must have binary data")
        image_bytes = image_data.binary
    
    # Create PIL Image from bytes
    img = Image.open(io.BytesIO(image_bytes))
    try:
        img.show()
    except Exception as e:
        pass
    return img
    # Try to use IPython display if available (Jupyter)
    try:
        from IPython.display import display
        display(img)
    except ImportError:
        # Not in Jupyter, use PIL's show() method (opens default viewer)
        try:
            img.show()
        except Exception:
            pass

