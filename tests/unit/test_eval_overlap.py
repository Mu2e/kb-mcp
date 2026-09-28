"""Unit tests for the question-document wording overlap measure."""

from kb_mcp.eval_utils.overlap import question_overlap

DOC = ("The position resolution was determined by comparing reconstructed hit positions "
       "with the bottom-sector reference position. Middle sector sigma 128 mm.")


def test_copied_wording_scores_high():
    o = question_overlap("How was it found by comparing reconstructed hit positions with the bottom-sector reference position?", DOC)
    assert o["longest_shared_words"] >= 8
    assert o["bigram_overlap"] > 0.5


def test_own_wording_scores_low():
    o = question_overlap("What position resolution does the Mu2e Cosmic Ray Veto achieve along a counter?", DOC)
    assert o["longest_shared_words"] <= 2
    assert o["bigram_overlap"] < 0.2


def test_function_words_alone_are_not_overlap():
    assert question_overlap("What is the of the with the", DOC)["bigram_overlap"] == 0.0


def test_empty_inputs():
    assert question_overlap("", DOC) == {"bigram_overlap": 0.0, "longest_shared_words": 0}
    assert question_overlap("x", "") == {"bigram_overlap": 0.0, "longest_shared_words": 0}
