"""Resolve Compose credential expressions without evaluating shell commands."""

from collections.abc import Mapping
from io import StringIO
from pathlib import Path
import re

from dotenv.parser import parse_stream


_VARIABLE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_EXPRESSION = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)(?:(:?[-+?])(.*))?", re.DOTALL)


def interpolate(value: str, variables: Mapping[str, str], depth: int = 0) -> str:
    """Handle Compose substitutions, defaults, alternatives, requirements and $$ escapes."""
    if depth > 20:
        raise ValueError("Credential interpolation exceeds the supported nesting limit")
    output: list[str] = []
    index = 0
    while index < len(value):
        if value[index] != "$":
            output.append(value[index])
            index += 1
            continue
        if value.startswith("$$", index):
            output.append("$")
            index += 2
            continue
        if value.startswith("${", index):
            end, nesting = index + 2, 1
            while end < len(value) and nesting:
                if value[end] == "{":
                    nesting += 1
                elif value[end] == "}":
                    nesting -= 1
                end += 1
            expression = (
                _EXPRESSION.fullmatch(value[index + 2 : end - 1])
                if not nesting
                else None
            )
            if expression is None:
                raise ValueError("Unsupported credential interpolation expression")
            name, operator, operand = expression.groups()
            present = name in variables
            actual = variables.get(name, "")
            usable = present and (
                bool(actual) if operator and operator.startswith(":") else True
            )
            if not operator:
                replacement = actual
            elif operator.endswith("-"):
                replacement = (
                    actual if usable else interpolate(operand, variables, depth + 1)
                )
            elif operator.endswith("+"):
                replacement = (
                    interpolate(operand, variables, depth + 1) if usable else ""
                )
            elif usable:
                replacement = actual
            else:
                raise ValueError(
                    "A required credential interpolation variable is missing"
                )
            output.append(replacement)
            index = end
            continue
        variable = _VARIABLE.match(value, index + 1)
        if variable is not None:
            output.append(variables.get(variable.group(), ""))
            index = variable.end()
        else:
            output.append("$")
            index += 1
    return "".join(output)


def dotenv_variables(path: Path, inherited: Mapping[str, str]) -> dict[str, str]:
    """Parse quoted dotenv values and expand non-literal entries in file order."""
    if not path.is_file():
        return {}
    text = path.read_text()
    values: dict[str, str] = {}
    for binding in parse_stream(StringIO(text)):
        if binding.error:
            normalized = re.sub(
                r"^(\s*(?:export\s+)?[A-Za-z_][A-Za-z0-9_]*\s*):\s*",
                r"\1=",
                binding.original.string,
                count=1,
            )
            converted = list(parse_stream(StringIO(normalized)))
            if (
                normalized == binding.original.string
                or len(converted) != 1
                or converted[0].error
            ):
                raise ValueError(
                    "Unsupported dotenv syntax in credential configuration"
                )
            binding = converted[0]
        if binding.key is None or binding.value is None:
            continue
        raw_value = binding.original.string.split("=", 1)[-1].lstrip()
        values[binding.key] = (
            binding.value
            if raw_value.startswith("'")
            else interpolate(binding.value, {**values, **inherited})
        )
    return values
