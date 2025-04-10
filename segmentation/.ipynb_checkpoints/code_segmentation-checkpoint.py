from typing import List, Tuple, Optional
import sys
import os
import ast
import json
import re

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from Data.pretokenizer.pretokenizer import pretokenize
from pretty_printer import pretty_print_span, pretty_print_spans, pretty_print_tokens


def segment_tokens(tokens: List[str], max_len: int, protected_spans: List[Tuple[int, int]]):
    """
    Segments a list of tokens into chunks of at most max_len, minimizing cuts across protected spans.

    Args:
        tokens: A list of token strings.
        max_len: Max number of tokens per segment.
        protected_spans: List of (start, end) index ranges for logical code units (e.g., functions).

    Returns:
        A list of (start, end) indices indicating segment boundaries.
    """
    n = len(tokens)
    cost = [0] * n

    # Step 1: Build cost array
    for l, r in protected_spans:
        for i in range(l, r):
            if 0 <= i < n:
                cost[i] += 1

    # Step 2: Dynamic programming (naive version)
    m = (n + max_len - 1) // max_len  # max possible number of segments
    dp = [[float('inf')] * (n + 1) for _ in range(m + 1)]
    prev = [[-1] * (n + 1) for _ in range(m + 1)]
    dp[0][0] = 0

    for k in range(1, m + 1):
        for i in range(1, n + 1):
            for j in range(max(0, i - max_len), i):
                new_cost = dp[k - 1][j] + cost[i - 1]
                # print(k, i, j, new_cost)
                if new_cost < dp[k][i]:
                    dp[k][i] = new_cost
                    prev[k][i] = j

    # Step 3: Backtrack to get best segmentation
    best_k = min(range(1, m + 1), key=lambda t: dp[t][n])
    cuts = []
    i = n
    while best_k > 0:
        j = prev[best_k][i]
        cuts.append(i)
        i = j
        best_k -= 1
    cuts = sorted(cuts)

    # Step 4: Convert to (start, end) segments
    segments = []
    start = 0
    for end in cuts:
        segments.append((start, end))
        start = end

    return segments


def tokenize_pretokenized_string(s):
    # Tokenizes strings like [DEF]train[DELIMIT_1_L]... into separate tokens
    return re.findall(r'\[[^\[\]]+\]|[^\[\]]+', s)


def extract_control_structure_span(tokens: List[str], control_tag: str) -> List[Tuple[int, int]]:
    spans = []
    i = 0
    while i < len(tokens):
        if tokens[i] == control_tag:
            header_start = i
            while i < len(tokens) and tokens[i] != "[INDENT]":
                i += 1
            if i < len(tokens) and tokens[i] == "[INDENT]":
                depth = 1
                block_start = i + 1
                i += 1
                while i < len(tokens) and depth > 0:
                    if tokens[i] == "[INDENT]":
                        depth += 1
                    elif tokens[i] == "[DEDENT]":
                        depth -= 1
                    i += 1
                spans.append((header_start, i - 1))
        else:
            i += 1
    return spans


def extract_single_line_span(tokens: List[str], keyword_tag: str) -> List[Tuple[int, int]]:
    spans = []
    i = 0
    while i < len(tokens):
        if tokens[i] == keyword_tag:
            start = i
            end = i + 1
            while end < len(tokens) and tokens[end] not in ("[NEW_LINE]", "[INDENT]", "[DEDENT]"):
                end += 1
            spans.append((start, end - 1 if end < len(tokens) and tokens[end] in ("[NEW_LINE]", "[INDENT]", "[DEDENT]") else end))
            i = end
        else:
            i += 1
    return spans


def extract_delimited_spans(tokens: List[str], left_tag: str, right_tag: str) -> List[Tuple[int, int]]:
    spans = []
    stack = []
    for i, token in enumerate(tokens):
        if token == left_tag:
            stack.append(i)
        elif token == right_tag and stack:
            start = stack.pop()
            spans.append((start, i))
    return spans


