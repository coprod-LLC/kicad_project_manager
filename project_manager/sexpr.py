"""Read KiCad expression boundaries without rewriting board files."""
from __future__ import annotations

import json
import re


class ExpressionError(ValueError):
    pass


TOKEN = re.compile(r'\s+|\#[^\r\n]*|"(?:\\.|[^"\\])*"|[()]|[^\s()"#]+')


def tokens(text: str):
    end = 0
    for match in TOKEN.finditer(text):
        if match.start() != end:
            raise ExpressionError('Unclosed or unsupported quoted string.')
        end = match.end()
        token = match[0]
        if not token.isspace() and not token.startswith('#'):
            yield token, match.start(), end
    if end != len(text):
        raise ExpressionError('Unclosed or unsupported quoted string.')


def spans(text: str, *, children: bool = False):
    """Yield ``(name, start, end)`` for top-level forms, or children of one form."""
    target = 2 if children else 1
    depth, roots, start, name = 0, 0, 0, ''
    for token, left, right in tokens(text):
        if token == '(':
            if depth == 0:
                roots += 1
            depth += 1
            if depth == target:
                start, name = left, ''
        elif token == ')':
            if depth <= 0:
                raise ExpressionError('Unbalanced parentheses.')
            if depth == target:
                if not name:
                    raise ExpressionError('Empty expression.')
                yield name, start, right
            depth -= 1
        elif depth == 0:
            raise ExpressionError('Unexpected text outside an expression.')
        elif depth == target and not name:
            name = token
    if depth or (children and roots != 1):
        raise ExpressionError('Expected balanced KiCad expressions.')


def forms(text: str, *, children: bool = False):
    """Yield top-level forms, or immediate children of one enclosing form."""
    for name, start, end in spans(text, children=children):
        yield name, text[start:end]


def atoms(text: str) -> list[str]:
    result, depth = [], 0
    for token, _, _ in tokens(text):
        if token == '(':
            depth += 1
        elif token == ')':
            depth -= 1
        elif depth == 1:
            try:
                result.append(json.loads(token) if token.startswith('"') else token)
            except ValueError as exc:
                raise ExpressionError('Unsupported escape in a KiCad field.') from exc
    return result


def canonical(text: str) -> str:
    """Comparable expression text, independent of indentation and comments."""
    lines, depth = [], 0
    for token, _, _ in tokens(text):
        if token == '(':
            lines.append('  ' * depth + '(')
            depth += 1
        elif token == ')':
            depth -= 1
            lines[-1] += ')'
        else:
            lines[-1] += ('' if lines[-1].endswith('(') else ' ') + token
    return '\n'.join(lines) + '\n' if lines else ''
