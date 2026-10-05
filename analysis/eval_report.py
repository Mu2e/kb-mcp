"""Numbers and figures for an eval report (e.g. a LaTeX internal note).

Reads one grid's runs from the database (selected by run-name prefix) and
writes, into an output directory kept outside the repository:

  data.json    every number the report uses, for traceability
  numbers.tex  the same numbers as LaTeX macros (\\AccAgenticSonnet, ...), so a
               note's text cannot drift from the data when the grid is re-run
  fig_*.pdf    the figures

Usage (from the repo root, with the analysis extra installed):

    python analysis/eval_report.py --prefix large-20260927- \\
        --questions bd684ccb-0547-488f-a403-31effd16b38e \\
        --questions ef8c0b4f-ab81-4d55-8dab-96d1d8171069 \\
        --out /exp/mu2e/data/users/$USER/kb-mcp-data/notes/eval-2026-10/generated

Run naming follows `kb eval grid`: <prefix><mode>-<answer model>-q<set>-judge-<judge>[-v<N>].
"""

import argparse
import json
from collections import Counter
from pathlib import Path

from sqlalchemy import func

from kb_mcp.kb.database import get_db_session
from kb_mcp.kb.db_models import LLMUsage
from kb_mcp.kb.eval.db_models import EvalAudit, EvalDataset, EvalGeneration, EvalResult, EvalRun, get_eval_questions
from kb_mcp.kb.eval.grid import _agentic_summary, conflict_findings, generation_label, model_label

#: Answer models in the report, in display order, with the macro/legend names.
MODELS = [
    ("gpt-oss:120b", "GptOss", "gpt-oss-120b"),
    ("argo:claude-haiku-4.5", "Haiku", "haiku-4.5"),
    ("argo:claude-sonnet-5", "Sonnet", "sonnet-5"),
    ("argo:claude-opus-5", "Opus", "opus-5"),
]
MODES = [("llm_only", "LlmOnly", "Model alone"), ("rag", "Rag", "One search + answer"),
         ("agentic", "Agentic", "Agent + MCP tools")]
#: Retrieval variants (run name part) with display names.
RETRIEVAL = [("semantic", "Semantic"), ("fulltext", "Full-text"), ("hybrid", "Hybrid (MCP default)"),
             ("hybrid-rerank", "Hybrid + reranker"), ("hybrid-section512", "Hybrid, section chunks only"),
             ("hybrid-summary512", "Hybrid, summary chunks only"), ("hybrid-tokens512", "Hybrid, token chunks only")]

#: Validated categorical palette (dataviz reference, slots 1-4, light surface):
#: one fixed color per model, the same in every figure.
COLORS = {"GptOss": "#2a78d6", "Haiku": "#eb6834", "Sonnet": "#1baf7a", "Opus": "#eda100"}
INK, INK2, MUTED, GRID = "#0b0b0b", "#52514e", "#898781", "#e1e0d9"


def _results(session, name):
    run = session.query(EvalRun).filter_by(name=name).order_by(EvalRun.created_time.desc()).first()
    if run is None:
        return None, None
    return run, {r.question_id: r for r in session.query(EvalResult).filter_by(run_id=run.id)}