def extract_protected_spans(
    tokens: List[str],
    tags: Optional[List[str]] = None,
    control_tags: bool = False,
    inline_tags: bool = False,
    delimiters: bool = False,
    lines: bool = False,
    indented_blocks: bool = False,
    all_options: bool = False
) -> List[Tuple[int, int]]:
    """
    Extract spans of interest from a sequence of tokens based on specific language constructs.

    Args:
        tokens (List[str]): The tokenized input sequence.
        tags (Optional[List[str]]): A list of tags to extract spans for, used when corresponding flags are False.
        control_tags (bool): If True, extract spans for known control-structure keywords.
        inline_tags (bool): If True, extract single-line spans for known inline keywords.
        delimiters (bool): If True, extract spans enclosed by known delimiter pairs.
        lines (bool): If True, extract line-based spans from [NEW_LINE], [INDENT], or [DEDENT] markers.
        indented_blocks (bool): If True, extract indented code blocks delimited by [INDENT] and [DEDENT].
        all_options (bool): If True, enables all other flags (control_tags, inline_tags, delimiters, lines, indented_blocks).

    Returns:
        List[Tuple[int, int]]: A list of unique spans, where each span is a tuple of (start_index, end_index).
    """
    spans = []
    stack = []
    tags = tags or []

    if all_options:
        control_tags = inline_tags = delimiters = lines = indented_blocks = True

    if lines or "[NEW_LINE]" in tags:
        last_line_break = -1
        i = 0
        while i < len(tokens):
            if tokens[i] in ("[NEW_LINE]", "[INDENT]", "[DEDENT]"):
                if tokens[i] == "[DEDENT]":
                    dedent_start = i
                    while i + 1 < len(tokens) and tokens[i + 1] == "[DEDENT]":
                        i += 1
                    if 0 <= last_line_break < dedent_start - 1:
                        spans.append((last_line_break, dedent_start - 1))
                    last_line_break = dedent_start
                else:
                    if 0 <= last_line_break < i - 1:
                        spans.append((last_line_break, i - 1))
                    last_line_break = i
            i += 1
        if last_line_break < len(tokens) - 1:
            spans.append((last_line_break, len(tokens) - 1))

    if indented_blocks or "[INDENT]" in tags:
        for i, token in enumerate(tokens):
            if token == "[INDENT]":
                stack.append(i)
            elif token == "[DEDENT]" and stack:
                start = stack.pop()
                spans.append((start, i))

    control_keywords = [
        "[IF]", "[ELIF]", "[ELSE]",
        "[FOR]", "[ASYNC_FOR]",
        "[WHILE]",
        "[DEF]", "[ASYNC_DEF]",
        "[CLASS]",
        "[TRY]", "[EXCEPT]", "[EXCEPT_STAR]", "[FINALLY]",
        "[WITH]", "[ASYNC_WITH]",
        "[MATCH]", "[CASE]", "[MATCH_DEFAULT]", "[MATCH_STAR]"
    ]

    if control_tags:
        for control_tag in control_keywords:
            spans.extend(extract_control_structure_span(tokens, control_tag))
    else:
        for tag in tags:
            if tag in control_keywords:
                spans.extend(extract_control_structure_span(tokens, tag))

    single_line_keywords = [
        "[RAISE]", "[RETURN]", "[YIELD]", "[YIELD_FROM]",
        "[ASSERT]", "[AWAIT]", "[DEL]", "[IMPORT]", "[FROM]",
        "[GLOBAL]", "[NONLOCAL]"
    ]

    if inline_tags:
        for single_tag in single_line_keywords:
            spans.extend(extract_single_line_span(tokens, single_tag))
    else:
        for tag in tags:
            if tag in single_line_keywords:
                spans.extend(extract_single_line_span(tokens, tag))

    delimiter_pairs = [
        ("[DELIMIT_1_L]", "[DELIMIT_1_R]"),
        ("[DELIMIT_2_L]", "[DELIMIT_2_R]"),
        ("[DELIMIT_3_L]", "[DELIMIT_3_R]")
    ]

    if delimiters:
        for left, right in delimiter_pairs:
            spans.extend(extract_delimited_spans(tokens, left, right))
    else:
        for left, right in delimiter_pairs:
            if left in tags or right in tags:
                spans.extend(extract_delimited_spans(tokens, left, right))

    spans = sorted(set(spans))
    return spans


if __name__ == "__main__":
    examples = open("preprocessed_dataset_100.json", "r").read().split("\n\n\n\n")

    codes = []
    with open("preprocessed_dataset_100.json", "r", encoding="utf-8") as f:
        for line in f:
            obj = json.loads(line)
            if "code" in obj:
                codes.append(obj["code"])

    examples = []

    for code in codes:
        parsed = pretokenize(ast.parse(code), _use_dedent=True, _use_semantics=True)
        new_tokens = tokenize_pretokenized_string(parsed)
        examples.append(new_tokens)


    print(codes[0])
    # print(examples[0])

    new_spans = extract_protected_spans(examples[0], all_options=True)
    print(pretty_print_tokens(examples[0]))
    # print(new_spans)
    # print(segment_tokens(examples[0], 60, new_spans))
    pretty_print_spans(examples[0], new_spans)
