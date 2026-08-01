from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Any

import pandas as pd


ALLOWED_FUNCTIONS = {"abs": abs, "min": min, "max": max}
ALLOWED_NODES = (
    ast.Expression,
    ast.BoolOp,
    ast.BinOp,
    ast.UnaryOp,
    ast.Compare,
    ast.Name,
    ast.Load,
    ast.Constant,
    ast.And,
    ast.Or,
    ast.Not,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.Mod,
    ast.Pow,
    ast.USub,
    ast.Eq,
    ast.NotEq,
    ast.Gt,
    ast.GtE,
    ast.Lt,
    ast.LtE,
    ast.Call,
)


@dataclass(frozen=True)
class CompiledExpression:
    expression: str
    code: Any
    names: set[str]

    def evaluate(self, row: pd.Series) -> Any:
        context = {name: row[name] for name in self.names}
        context.update(ALLOWED_FUNCTIONS)
        return eval(self.code, {"__builtins__": {}}, context)


def compile_expression(expression: str, supported_names: set[str]) -> CompiledExpression:
    tree = ast.parse(expression, mode="eval")
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ALLOWED_NODES):
            raise ValueError(f"unsupported syntax: {type(node).__name__}")
        if isinstance(node, ast.Name):
            names.add(node.id)
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in ALLOWED_FUNCTIONS:
                raise ValueError("unsupported function call")
    unknown = names.difference(supported_names).difference(ALLOWED_FUNCTIONS)
    if unknown:
        raise ValueError(f"unsupported names: {sorted(unknown)}")
    code = compile(tree, "<strategy-expression>", "eval")
    return CompiledExpression(expression=expression, code=code, names=names.difference(ALLOWED_FUNCTIONS))
