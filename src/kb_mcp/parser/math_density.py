"""Quick PyPDF2-based math-character-density score for PDFs.

Used by the formula-enrichment auto-detect path (`PARSE_FORMULA_ENRICHMENT_AUTO`):
during mass ingest we don't know up-front which documents are equation-
heavy. A cheap pre-scan with PyPDF2 (~0.05 s / page) extracts text and
counts math-indicator characters; the dispatch in `DoclingParser` uses
the result to decide whether to enable Docling's
`do_formula_enrichment` for that single document.

Score is in [0, 1] = (math-indicator chars) / (total chars). On the
12-PDF dev sample the equation-heavy doc (53490) scores ≈ 0.005-0.01
(0.5–1 %); meeting summaries score ≈ 0 (zero math glyphs).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def _math_density(text: str) -> float:
    """Fraction of characters in `text` that look like math indicators.

    Counts characters from Unicode blocks that PDF math typically uses:
      * Italic-Unicode mathematical letters (0x1D400–0x1D7FF) — the
        canonical rendering of subscripts/superscripts in equations,
        e.g. `𝑆`, `𝑐𝑚`, `𝜇`. Common in slide decks with inline math.
      * Greek letters (0x0370–0x03FF: α, β, γ, μ, σ, π, Γ, Σ, ...).
      * Mathematical operators block (0x2200–0x22FF: ∫, ∑, ∂, ∇, ≤, ≥, ≠, ∞).
      * Arrows block (0x2190–0x21FF: →, ⇒, ↔, ...).
      * Combining marks (0x0300–0x036F: hat, bar, tilde, dot).
      * Superscript / subscript digits and signs (0x2070–0x209F).
      * Letterlike Symbols (0x2100–0x214F: ℛ, ℬ, ℋ, ℓ, ℏ, ℤ, ℝ, ℂ).
      * Mathematical alphanumeric symbols additional ranges
        (0x2102, 0x210D, 0x2115, 0x2119, etc. — already covered by
        Letterlike).
      * Scientific Latin-1 supplement specials (0x00B0–0x00BF: ° ± × ÷).
    """
    if not text:
        return 0.0
    n = 0
    for ch in text:
        cp = ord(ch)
        if (
            0x1D400 <= cp <= 0x1D7FF
            or 0x0370 <= cp <= 0x03FF
            or 0x2200 <= cp <= 0x22FF
            or 0x2190 <= cp <= 0x21FF
            or 0x0300 <= cp <= 0x036F
            or 0x2070 <= cp <= 0x209F
            or 0x2100 <= cp <= 0x214F
            or 0x00B0 <= cp <= 0x00BF
        ):
            n += 1
    return n / len(text)


def pdf_math_density(file_path: str | Path) -> float:
    """Score the PDF's math-character density. Returns 0.0 on any error.

    Cheap pre-scan: uses PyPDF2 (already a dependency) to extract text,
    walks characters once. No ML, no image rendering — typically
    finishes in under 0.1 s for a 14-page slide deck. Designed to be
    called BEFORE the heavy Docling parse to decide whether formula
    enrichment is worth its cost on this particular document.
    """
    try:
        import PyPDF2  # type: ignore
    except Exception:
        logger.debug("pdf_math_density: PyPDF2 not available; returning 0.0")
        return 0.0

    try:
        reader = PyPDF2.PdfReader(str(file_path))
        parts = []
        for page in reader.pages:
            try:
                parts.append(page.extract_text() or "")
            except Exception:
                continue
        text = "\n".join(parts)
    except Exception as e:
        logger.debug(f"pdf_math_density: failed to read {file_path}: {e}")
        return 0.0

    return _math_density(text)


def should_enable_formula_enrichment(
    file_path: str | Path,
    threshold: float = 0.0005,
) -> tuple[bool, float]:
    """Decide whether to enable formula enrichment for this PDF.

    Returns `(decision, density)`. Density is reported back so callers
    can log the score for debug / auditing.

    Default threshold (0.0005) calibrated empirically on the dev sample
    + a real Mu2e arXiv paper:
      * Equation-heavy slide decks score 0.005–0.008.
      * Real arXiv papers (typeset math, partly extracted as Unicode
        glyphs by PyPDF2) score 0.001–0.002.
      * Meeting summaries / ops planning / procurement score 0.0000.
    The empirical gap between "had at least some math indicator" and
    "literally none" is wide, so 0.0005 keeps false-positives essentially
    zero on the calibration set while catching the arXiv preprint case.
    Bias is intentionally toward false-positives — the cost is a
    one-time +30 s parse for a doc that turned out not to need it,
    vs. losing equations as `<!-- formula-not-decoded -->` placeholders.
    """
    density = pdf_math_density(file_path)
    return density >= threshold, density
