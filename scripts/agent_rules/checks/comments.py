from __future__ import annotations

import io
import re
import tokenize
from dataclasses import dataclass
from pathlib import PurePosixPath

from agent_rules.violation import Violation

RULE = "comment"
GENERATED_MARKER = re.compile(r"^(//|#) Code generated .* DO NOT EDIT\.$", re.MULTILINE)
ENCODING_DECLARATION = re.compile(r"^#.*coding[:=]\s*[-\w.]+")
SLASH_DIRECTIVES = ("//go:", "//nolint", "// Code generated", "// @ts-", "/// <reference")
HASH_DIRECTIVES = ("# noqa", "# type:", "# pragma")
GO_STRING_QUOTES = ('"', "'")
GO_RAW_QUOTE = "`"
SCRIPT_STRING_QUOTES = ('"', "'")
SCRIPT_TEMPLATE_QUOTE = "`"
SNIPPET_LENGTH = 60
REGEX_PRECEDING_CHARACTERS = frozenset("(,=:[!&|?{};+-*%<>~^")
REGEX_PRECEDING_KEYWORDS = frozenset({"return", "typeof", "case", "void", "in", "of"})
TRAILING_WORD = re.compile(r"[A-Za-z_$][\w$]*\Z")


@dataclass(frozen=True)
class Comment:
    line: int
    text: str


def is_allowed_slash_comment(comment: Comment) -> bool:
    return comment.text.startswith(SLASH_DIRECTIVES)


def is_allowed_hash_comment(comment: Comment) -> bool:
    if comment.text.startswith(HASH_DIRECTIVES):
        return True
    if comment.line == 1 and comment.text.startswith("#!"):
        return True
    return comment.line <= 2 and ENCODING_DECLARATION.match(comment.text) is not None


def skip_quoted(text: str, start: int, quote: str, escapes: bool) -> int:
    index = start + 1
    while index < len(text):
        character = text[index]
        if escapes and character == "\\":
            index += 2
            continue
        if character == quote:
            return index + 1
        if character == "\n" and quote != "`":
            return index
        index += 1
    return index


def starts_regex_literal(text: str, index: int) -> bool:
    position = index - 1
    while position >= 0 and text[position] in " \t\r\n":
        position -= 1
    if position < 0 or text[position] in REGEX_PRECEDING_CHARACTERS:
        return True
    word = TRAILING_WORD.search(text, max(0, position - 16), position + 1)
    return word is not None and word.group(0) in REGEX_PRECEDING_KEYWORDS


def skip_regex_literal(text: str, start: int) -> int:
    index = start + 1
    in_character_class = False
    while index < len(text) and text[index] != "\n":
        character = text[index]
        if character == "\\":
            index += 2
            continue
        if character == "[":
            in_character_class = True
        elif character == "]":
            in_character_class = False
        elif character == "/" and not in_character_class:
            return index + 1
        index += 1
    return index


def slash_comments(
    text: str,
    string_quotes: tuple[str, ...],
    raw_quote: str,
    raw_escapes: bool,
    regex_literals: bool = False,
) -> list[Comment]:
    comments: list[Comment] = []
    index = 0
    while index < len(text):
        character = text[index]
        if character in string_quotes:
            index = skip_quoted(text, index, character, escapes=True)
        elif character == raw_quote:
            index = skip_quoted(text, index, character, escapes=raw_escapes)
        elif text.startswith("//", index):
            end = text.find("\n", index)
            end = len(text) if end == -1 else end
            comments.append(Comment(text.count("\n", 0, index) + 1, text[index:end].rstrip()))
            index = end
        elif text.startswith("/*", index):
            end = text.find("*/", index + 2)
            end = len(text) if end == -1 else end + 2
            comments.append(Comment(text.count("\n", 0, index) + 1, text[index:end]))
            index = end
        elif regex_literals and character == "/" and starts_regex_literal(text, index):
            index = skip_regex_literal(text, index)
        else:
            index += 1
    return comments


def go_comments(text: str) -> list[Comment]:
    return slash_comments(text, GO_STRING_QUOTES, GO_RAW_QUOTE, raw_escapes=False)


def script_comments(text: str) -> list[Comment]:
    return slash_comments(
        text, SCRIPT_STRING_QUOTES, SCRIPT_TEMPLATE_QUOTE, raw_escapes=True, regex_literals=True
    )


def python_comments(text: str) -> list[Comment]:
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, SyntaxError):
        return []
    return [
        Comment(token.start[0], token.string) for token in tokens if token.type == tokenize.COMMENT
    ]


def comments_in(path: str, text: str) -> list[Comment] | None:
    suffix = PurePosixPath(path).suffix
    if suffix == ".go":
        return [comment for comment in go_comments(text) if not is_allowed_slash_comment(comment)]
    if suffix in (".js", ".mjs"):
        return [
            comment for comment in script_comments(text) if not is_allowed_slash_comment(comment)
        ]
    if suffix == ".py":
        return [
            comment for comment in python_comments(text) if not is_allowed_hash_comment(comment)
        ]
    return None


def is_generated(text: str) -> bool:
    return GENERATED_MARKER.search(text[:2000]) is not None


def snippet(comment: Comment) -> str:
    first_line = comment.text.splitlines()[0] if comment.text else ""
    if len(first_line) > SNIPPET_LENGTH:
        return first_line[:SNIPPET_LENGTH] + "..."
    return first_line


def comment_violation(path: str, comment: Comment) -> Violation:
    message = f"remove the comment and let names carry it: {snippet(comment)}"
    return Violation(path, comment.line, RULE, message)


def comment_violations(path: str, text: str) -> list[Violation]:
    if is_generated(text):
        return []
    comments = comments_in(path, text) or []
    return [comment_violation(path, comment) for comment in comments]
