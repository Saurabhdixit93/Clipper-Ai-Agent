"""Unit tests for trigger detection, dual condition correlation, and cooldown."""

import time
import unittest
from live_clipper.config import ClipperConfig
from live_clipper.chat_monitor import TwitchChatMonitor
from live_clipper.audio_analyzer import AudioAnalyzer
from live_clipper.trigger_engine import TriggerEngine


class TestTriggerEngine(unittest.TestCase):
    def setUp(self):
        self.config = ClipperConfig(
            chat_spike_multiplier=3.0,
            audio_spike_db=6.0,
            correlation_window_sec=15.0,
            cooldown_sec=120.0,
            warmup_duration_sec=300
        )
        self.chat_monitor = TwitchChatMonitor(self.config)
        self.audio_analyzer = AudioAnalyzer(self.config)
        self.trigger = TriggerEngine(self.config, self.chat_monitor, self.audio_analyzer)

    def _simulate_warmup(self):
        self.chat_monitor.is_connected = True
        self.chat_monitor.connection_start_time = time.time() - 310.0

    def test_warmup_blocks_triggers(self):
        # Even with spikes, trigger should return None before warmup
        now = time.time()
        self.trigger.record_audio_sample(now, loudness=-12.0, baseline=-24.0) # +12dB
        self.trigger.record_chat_sample(now, chat_rate=50, baseline=10) # 5x
        event = self.trigger.evaluate(now)
        self.assertIsNone(event)

    def test_dual_condition_within_15s_triggers(self):
        self._simulate_warmup()
        t_base = time.time()
        
        # Audio spike at t_base
        self.trigger.record_audio_sample(t_base, loudness=-15.0, baseline=-24.0) # +9dB spike (>= +6dB)
        
        # Chat spike at t_base + 8s (within 15s)
        self.trigger.record_chat_sample(t_base + 8.0, chat_rate=45, baseline=10) # 4.5x spike (>= 3x)
        
        event = self.trigger.evaluate(t_base + 8.0)
        self.assertIsNotNone(event)
        self.assertEqual(event.event_time, t_base + 8.0)
        # Slicing: 30s before event, 15s after event
        self.assertEqual(event.clip_start_time, (t_base + 8.0) - 30.0)
        self.assertEqual(event.clip_end_time, (t_base + 8.0) + 15.0)
        self.assertEqual(event.clip_duration, 45.0)

    def test_spikes_outside_15s_window_do_not_trigger(self):
        self._simulate_warmup()
        t_base = time.time()
        
        # Audio spike at t_base
        self.trigger.record_audio_sample(t_base, loudness=-15.0, baseline=-24.0)
        
        # Chat spike 25 seconds later (exceeds 15s window)
        self.trigger.record_chat_sample(t_base + 25.0, chat_rate=45, baseline=10)
        
        event = self.trigger.evaluate(t_base + 25.0)
        self.assertIsNone(event)

    def test_cooldown_enforced(self):
        self._simulate_warmup()
        t_base = time.time()
        
        # First trigger
        self.trigger.record_audio_sample(t_base, loudness=-15.0, baseline=-24.0)
        self.trigger.record_chat_sample(t_base + 2.0, chat_rate=40, baseline=10)
        event1 = self.trigger.evaluate(t_base + 2.0)
        self.assertIsNotNone(event1)
        self.assertTrue(self.trigger.is_in_cooldown)

        # Immediate second trigger should be blocked by 120s cooldown
        self.trigger.record_audio_sample(t_base + 10.0, loudness=-10.0, baseline=-24.0)
        self.trigger.record_chat_sample(t_base + 12.0, chat_rate=50, baseline=10)
        event2 = self.trigger.evaluate(t_base + 12.0)
        self.assertIsNone(event2)


if __name__ == "__main__":
    unittest.main()