def collect(prefix, generation_ids, judge="argo:gpt-5.5", judge_version=3):
    judge_part = f"judge-{model_label(judge)}" + (f"-v{judge_version}" if judge_version > 1 else "")
    data = {"prefix": prefix, "judge": judge, "judge_version": judge_version}
    with get_db_session() as session:
        gens = {gid: session.get(EvalGeneration, gid) for gid in generation_ids}
        qsets = {gid: f"q{generation_label(g)}" for gid, g in gens.items()}

        # Question funnel: generated -> overlap check -> LLM audit -> usable.
        funnel = {"generated": 0, "overlap_rejected": 0, "audit_rejected": 0, "usable": 0, "documents": set()}
        for gid in generation_ids:
            questions = session.query(EvalDataset).filter_by(generation_id=gid).all()
            funnel["generated"] += len(questions)
            funnel["documents"].update(q.source_document_id for q in questions)
            ids = [q.id for q in questions]
            audits = session.query(EvalAudit).filter(EvalAudit.question_id.in_(ids)).all()
            funnel["overlap_rejected"] += sum(1 for a in audits if a.audit_type == "overlap_check" and not a.is_valid)
            funnel["audit_rejected"] += sum(1 for a in audits if a.audit_type == "llm_judge" and not a.is_valid)
            funnel["usable"] += len(get_eval_questions(generation_id=gid, audit_filter={"strict": True}, session=session))
        funnel["documents"] = len(funnel["documents"])
        data["funnel"] = funnel

        # Accuracy by mode and model, both question sets combined.
        acc, per_set = {}, {}
        for mode, _, _ in MODES:
            for model, key, _ in MODELS:
                ok = n = 0
                for gid in generation_ids:
                    _, res = _results(session, f"{prefix}{mode}-{model_label(model)}-{qsets[gid]}-{judge_part}")
                    if not res:
                        continue
                    judged = [r for r in res.values() if r.is_judge_hit is not None]
                    k = sum(1 for r in judged if r.is_judge_hit)
                    ok += k; n += len(judged)
                    per_set[f"{mode}|{key}|{qsets[gid]}"] = {"correct": k, "n": len(judged)}
                if n:
                    acc[f"{mode}|{key}"] = {"correct": ok, "n": n}
        data["accuracy"], data["accuracy_per_set"] = acc, per_set

        # Paired comparison of all agents on the question set every model answered.
        paired_gid = generation_ids[-1]
        runs = {key: _results(session, f"{prefix}agentic-{model_label(m)}-{qsets[paired_gid]}-{judge_part}")[1]
                for m, key, _ in MODELS}
        runs = {k: v for k, v in runs.items() if v}
        common = set.intersection(*(set(v) for v in runs.values())) if runs else set()
        data["paired"] = {"question_set": qsets[paired_gid], "n": len(common),
                          "correct": {k: sum(1 for q in common if v[q].is_judge_hit) for k, v in runs.items()}}
        if "Opus" in runs and "Sonnet" in runs:
            o, s = runs["Opus"], runs["Sonnet"]
            data["paired"]["opus_only"] = sum(1 for q in common if o[q].is_judge_hit and not s[q].is_judge_hit)
            data["paired"]["sonnet_only"] = sum(1 for q in common if s[q].is_judge_hit and not o[q].is_judge_hit)

        # Agent behaviour per model (from the answers; judged runs copy them).
        behaviour, conflicts_by_model = {}, {}
        for model, key, _ in MODELS:
            times, tools, opened, saw, rel, ack, n = [], [], 0, 0, Counter(), 0, 0
            for gid in generation_ids:
                _, res = _results(session, f"{prefix}agentic-{model_label(model)}-{qsets[gid]}-{judge_part}")
                for r in (res or {}).values():
                    meta = r.meta or {}
                    summary = _agentic_summary(meta.get("agentic_trace")) or {}
                    n += 1
                    if meta.get("llm_answer_time") is not None:
                        times.append(meta["llm_answer_time"])
                    tools.append(summary.get("tool_calls") or 0)
                    opened += bool(summary.get("opened_source")); saw += bool(summary.get("saw_source"))
                    rel[meta.get("judge_source_relation")] += 1
                    ack += bool(meta.get("judge_acknowledges_conflict"))
            if n:
                behaviour[key] = {"n": n, "seconds_per_answer": sum(times) / len(times) if times else None,
                                  "tool_calls_per_answer": sum(tools) / n, "opened_source": opened / n,
                                  "saw_source": saw / n}
                conflicts_by_model[key] = {"newer": rel["newer_source"], "older": rel["older_source"],
                                           "conflicting": rel["conflicting_source"], "acknowledged": ack, "n": n}
        data["behaviour"], data["conflicts_by_model"] = behaviour, conflicts_by_model

        # Answer tokens per agentic question, from llm_usage tagged with the
        # answering runs (judge v1 run names). Claude counts are a lower bound.
        tokens = {}
        for model, key, _ in MODELS:
            run_ids = [run.id for gid in generation_ids
                       for run in [_results(session, f"{prefix}agentic-{model_label(model)}-{qsets[gid]}-judge-{model_label(judge)}")[0]]
                       if run]
            if not run_ids:
                continue
            total, questions = session.query(func.sum(LLMUsage.total_tokens),
                                             func.count(func.distinct(LLMUsage.meta["eval_question_id"].as_string()))).filter(
                LLMUsage.stage == "eval_answer", LLMUsage.meta["eval_run_id"].as_string().in_(run_ids)).one()
            if questions:
                tokens[key] = {"tokens_per_question": int(total or 0) / questions, "questions": questions,
                               "lower_bound": model.startswith("argo:claude")}
        data["tokens"] = tokens

        # Retrieval ablations (entry-level hit@k).
        retrieval = {}
        for variant, label in RETRIEVAL:
            ranks = []
            for gid in generation_ids:
                _, res = _results(session, f"{prefix}ret-{variant}-{qsets[gid]}")
                for r in (res or {}).values():
                    rank = (r.meta or {}).get("entry_hit_rank")
                    ranks.append(int(rank) if rank else None)
            if ranks:
                retrieval[variant] = {"label": label, "n": len(ranks),
                                      **{f"hit_at_{k}": sum(1 for x in ranks if x and x <= k) / len(ranks) for k in (1, 5, 10, 20)},
                                      "mrr": sum(1 / x for x in ranks if x) / len(ranks)}
        data["retrieval"] = retrieval

        # Judge consistency: repeated v2 judgments of the same answers.
        reps = [_results(session, f"{prefix}agentic-claude-sonnet-5-{qsets[paired_gid]}-judge-{model_label(judge)}-v2{s}")[1]
                for s in ("", "-rep2", "-rep3")]
        if all(reps):
            qs = set.intersection(*(set(r) for r in reps))
            flips = sum(1 for q in qs for a, b in ((0, 1), (0, 2), (1, 2)) if reps[a][q].is_judge_hit != reps[b][q].is_judge_hit)
            data["judge_noise"] = {"n": len(qs), "correct": [sum(1 for q in qs if r[q].is_judge_hit) for r in reps],
                                   "pairwise_flip_rate": flips / (3 * len(qs)),
                                   "questions_disagreeing": sum(1 for q in qs if len({r[q].is_judge_hit for r in reps}) > 1)}

    findings = conflict_findings(prefix, min_judge_version=judge_version)
    data["conflicts"] = {"questions": len(findings),
                         "corroborated": sum(1 for f in findings if f["models_citing_same_newer_doc"] > 1)}
    return data


