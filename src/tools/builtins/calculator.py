"""
Builtin tool: Safe arithmetic calculator.

Tool ID: ``math.calculator``

Evaluates arithmetic expressions using a strict AST-based safe evaluator.
The ``eval()`` built-in is NEVER used on untrusted input.  Only a tiny
whitelist of operations and functions is permitted.
"""

from __future__ import annotations

import ast
import math
import operator
from typing import TYPE_CHECKING, Union

import structlog

from src.tools.manifests import ResourceLimits, RuntimeConfig, SideEffects, ToolManifest

if TYPE_CHECKING:
    from src.tools.registry import ToolExecutionContext

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_EXPRESSION_LENGTH: int = 200

# ---------------------------------------------------------------------------
# Safe evaluator
# ---------------------------------------------------------------------------

# Mapping of AST operator nodes to their Python equivalents
_BINARY_OPS: dict[type, object] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.FloorDiv: operator.floordiv,
}

_UNARY_OPS: dict[type, object] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}

# Whitelist of safe built-in math functions that may be called by name
_SAFE_FUNCTIONS: dict[str, object] = {
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "asin": math.asin,
    "acos": math.acos,
    "atan": math.atan,
    "atan2": math.atan2,
    "sqrt": math.sqrt,
    "log": math.log,
    "log2": math.log2,
    "log10": math.log10,
    "exp": math.exp,
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "floor": math.floor,
    "ceil": math.ceil,
    "pow": math.pow,
    "hypot": math.hypot,
    "factorial": math.factorial,
    "gcd": math.gcd,
    "degrees": math.degrees,
    "radians": math.radians,
    "pi": math.pi,
    "e": math.e,
    "tau": math.tau,
    "inf": math.inf,
}

# Type alias for safe numeric result
_Numeric = Union[int, float]


class _SafeEvaluator(ast.NodeVisitor):
    """
    Walk an AST and evaluate only whitelisted numeric operations.

    Any node type that is not explicitly handled raises
    :class:`UnsafeExpressionError` — this guarantees that arbitrary code
    (imports, attribute access, calls to non-whitelisted functions, etc.)
    is always rejected.
    """

    def visit_Expression(self, node: ast.Expression) -> _Numeric:
        """Entry point — evaluate the body of an expression statement."""
        return self.visit(node.body)

    def visit_Constant(self, node: ast.Constant) -> _Numeric:
        """Allow numeric literals only (int, float). Reject strings, bytes, etc."""
        if not isinstance(node.value, (int, float)):
            raise UnsafeExpressionError(
                f"Literal of type '{type(node.value).__name__}' is not allowed."
            )
        return node.value  # type: ignore[return-value]

    def visit_BinOp(self, node: ast.BinOp) -> _Numeric:
        """Handle binary arithmetic operations."""
        op_fn = _BINARY_OPS.get(type(node.op))
        if op_fn is None:
            raise UnsafeExpressionError(
                f"Binary operator '{type(node.op).__name__}' is not allowed."
            )
        left = self.visit(node.left)
        right = self.visit(node.right)
        try:
            result = op_fn(left, right)  # type: ignore[operator]
        except ZeroDivisionError:
            raise ZeroDivisionError("Division by zero.")
        except OverflowError:
            raise OverflowError("Arithmetic overflow.")
        return result  # type: ignore[return-value]

    def visit_UnaryOp(self, node: ast.UnaryOp) -> _Numeric:
        """Handle unary + and - operators."""
        op_fn = _UNARY_OPS.get(type(node.op))
        if op_fn is None:
            raise UnsafeExpressionError(
                f"Unary operator '{type(node.op).__name__}' is not allowed."
            )
        operand = self.visit(node.operand)
        return op_fn(operand)  # type: ignore[operator,return-value]

    def visit_Call(self, node: ast.Call) -> _Numeric:
        """Allow calls to whitelisted math functions only."""
        if not isinstance(node.func, ast.Name):
            raise UnsafeExpressionError(
                "Only direct function calls are allowed (e.g. sqrt(4), not obj.method())."
            )
        func_name = node.func.id
        fn = _SAFE_FUNCTIONS.get(func_name)
        if fn is None or not callable(fn):
            raise UnsafeExpressionError(
                f"Function '{func_name}' is not in the allowed function list."
            )
        if node.keywords:
            raise UnsafeExpressionError("Keyword arguments are not allowed in expressions.")
        args = [self.visit(arg) for arg in node.args]
        try:
            result = fn(*args)  # type: ignore[operator]
        except (ValueError, OverflowError) as exc:
            raise ValueError(f"Math error in '{func_name}': {exc}") from exc
        return result  # type: ignore[return-value]

    def visit_Name(self, node: ast.Name) -> _Numeric:
        """Allow whitelisted named constants (pi, e, tau, inf)."""
        value = _SAFE_FUNCTIONS.get(node.id)
        if value is None or callable(value):
            raise UnsafeExpressionError(
                f"Name '{node.id}' is not an allowed constant."
            )
        return value  # type: ignore[return-value]

    def generic_visit(self, node: ast.AST) -> _Numeric:  # type: ignore[override]
        """Reject any AST node type not explicitly whitelisted."""
        raise UnsafeExpressionError(
            f"AST node type '{type(node).__name__}' is not allowed in expressions."
        )


