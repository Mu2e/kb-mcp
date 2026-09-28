"""How much of a question's wording is taken from its source document.

A question that reuses its document's own phrasing ("...by comparing
reconstructed hit positions with the bottom-sector reference position") is
easy to retrieve for the wrong reason: search matches the document's
vocabulary rather than understanding the question. These measures flag that
without a model, so question sets can be compared on it.
"""

import re
from typing import Dict

_WORD = re.compile(r"\w+", re.UNICODE)

# Function words carry no retrieval signal; a shared "of the" is not a leak.
_STOPWORDS = frozenset("""
a an and are as at be by can did do does for from has have how in is it its of on or
that the their these this to was were what when where which who why will with
""".split())


def _words(text: str):
    return [w.lower() for w in _WORD.findall(text or "")]


def question_overlap(question: str, document_text: str) -> Dict[str, float]:
    """Wording shared between a question and its source document.

    Returns:
        bigram_overlap: share (0-1) of the question's content-word bigrams --
            adjacent words after dropping function words -- that also occur in
            the document. Content words shared as pairs are what a lexical
            search matches on.
        longest_shared_words: length of the longest word sequence the question
            copies verbatim from the document (function words included).
    """
    q_words = _words(question)
    d_words = _words(document_text)

    def content_bigrams(words):
        content = [w for w in words if w not in _STOPWORDS]
        return list(zip(content, content[1:]))

    q_bigrams = content_bigrams(q_words)
    d_bigrams = set(content_bigrams(d_words))
    bigram_overlap = (
        sum(1 for b in q_bigrams if b in d_bigrams) / len(q_bigrams) if q_bigrams else 0.0
    )

    longest = 0
    d_ngrams = set(d_words)
    n = 1
    while n <= len(q_words) and any(
        tuple(q_words[i:i + n]) in d_ngrams if n > 1 else q_words[i] in d_ngrams
        for i in range(len(q_words) - n + 1)
    ):
        longest = n
        n += 1
        d_ngrams = {tuple(d_words[i:i + n]) for i in range(len(d_words) - n + 1)}

    return {"bigram_overlap": round(bigram_overlap, 3), "longest_shared_words": longest}