def _pct(x):
    return f"{100 * x:.0f}\\%"


def write_numbers(data, path):
    """Every number as a LaTeX macro; names use letters only (TeX rule)."""
    lines = ["% Generated by analysis/eval_report.py -- do not edit by hand.",
             f"% prefix {data['prefix']}, judge {data['judge']} v{data['judge_version']}"]
    def m(name, value):
        lines.append(f"\\newcommand{{\\{name}}}{{{value}}}")
    f = data["funnel"]
    for k, name in (("generated", "QGenerated"), ("overlap_rejected", "QOverlapRejected"),
                    ("audit_rejected", "QAuditRejected"), ("usable", "QUsable"), ("documents", "QDocuments")):
        m(name, f[k])
    for mode, mkey, _ in MODES:
        for _, key, _ in MODELS:
            a = data["accuracy"].get(f"{mode}|{key}")
            if a:
                m(f"Acc{mkey}{key}", _pct(a["correct"] / a["n"]))
                m(f"Acc{mkey}{key}Frac", f"{a['correct']}/{a['n']}")
    p = data["paired"]
    m("PairedN", p["n"])
    for key, ok in p["correct"].items():
        m(f"Paired{key}", f"{100 * ok / p['n']:.1f}\\%")
    if "opus_only" in p:
        m("PairedOpusOnly", p["opus_only"]); m("PairedSonnetOnly", p["sonnet_only"])
    for key, b in data["behaviour"].items():
        if b["seconds_per_answer"] is not None:
            m(f"Sec{key}", f"{b['seconds_per_answer']:.0f}")
        m(f"Tools{key}", f"{b['tool_calls_per_answer']:.1f}")
        m(f"Opened{key}", _pct(b["opened_source"]))
    for key, t in data["tokens"].items():
        m(f"Tok{key}", f"{t['tokens_per_question'] / 1000:.0f}k")
    for key, c in data["conflicts_by_model"].items():
        for k in ("newer", "older", "conflicting", "acknowledged"):
            m(f"Src{key}{k.capitalize()}", c[k])
    names = {"semantic": "Semantic", "fulltext": "Fulltext", "hybrid": "Hybrid", "hybrid-rerank": "Rerank",
             "hybrid-section512": "Section", "hybrid-summary512": "Summary", "hybrid-tokens512": "Tokens"}
    for variant, r in data["retrieval"].items():
        for k in (1, 5, 10, 20):
            m(f"Hit{names[variant]}{['One', 'Five', 'Ten', 'Twenty'][[1, 5, 10, 20].index(k)]}", _pct(r[f"hit_at_{k}"]))
    if "judge_noise" in data:
        j = data["judge_noise"]
        m("NoiseN", j["n"]); m("NoiseFlip", f"{100 * j['pairwise_flip_rate']:.1f}\\%")
        m("NoiseDisagree", j["questions_disagreeing"])
    m("ConflictQuestions", data["conflicts"]["questions"]); m("ConflictCorroborated", data["conflicts"]["corroborated"])
    path.write_text("\n".join(lines) + "\n")


def _style(ax):
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color("#c3c2b7")
    ax.tick_params(colors=INK2, labelsize=8)
    ax.yaxis.grid(True, color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)


