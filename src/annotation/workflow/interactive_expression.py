"""Small, non-executing expression parser for A-004 function specifications.

The frontend repeats this allow-list with math.js before compiling a formula.
Keeping a local parser here lets the workflow reject unsafe or mathematically
undefined candidates before a browser is started.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Literal


_NUMBER = re.compile(r"(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_ALLOWED_FUNCTIONS = {"abs", "cos", "exp", "log", "sin", "sqrt", "tan"}


@dataclass(frozen=True)
class Token:
    kind: Literal["number", "identifier", "operator", "eof"]
    value: str


ExpressionAst = tuple[object, ...]


def _tokens(value: str) -> list[Token]:
    tokens: list[Token] = []
    index = 0
    while index < len(value):
        character = value[index]
        if character.isspace():
            index += 1
            continue
        number = _NUMBER.match(value, index)
        if number is not None:
            tokens.append(Token("number", number.group(0)))
            index = number.end()
            continue
        identifier = _IDENTIFIER.match(value, index)
        if identifier is not None:
            tokens.append(Token("identifier", identifier.group(0)))
            index = identifier.end()
            continue
        if character in "+-*/^(),":
            tokens.append(Token("operator", character))
            index += 1
            continue
        raise ValueError(f"unsupported character: {character!r}")
    tokens.append(Token("eof", ""))
    return tokens


class _Parser:
    def __init__(self, tokens: list[Token]) -> None:
        self.tokens = tokens
        self.index = 0

    @property
    def current(self) -> Token:
        return self.tokens[self.index]

    def _consume(self, value: str | None = None) -> Token:
        token = self.current
        if value is not None and token.value != value:
            raise ValueError(f"expected {value!r}, found {token.value!r}")
        self.index += 1
        return token

    def parse(self) -> ExpressionAst:
        result = self._additive()
        if self.current.kind != "eof":
            raise ValueError(f"unexpected token: {self.current.value!r}")
        return result

    def _additive(self) -> ExpressionAst:
        result = self._multiplicative()
        while self.current.value in {"+", "-"}:
            operator = self._consume().value
            result = ("binary", operator, result, self._multiplicative())
        return result

    def _multiplicative(self) -> ExpressionAst:
        result = self._unary()
        while self.current.value in {"*", "/"}:
            operator = self._consume().value
            result = ("binary", operator, result, self._unary())
        return result

    def _unary(self) -> ExpressionAst:
        if self.current.value in {"+", "-"}:
            return ("unary", self._consume().value, self._unary())
        return self._power()

    def _power(self) -> ExpressionAst:
        result = self._primary()
        if self.current.value == "^":
            self._consume("^")
            result = ("binary", "^", result, self._unary())
        return result

    def _primary(self) -> ExpressionAst:
        token = self.current
        if token.kind == "number":
            self._consume()
            return ("number", float(token.value))
        if token.value == "(":
            self._consume("(")
            result = self._additive()
            self._consume(")")
            return result
        if token.kind != "identifier":
            raise ValueError(f"expected a number, x, or allowed function; found {token.value!r}")
        name = self._consume().value
        if name == "x":
            return ("variable", "x")
        if name not in _ALLOWED_FUNCTIONS:
            raise ValueError(f"unsupported identifier: {name}")
        self._consume("(")
        argument = self._additive()
        if self.current.value == ",":
            raise ValueError(f"function {name} must have exactly one argument")
        self._consume(")")
        return ("call", name, argument)


def parse_restricted_expression(value: str) -> ExpressionAst:
    """Return an AST for the tiny A-004 formula language or raise ValueError."""

    normalized = value.strip()
    if not normalized:
        raise ValueError("formula is empty")
    if len(normalized) > 500:
        raise ValueError("formula exceeds the maximum length")
    return _Parser(_tokens(normalized)).parse()


def evaluate_restricted_expression(ast: ExpressionAst, x: float) -> float | None:
    """Evaluate a parsed AST without exposing Python or math.js evaluation APIs."""

    try:
        tag = ast[0]
        if tag == "number":
            value = float(ast[1])
        elif tag == "variable":
            value = float(x)
        elif tag == "unary":
            operand = evaluate_restricted_expression(ast[2], x)  # type: ignore[arg-type]
            if operand is None:
                return None
            value = operand if ast[1] == "+" else -operand
        elif tag == "binary":
            left = evaluate_restricted_expression(ast[2], x)  # type: ignore[arg-type]
            right = evaluate_restricted_expression(ast[3], x)  # type: ignore[arg-type]
            if left is None or right is None:
                return None
            operator = ast[1]
            if operator == "+":
                value = left + right
            elif operator == "-":
                value = left - right
            elif operator == "*":
                value = left * right
            elif operator == "/":
                value = left / right
            elif operator == "^":
                value = left ** right
            else:
                return None
        elif tag == "call":
            argument = evaluate_restricted_expression(ast[2], x)  # type: ignore[arg-type]
            if argument is None:
                return None
            function = ast[1]
            if function == "abs":
                value = abs(argument)
            elif function == "cos":
                value = math.cos(argument)
            elif function == "exp":
                value = math.exp(argument)
            elif function == "log":
                value = math.log(argument)
            elif function == "sin":
                value = math.sin(argument)
            elif function == "sqrt":
                value = math.sqrt(argument)
            elif function == "tan":
                value = math.tan(argument)
            else:
                return None
        else:
            return None
    except (ArithmeticError, OverflowError, ValueError, TypeError):
        return None
    return value if math.isfinite(value) else None


def function_domain_probe_errors(
    formula: str,
    *,
    domain_start: float,
    domain_end: float,
    sample_points: list[float],
    excluded_points: list[float],
) -> list[str]:
    """Check declared samples plus a fixed grid, skipping explicit holes."""

    try:
        ast = parse_restricted_expression(formula)
    except ValueError as exc:
        return [f"函数表达式不在受限语法范围内：{exc}。"]
    if domain_start >= domain_end:
        return ["函数定义域起点必须小于终点。"]
    probe_points = list(sample_points)
    step = (domain_end - domain_start) / 32
    probe_points.extend(domain_start + step * index for index in range(33))
    errors: list[str] = []
    for point in probe_points:
        if any(math.isclose(point, excluded, rel_tol=0.0, abs_tol=1e-9) for excluded in excluded_points):
            continue
        if evaluate_restricted_expression(ast, point) is None:
            rendered = f"{point:.8g}"
            errors.append(f"函数在声明定义域的探针 x={rendered} 处无有限值，需收紧定义域或声明排除点。")
    return list(dict.fromkeys(errors))


__all__ = [
    "evaluate_restricted_expression",
    "function_domain_probe_errors",
    "parse_restricted_expression",
]
