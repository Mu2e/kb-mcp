"""Unit tests for the web comparison page's table rendering."""

from kb_mcp.server.web.routes.eval import _render_compare_table, _render_question_matrix

ROW = {
    "run_id": "r1", "name": "grid-agentic-claude-sonnet-5-qx-judge-gpt-5.5", "search_type": "agentic",
    "answer_model": "argo:claude-sonnet-5", "judge_model": "argo:gpt-5.5", "questions": 10,
    "exact_hits": 5, "entry_hits": None, "judged": 10, "judge_correct": 9, "agent_saw_source": 8,
    "agent_opened_source": 3, "turn_limit": 0, "stopped": 0, "avg_answer_seconds": 11.4,
    "answer_tokens": 404979, "judge_tokens": None,
}


def test_summary_table_links_runs_and_shades_rates():
    html = _render_compare_table([ROW], "grid-")
    assert 'href="/web/eval/run/r1"' in html
    assert ">agentic-claude-sonnet-5-qx-judge-gpt-5.5<" in html  # prefix stripped
    assert "9/10" in html and "#e8f5e9" in html  # 90% shaded green
    assert "5/10" in html and "#fff8e1" in html  # 50% shaded yellow
    assert "404,979" in html


def test_question_matrix_marks_and_escapes():
    block = {
        "generation_id": "g1", "generation_name": "set <1>",
        "runs": [{"run_id": "r1", "name": "grid-a"}, {"run_id": "r2", "name": "grid-b"}],
        "rows": [{"question_id": "q1", "question": "Is x < y & z?",
                  "cells": [{"result_id": "res1", "ok": True}, None]}],
    }
    html = _render_question_matrix(block, "grid-")
    assert 'href="/web/eval/result/res1"' in html and "✓" in html
    assert "Is x &lt; y &amp; z?" in html and "set &lt;1&gt;" in html
    assert "·" in html  # run without a result for this question
