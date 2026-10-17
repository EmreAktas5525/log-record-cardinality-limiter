"""
Core tracking logic.

The design choice is to track distinct (attribute_key, attribute_value)
pairs independently per key. If a ceiling of 1000 is set and 1000
user_ids and 1000 request_paths have already been seen, a new value for
either key overflows. This is chosen over tracking the product space
(key1=A, key2=B) because the product space grows unboundedly under
normal log rotation and is almost never what an operator trying to cap
log volume actually wants.
"""

from __future__ import annotations

from typing import Dict, FrozenSet, Iterable, Tuple


class BucketedRecord:
    """
    Immutable structure returned for a record that overflowed.

    Attributes:
        attributes: the attributes that were accepted before overflow.
            This set may be empty if the first checked key overflowed.
        overflowing_keys: the keys for which the new (key, value) pair
            was new to its bucket and would have pushed that bucket past
            the ceiling. Callers can use this to emit a marker such as
            ``{key: "<overflow>"}``.

    We return a frozen set of keys rather than a list so callers can do
    cheap membership checks without worrying about ordering or mutation.
    The attributes are returned as a plain dict because they are the
    caller's own data re-handed back; copying is the caller's job.
    """

    __slots__ = ("attributes", "overflowing_keys")

    def __init__(
        self,
        attributes: Dict[str, str],
        overflowing_keys: FrozenSet[str],
    ) -> None:
        self.attributes = attributes
        self.overflowing_keys = overflowing_keys

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, BucketedRecord):
            return NotImplemented
        return (
            self.attributes == other.attributes
            and self.overflowing_keys == other.overflowing_keys
        )

    def __repr__(self) -> str:
        return (
            f"BucketedRecord(attributes={self.attributes!r}, "
            f"overflowing_keys={set(self.overflowing_keys)!r})"
        )


class CardinalityLimiter:
    """
    Track per-key distinct values and gate records past a ceiling.

    Args:
        ceiling: maximum number of distinct values retained per key.
            Must be >= 0. A ceiling of 0 means every value is rejected
            on first sight — useful when the caller only wants to see
            overflow.
        bucket_strategy: one of "drop" or "mark".
            - "drop": `process` returns ``None`` for any record in which
              any key overflows. Already-seen values for other keys are
              not evicted; only the in-flight record is suppressed.
            - "mark": `process` returns a `BucketedRecord` whose
              `overflowing_keys` lists the keys that would have been new
              beyond the ceiling. Values for those keys are NOT added to
              the bucket. Values for non-overflowing keys ARE added (if
              new) so subsequent identical records pass cleanly.

    Why ``bucket_strategy`` rather than per-record configuration: an
    operator sets a policy once, at logger setup time, and the limiter
    applies it uniformly. Per-record strategy would couple caller and
    limiter in a way that defeats the purpose.

    Values are hashed for set membership only; they are never stored in
    a way that requires serialization, so non-string values work too as
    long as they are hashable.
    """

    __slots__ = ("_ceiling", "_strategy", "_buckets")

    def __init__(
        self,
        ceiling: int,
        bucket_strategy: str = "mark",
    ) -> None:
        if not isinstance(ceiling, int) or isinstance(ceiling, bool):
            raise TypeError("ceiling must be an int")
        if ceiling < 0:
            raise ValueError("ceiling must be >= 0")
        if bucket_strategy not in ("drop", "mark"):
            raise ValueError(
                f"bucket_strategy must be 'drop' or 'mark', got {bucket_strategy!r}"
            )
        self._ceiling = ceiling
        self._strategy = bucket_strategy
        self._buckets: Dict[str, set] = {}

    @property
    def ceiling(self) -> int:
        return self._ceiling

    @property
    def strategy(self) -> str:
        return self._strategy

    def bucket_size(self, key: str) -> int:
        """Number of distinct values currently tracked for ``key``."""
        return len(self._buckets.get(key, ()))

    def known_value(self, key: str, value) -> bool:
        """True if ``value`` is already in the bucket for ``key``."""
        bucket = self._buckets.get(key)
        return bucket is not None and value in bucket

    def reset(self) -> None:
        """Clear all tracked buckets."""
        self._buckets.clear()

    def process(
        self, attributes: Dict[str, object]
    ) -> Dict[str, object] | None | BucketedRecord:
        """
        Apply the configured strategy to ``attributes``.

        Returns:
            - For ``bucket_strategy="drop"``: ``None`` if any key's new
              value would exceed the ceiling; otherwise the unchanged
              attributes dict.
            - For ``bucket_strategy="mark"``: a `BucketedRecord` if at
              least one key overflowed, otherwise the unchanged
              attributes dict.

        Iteration is over ``list(attributes.items())`` to avoid mutation
        during iteration in case the caller reuses the same dict object
        between calls (a common pattern in hot logging paths).
        """
        if self._strategy == "drop":
            return self._process_drop(attributes)
        return self._process_mark(attributes)

    def _would_overflow(self, key: str, value) -> bool:
        """True if ``value`` is new and the bucket is already at ceiling."""
        bucket = self._buckets.get(key)
        if bucket is None:
            # First ever value for this key. Overflow only if ceiling
            # is 0.
            return self._ceiling == 0
        if value in bucket:
            return False
        return len(bucket) >= self._ceiling

    def _admit(self, key: str, value) -> None:
        """Record ``value`` against ``key``'s bucket if not present."""
        bucket = self._buckets.get(key)
        if bucket is None:
            bucket = set()
            self._buckets[key] = bucket
        bucket.add(value)

    def _process_drop(
        self, attributes: Dict[str, object]
    ) -> Dict[str, object] | None:
        """
        Drop strategy: admit accepted values, return None on overflow.

        We admit known values for non-overflowing keys as we go. If any
        key overflows we stop processing the record and return None; the
        already-admitted values in this record were valid and remain.
        """
        for key, value in list(attributes.items()):
            if self._would_overflow(key, value):
                return None
            self._admit(key, value)
        return attributes

    def _process_mark(
        self, attributes: Dict[str, object]
    ) -> Dict[str, object] | BucketedRecord:
        """
        Mark strategy: return BucketedRecord with accepted + overflowing.

        We must decide per key whether it overflows, and then admit the
        ones that do not. Non-overflowing new values get admitted so the
        next identical record passes without a marker.
        """
        overflowing: list[str] = []
        for key, value in list(attributes.items()):
            if self._would_overflow(key, value):
                overflowing.append(key)
                continue
            self._admit(key, value)
        if overflowing:
            overflow_set = frozenset(overflowing)
            return BucketedRecord(dict(attributes), overflow_set)
        return attributes

    def __repr__(self) -> str:
        return (
            f"CardinalityLimiter(ceiling={self._ceiling}, "
            f"bucket_strategy={self._strategy!r})"
        )
