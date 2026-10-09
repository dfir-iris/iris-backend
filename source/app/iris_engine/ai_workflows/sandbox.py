#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU Lesser General Public
#  License as published by the Free Software Foundation; either
#  version 3 of the License, or (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
#  Lesser General Public License for more details.
#
#  You should have received a copy of the GNU Lesser General Public License
#  along with this program; if not, write to the Free Software Foundation,
#  Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.

"""The sandbox of the `python` node: a small Python subset, interpreted.

The script is never given to `exec` / `eval` / `compile`: it is parsed
with `ast.parse` and every node of the tree is evaluated by this module,
which only knows plain data — None, booleans, numbers, strings, lists,
tuples, dicts, sets — and the functions the script defines. There is no
attribute access (only calls of whitelisted methods of those types), no
import, no class, no global / nonlocal, no `with`, no `raise`; names
starting with `_` are refused. The builtins are a fixed list of pure
functions (`len`, `sorted`, `json_parse`, `re_search`, `base64_encode` …).
So a script can transform the data it is given and nothing else: no file,
network, process, environment or Python object is reachable from it.

On top of that, every evaluation step is counted against a budget, the
size of strings, containers and integers is capped as they are built,
and in production (`ai_workflows_sandbox_execute`) the interpreter runs
in a short-lived child process — `python -I -S` running this very file,
with an empty environment, CPU / memory / file-size rlimits and a wall
clock timeout — so even a regex that backtracks forever, or a bug in this
module, only costs that child.

This module imports nothing from `app`: the child process runs it alone.
"""

import ast
import base64
import datetime
import hashlib
import ipaddress
import json
import math
import os
import re
import subprocess
import sys
import time
from urllib.parse import urlsplit

DEFAULT_MAX_STEPS = 200_000
MAX_STEPS = 2_000_000
DEFAULT_TIMEOUT_SECONDS = 5
MAX_TIMEOUT_SECONDS = 30
MAX_SOURCE_CHARS = 20_000

_MAX_STR = 1_000_000
_MAX_ITEMS = 100_000
_MAX_INT_BITS = 4096
_MAX_CALL_DEPTH = 32
_MAX_AST_DEPTH = 60
_MAX_LOGS = 50
_MAX_LOG_CHARS = 2000
_MAX_OUTPUT_BYTES = 1024 * 1024
_MAX_OUTPUT_DEPTH = 32
_MAX_PATTERN = 1000
_MEMORY_LIMIT_BYTES = 512 * 1024 * 1024

_ALLOWED_NODES = (
    ast.Module, ast.Expr, ast.Assign, ast.AugAssign, ast.If, ast.For, ast.While, ast.Break, ast.Continue,
    ast.Pass, ast.Return, ast.FunctionDef, ast.Try, ast.ExceptHandler, ast.arguments, ast.arg,
    ast.Constant, ast.Name, ast.Load, ast.Store, ast.List, ast.Tuple, ast.Dict, ast.Set,
    ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare, ast.IfExp, ast.Subscript, ast.Slice, ast.Call,
    ast.keyword, ast.Attribute, ast.Lambda, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp,
    ast.comprehension, ast.JoinedStr, ast.FormattedValue,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow, ast.BitAnd, ast.BitOr,
    ast.BitXor, ast.LShift, ast.RShift, ast.UAdd, ast.USub, ast.Not, ast.Invert, ast.And, ast.Or,
    ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Is, ast.IsNot, ast.In, ast.NotIn,
)

_STR_METHODS = frozenset((
    'lower', 'upper', 'strip', 'lstrip', 'rstrip', 'split', 'rsplit', 'join', 'replace', 'startswith',
    'endswith', 'find', 'rfind', 'count', 'title', 'capitalize', 'casefold', 'swapcase', 'isdigit',
    'isalpha', 'isalnum', 'isspace', 'islower', 'isupper', 'isnumeric', 'isdecimal', 'splitlines',
    'partition', 'rpartition', 'zfill', 'ljust', 'rjust', 'center', 'removeprefix', 'removesuffix',
    'index', 'rindex', 'encode_hex',
))
_LIST_METHODS = frozenset(('append', 'extend', 'insert', 'pop', 'remove', 'index', 'count', 'sort', 'reverse',
                           'copy', 'clear'))
_DICT_METHODS = frozenset(('get', 'keys', 'values', 'items', 'pop', 'setdefault', 'update', 'copy', 'clear'))
_SET_METHODS = frozenset(('add', 'discard', 'remove', 'union', 'intersection', 'difference',
                          'symmetric_difference', 'issubset', 'issuperset', 'isdisjoint', 'copy', 'clear',
                          'pop', 'update'))
_TUPLE_METHODS = frozenset(('index', 'count'))
_METHODS = {str: _STR_METHODS, list: _LIST_METHODS, dict: _DICT_METHODS, set: _SET_METHODS,
            tuple: _TUPLE_METHODS}
_ALL_METHODS = _STR_METHODS | _LIST_METHODS | _DICT_METHODS | _SET_METHODS | _TUPLE_METHODS

# Exceptions of the operations themselves (a missing key, int('x'), 1/0):
# script errors, which `try` / `except` may catch
_DATA_ERRORS = (ValueError, TypeError, KeyError, IndexError, ZeroDivisionError, OverflowError,
                UnicodeError, re.error)

_MAX_MESSAGE = 500
_FORMAT_SPEC = re.compile(r'^(?:.?[<>^=])?[+\- ]?#?0?(?:\d{1,3})?[,_]?(?:\.\d{1,2})?[bcdeEfFgGnosxX%]?$')
_RE_FLAGS = {'i': re.IGNORECASE, 'm': re.MULTILINE, 's': re.DOTALL, 'x': re.VERBOSE}


class SandboxError(Exception):
    """A script error: what the script did wrong, with its line."""

    def __init__(self, message, line=None):
        # A message may quote script data (a format spec): bounded
        message = message if len(message) <= _MAX_MESSAGE else f'{message[:_MAX_MESSAGE]}…'
        super().__init__(message)
        self.message = message
        self.line = line

    def describe(self) -> str:
        return f'line {self.line}: {self.message}' if self.line else self.message


class SandboxLimitError(SandboxError):
    """A budget ran out (steps, size, depth, time); never catchable."""


class SandboxFailure(SandboxError):
    """`fail(message)`: the script ends on the error port; never catchable."""


