"""Resolve stable Node.js release lines without executing repository input."""

from __future__ import annotations

import re

Version = tuple[int, int, int]
Interval = tuple[Version, Version | None]
DEFAULT_NODE_IMAGE = "node:20-bookworm-slim"
ALTERNATIVE_NODE_MAJORS = (22, 24)
_ZERO: Version = (0, 0, 0)
_PART = r"(?:0|[1-9][0-9]{0,5}|[xX*])"
_NUMBER = rf"v?{_PART}(?:\.{_PART}){{0,2}}"
_COMPARATOR = re.compile(rf"(>=|<=|>|<|=|\^|~)?\s*({_NUMBER})(?=\s|$)")
_NODE_IMAGE = re.compile(
    r"(?:docker\.io/(?:library/)?)?node:(\d+(?:\.\d+){0,2})"
    r"(?:-(?:(?:bookworm|bullseye|trixie)(?:-slim)?|slim|alpine(?:\d+\.\d+)?))?"
)


def _next(version: Version, index: int) -> Version:
    parts = (*version[:index], version[index] + 1, *(0,) * (2 - index))
    return parts


def _partial(value: str) -> tuple[Version, int]:
    fields = value.removeprefix("v").split(".")
    numbers = []
    for field in fields:
        if field in {"x", "X", "*"}:
            break
        numbers.append(int(field))
    if any(field not in {"x", "X", "*"} for field in fields[len(numbers):]):
        raise ValueError("Invalid Node.js version range")
    parts = tuple(numbers + [0] * (3 - len(numbers)))
    return parts, len(numbers)


def _interval(operator: str, value: str) -> Interval:
    start, length = _partial(value)
    if not length:
        if operator in {"", "=", ">=", "<=", "^", "~"}:
            return _ZERO, None
        return _ZERO, _ZERO
    end = _next(start, length - 1)
    if operator in {"", "="}:
        return start, end
    if operator == ">=":
        return start, None
    if operator == ">":
        return end, None
    if operator == "<":
        return _ZERO, start
    if operator == "<=":
        return _ZERO, end
    if operator == "~":
        return start, _next(start, min(1, length - 1))
    # A caret allows changes to the right of the first nonzero component.
    index = next((i for i in range(length) if start[i]), length - 1)
    return start, _next(start, index)


def _parse_range(constraint: str) -> list[Interval]:
    ranges = []
    for alternative in constraint.split("||"):
        clause = alternative.strip()
        hyphen = re.fullmatch(rf"({_NUMBER})\s+-\s+({_NUMBER})", clause)
        if hyphen:
            lower, _ = _partial(hyphen[1])
            upper, length = _partial(hyphen[2])
            ranges.append((lower, _next(upper, length - 1) if length else None))
            continue
        lower, upper = _ZERO, None
        while clause:
            comparator = _COMPARATOR.match(clause)
            if comparator is None:
                raise ValueError(
                    "Unsupported Node.js range in engines.node. Use a stable npm version range."
                )
            start, end = _interval(comparator[1] or "", comparator[2])
            lower = max(lower, start)
            if end is not None:
                upper = end if upper is None else min(upper, end)
            clause = clause[comparator.end():].lstrip()
        ranges.append((lower, upper))
    return ranges


def _covered(version: str, ranges: list[Interval]) -> bool:
    # Both the Docker tag and setup-node can float within this version prefix.
    # Use it only if every stable version in the prefix satisfies one range.
    start, length = _partial(version)
    end = _next(start, length - 1)
    return any(lo <= start and (hi is None or end <= hi) for lo, hi in ranges)


def select_node_runtime(constraint: str | None, base_image: str) -> tuple[str, str]:
    """Return the Docker image and corresponding Actions setup-node version."""
    image_match = _NODE_IMAGE.fullmatch(base_image)
    configured_version = image_match[1] if image_match else None
    if constraint is None or not constraint.strip():
        return base_image, configured_version or "20"

    if len(constraint) > 200:
        raise ValueError("Node.js range in engines.node is too long.")
    ranges = _parse_range(constraint)
    if configured_version and _covered(configured_version, ranges):
        return base_image, configured_version
    if base_image == DEFAULT_NODE_IMAGE:
        for major in ALTERNATIVE_NODE_MAJORS:
            version = str(major)
            if _covered(version, ranges):
                return f"node:{version}-bookworm-slim", version
    raise ValueError(
        "No compatible Node.js version for engines.node in the deployment profile. "
        "Choose a profile image with a version covered by the declared range."
    )