class UnsafeExpressionError(ValueError):
    """Raised when the expression contains disallowed AST nodes."""


def _safe_eval(expression: str) -> _Numeric:
    """
    Parse and safely evaluate an arithmetic expression string.

    Uses :class:`_SafeEvaluator` to walk the AST — ``eval()`` is never called
    on the raw string.

    Args:
        expression: A string expression (max :data:`MAX_EXPRESSION_LENGTH` chars).

    Returns:
        The numeric result (int or float).

    Raises:
        UnsafeExpressionError: If the expression contains disallowed constructs.
        SyntaxError: If the expression cannot be parsed.
        ZeroDivisionError: On division by zero.
        OverflowError: On arithmetic overflow.
    """
    if len(expression) > MAX_EXPRESSION_LENGTH:
        raise ValueError(
            f"Expression exceeds maximum length of {MAX_EXPRESSION_LENGTH} characters."
        )

    try:
        tree = ast.parse(expression.strip(), mode="eval")
    except SyntaxError as exc:
        raise SyntaxError(f"Invalid expression syntax: {exc}") from exc

    evaluator = _SafeEvaluator()
    return evaluator.visit(tree)


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------


async def calculator_handler(
    payload: dict,
    context: "ToolExecutionContext",
) -> dict:
    """
    Evaluate an arithmetic expression and return the result.

    Input payload keys:
    - ``expression`` (str, required): The arithmetic expression to evaluate.
      Maximum length: 200 characters.

    Security constraints applied:
    1. Expression is parsed to an AST — ``eval()`` is never called.
    2. Only numeric literals, whitelisted operators, and whitelisted math
       functions are accepted; everything else is rejected.
    3. Length is capped at 200 characters before parsing.

    Returns a dict with: ``result``, ``expression``, ``error``.
    """
    log = logger.bind(
        tool_id="math.calculator",
        execution_id=context.execution_id,
        agent_id=context.agent_id,
    )

    expression: str = str(payload.get("expression", "")).strip()
    log.info("calculator.evaluate", expression_len=len(expression))

    error: str | None = None
    result: _Numeric | None = None

    try:
        result = _safe_eval(expression)
        # Normalise: if result is float but whole number, convert to int for cleanliness
        if isinstance(result, float) and result.is_integer() and not math.isinf(result):
            result = int(result)
    except (UnsafeExpressionError, SyntaxError, ValueError, ZeroDivisionError, OverflowError) as exc:
        error = str(exc)
        log.warning("calculator.error", error=error)
    except Exception as exc:
        error = f"Unexpected evaluation error: {type(exc).__name__}"
        log.error("calculator.unexpected_error", error=str(exc))

    return {
        "result": result,
        "expression": expression,
        "error": error,
    }


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


def get_manifest() -> ToolManifest:
    """Return the :class:`ToolManifest` for the ``math.calculator`` tool."""
    return ToolManifest(
        tool_id="math.calculator",
        name="Safe Arithmetic Calculator",
        version="1.0.0",
        description=(
            "Evaluates arithmetic expressions safely using an AST-based evaluator. "
            "Supports basic operators (+, -, *, /, **, %, //) and whitelisted math "
            "functions (sin, cos, sqrt, log, abs, round, min, max, and more). "
            "eval() is never called on user input."
        ),
        capabilities=["math", "arithmetic", "calculator"],
        input_schema={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "required": ["expression"],
            "additionalProperties": False,
            "properties": {
                "expression": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 200,
                    "description": (
                        "Arithmetic expression to evaluate. "
                        "Allowed: numbers, +, -, *, /, **, %, //, parentheses, "
                        "and functions: sin, cos, tan, sqrt, log, abs, round, min, max, "
                        "floor, ceil, pow, exp, pi, e, tau."
                    ),
                    "examples": [
                        "2 + 2",
                        "sqrt(144)",
                        "sin(pi / 2)",
                        "(3 ** 4) / 2",
                        "round(3.14159, 2)",
                    ],
                },
            },
        },
        output_schema={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "required": ["result", "expression", "error"],
            "properties": {
                "result": {
                    "type": ["number", "null"],
                    "description": "The numeric result, or null if evaluation failed.",
                },
                "expression": {
                    "type": "string",
                    "description": "The evaluated expression (echoed back).",
                },
                "error": {
                    "type": ["string", "null"],
                    "description": "Error message if evaluation failed, otherwise null.",
                },
            },
        },
        risk_level="low",
        side_effects=SideEffects.NONE,
        permissions=[],
        runtime=RuntimeConfig(type="builtin"),
        limits=ResourceLimits(
            timeout_seconds=5,
            max_memory_mb=32,
            max_output_kb=16,
        ),
    )


CALCULATOR_MANIFEST = get_manifest()
calculate = calculator_handler
