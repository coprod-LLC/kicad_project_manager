"""Change selected JSON values without reformatting the rest of the file."""
from __future__ import annotations

import json
import re


class JsonTextError(ValueError):
    pass


_STRING = re.compile(r'"(?:\\.|[^"\\])*"')
_NUMBER = re.compile(r'-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?')


class Node:
    __slots__ = ('kind', 'start', 'end', 'keys', 'items')

    def __init__(self, kind: str, start: int, end: int, keys=None, items=None):
        self.kind = kind
        self.start = start
        self.end = end
        self.keys = keys or []
        self.items = items or []


def _skip(text: str, index: int) -> int:
    while index < len(text) and text[index] in ' \t\r\n':
        index += 1
    return index


def _parse(text: str, index: int) -> tuple[Node, int]:
    index = _skip(text, index)
    if index >= len(text):
        raise JsonTextError('Incomplete JSON.')
    mark = text[index]
    if mark == '{':
        return _object(text, index)
    if mark == '[':
        return _array(text, index)
    if mark == '"':
        match = _STRING.match(text, index)
        if not match:
            raise JsonTextError('Unclosed JSON string.')
        return Node('string', index, match.end()), match.end()
    for literal in ('true', 'false', 'null'):
        if text.startswith(literal, index):
            return Node('atom', index, index + len(literal)), index + len(literal)
    match = _NUMBER.match(text, index)
    if not match:
        raise JsonTextError('Invalid JSON value.')
    return Node('atom', index, match.end()), match.end()


def _object(text: str, index: int) -> tuple[Node, int]:
    start, index, keys = index, index + 1, []
    while True:
        index = _skip(text, index)
        if index < len(text) and text[index] == '}':
            return Node('object', start, index + 1, keys=keys), index + 1
        match = _STRING.match(text, index)
        if not match:
            raise JsonTextError('Expected a JSON key.')
        try:
            key = json.loads(match.group())
        except ValueError as exc:
            raise JsonTextError('Invalid JSON key.') from exc
        if any(existing == key for existing, *_rest in keys):
            raise JsonTextError(f'The project file repeats the JSON key {key}.')
        index = _skip(text, match.end())
        if index >= len(text) or text[index] != ':':
            raise JsonTextError('Expected a colon.')
        value, index = _parse(text, index + 1)
        keys.append((key, match.start(), match.end(), value))
        index = _skip(text, index)
        if index < len(text) and text[index] == ',':
            index += 1
            continue
        if index < len(text) and text[index] == '}':
            return Node('object', start, index + 1, keys=keys), index + 1
        raise JsonTextError('Expected a comma or closing brace.')


def _array(text: str, index: int) -> tuple[Node, int]:
    start, index, items = index, index + 1, []
    while True:
        index = _skip(text, index)
        if index < len(text) and text[index] == ']':
            return Node('array', start, index + 1, items=items), index + 1
        value, index = _parse(text, index)
        items.append(value)
        index = _skip(text, index)
        if index < len(text) and text[index] == ',':
            index += 1
            continue
        if index < len(text) and text[index] == ']':
            return Node('array', start, index + 1, items=items), index + 1
        raise JsonTextError('Expected a comma or closing bracket.')


def parse_document(text: str) -> Node:
    node, index = _parse(text, 0)
    if _skip(text, index) != len(text):
        raise JsonTextError('Unexpected text after JSON.')
    return node


def _descend(node: Node, path: tuple[str, ...]) -> Node:
    current, walked = node, []
    for key in path:
        walked.append(key)
        label = '.'.join(walked)
        if current.kind != 'object':
            raise JsonTextError(f'This project has no {label} value.')
        matches = [item for item in current.keys if item[0] == key]
        if len(matches) != 1:
            raise JsonTextError(f'This project has no {label} value.')
        current = matches[0][3]
    return current


def _indent_at(text: str, index: int) -> str:
    line = text.rfind('\n', 0, index) + 1
    width = index
    while width > line and text[width - 1] in ' \t':
        width -= 1
    return text[width:index] if width == line or text[line:width].strip() == '' else ''


def _same(current, new) -> bool:
    if isinstance(current, bool) or isinstance(new, bool):
        return type(current) is bool and type(new) is bool and current == new
    return current == new


def _literal(value) -> str:
    return json.dumps(value, ensure_ascii=False)