class _Return(Exception):

    def __init__(self, value):
        super().__init__()
        self.value = value


class _Break(Exception):
    pass


class _Continue(Exception):
    pass


# ---- Static check -------------------------------------------------------------

def _depth(node, limit) -> int:
    """Depth of the tree, iteratively (the parser accepts deeper trees than
    a recursive walk would)."""
    deepest = 0
    stack = [(node, 1)]
    while stack:
        current, depth = stack.pop()
        if depth > deepest:
            deepest = depth
            if deepest > limit:
                return deepest
        stack.extend((child, depth + 1) for child in ast.iter_child_nodes(current))
    return deepest


def _check_node(node, errors, callees):
    line = getattr(node, 'lineno', None)
    if not isinstance(node, _ALLOWED_NODES):
        errors.append({'line': line, 'message': f'{type(node).__name__} is not allowed'})
        return
    if isinstance(node, ast.Name) and node.id.startswith('_'):
        errors.append({'line': line, 'message': f'Names starting with "_" are not allowed ({node.id})'})
    elif isinstance(node, ast.Attribute) and id(node) not in callees:
        errors.append({'line': line, 'message': f'Attribute access is not allowed (.{node.attr}); only method '
                                                'calls such as text.lower() are'})
    elif isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Attribute):
            if func.attr.startswith('_') or func.attr not in _ALL_METHODS:
                errors.append({'line': line, 'message': f'Unknown method .{func.attr}()'})
        if any(k.arg is None for k in node.keywords):
            errors.append({'line': line, 'message': '**kwargs is not allowed'})
        if any(isinstance(a, ast.Starred) for a in node.args):
            errors.append({'line': line, 'message': '*args is not allowed'})
    elif isinstance(node, (ast.FunctionDef, ast.Lambda)):
        args = node.args
        if args.vararg or args.kwarg or args.kwonlyargs or getattr(args, 'posonlyargs', None):
            errors.append({'line': line, 'message': 'Functions only take plain parameters'})
        for arg in args.args:
            if arg.arg.startswith('_'):
                errors.append({'line': line, 'message': f'Names starting with "_" are not allowed ({arg.arg})'})
            if arg.annotation is not None:
                errors.append({'line': line, 'message': 'Annotations are not allowed'})
        if isinstance(node, ast.FunctionDef):
            if node.decorator_list:
                errors.append({'line': line, 'message': 'Decorators are not allowed'})
            if node.returns is not None:
                errors.append({'line': line, 'message': 'Annotations are not allowed'})
            if node.name.startswith('_'):
                errors.append({'line': line, 'message': f'Names starting with "_" are not allowed ({node.name})'})
    elif isinstance(node, ast.ExceptHandler):
        if node.name and node.name.startswith('_'):
            errors.append({'line': line, 'message': f'Names starting with "_" are not allowed ({node.name})'})
    elif isinstance(node, ast.Try):
        if node.finalbody:
            errors.append({'line': line, 'message': 'finally is not allowed'})
    elif isinstance(node, ast.comprehension):
        if node.is_async:
            errors.append({'line': line, 'message': 'async comprehensions are not allowed'})


