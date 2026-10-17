"""Edit a small, explicit subset of YAML without reserializing KiBot templates.

Only single-line scalar fields in a top-level block are supported. Unknown or
ambiguous structures fail closed; the rest of the document remains byte-for-byte.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import re


class SetupError(ValueError):
    """An actionable project setup problem."""


@dataclass(frozen=True)
class Field:
    value: str
    start: int
    end: int


def plain_text(value: str) -> bool:
    """Conservative plain-string subset shared by YAML 1.1 and 1.2 readers."""
    return bool(value and value == value.strip()
                and (value[0].isalpha() or value[0] == '_')
                and value.lower() not in {'y', 'n', 'yes', 'no', 'on', 'off', 'true', 'false', 'null'}
                and not re.search(r':(?:\s|$)|\s#|[\[\]{}\x85\u2028\u2029]', value))


def scalar(raw: str) -> tuple[str, int]:
    """Return a scalar's text and its end offset, excluding trailing comments."""
    if not raw or raw[0] in '|>&*!{[':
        raise SetupError('Use a single-line text value for this field.')
    if raw.startswith("'"):
        match = re.match(r"'((?:[^']|'')*)'", raw)
        if not match:
            raise SetupError('Unclosed single-quoted value.')
        value, end = match[1].replace("''", "'"), match.end()
    elif raw.startswith('"'):
        # JSON strings are a safe subset of YAML double-quoted scalars.
        try:
            value, end = json.JSONDecoder().raw_decode(raw)
        except ValueError as exc:
            raise SetupError('Unsupported double-quoted value; use plain text or single quotes.') from exc
        if not isinstance(value, str):
            raise SetupError('Expected a text value.')
    else:
        comment = re.search(r'(^|\s)#', raw)
        end = comment.start() if comment else len(raw)
        end = len(raw[:end].rstrip())
        value = raw[:end]
        if not value or re.search(r':(?:\s|$)', value):
            raise SetupError('Expected a single-line scalar, not an empty or nested value.')
    remainder = raw[end:]
    if remainder.strip() and not re.fullmatch(r'\s+#.*', remainder):
        raise SetupError('Unexpected text after a quoted value.')
    return value, end


class ScalarBlock:
    def __init__(self, data: bytes, block: str, keys: tuple[str, ...], *, optional: tuple[str, ...] = ()):
        self.data = data
        self.optional = optional
        keys = tuple(dict.fromkeys((*keys, *optional)))
        try:
            self.text = data.decode('utf-8')
        except UnicodeError as exc:
            raise SetupError('Configuration must use UTF-8 encoding.') from exc
        header = re.compile(r'^\ufeff?' + re.escape(block) + r':[ \t]*(?:#[^\r\n]*)?\r?$', re.M)
        headers = list(header.finditer(self.text))
        named_blocks = re.findall(r'^\ufeff?(?:' + re.escape(block) + r"|'" + re.escape(block)
                                  + r"'|\"" + re.escape(block) + r'\")[ \t]*:', self.text, re.M)
        if len(headers) != 1 or len(named_blocks) != 1:
            raise SetupError(f'Expected exactly one top-level {block}: block.')
        start = headers[0].end()
        end = len(self.text)
        lines = list(re.finditer(r'[^\r\n]+', self.text[start:]))
        children = []
        for line in lines:
            content = line[0]
            if not content.strip() or content.lstrip().startswith('#'):
                continue
            if not content.startswith(' '):
                end = start + line.start()
                break
            if '\t' in content[:len(content) - len(content.lstrip())]:
                raise SetupError('Tab indentation is unsupported.')
            children.append((start + line.start(), content))
        indent = min((len(s) - len(s.lstrip(' ')) for _, s in children), default=0)
        self.fields: dict[str, Field] = {}
        for offset, content in children:
            if re.match(r' {' + str(indent) + r'}[\'\"](?:' + '|'.join(map(re.escape, keys)) + r')[\'\"][ \t]*:', content):
                raise SetupError('Quoted managed keys are unsupported; use one unquoted key per field.')
            match = re.match(r' {' + str(indent) + r'}([A-Za-z_][\w]*)[ \t]*:[ \t]*', content)
            if not match or match[1] not in keys:
                continue
            key = match[1]
            if key in self.fields:
                raise SetupError(f'Duplicate {block}.{key}; edit the file to resolve it.')
            try:
                value, length = scalar(content[match.end():])
            except SetupError as exc:
                raise SetupError(f'{block}.{key}: {exc}') from exc
            value_start = offset + match.end()
            self.fields[key] = Field(value, value_start, value_start + length)
            # A continuation below a managed scalar would change its meaning.
            following = self.text[offset + len(content):end].splitlines()
            for continuation in following:
                if not continuation.strip() or continuation.lstrip().startswith('#'):
                    continue
                if len(continuation) - len(continuation.lstrip(' ')) > indent:
                    raise SetupError(f'{block}.{key}: multiline values are unsupported.')
                break
        self.insert_at = end
        self.indent = indent or 2
        missing = set(keys) - self.fields.keys() - set(optional)
        if missing:
            raise SetupError(f'Missing {block} field(s): {", ".join(sorted(missing))}.')

    @property
    def values(self) -> dict[str, str]:
        return {**dict.fromkeys(self.optional, ''),
                **{key: field.value for key, field in self.fields.items()}}

    def replace(self, values: dict[str, str], *, prefer_plain: tuple[str, ...] = ()) -> bytes:
        if values.keys() - self.fields.keys() - set(self.optional):
            raise SetupError('Attempt to change an unmanaged field.')
        result = self.text
        additions = []
        for key in self.optional:
            if key not in self.fields and values.get(key, ''):
                value = values[key]
                if not isinstance(value, str) or any(ord(c) < 32 for c in value):
                    raise SetupError(f'{key} must be single-line text.')
                encoded = value if key in prefer_plain and plain_text(value) else json.dumps(value, ensure_ascii=False)
                additions.append(' ' * self.indent + key + ': ' + encoded)
        if additions:
            newline = '\r\n' if '\r\n' in result else '\n'
            prefix = '' if result[:self.insert_at].endswith('\n') else newline
            result = (result[:self.insert_at] + prefix + newline.join(additions) + newline
                      + result[self.insert_at:])
        for key, field in sorted(self.fields.items(), key=lambda item: item[1].start, reverse=True):
            if key not in values or values[key] == field.value:
                continue
            value = values[key]
            if not isinstance(value, str) or any(ord(c) < 32 for c in value):
                raise SetupError(f'{key} must be single-line text.')
            original = self.text[field.start:field.end]
            if key in prefer_plain and plain_text(value):
                encoded = value
            elif original.startswith('"'):
                encoded = json.dumps(value, ensure_ascii=False)
            elif not original.startswith("'") and plain_text(value):
                encoded = value
            else:
                encoded = "'" + value.replace("'", "''") + "'"
            result = result[:field.start] + encoded + result[field.end:]
        updated = result.encode('utf-8')
        return updated
