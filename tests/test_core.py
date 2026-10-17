import unittest

from log_record_cardinality_limiter import CardinalityLimiter, BucketedRecord


class TestCardinalityLimiterInit(unittest.TestCase):
    def test_default_strategy_is_mark(self):
        limiter = CardinalityLimiter(ceiling=10)
        self.assertEqual(limiter.strategy, "mark")
        self.assertEqual(limiter.ceiling, 10)

    def test_negative_ceiling_raises(self):
        with self.assertRaises(ValueError):
            CardinalityLimiter(ceiling=-1)

    def test_non_int_ceiling_raises(self):
        with self.assertRaises(TypeError):
            CardinalityLimiter(ceiling="5")

    def test_bool_ceiling_raises(self):
        # bool is a subclass of int, but it would mislead callers.
        with self.assertRaises(TypeError):
            CardinalityLimiter(ceiling=True)

    def test_invalid_strategy_raises(self):
        with self.assertRaises(ValueError):
            CardinalityLimiter(ceiling=5, bucket_strategy="tag")


class TestMarkStrategy(unittest.TestCase):
    def test_known_value_passes_through(self):
        limiter = CardinalityLimiter(ceiling=2)
        limiter.process({"user": "alice"})
        out = limiter.process({"user": "alice"})
        self.assertEqual(out, {"user": "alice"})

    def test_second_distinct_value_within_ceiling_passes(self):
        limiter = CardinalityLimiter(ceiling=2)
        limiter.process({"user": "alice"})
        out = limiter.process({"user": "bob"})
        self.assertEqual(out, {"user": "bob"})

    def test_new_value_at_ceiling_marked(self):
        limiter = CardinalityLimiter(ceiling=2)
        limiter.process({"user": "alice"})
        limiter.process({"user": "bob"})
        out = limiter.process({"user": "carol"})
        self.assertIsInstance(out, BucketedRecord)
        self.assertEqual(out.overflowing_keys, frozenset({"user"}))
        self.assertEqual(out.attributes, {"user": "carol"})

    def test_one_overflowing_one_fine(self):
        limiter = CardinalityLimiter(ceiling=1)
        limiter.process({"user": "alice", "path": "/"})
        out = limiter.process({"user": "alice", "path": "/x"})
        self.assertIsInstance(out, BucketedRecord)
        self.assertEqual(out.overflowing_keys, frozenset({"path"}))

    def test_both_keys_overflow(self):
        limiter = CardinalityLimiter(ceiling=1)
        limiter.process({"user": "a", "path": "/"})
        out = limiter.process({"user": "b", "path": "/x"})
        self.assertIsInstance(out, BucketedRecord)
        self.assertEqual(out.overflowing_keys, frozenset({"user", "path"}))

    def test_overflowing_value_not_admitted(self):
        limiter = CardinalityLimiter(ceiling=1)
        limiter.process({"user": "alice"})
        limiter.process({"user": "carol"})  # overflows, not admitted
        self.assertEqual(limiter.bucket_size("user"), 1)

    def test_non_overflowing_new_value_admitted(self):
        limiter = CardinalityLimiter(ceiling=2)
        limiter.process({"user": "alice"})
        # First path value, fresh and within ceiling.
        out = limiter.process({"user": "alice", "path": "/x"})
        # Not overflowing.
        self.assertEqual(out, {"user": "alice", "path": "/x"})
        self.assertEqual(limiter.bucket_size("path"), 1)

    def test_no_overflow_returns_plain_dict(self):
        limiter = CardinalityLimiter(ceiling=5)
        out = limiter.process({"user": "a"})
        self.assertEqual(out, {"user": "a"})

    def test_int_values(self):
        limiter = CardinalityLimiter(ceiling=2)
        limiter.process({"status": 200})
        limiter.process({"status": 404})
        out = limiter.process({"status": 500})
        self.assertIsInstance(out, BucketedRecord)
        self.assertEqual(out.overflowing_keys, frozenset({"status"}))

    def test_ceiling_zero_marks_everything(self):
        limiter = CardinalityLimiter(ceiling=0)
        out = limiter.process({"user": "a"})
        self.assertIsInstance(out, BucketedRecord)
        self.assertEqual(out.overflowing_keys, frozenset({"user"}))
        self.assertEqual(limiter.bucket_size("user"), 0)

    def test_reset_clears_buckets(self):
        limiter = CardinalityLimiter(ceiling=1)
        limiter.process({"user": "a"})
        limiter.reset()
        self.assertEqual(limiter.bucket_size("user"), 0)
        self.assertFalse(limiter.known_value("user", "a"))

    def test_known_value_query(self):
        limiter = CardinalityLimiter(ceiling=5)
        limiter.process({"user": "a"})
        self.assertTrue(limiter.known_value("user", "a"))
        self.assertFalse(limiter.known_value("user", "b"))
        self.assertFalse(limiter.known_value("never_seen", "x"))

    def test_int_and_str_one_hundred_not_equal(self):
        # Python hashing makes 100 and "100" different objects; our
        # set-based bucket respects that.
        limiter = CardinalityLimiter(ceiling=2)
        limiter.process({"code": 100})
        out = limiter.process({"code": "100"})
        self.assertEqual(out, {"code": "100"})
        self.assertEqual(limiter.bucket_size("code"), 2)


class TestDropStrategy(unittest.TestCase):
    def test_drop_returns_none_on_overflow(self):
        limiter = CardinalityLimiter(ceiling=1, bucket_strategy="drop")
        limiter.process({"user": "a"})
        out = limiter.process({"user": "b"})
        self.assertIsNone(out)

    def test_drop_admits_known_in_record(self):
        limiter = CardinalityLimiter(ceiling=1, bucket_strategy="drop")
        limiter.process({"user": "a", "path": "/x"})
        out = limiter.process({"user": "a", "path": "/y"})
        # path overflows; whole record dropped.
        self.assertIsNone(out)

    def test_drop_all_known_passes(self):
        limiter = CardinalityLimiter(ceiling=2, bucket_strategy="drop")
        limiter.process({"user": "a", "path": "/x"})
        out = limiter.process({"user": "a", "path": "/x"})
        self.assertEqual(out, {"user": "a", "path": "/x"})

    def test_drop_ceiling_zero_drops_all(self):
        limiter = CardinalityLimiter(ceiling=0, bucket_strategy="drop")
        out = limiter.process({"user": "a"})
        self.assertIsNone(out)


class TestBucketedRecord(unittest.TestCase):
    def test_equality_and_repr(self):
        b1 = BucketedRecord({"user": "a"}, frozenset({"path"}))
        b2 = BucketedRecord({"user": "a"}, frozenset({"path"}))
        b3 = BucketedRecord({"user": "a"}, frozenset({"user"}))
        self.assertEqual(b1, b2)
        self.assertNotEqual(b1, b3)
        self.assertNotEqual(b1, {"user": "a"})
        r = repr(b1)
        self.assertIn("BucketedRecord", r)
        self.assertIn("path", r)


if __name__ == "__main__":
    unittest.main()