def ai_workflows_sandbox_parse(source):
    """`(tree, errors)`: the parsed script, or None and `[{line, message}]`."""
    if not isinstance(source, str):
        return None, [{'line': None, 'message': 'The script must be text'}]
    if len(source) > MAX_SOURCE_CHARS:
        return None, [{'line': None, 'message': f'The script is over {MAX_SOURCE_CHARS} characters'}]
    try:
        tree = ast.parse(source, mode='exec')
    except SyntaxError as e:
        return None, [{'line': e.lineno, 'message': f'Syntax error: {e.msg}'}]
    except (ValueError, RecursionError, MemoryError):
        return None, [{'line': None, 'message': 'The script could not be parsed (too deeply nested?)'}]
    if _depth(tree, _MAX_AST_DEPTH) > _MAX_AST_DEPTH:
        return None, [{'line': None, 'message': f'The script is nested more than {_MAX_AST_DEPTH} levels deep'}]
    errors = []
    # An attribute is only allowed as the callee of a call: `text.lower()`
    callees = {id(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    for node in ast.walk(tree):
        _check_node(node, errors, callees)
        if len(errors) >= 20:
            break
    if errors:
        return None, errors
    return tree, []


def ai_workflows_sandbox_check(source) -> list:
    """Static errors of a script: `[{line, message}]`, empty when it may run."""
    return ai_workflows_sandbox_parse(source)[1]


# ---- Values ---------------------------------------------------------------------

class _Function:
    """A function the script defined (`def` or `lambda`)."""

    def __init__(self, interpreter, name, node, scope, defaults):
        self.interpreter = interpreter
        self.name = name
        self.node = node
        self.scope = scope
        self.defaults = defaults

    def __call__(self, *args, **kwargs):
        return self.interpreter.call_function(self, list(args), kwargs)

    def __repr__(self):
        return f'<function {self.name}>'


class _Builtin:

    def __init__(self, name, function):
        self.name = name
        self.function = function

    def __repr__(self):
        return f'<builtin {self.name}>'


_PLAIN = (type(None), bool, int, float, str, list, tuple, dict, set)


def _type_name(value) -> str:
    if value is None:
        return 'none'
    if isinstance(value, (_Function, _Builtin)):
        return 'function'
    return {bool: 'bool', int: 'int', float: 'float', str: 'str', list: 'list', tuple: 'tuple',
            dict: 'dict', set: 'set'}.get(type(value), 'unknown')


class _Scope:

    def __init__(self, parent=None):
        self.vars = {}
        self.parent = parent

    def lookup(self, name):
        scope = self
        while scope is not None:
            if name in scope.vars:
                return True, scope.vars[name]
            scope = scope.parent
        return False, None


# ---- Interpreter ------------------------------------------------------------------

class _Interpreter:

    def __init__(self, max_steps, deadline):
        self.max_steps = max_steps
        self.deadline = deadline
        self.steps = 0
        self.next_clock_check = 1024
        self.depth = 0
        self.logs = []
        self.line = None
        self.builtins = self._builtins()

    # -- Budgets

    def tick(self, cost=1):
        self.steps += cost
        if self.steps > self.max_steps:
            raise SandboxLimitError(f'The script ran over its budget of {self.max_steps} steps', self.line)
        if self.deadline is not None and self.steps >= self.next_clock_check:
            self.next_clock_check = self.steps + 1024
            if time.monotonic() > self.deadline:
                raise SandboxLimitError('The script ran out of time', self.line)

    def sized(self, value):
        """`value`, once its size is within the caps; building it costs
        steps in proportion to that size."""
        if isinstance(value, str):
            if len(value) > _MAX_STR:
                raise SandboxLimitError(f'A string is over {_MAX_STR} characters', self.line)
            self.tick(1 + len(value) // 256)
        elif isinstance(value, (list, tuple, dict, set)):
            if len(value) > _MAX_ITEMS:
                raise SandboxLimitError(f'A collection is over {_MAX_ITEMS} items', self.line)
            self.tick(1 + len(value) // 16)
        elif isinstance(value, int) and not isinstance(value, bool):
            if value.bit_length() > _MAX_INT_BITS:
                raise SandboxLimitError(f'An integer is over {_MAX_INT_BITS} bits', self.line)
        return value

    def error(self, message):
        return SandboxError(message, self.line)

    # -- Builtins

    def _builtins(self) -> dict:
        def b(name, function):
            return name, _Builtin(name, function)

        return dict((
            b('len', lambda value: len(self.container(value, 'len'))),
            b('str', self.to_str),
            b('repr', lambda value: self.sized(repr(self.plain(value)))),
            b('int', self.to_int),
            b('float', lambda value=0.0: float(value) if not isinstance(value, (list, dict, set, tuple)) else
              self.fail_type('float', value)),
            b('bool', lambda value=False: bool(value)),
            b('list', lambda value=(): self.sized(list(self.iterable(value)))),
            b('tuple', lambda value=(): self.sized(tuple(self.iterable(value)))),
            b('set', lambda value=(): self.sized(set(self.iterable(value)))),
            b('dict', self.to_dict),
            b('abs', abs),
            b('min', self.min_max(min)),
            b('max', self.min_max(max)),
            b('sum', lambda values, start=0: self.sized(sum(self.iterable(values), start))),
            b('round', lambda value, digits=None: round(value, digits) if digits is not None else round(value)),
            b('sorted', lambda values, key=None, reverse=False:
              self.sized(sorted(self.iterable(values), key=self.callable_or_none(key), reverse=bool(reverse)))),
            b('reversed', lambda values: self.sized(list(reversed(list(self.iterable(values)))))),
            b('enumerate', lambda values, start=0: self.sized(list(enumerate(self.iterable(values), start)))),
            b('zip', lambda *values: self.sized(list(zip(*(self.iterable(v) for v in values))))),
            b('range', self.range),
            b('any', lambda values: any(self.iterable(values))),
            b('all', lambda values: all(self.iterable(values))),
            b('map', lambda function, values: self.sized([self.callable(function)(v)
                                                          for v in self.iterable(values)])),
            b('filter', lambda function, values: self.sized([v for v in self.iterable(values)
                                                             if (self.callable(function)(v) if function is not None
                                                                 else v)])),
            b('type_of', _type_name),
            b('is_str', lambda value: isinstance(value, str)),
            b('is_number', lambda value: isinstance(value, (int, float)) and not isinstance(value, bool)),
            b('is_list', lambda value: isinstance(value, (list, tuple))),
            b('is_dict', lambda value: isinstance(value, dict)),
            b('print', self.log),
            b('fail', self.fail),
            b('json_parse', self.json_parse),
            b('json_dumps', lambda value, indent=None, sort_keys=False:
              self.sized(json.dumps(self.plain(value), indent=indent if indent in (None, 2, 4) else None,
                                    sort_keys=bool(sort_keys), ensure_ascii=False))),
            b('base64_encode', self.base64_encode),
            b('base64_decode', self.base64_decode),
            b('sha256', lambda text: hashlib.sha256(self.text(text).encode('utf-8')).hexdigest()),
            b('sha1', lambda text: hashlib.sha1(self.text(text).encode('utf-8')).hexdigest()),
            b('md5', lambda text: hashlib.md5(self.text(text).encode('utf-8')).hexdigest()),
            b('re_search', lambda pattern, text, flags='': self.match_groups(
                self.regex(pattern, flags).search(self.text(text)))),
            b('re_match', lambda pattern, text, flags='': self.match_groups(
                self.regex(pattern, flags).match(self.text(text)))),
            b('re_fullmatch', lambda pattern, text, flags='': self.match_groups(
                self.regex(pattern, flags).fullmatch(self.text(text)))),
            b('re_findall', lambda pattern, text, flags='': self.sized(
                self.regex(pattern, flags).findall(self.text(text)))),
            b('re_sub', self.re_sub),
            b('re_split', lambda pattern, text, flags='': self.sized(
                self.regex(pattern, flags).split(self.text(text)))),
            b('ip_info', self.ip_info),
            b('url_parse', self.url_parse),
            b('iso_datetime', self.iso_datetime),
            b('floor', math.floor),
            b('ceil', math.ceil),
            b('sqrt', math.sqrt),
            b('log', math.log),
        ))

    def fail_type(self, name, value):
        raise self.error(f'{name}() does not take a {_type_name(value)}')

    def container(self, value, name):
        if not isinstance(value, (str, list, tuple, dict, set)):
            self.fail_type(name, value)
        return value

    def iterable(self, value):
        if isinstance(value, dict):
            return list(value.keys())
        if isinstance(value, set):
            # A copy: a callback may change the set while it is iterated
            return list(value)
        if not isinstance(value, (str, list, tuple)):
            raise self.error(f'A {_type_name(value)} is not iterable')
        return value

    def callable(self, value):
        if isinstance(value, _Function):
            return value
        if isinstance(value, _Builtin):
            return value.function
        raise self.error(f'A {_type_name(value)} is not callable')

    def callable_or_none(self, value):
        return None if value is None else self.callable(value)

    def text(self, value):
        if not isinstance(value, str):
            raise self.error(f'Expected a string, got a {_type_name(value)}')
        return value

    def to_str(self, value=''):
        if isinstance(value, str):
            return value
        return self.sized(str(self.plain(value)))

    def to_int(self, value=0, base=None):
        if base is None:
            if isinstance(value, (list, tuple, dict, set)):
                self.fail_type('int', value)
            if isinstance(value, str) and len(value) > 1300:
                raise SandboxLimitError(f'An integer is over {_MAX_INT_BITS} bits', self.line)
            return self.sized(int(value))
        return self.sized(int(self.text(value)[:1300], base))

    def to_dict(self, pairs=None, **kwargs):
        result = {}
        if isinstance(pairs, dict):
            result.update(pairs)
        elif pairs is not None:
            for pair in self.iterable(pairs):
                if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                    raise self.error('dict() takes a dict or a list of (key, value) pairs')
                result[pair[0]] = pair[1]
        result.update(kwargs)
        return self.sized(result)

    def min_max(self, function):
        def run(*values, key=None, default=None):
            items = self.iterable(values[0]) if len(values) == 1 else values
            if not items:
                if default is not None:
                    return default
                raise self.error(f'{function.__name__}() of an empty sequence')
            return function(items, key=self.callable_or_none(key))
        return run

    def range(self, *args):
        if not 1 <= len(args) <= 3 or not all(isinstance(a, int) and not isinstance(a, bool) for a in args):
            raise self.error('range() takes 1 to 3 integers')
        values = range(*args)
        if len(values) > _MAX_ITEMS:
            raise SandboxLimitError(f'A range is over {_MAX_ITEMS} items', self.line)
        return self.sized(list(values))

    def log(self, *values):
        if len(self.logs) < _MAX_LOGS:
            self.logs.append(' '.join(self.to_str(v) for v in values)[:_MAX_LOG_CHARS])

    def fail(self, message='The script failed'):
        raise SandboxFailure(self.to_str(message)[:_MAX_LOG_CHARS], self.line)

    def json_parse(self, text):
        try:
            return self.sized(json.loads(self.text(text)))
        except RecursionError:
            raise SandboxLimitError('The JSON is nested too deeply', self.line)

    def base64_encode(self, text, urlsafe=False, padding=True):
        data = self.text(text).encode('utf-8')
        encoded = (base64.urlsafe_b64encode(data) if urlsafe else base64.b64encode(data)).decode('ascii')
        return self.sized(encoded if padding else encoded.rstrip('='))

    def base64_decode(self, text, urlsafe=False):
        data = self.text(text).strip()
        data += '=' * (-len(data) % 4)
        decoded = base64.urlsafe_b64decode(data) if urlsafe else base64.b64decode(data, validate=False)
        return self.sized(decoded.decode('utf-8', errors='replace'))

    def regex(self, pattern, flags):
        pattern = self.text(pattern)
        if len(pattern) > _MAX_PATTERN:
            raise SandboxLimitError(f'A regular expression is over {_MAX_PATTERN} characters', self.line)
        value = 0
        for flag in self.text(flags):
            if flag not in _RE_FLAGS:
                raise self.error(f'Unknown regular expression flag {flag!r} (use i, m, s or x)')
            value |= _RE_FLAGS[flag]
        self.tick(10)
        return re.compile(pattern, value)

    def match_groups(self, match):
        if match is None:
            return None
        return {'match': match.group(0), 'groups': list(match.groups()), 'named': match.groupdict(),
                'start': match.start(), 'end': match.end()}

    def re_sub(self, pattern, replacement, text, count=0, flags=''):
        compiled = self.regex(pattern, flags)
        text = self.text(text)
        replacement = self.text(replacement)
        matches = len(compiled.findall(text)) if count == 0 else int(count)
        if len(text) + matches * len(replacement) > _MAX_STR:
            raise SandboxLimitError(f'A string is over {_MAX_STR} characters', self.line)
        return self.sized(compiled.sub(replacement, text, count=int(count)))

    def ip_info(self, text):
        try:
            address = ipaddress.ip_address(self.text(text).strip())
        except ValueError:
            return None
        return {'version': address.version, 'is_private': address.is_private, 'is_global': address.is_global,
                'is_loopback': address.is_loopback, 'is_multicast': address.is_multicast,
                'is_reserved': address.is_reserved, 'compressed': address.compressed}

    def url_parse(self, text):
        try:
            parts = urlsplit(self.text(text).strip())
            port = parts.port
        except ValueError:
            return None
        return {'scheme': parts.scheme, 'host': parts.hostname, 'port': port, 'path': parts.path,
                'query': parts.query, 'fragment': parts.fragment}

    @staticmethod
    def iso_datetime(seconds):
        """A Unix timestamp as `YYYY-MM-DDTHH:MM:SSZ` (UTC), None when not one."""
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
            return None
        try:
            moment = datetime.datetime.fromtimestamp(seconds, tz=datetime.timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
        return moment.strftime('%Y-%m-%dT%H:%M:%SZ')

    # -- Conversions

    def plain(self, value, depth=0):
        """`value` as JSON-able data (tuples to lists, sets to sorted lists)."""
        if depth > _MAX_OUTPUT_DEPTH:
            raise SandboxLimitError(f'A value is nested more than {_MAX_OUTPUT_DEPTH} levels deep', self.line)
        if value is None or isinstance(value, (bool, int, str)):
            return value
        if isinstance(value, float):
            return value if math.isfinite(value) else None
        if isinstance(value, (list, tuple, set, dict)):
            self.tick(1 + len(value) // 16)
        if isinstance(value, (list, tuple)):
            return [self.plain(v, depth + 1) for v in value]
        if isinstance(value, set):
            items = [self.plain(v, depth + 1) for v in value]
            try:
                return sorted(items)
            except TypeError:
                return items
        if isinstance(value, dict):
            return {k if isinstance(k, str) else json.dumps(self.plain(k, depth + 1)): self.plain(v, depth + 1)
                    for k, v in value.items()}
        return repr(value)

    # -- Statements

    def run(self, tree, variables):
        scope = _Scope()
        scope.vars.update(variables)
        try:
            self.block(tree.body, scope)
        except _Return as r:
            return r.value
        except (_Break, _Continue):
            raise SandboxError('break / continue outside a loop', self.line)
        found, value = scope.lookup('result')
        return value if found else None

    def block(self, statements, scope):
        for statement in statements:
            self.statement(statement, scope)

    def statement(self, node, scope):
        self.line = getattr(node, 'lineno', self.line)
        self.tick()
        kind = type(node)
        if kind is ast.Expr:
            self.eval(node.value, scope)
        elif kind is ast.Assign:
            value = self.eval(node.value, scope)
            for target in node.targets:
                self.assign(target, value, scope)
        elif kind is ast.AugAssign:
            current = self.eval(_as_load(node.target), scope)
            value = self.binop(node.op, current, self.eval(node.value, scope))
            self.assign(node.target, value, scope)
        elif kind is ast.If:
            self.block(node.body if self.truth(self.eval(node.test, scope)) else node.orelse, scope)
        elif kind is ast.For:
            self.for_loop(node, scope)
        elif kind is ast.While:
            self.while_loop(node, scope)
        elif kind is ast.Break:
            raise _Break()
        elif kind is ast.Continue:
            raise _Continue()
        elif kind is ast.Pass:
            pass
        elif kind is ast.Return:
            raise _Return(self.eval(node.value, scope) if node.value is not None else None)
        elif kind is ast.FunctionDef:
            defaults = [self.eval(d, scope) for d in node.args.defaults]
            scope.vars[node.name] = _Function(self, node.name, node, scope, defaults)
        elif kind is ast.Try:
            self.try_block(node, scope)
        else:
            raise self.error(f'{kind.__name__} is not allowed')

    def for_loop(self, node, scope):
        items = list(self.iterable(self.eval(node.iter, scope)))
        broke = False
        for item in items:
            self.tick()
            self.assign(node.target, item, scope)
            try:
                self.block(node.body, scope)
            except _Break:
                broke = True
                break
            except _Continue:
                continue
        if not broke:
            self.block(node.orelse, scope)

    def while_loop(self, node, scope):
        broke = False
        while self.truth(self.eval(node.test, scope)):
            self.tick()
            try:
                self.block(node.body, scope)
            except _Break:
                broke = True
                break
            except _Continue:
                continue
        if not broke:
            self.block(node.orelse, scope)

    def try_block(self, node, scope):
        try:
            self.block(node.body, scope)
        except (SandboxLimitError, SandboxFailure):
            raise
        except (SandboxError, *_DATA_ERRORS) as e:
            if not node.handlers:
                raise
            handler = node.handlers[0]
            if handler.name:
                scope.vars[handler.name] = e.message if isinstance(e, SandboxError) else _short(e)
            self.block(handler.body, scope)
            return
        self.block(node.orelse, scope)

    def assign(self, target, value, scope):
        kind = type(target)
        if kind is ast.Name:
            scope.vars[target.id] = value
        elif kind in (ast.Tuple, ast.List):
            items = list(self.iterable(value))
            if len(items) != len(target.elts):
                raise self.error(f'Expected {len(target.elts)} values to unpack, got {len(items)}')
            for element, item in zip(target.elts, items):
                self.assign(element, item, scope)
        elif kind is ast.Subscript:
            container = self.eval(target.value, scope)
            key = self.eval(target.slice, scope)
            if isinstance(container, list):
                if isinstance(key, slice):
                    container[key] = list(self.iterable(value))
                    self.sized(container)
                elif isinstance(key, int) and not isinstance(key, bool):
                    container[key] = value
                else:
                    raise self.error('List indices must be integers')
            elif isinstance(container, dict):
                self.hashable(key)
                container[key] = value
                self.sized(container)
            else:
                raise self.error(f'A {_type_name(container)} does not support item assignment')
        else:
            raise self.error(f'Cannot assign to {kind.__name__}')

    def hashable(self, key):
        if not isinstance(key, (type(None), bool, int, float, str, tuple)):
            raise self.error(f'A {_type_name(key)} cannot be a key')
        return key

    # -- Expressions

    @staticmethod
    def truth(value) -> bool:
        return bool(value)

    def eval(self, node, scope):
        self.tick()
        kind = type(node)
        if kind is ast.Constant:
            if not isinstance(node.value, _PLAIN):
                raise self.error(f'{type(node.value).__name__} literals are not allowed')
            return node.value
        if kind is ast.Name:
            found, value = scope.lookup(node.id)
            if found:
                return value
            if node.id in self.builtins:
                return self.builtins[node.id]
            if node.id in ('True', 'False', 'None'):
                return {'True': True, 'False': False, 'None': None}[node.id]
            raise self.error(f'Unknown name {node.id}')
        if kind is ast.List:
            return self.sized([self.eval(e, scope) for e in node.elts])
        if kind is ast.Tuple:
            return self.sized(tuple(self.eval(e, scope) for e in node.elts))
        if kind is ast.Set:
            return self.sized({self.hashable(self.eval(e, scope)) for e in node.elts})
        if kind is ast.Dict:
            result = {}
            for key, value in zip(node.keys, node.values):
                if key is None:
                    raise self.error('** in a dict literal is not allowed')
                result[self.hashable(self.eval(key, scope))] = self.eval(value, scope)
            return self.sized(result)
        if kind is ast.BinOp:
            return self.binop(node.op, self.eval(node.left, scope), self.eval(node.right, scope))
        if kind is ast.UnaryOp:
            return self.unaryop(node.op, self.eval(node.operand, scope))
        if kind is ast.BoolOp:
            return self.boolop(node, scope)
        if kind is ast.Compare:
            return self.compare(node, scope)
        if kind is ast.IfExp:
            return self.eval(node.body if self.truth(self.eval(node.test, scope)) else node.orelse, scope)
        if kind is ast.Subscript:
            return self.subscript(self.eval(node.value, scope), self.eval(node.slice, scope))
        if kind is ast.Slice:
            parts = [self.eval(p, scope) if p is not None else None for p in (node.lower, node.upper, node.step)]
            if not all(p is None or (isinstance(p, int) and not isinstance(p, bool)) for p in parts):
                raise self.error('Slice bounds must be integers')
            if parts[2] == 0:
                raise self.error('A slice step cannot be zero')
            return slice(*parts)
        if kind is ast.Call:
            return self.call(node, scope)
        if kind is ast.Lambda:
            defaults = [self.eval(d, scope) for d in node.args.defaults]
            return _Function(self, 'lambda', node, scope, defaults)
        if kind in (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp):
            return self.comprehension(node, scope)
        if kind is ast.JoinedStr:
            return self.sized(''.join(self.format_part(v, scope) for v in node.values))
        if kind is ast.Attribute:
            raise self.error('Attribute access is not allowed')
        raise self.error(f'{kind.__name__} is not allowed')

    def format_part(self, node, scope):
        if isinstance(node, ast.Constant):
            return str(node.value)
        value = self.eval(node.value, scope)
        if node.conversion == ord('r'):
            value = repr(self.plain(value))
        elif node.conversion in (ord('s'), ord('a')):
            value = self.to_str(value)
        spec = ''
        if node.format_spec is not None:
            spec = ''.join(self.format_part(v, scope) for v in node.format_spec.values)
        if not spec:
            return self.to_str(value)
        if not _FORMAT_SPEC.match(spec) or not isinstance(value, (int, float, str)):
            raise self.error(f'Unsupported format {spec!r}')
        return format(value, spec)

    def binop(self, op, left, right):
        kind = type(op)
        both_ints = isinstance(left, int) and isinstance(right, int)
        if kind is ast.Mult:
            for sequence, count in ((left, right), (right, left)):
                if isinstance(sequence, (str, list, tuple)) and isinstance(count, int):
                    if len(sequence) * max(count, 0) > (_MAX_STR if isinstance(sequence, str) else _MAX_ITEMS):
                        raise SandboxLimitError('The repeated value would be too large', self.line)
            if both_ints and left.bit_length() + right.bit_length() > _MAX_INT_BITS:
                raise SandboxLimitError(f'An integer would be over {_MAX_INT_BITS} bits', self.line)
        elif kind is ast.Pow:
            if both_ints and right > 0 and abs(left) > 1 \
                    and right * max(left.bit_length(), 1) > _MAX_INT_BITS:
                raise SandboxLimitError(f'An integer would be over {_MAX_INT_BITS} bits', self.line)
            if isinstance(right, (int, float)) and abs(right) > 10_000:
                raise SandboxLimitError('The exponent is too large', self.line)
        elif kind is ast.LShift:
            if both_ints and right > 0 and left.bit_length() + right > _MAX_INT_BITS:
                raise SandboxLimitError(f'An integer would be over {_MAX_INT_BITS} bits', self.line)
        elif kind is ast.Mod and isinstance(left, str):
            raise self.error('%-formatting is not allowed; use an f-string')
        elif kind is ast.Add and isinstance(left, (str, list, tuple)) and isinstance(right, type(left)):
            limit = _MAX_STR if isinstance(left, str) else _MAX_ITEMS
            if len(left) + len(right) > limit:
                raise SandboxLimitError('The concatenated value would be too large', self.line)
        operators = {
            ast.Add: lambda a, b: a + b,
            ast.Sub: lambda a, b: a - b,
            ast.Mult: lambda a, b: a * b,
            ast.Div: lambda a, b: a / b,
            ast.FloorDiv: lambda a, b: a // b,
            ast.Mod: lambda a, b: a % b,
            ast.Pow: lambda a, b: a ** b,
            ast.BitAnd: lambda a, b: a & b,
            ast.BitOr: lambda a, b: a | b,
            ast.BitXor: lambda a, b: a ^ b,
            ast.LShift: lambda a, b: a << b,
            ast.RShift: lambda a, b: a >> b,
        }
        if kind not in operators:
            raise self.error(f'{kind.__name__} is not allowed')
        for operand in (left, right):
            if not isinstance(operand, (type(None), bool, int, float, str, list, tuple, dict, set)):
                raise self.error(f'Unsupported operand: {_type_name(operand)}')
        result = operators[kind](left, right)
        if isinstance(result, complex):
            raise self.error('The result is a complex number, which is not supported')
        return self.sized(result)

    def unaryop(self, op, value):
        kind = type(op)
        if kind is ast.Not:
            return not self.truth(value)
        if not isinstance(value, (int, float)):
            raise self.error(f'Bad operand for unary operator: {_type_name(value)}')
        if kind is ast.USub:
            return -value
        if kind is ast.UAdd:
            return +value
        if kind is ast.Invert and isinstance(value, int):
            return ~value
        raise self.error(f'{kind.__name__} is not allowed')

    def boolop(self, node, scope):
        is_and = isinstance(node.op, ast.And)
        value = None
        for operand in node.values:
            value = self.eval(operand, scope)
            if is_and and not self.truth(value):
                return value
            if not is_and and self.truth(value):
                return value
        return value

    def compare(self, node, scope):
        left = self.eval(node.left, scope)
        for op, comparator in zip(node.ops, node.comparators):
            right = self.eval(comparator, scope)
            kind = type(op)
            if kind in (ast.In, ast.NotIn):
                if not isinstance(right, (str, list, tuple, dict, set)):
                    raise self.error(f'"in" needs a string or a collection, not a {_type_name(right)}')
                if isinstance(right, str) and not isinstance(left, str):
                    raise self.error('"in <string>" needs a string on the left')
                self.tick(1 + len(right) // 64)
                result = (left in right) if kind is ast.In else (left not in right)
            elif kind is ast.Is:
                result = left is right if left is None or right is None or isinstance(left, bool) \
                    else left == right and type(left) is type(right)
            elif kind is ast.IsNot:
                result = not (left is right if left is None or right is None or isinstance(left, bool)
                              else left == right and type(left) is type(right))
            else:
                result = {
                    ast.Eq: lambda a, b: a == b,
                    ast.NotEq: lambda a, b: a != b,
                    ast.Lt: lambda a, b: a < b,
                    ast.LtE: lambda a, b: a <= b,
                    ast.Gt: lambda a, b: a > b,
                    ast.GtE: lambda a, b: a >= b,
                }[kind](left, right)
            if not result:
                return False
            left = right
        return True

    def subscript(self, container, key):
        if isinstance(container, dict):
            if isinstance(key, slice):
                raise self.error('A dict cannot be sliced')
            if key not in container:
                raise SandboxError(f'Missing key {self.to_str(key)[:100]!r}', self.line)
            return container[key]
        if isinstance(container, (str, list, tuple)):
            if isinstance(key, slice):
                return self.sized(container[key])
            if not isinstance(key, int) or isinstance(key, bool):
                raise self.error(f'Indices must be integers, not {_type_name(key)}')
            if not -len(container) <= key < len(container):
                raise SandboxError(f'Index {key} out of range', self.line)
            return container[key]
        raise self.error(f'A {_type_name(container)} cannot be indexed')

    def comprehension(self, node, scope):
        inner = _Scope(scope)
        results = []

        def walk(index):
            if index == len(node.generators):
                self.tick()
                if isinstance(node, ast.DictComp):
                    results.append((self.hashable(self.eval(node.key, inner)), self.eval(node.value, inner)))
                else:
                    results.append(self.eval(node.elt, inner))
                if len(results) > _MAX_ITEMS:
                    raise SandboxLimitError(f'A collection is over {_MAX_ITEMS} items', self.line)
                return
            generator = node.generators[index]
            for item in list(self.iterable(self.eval(generator.iter, inner))):
                self.tick()
                self.assign(generator.target, item, inner)
                if all(self.truth(self.eval(condition, inner)) for condition in generator.ifs):
                    walk(index + 1)

        walk(0)
        if isinstance(node, ast.DictComp):
            return self.sized(dict(results))
        if isinstance(node, ast.SetComp):
            return self.sized({self.hashable(v) for v in results})
        return self.sized(results)

    def call(self, node, scope):
        args = [self.eval(a, scope) for a in node.args]
        kwargs = {k.arg: self.eval(k.value, scope) for k in node.keywords}
        func = node.func
        if isinstance(func, ast.Attribute):
            return self.call_method(self.eval(func.value, scope), func.attr, args, kwargs)
        target = self.eval(func, scope)
        if isinstance(target, _Function):
            return self.call_function(target, args, kwargs)
        if isinstance(target, _Builtin):
            for value in list(args) + list(kwargs.values()):
                if isinstance(value, (str, list, tuple, dict, set)):
                    self.tick(1 + len(value) // 64)
            try:
                return target.function(*args, **kwargs)
            except (SandboxError, _Return, _Break, _Continue):
                raise
            except _DATA_ERRORS as e:
                raise SandboxError(f'{target.name}(): {_short(e)}', self.line)
        raise self.error(f'A {_type_name(target)} is not callable')

    def call_function(self, function, args, kwargs):
        self.depth += 1
        if self.depth > _MAX_CALL_DEPTH:
            self.depth -= 1
            raise SandboxLimitError(f'Functions are nested more than {_MAX_CALL_DEPTH} calls deep', self.line)
        line = self.line
        try:
            parameters = [a.arg for a in function.node.args.args]
            if len(args) > len(parameters):
                raise self.error(f'{function.name}() takes {len(parameters)} arguments, {len(args)} given')
            local = _Scope(function.scope)
            first_default = len(parameters) - len(function.defaults)
            for index, name in enumerate(parameters):
                if index < len(args):
                    if name in kwargs:
                        raise self.error(f'{function.name}() got two values for {name}')
                    local.vars[name] = args[index]
                elif name in kwargs:
                    local.vars[name] = kwargs[name]
                elif index >= first_default:
                    local.vars[name] = function.defaults[index - first_default]
                else:
                    raise self.error(f'{function.name}() is missing the argument {name}')
            unknown = [k for k in kwargs if k not in parameters]
            if unknown:
                raise self.error(f'{function.name}() has no parameter {unknown[0]}')
            if isinstance(function.node, ast.Lambda):
                return self.eval(function.node.body, local)
            try:
                self.block(function.node.body, local)
            except _Return as r:
                return r.value
            except (_Break, _Continue):
                raise self.error('break / continue outside a loop')
            return None
        finally:
            self.depth -= 1
            self.line = line

    def call_method(self, obj, name, args, kwargs):
        allowed = _METHODS.get(type(obj))
        if allowed is None or name not in allowed:
            raise self.error(f'A {_type_name(obj)} has no method {name}()')
        if isinstance(obj, (str, list, tuple, dict, set)):
            self.tick(1 + len(obj) // 64)
        if isinstance(obj, str):
            self.check_str_method(obj, name, args)
        if name == 'encode_hex':
            return obj.encode('utf-8').hex()
        if name == 'sort':
            key = self.callable_or_none(kwargs.get('key'))
            obj.sort(key=key, reverse=bool(kwargs.get('reverse', False)))
            return None
        if name in ('extend', 'update', 'union', 'intersection', 'difference', 'symmetric_difference'):
            args = [self.iterable(a) if not isinstance(a, dict) else a for a in args]
        try:
            result = getattr(obj, name)(*args, **kwargs)
        except (SandboxError, _Return, _Break, _Continue):
            raise
        except _DATA_ERRORS as e:
            raise SandboxError(f'.{name}(): {_short(e)}', self.line)
        if isinstance(obj, dict) and name in ('keys', 'values'):
            result = list(result)
        elif isinstance(obj, dict) and name == 'items':
            result = [tuple(item) for item in result]
        if isinstance(obj, (list, dict, set)):
            self.sized(obj)
        return self.sized(result) if isinstance(result, (str, list, tuple, dict, set, int)) \
            and not isinstance(result, bool) else result

    def check_str_method(self, text, name, args):
        if name == 'replace' and len(args) >= 2 and isinstance(args[0], str) and isinstance(args[1], str):
            grows = len(args[1]) - len(args[0])
            if grows > 0:
                occurrences = text.count(args[0]) if args[0] else len(text) + 1
                if len(text) + occurrences * grows > _MAX_STR:
                    raise SandboxLimitError(f'A string would be over {_MAX_STR} characters', self.line)
        elif name in ('ljust', 'rjust', 'center', 'zfill') and args:
            if not isinstance(args[0], int) or args[0] > _MAX_STR:
                raise SandboxLimitError(f'A string would be over {_MAX_STR} characters', self.line)
        elif name == 'join' and args:
            items = self.iterable(args[0])
            total = len(text) * max(len(items) - 1, 0) + sum(len(i) for i in items if isinstance(i, str))
            if total > _MAX_STR:
                raise SandboxLimitError(f'A string would be over {_MAX_STR} characters', self.line)


def _as_load(target):
    """The read form of an assignment target (for `x += 1`)."""
    if isinstance(target, ast.Name):
        return ast.Name(id=target.id, ctx=ast.Load(), lineno=getattr(target, 'lineno', 0))
    if isinstance(target, ast.Subscript):
        return ast.Subscript(value=target.value, slice=target.slice, ctx=ast.Load(),
                             lineno=getattr(target, 'lineno', 0))
    raise SandboxError('Cannot use this target with an augmented assignment')


def _short(error) -> str:
    text = str(error) or error.__class__.__name__
    return text[:300]


def _clamp(value, default, low, high) -> int:
    try:
        value = int(value)
    except (TypeError, ValueError):
        value = default
    return min(max(value, low), high)


# ---- Entry points -------------------------------------------------------------------

def ai_workflows_sandbox_run(source, variables, max_steps=DEFAULT_MAX_STEPS, timeout_seconds=None) -> dict:
    """Run `source` in this process. `variables` (JSON-able data) are its
    globals. Returns `{ok, result, logs, steps}` or `{ok: False, error,
    line, kind}`; never raises on what the script does.

    In-process runs are bounded by the step and size budgets only: the
    worker must go through `ai_workflows_sandbox_execute`, which adds the
    child process and its limits."""
    tree, errors = ai_workflows_sandbox_parse(source)
    if errors:
        first = errors[0]
        return {'ok': False, 'kind': 'invalid', 'error': first['message'], 'line': first['line'], 'logs': [],
                'steps': 0}
    max_steps = _clamp(max_steps, DEFAULT_MAX_STEPS, 1, MAX_STEPS)
    deadline = time.monotonic() + timeout_seconds if timeout_seconds else None
    interpreter = _Interpreter(max_steps, deadline)
    try:
        result = interpreter.run(tree, json.loads(json.dumps(variables if isinstance(variables, dict) else {},
                                                             default=str)))
        output = interpreter.plain(result)
        size = len(json.dumps(output, ensure_ascii=False).encode('utf-8'))
        if size > _MAX_OUTPUT_BYTES:
            raise SandboxLimitError(f'The result is over {_MAX_OUTPUT_BYTES // 1024} KB')
    except SandboxError as e:
        kind = 'limit' if isinstance(e, SandboxLimitError) else 'failed' if isinstance(e, SandboxFailure) \
            else 'error'
        return {'ok': False, 'kind': kind, 'error': e.message, 'line': e.line, 'logs': interpreter.logs,
                'steps': interpreter.steps}
    except RecursionError:
        return {'ok': False, 'kind': 'limit', 'error': 'The script is nested too deeply', 'line': interpreter.line,
                'logs': interpreter.logs, 'steps': interpreter.steps}
    except MemoryError:
        return {'ok': False, 'kind': 'limit', 'error': 'The script ran out of memory', 'line': interpreter.line,
                'logs': interpreter.logs, 'steps': interpreter.steps}
    except (*_DATA_ERRORS, RuntimeError) as e:
        return {'ok': False, 'kind': 'error', 'error': _short(e), 'line': interpreter.line,
                'logs': interpreter.logs, 'steps': interpreter.steps}
    return {'ok': True, 'result': output, 'logs': interpreter.logs, 'steps': interpreter.steps}


def ai_workflows_sandbox_execute(source, variables, max_steps=DEFAULT_MAX_STEPS,
                                 timeout_seconds=DEFAULT_TIMEOUT_SECONDS) -> dict:
    """Run `source` in a child process (`python -I -S` on this file, empty
    environment, rlimits set by the child before it reads the script),
    killed at the timeout. Same result shape as `ai_workflows_sandbox_run`."""
    timeout_seconds = _clamp(timeout_seconds, DEFAULT_TIMEOUT_SECONDS, 1, MAX_TIMEOUT_SECONDS)
    errors = ai_workflows_sandbox_check(source)
    if errors:
        return {'ok': False, 'kind': 'invalid', 'error': errors[0]['message'], 'line': errors[0]['line'],
                'logs': [], 'steps': 0}
    request = json.dumps({'source': source, 'variables': variables, 'max_steps': max_steps,
                          'timeout_seconds': timeout_seconds}, default=str)
    try:
        completed = subprocess.run(
            [sys.executable, '-I', '-S', os.path.abspath(__file__)],
            input=request.encode('utf-8'), capture_output=True, timeout=timeout_seconds + 2,
            env={'LANG': 'C.UTF-8'}, cwd='/', close_fds=True, check=False,
        )
    except subprocess.TimeoutExpired:
        return {'ok': False, 'kind': 'limit', 'error': 'The script ran out of time', 'line': None, 'logs': [],
                'steps': 0}
    except OSError as e:
        return {'ok': False, 'kind': 'error', 'error': f'The sandbox could not start ({e.__class__.__name__})',
                'line': None, 'logs': [], 'steps': 0}
    try:
        outcome = json.loads(completed.stdout.decode('utf-8')) if completed.stdout else None
    except ValueError:
        outcome = None
    if not isinstance(outcome, dict) or 'ok' not in outcome:
        # Killed by an rlimit (CPU: SIGXCPU / SIGKILL, memory: MemoryError
        # before any output could be written)
        return {'ok': False, 'kind': 'limit', 'error': 'The script was stopped (CPU time or memory limit)',
                'line': None, 'logs': [], 'steps': 0}
    return outcome


def _limit_child(timeout_seconds):
    import resource
    limits = (
        (resource.RLIMIT_CPU, (timeout_seconds + 1, timeout_seconds + 1)),
        (resource.RLIMIT_AS, (_MEMORY_LIMIT_BYTES, _MEMORY_LIMIT_BYTES)),
        (resource.RLIMIT_FSIZE, (0, 0)),
        (resource.RLIMIT_CORE, (0, 0)),
    )
    for name, value in limits:
        try:
            resource.setrlimit(name, value)
        except (ValueError, OSError):
            pass
    if hasattr(resource, 'RLIMIT_NPROC'):
        try:
            resource.setrlimit(resource.RLIMIT_NPROC, (0, 0))
        except (ValueError, OSError):
            pass


def _child_main():
    try:
        request = json.loads(sys.stdin.buffer.read(16 * 1024 * 1024 + 1).decode('utf-8'))
        timeout_seconds = _clamp(request.get('timeout_seconds'), DEFAULT_TIMEOUT_SECONDS, 1, MAX_TIMEOUT_SECONDS)
    except (ValueError, AttributeError):
        sys.stdout.write(json.dumps({'ok': False, 'kind': 'error', 'error': 'Bad sandbox request', 'line': None,
                                     'logs': [], 'steps': 0}))
        return
    _limit_child(timeout_seconds)
    outcome = ai_workflows_sandbox_run(request.get('source'), request.get('variables'),
                                       max_steps=request.get('max_steps'), timeout_seconds=timeout_seconds)
    sys.stdout.write(json.dumps(outcome, ensure_ascii=False))


if __name__ == '__main__':
    _child_main()
