"""Benchmark docling parse cost per document, OCR on vs off.

Parse only — no LLM calls, no DB writes. The point is to find out how much of
a full reparse is docling on this 4-core, GPU-less node, and how much of that
OCR is responsible for, before committing to a multi-hour run.
"""
import os
import sys
import time
import importlib

# Documents chosen to span the corpus: born-digital slides/papers, a large
# text-layer "scan", and the drawing-type PDFs that currently store 0 chars.
DOCS = [
    ("slides-small",  "mu2e-docdb-56353-opsmeeting_10thApr.pdf"),
    ("paper",         "mu2e-docdb-57799-IFAE_2026_Proceedings.pdf"),
    ("drawing-1",     "mu2e-docdb-12815-F10085228-D-DWG1.pdf"),
    ("drawing-2",     "mu2e-docdb-12815-F10090293-A-DWG1.pdf"),
    ("datasheet",     "mu2e-docdb-12815-DPT146-Datasheet-B211159EN-H.pdf"),
    ("pressure-ves",  "mu2e-docdb-12815-Pressure_Vessel_EN15770_FESHM_5031_Document.pdf"),
    ("piping",        "mu2e-docdb-12815-EN02612_Piping_EN_compressed_air_Mu2e.pdf"),
    ("receiver-pv",   "mu2e-docdb-12815-Receiver_PV_EN02498.pdf"),
]
BASE = "/exp/mu2e/app/users/scorrodi/kb-mcp/data/sources/mu2e-docdb"


def run(path, ocr):
    os.environ["PARSE_OCR"] = "true" if ocr else "false"
    from kb_mcp.parser import parser_docling
    importlib.reload(parser_docling)
    parser_docling._CONVERTER_CACHE.clear()
    t = time.time()
    conv = parser_docling._get_converter(False)
    res = conv.convert(path)
    md = res.document.export_to_markdown()
    n_pics = len(getattr(res.document, "pictures", []) or [])
    return time.time() - t, len(md), n_pics


def _env_report():
    """Print what hardware this actually ran on.

    The parser silently falls back to CPU when CUDA is unavailable, so a GPU
    run that quietly used the CPU would look like "the GPU didn't help".
    """
    import platform
    line = f"host={platform.node()} cpus={os.cpu_count()}"
    try:
        import torch
        cuda = torch.cuda.is_available()
        line += f" torch={torch.__version__} cuda={cuda}"
        if cuda:
            line += f" gpu={torch.cuda.get_device_name(0)} n={torch.cuda.device_count()}"
    except Exception as e:
        line += f" torch=<unavailable: {e}>"
    print(line, flush=True)


def main():
    _env_report()
    print(f"{'doc':14s} {'MB':>5s} {'ocr_on':>9s} {'ocr_off':>9s} {'x':>5s} "
          f"{'chars_on':>9s} {'chars_off':>9s} {'imgs':>5s}", flush=True)
    print("-" * 78, flush=True)
    tot_on = tot_off = 0.0
    for label, fn in DOCS:
        path = os.path.join(BASE, fn)
        if not os.path.isfile(path):
            print(f"{label:14s} MISSING", flush=True)
            continue
        mb = os.path.getsize(path) / 1e6
        try:
            t_on, c_on, n_pics = run(path, True)
            t_off, c_off, _ = run(path, False)
        except Exception as e:
            print(f"{label:14s} ERROR {type(e).__name__}: {e}", flush=True)
            continue
        tot_on += t_on
        tot_off += t_off
        print(f"{label:14s} {mb:5.1f} {t_on:8.1f}s {t_off:8.1f}s "
              f"{t_on / max(t_off, .01):4.1f}x {c_on:9d} {c_off:9d} {n_pics:5d}",
              flush=True)
    print("-" * 78, flush=True)
    n = len(DOCS)
    print(f"total: ocr_on={tot_on:.1f}s ocr_off={tot_off:.1f}s "
          f"(saving {tot_on - tot_off:.1f}s = {(1 - tot_off / max(tot_on, .01)) * 100:.0f}%)",
          flush=True)
    print(f"mean/doc: on={tot_on / n:.1f}s off={tot_off / n:.1f}s", flush=True)
    print(f"extrapolated to 595 docs: on={tot_on / n * 595 / 60:.0f}min "
          f"off={tot_off / n * 595 / 60:.0f}min  (parse only, no LLM)", flush=True)


if __name__ == "__main__":
    main()
