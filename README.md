# log-record-cardinality-limiter

Limits how many distinct values a structured-log attribute can take before further new values for that key are dropped or marked. Pass the log record's attribute dict to `process`; you get back either the dict itself (everything admitted) or, under the `"mark"` strategy, a `BucketedRecord` listing which keys overflowed.

```python
from log_record_cardinality_limiter import CardinalityLimiter, BucketedRecord

limiter = CardinalityLimiter(ceiling=1000, bucket_strategy="mark")

result = limiter.process({"user_id": "u-42", "path": "/checkout"})
if isinstance(result, BucketedRecord):
    # At least one key hit its ceiling; result.overflowing_keys
    # tells you which ones to replace with a sentinel.
    safe = {k: v if k not in result.overflowing_keys else "<overflow>"
            for k, v in result.attributes.items()}
else:
    safe = result  # plain dict, pass it through
```

## Why this exists

Structured-log cardinality blowups are usually driven by one or two high-cardinality keys — a request id, a user id, a raw URL. This library caps each key independently at a fixed ceiling of distinct values. Past the ceiling, new values for that key are either suppressed (`"drop"` strategy, the whole record is discarded) or surfaced to the caller as `BucketedRecord.overflowing_keys` so the caller can swap in a sentinel and keep the record flowing.

The trade-off is per-key independence: if two keys are correlated (e.g. `tenant` and `tenant_region`), both can fill their buckets at roughly the same rate. We chose per-key tracking over tracking the full product space because the product space grows without bound under normal log churn and is almost never what an operator trying to cap log volume actually wants.

## Edge to know about

With `ceiling=0`, every value is treated as an overflow on first sight. Use it only when you want the limiter to surface every new value without storing anything. Also, because buckets store values in a Python `set`, values must be hashable — a list or dict as an attribute value will raise `TypeError`. Use tuples or strings for complex values.
