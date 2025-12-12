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

