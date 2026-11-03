"""Deterministic, platform-independent random number generation.

Simulations must be exactly reproducible from a seed on any machine. Two common sources of
irreproducibility are avoided here:

* Library-defined distributions (``random.gauss``, NumPy ``Generator`` methods) may change
  their algorithms between versions.
* Transcendental functions (``math.log``, ``math.cos``) come from the platform's C math library
  and can differ in the last bits between operating systems.

The generator therefore draws raw 64-bit words from SplitMix64 and builds every distribution
from IEEE-754 operations that are exactly rounded everywhere (``+ - * /`` and ``sqrt``), with a
self-contained natural logarithm. Same seed and same call sequence give bit-identical results on
every platform and Python version.
"""

import hashlib
import math
import struct
from collections.abc import Sequence
from typing import Final, TypeVar

_MASK64: Final = (1 << 64) - 1
_LN2: Final = 0.6931471805599453  # Nearest double to ln(2).
_SQRT2: Final = 1.4142135623730951
_INV_2_53: Final = 1.0 / (1 << 53)

T = TypeVar("T")


def deterministic_log(x: float) -> float:
    """Natural logarithm computed with exactly rounded arithmetic only.

    Accurate to a few ulps for positive finite ``x``; raises ValueError otherwise. Uses
    x = m * 2**e with m in [sqrt(1/2), sqrt(2)), then ln(m) = 2 * atanh(s) with s = (m-1)/(m+1),
    |s| < 0.172, summed as an odd power series to below double precision.
    """
    if not (x > 0.0 and math.isfinite(x)):
        raise ValueError(f"deterministic_log: argument must be positive and finite, got {x!r}")
    mantissa, exponent = math.frexp(x)  # Exact: x = mantissa * 2**exponent, 0.5 <= mantissa < 1.
    if mantissa < _SQRT2 / 2.0:
        mantissa *= 2.0
        exponent -= 1
    s = (mantissa - 1.0) / (mantissa + 1.0)
    s2 = s * s
    term = s
    total = 0.0
    k = 1
    while True:  # Converges in at most ~12 terms since s**2 < 0.0295.
        contribution = term / k
        if abs(contribution) < 1e-18 * abs(total) or contribution == 0.0:
            break
        total += contribution
        term *= s2
        k += 2
    return exponent * _LN2 + 2.0 * total


class DeterministicRng:
    """SplitMix64 generator with platform-independent distributions."""

    __slots__ = ("_state",)

    def __init__(self, seed: int) -> None:
        if not 0 <= seed <= _MASK64:
            raise ValueError("seed must be an integer in [0, 2**64)")
        self._state = seed

    @classmethod
    def derive(cls, seed: int, *stream: int | str) -> "DeterministicRng":
        """An independent generator for a named stream (e.g. one per participant)."""
        hasher = hashlib.blake2b(digest_size=8)
        hasher.update(struct.pack("<Q", seed & _MASK64))
        for label in stream:
            encoded = str(label).encode()
            hasher.update(struct.pack("<I", len(encoded)))
            hasher.update(encoded)
        return cls(int.from_bytes(hasher.digest(), "little"))

    def next_u64(self) -> int:
        self._state = (self._state + 0x9E3779B97F4A7C15) & _MASK64
        z = self._state
        z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & _MASK64
        z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & _MASK64
        return z ^ (z >> 31)

    def random(self) -> float:
        """Uniform on [0, 1) with 53 bits of resolution."""
        return (self.next_u64() >> 11) * _INV_2_53

    def uniform(self, low: float, high: float) -> float:
        if not low <= high:
            raise ValueError("uniform: low must not exceed high")
        return low + (high - low) * self.random()

    def randint(self, low: int, high: int) -> int:
        """Uniform integer in [low, high] (inclusive), without modulo bias."""
        if low > high:
            raise ValueError("randint: low must not exceed high")
        span = high - low + 1
        if span > 1 << 64:
            raise ValueError("randint: range wider than 2**64")
        if span & (span - 1) == 0:  # Power of two: take the low bits directly.
            return low + (self.next_u64() & (span - 1))
        limit = (1 << 64) - ((1 << 64) % span)  # Largest multiple of span not above 2**64.
        while True:
            value = self.next_u64()
            if value < limit:
                return low + value % span

    def bernoulli(self, probability: float) -> bool:
        if not 0.0 <= probability <= 1.0:
            raise ValueError("bernoulli: probability must lie in [0, 1]")
        return self.random() < probability

    def exponential(self, rate: float) -> float:
        """Exponential with the given rate (mean 1 / rate), by inversion."""
        if not rate > 0.0:
            raise ValueError("exponential: rate must be positive")
        return -deterministic_log(1.0 - self.random()) / rate  # 1 - U lies in (0, 1].

    def normal(self, mean: float = 0.0, stddev: float = 1.0) -> float:
        """Normal via the Marsaglia polar method (needs only log and the exact sqrt)."""
        if not stddev >= 0.0:
            raise ValueError("normal: stddev must be non-negative")
        while True:
            u = 2.0 * self.random() - 1.0
            v = 2.0 * self.random() - 1.0
            s = u * u + v * v
            if 0.0 < s < 1.0:
                return mean + stddev * u * math.sqrt(-2.0 * deterministic_log(s) / s)

    def choice(self, items: Sequence[T]) -> T:
        if not items:
            raise ValueError("choice: empty sequence")
        return items[self.randint(0, len(items) - 1)]
