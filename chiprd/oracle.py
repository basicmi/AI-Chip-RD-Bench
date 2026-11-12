"""Independent floating-point oracle and deterministic stimulus generation.

This is the evaluator's test oracle. Agent-provided testbench results are not trusted.
The public repository's tests are *not* secret. Production hidden tests require a
separate, access-controlled evaluator service.
"""
from __future__ import annotations

import math
import random

SCALE = 1 << 14          # signed Q2.14 output
ANGLE_SCALE = 1 << 13    # signed Q3.13 angle, radians
MAX_ANGLE = round((math.pi / 2) * ANGLE_SCALE)
DEFAULT_TOLERANCE = 32  # output LSB, includes finite-precision CORDIC truncation


def vectors(seed: int = 20261112, count: int = 24) -> list[int]:
    """Deterministic, distinct inputs, including sign and range boundaries."""
    if count < 8:
        raise ValueError("count must be >= 8")
    fixed = [0, 1, -1, MAX_ANGLE, -MAX_ANGLE,
             ANGLE_SCALE, -ANGLE_SCALE, ANGLE_SCALE // 2,
             -(ANGLE_SCALE // 2), MAX_ANGLE - 1, -MAX_ANGLE + 1]
    rng = random.Random(seed)
    result = list(dict.fromkeys(fixed))
    while len(result) < count:
        a = rng.randint(-MAX_ANGLE, MAX_ANGLE)
        if a not in result:
            result.append(a)
    return result[:count]


def expected(angle: int) -> tuple[int, int]:
    if not -MAX_ANGLE <= angle <= MAX_ANGLE:
        raise ValueError("angle outside supported [-pi/2, pi/2]")
    theta = angle / ANGLE_SCALE
    return round(math.cos(theta) * SCALE), round(math.sin(theta) * SCALE)


def check(angle: int, x: int, y: int, tolerance: int = DEFAULT_TOLERANCE) -> bool:
    ex, ey = expected(angle)
    return abs(x - ex) <= tolerance and abs(y - ey) <= tolerance
