"""
Public exports for log_record_cardinality_limiter.

Names kept narrow deliberately: `CardinalityLimiter` is the class people
will use, and `BucketedRecord` is the structure they will get back for a
record that overflowed the ceiling.
"""

from .core import CardinalityLimiter, BucketedRecord

__all__ = ["CardinalityLimiter", "BucketedRecord"]