def write_figures(data, out):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update({"font.family": "sans-serif", "font.size": 8, "axes.edgecolor": "#c3c2b7",
                         "text.color": INK, "axes.labelcolor": INK2})

    # 1. Accuracy by mode and model: grouped bars, value labels (relief rule).
    fig, ax = plt.subplots(figsize=(6.2, 2.8))
    width = 0.19
    for i, (mode, _, mode_label) in enumerate(MODES):
        present = [(key, label) for _, key, label in MODELS if f"{mode}|{key}" in data["accuracy"]]
        offset0 = -(len(present) - 1) / 2
        for j, (key, label) in enumerate(present):
            a = data["accuracy"][f"{mode}|{key}"]
            v = 100 * a["correct"] / a["n"]
            x = i + (offset0 + j) * (width + 0.02)
            ax.bar(x, v, width, color=COLORS[key], label=label if i == len(MODES) - 1 else None, zorder=2)
            ax.text(x, v + 1.5, f"{v:.0f}", ha="center", va="bottom", fontsize=7, color=INK2)
    ax.set_xticks(range(len(MODES)), [m[2] for m in MODES])
    ax.set_ylim(0, 105); ax.set_ylabel("Judged correct (%)")
    _style(ax)
    ax.legend(frameon=False, fontsize=7, ncol=4, loc="upper left")
    fig.tight_layout(); fig.savefig(out / "fig_modes.pdf"); plt.close(fig)

    # 2. Retrieval ablations: hit@5 and hit@20 per variant, horizontal bars.
    variants = [(v, r) for v, _ in RETRIEVAL for vv, r in data["retrieval"].items() if vv == v]
    fig, ax = plt.subplots(figsize=(6.2, 2.9))
    for k, color, dy in ((5, "#2a78d6", -0.2), (20, "#eb6834", 0.2)):
        ys = [i + dy for i in range(len(variants))]
        vals = [100 * r[f"hit_at_{k}"] for _, r in variants]
        ax.barh(ys, vals, 0.36, color=color, label=f"source entry in top {k}", zorder=2)
        for y, v in zip(ys, vals):
            ax.text(v + 1, y, f"{v:.0f}", va="center", fontsize=7, color=INK2)
    ax.set_yticks(range(len(variants)), [r["label"] for _, r in variants])
    ax.invert_yaxis(); ax.set_xlim(0, 100); ax.set_xlabel("Questions (%)")
    _style(ax); ax.yaxis.grid(False); ax.xaxis.grid(True, color=GRID, linewidth=0.6)
    ax.legend(frameon=False, fontsize=7, loc="lower right")
    fig.tight_layout(); fig.savefig(out / "fig_retrieval.pdf"); plt.close(fig)

    # 3. Agent accuracy vs time per answer: one point per model, labelled.
    fig, ax = plt.subplots(figsize=(4.2, 2.8))
    for _, key, label in MODELS:
        a = data["accuracy"].get(f"agentic|{key}"); b = data["behaviour"].get(key)
        if not a or not b or b["seconds_per_answer"] is None:
            continue
        x, y = b["seconds_per_answer"], 100 * a["correct"] / a["n"]
        ax.scatter([x], [y], s=60, color=COLORS[key], edgecolor="#fcfcfb", linewidth=2, zorder=3)
        ax.annotate(label, (x, y), xytext=(6, -3), textcoords="offset points", fontsize=7, color=INK2)
    ax.set_xlabel("Seconds per answer (agent + MCP)"); ax.set_ylabel("Judged correct (%)")
    ax.set_xlim(left=0); _style(ax)
    fig.tight_layout(); fig.savefig(out / "fig_cost.pdf"); plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--prefix", required=True, help="Run name prefix of the grid, e.g. large-20260927-")
    parser.add_argument("--questions", action="append", required=True, help="Question set (generation id); repeatable, the last one is used for the paired comparison")
    parser.add_argument("--judge", default="argo:gpt-5.5", help="Judge model whose verdicts to report")
    parser.add_argument("--judge-version", type=int, default=3, help="Judge prompt version to report")
    parser.add_argument("--out", required=True, help="Output directory (keep it outside the repository)")
    parser.add_argument("--no-figures", action="store_true", help="Only write data.json and numbers.tex")
    args = parser.parse_args()

    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    data = collect(args.prefix, args.questions, args.judge, args.judge_version)
    (out / "data.json").write_text(json.dumps(data, indent=2, default=str))
    write_numbers(data, out / "numbers.tex")
    if not args.no_figures:
        write_figures(data, out)
    print(f"wrote {out}/data.json, numbers.tex" + ("" if args.no_figures else ", fig_modes.pdf, fig_retrieval.pdf, fig_cost.pdf"))


if __name__ == "__main__":
    main()
