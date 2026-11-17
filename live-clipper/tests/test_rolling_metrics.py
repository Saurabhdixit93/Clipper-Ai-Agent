"""Unit tests for rolling metrics, bucket counters, and warmup logic."""

import time
import unittest
from live_clipper.config import ClipperConfig
from live_clipper.chat_monitor import TwitchChatMonitor


class TestRollingMetrics(unittest.TestCase):
    def setUp(self):
        self.config = ClipperConfig(warmup_duration_sec=300)
        self.monitor = TwitchChatMonitor(self.config)

    def test_five_second_bucket_counters(self):
        t0 = 1000.0
        # 3 messages in bucket 200 (1000 // 5)
        self.monitor._record_message("u1", "msg1", t0)
        self.monitor._record_message("u2", "msg2", t0 + 1.0)
        self.monitor._record_message("u3", "msg3", t0 + 2.0)
        
        # 1 message in bucket 201 (1005 // 5)
        self.monitor._record_message("u4", "msg4", t0 + 5.5)

        self.assertEqual(self.monitor.buckets.get(200), 3)
        self.assertEqual(self.monitor.buckets.get(201), 1)

    def test_rolling_average_excludes_current_spike(self):
        # Fill 60 historical buckets with exactly 10 messages each
        # bucket 100 to 159
        base_time = 1000.0
        for b in range(100, 160):
            t = b * 5.0
            for i in range(10):
                self.monitor._record_message(f"user{i}", "test", t)

        # Current time is bucket 160. Inject massive spike of 100 messages into bucket 160!
        current_time = 160 * 5.0
        for i in range(100):
            self.monitor._record_message(f"spike_user{i}", "SPIKE", current_time)

        # Compute rolling baseline excluding current spike bucket
        # Mock time.time to be in bucket 160
        orig_time = time.time
        try:
            time.time = lambda: current_time
            baseline_excl = self.monitor.get_rolling_chat_average(exclude_current_spike=True)
            self.assertAlmostEqual(baseline_excl, 10.0, places=1)

            # If not excluding, baseline would be contaminated
            baseline_incl = self.monitor.get_rolling_chat_average(exclude_current_spike=False)
            self.assertGreater(baseline_incl, 10.0)
        finally:
            time.time = orig_time

    def test_warmup_status(self):
        self.assertFalse(self.monitor.is_warmed_up)
        self.assertEqual(self.monitor.warmup_progress_pct, 0.0)

        # Simulate 150 seconds connected
        self.monitor.is_connected = True
        self.monitor.connection_start_time = time.time() - 150.0
        self.assertFalse(self.monitor.is_warmed_up)
        self.assertAlmostEqual(self.monitor.warmup_progress_pct, 50.0, delta=1.0)

        # Simulate 300+ seconds connected
        self.monitor.connection_start_time = time.time() - 305.0
        self.assertTrue(self.monitor.is_warmed_up)
        self.assertEqual(self.monitor.warmup_progress_pct, 100.0)


if __name__ == "__main__":
    unittest.main()