def _apply(text: str, ops: list[tuple[int, int, str]]) -> str:
    ordered = sorted(ops)
    for (start, end, _repl), (next_start, _next_end, _next) in zip(ordered, ordered[1:]):
        if end > next_start:
            raise JsonTextError('Could not update the project file.')
    crlf = '\r\n' in text
    for start, end, repl in sorted(ops, reverse=True):
        if crlf:
            repl = repl.replace('\n', '\r\n')
        text = text[:start] + repl + text[end:]
    parse_document(text)
    return text


def _value_ops(text: str, node: Node, values: dict) -> list[tuple[int, int, str]]:
    if node.kind != 'object':
        raise JsonTextError('Expected a JSON object.')
    current = {}
    located = {}
    for key, _key_start, _key_end, value in node.keys:
        current[key] = json.loads(text[value.start:value.end])
        located[key] = value
    ops, inserted = [], []
    for key, new in values.items():
        if key in current:
            if not _same(current[key], new):
                value = located[key]
                ops.append((value.start, value.end, _literal(new)))
        else:
            inserted.append((key, new))
    if not inserted:
        return ops
    rendered = [_literal(key) + ': ' + _literal(value) for key, value in inserted]
    multiline = '\n' in text[node.start:node.end]
    if not node.keys:
        if multiline:
            base = _indent_at(text, node.end - 1)
            inner = base + '  '
            body = (',\n' + inner).join(rendered)
            ops.append((node.start + 1, node.end - 1, '\n' + inner + body + '\n' + base))
        else:
            ops.append((node.start + 1, node.end - 1, ', '.join(rendered)))
        return ops
    last = node.keys[-1][3]
    if multiline:
        indent = _indent_at(text, node.keys[-1][1])
        ops.append((last.end, last.end, ''.join(',\n' + indent + item for item in rendered)))
    else:
        ops.append((last.end, last.end, ', ' + ', '.join(rendered)))
    return ops


def update_object(text: str, path: tuple[str, ...], values: dict) -> str:
    """Replace or add keys in the object at ``path``. Other text stays as it is."""
    if not values:
        return text
    node = _descend(parse_document(text), path)
    ops = _value_ops(text, node, values)
    return text if not ops else _apply(text, ops)


def _shifted(value: dict, indent: str) -> str:
    raw = json.dumps(value, ensure_ascii=False, indent=2)
    return '\n'.join(indent + line for line in raw.split('\n'))


def merge_named_list(text: str, path: tuple[str, ...], items: list[dict], name_key: str = 'name') -> str:
    """Merge objects by name into an array. Missing names are appended. Nothing is deleted."""
    if not items:
        return text
    array = _descend(parse_document(text), path)
    if array.kind != 'array':
        raise JsonTextError(f'{ ".".join(path)} must be a list.')
    found = {}
    for item in array.items:
        if item.kind != 'object':
            continue
        names = [entry for entry in item.keys if entry[0] == name_key]
        if len(names) != 1 or names[0][3].kind != 'string':
            continue
        try:
            name = json.loads(text[names[0][3].start:names[0][3].end])
        except ValueError as exc:
            raise JsonTextError(f'{ ".".join(path)} contains an unreadable name.') from exc
        if name in found:
            raise JsonTextError(f'{name} is defined more than once.')
        found[name] = item
    ops, missing = [], []
    for item in items:
        if name_key not in item or not isinstance(item[name_key], str):
            raise JsonTextError('Each entry needs a name.')
        name = item[name_key]
        if name in found:
            ops.extend(_value_ops(text, found[name], {key: value for key, value in item.items() if key != name_key}))
        else:
            missing.append(item)
    if missing:
        multiline = '\n' in text[array.start:array.end]
        if not array.items:
            if multiline:
                indent = _indent_at(text, array.end - 1) + '  '
                body = ',\n'.join(_shifted(item, indent) for item in missing)
                ops.append((array.start + 1, array.end - 1, '\n' + body + '\n' + _indent_at(text, array.end - 1)))
            else:
                ops.append((array.start + 1, array.end - 1, ', '.join(_literal(item) for item in missing)))
        else:
            last = array.items[-1].end
            if multiline:
                indent = _indent_at(text, array.items[0].start)
                snippet = ''.join(',\n' + _shifted(item, indent) for item in missing)
            else:
                snippet = ''.join(', ' + _literal(item) for item in missing)
            ops.append((last, last, snippet))
    return text if not ops else _apply(text, ops)
