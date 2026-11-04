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
# ln(2) split in two (as in fdlibm): the high part has only 32 significant bits, so
# exponent * _LN2_HI is exact for every double's exponent, and _LN2_LO carries the rest.
_LN2_HI: Final = 6.93147180369123816490e-01
_LN2_LO: Final = 1.90821492927058770002e-10
_SQRT2: Final = 1.4142135623730951
_INV_2_53: Final = 1.0 / (1 << 53)

T = TypeVar("T")


def deterministic_log(x: float) -> float:
    """Natural logarithm computed with exactly rounded arithmetic only.

    Within 2 ulps of the true value for positive finite ``x`` (checked against a 60-digit
    reference); raises ValueError otherwise. Uses x = m * 2**e with m in [sqrt(1/2), sqrt(2)),
    then ln(m) = 2 * atanh(s) = 2s + 2s * z * (1/3 + z/5 + z**2/7 + ...) with s = (m-1)/(m+1),
    z = s**2 < 0.0295. The series is evaluated in Horner form, smallest term first, and
    e * ln(2) is added in two parts, so rounding errors do not accumulate.
    """
    if not (x > 0.0 and math.isfinite(x)):
        raise ValueError(f"deterministic_log: argument must be positive and finite, got {x!r}")
    mantissa, exponent = math.frexp(x)  # Exact: x = mantissa * 2**exponent, 0.5 <= mantissa < 1.
    if mantissa < _SQRT2 / 2.0:
        mantissa *= 2.0
        exponent -= 1
    s = (mantissa - 1.0) / (mantissa + 1.0)
    z = s * s
    series = 0.0
    for k in range(27, 1, -2):  # z**13 / 29 < 1e-21: far below double precision.
        series = series * z + 1.0 / k
    return exponent * _LN2_HI + (2.0 * s + (2.0 * s * z * series + exponent * _LN2_LO))


_EXP_OVERFLOW: Final = 709.782712893384  # Just below ln(DBL_MAX).
_EXP_UNDERFLOW: Final = -745.1332191019412  # Below this, exp rounds to zero.
_INV_FACTORIALS: Final = tuple(1.0 / math.factorial(k) for k in range(15, 1, -1))


def deterministic_exp(x: float) -> float:
    """e**x computed with exactly rounded arithmetic only (within 2 ulps; platform-independent).

    Uses x = k*ln(2) + r with |r| <= ln(2)/2, where k*ln(2) is subtracted in two parts so the
    reduction is exact, then a degree-14 Taylor polynomial for e**r in Horner form, then an exact
    scaling by 2**k. Raises OverflowError above ln(DBL_MAX), like math.exp.
    """
    if math.isnan(x):
        raise ValueError("deterministic_exp: argument is NaN")
    if x > _EXP_OVERFLOW:
        raise OverflowError(f"deterministic_exp: {x!r} overflows")
    if x < _EXP_UNDERFLOW:
        return 0.0
    k = round(x / (_LN2_HI + _LN2_LO))
    r = (x - k * _LN2_HI) - k * _LN2_LO
    poly = 0.0
    for coefficient in _INV_FACTORIALS:  # 1/15!, 1/14!, ..., 1/2!
        poly = poly * r + coefficient
    return math.ldexp(1.0 + (r + r * r * poly), k)


class DeterministicRng:
    """SplitMix64 generator with platform-independent distributions."""

    __slots__ = ("_state",)

    def __init__(self, seed: int) -> None:
        if not 0 <= seed <= _MASK64:
            raise ValueError("seed must be an integer in [0, 2**64)")
        self._state = seed

    @classmethod
    def derive(cls, seed: int, *stream: int | str) -> "DeterministicRng":
        """An independent generator for a named stream (e.g. one per participant). Labels are
        encoded with their type, so the int 3 and the string "3" name different streams."""
        if not 0 <= seed <= _MASK64:
            raise ValueError("seed must be an integer in [0, 2**64)")
        hasher = hashlib.blake2b(digest_size=8)
        hasher.update(struct.pack("<Q", seed))
        for label in stream:
            if isinstance(label, bool) or not isinstance(label, int | str):
                raise TypeError(f"stream labels must be int or str, not {type(label).__name__}")
            encoded = (b"i" if isinstance(label, int) else b"s") + str(label).encode()
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
