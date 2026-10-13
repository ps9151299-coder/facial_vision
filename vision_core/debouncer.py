import time
from collections import deque

class TemporalDebounceEngine:
    """
    Temporal Sliding Window & Hysteresis Cooldown Engine.
    Suppresses momentary false alarms (stretching, blinking, sneezing, turning briefly)
    by requiring sustained violation across a sliding temporal buffer.
    """
    def __init__(self, window_size=45, suspicion_threshold=0.75, cooldown_seconds=15.0):
        self.window_size = window_size
        self.suspicion_threshold = suspicion_threshold
        self.cooldown_seconds = cooldown_seconds

        # Buffers: (track_id, violation_type) -> deque of 0/1 booleans
        self.buffers = {}
        # Cooldowns: (track_id, violation_type) -> timestamp of last triggered alert
        self.cooldowns = {}
        # Active violation start times: (track_id, violation_type) -> timestamp
        self.active_start_times = {}

    def register_frame_signals(self, track_signals, current_time=None):
        """
        Processes frame signals and yields validated violation events.

        Args:
            track_signals: list of dicts:
                [{
                    'track_id': int,
                    'violation_type': str,
                    'is_active': bool,
                    'confidence': float,
                    'telemetry': dict
                }]
            current_time: float (optional timestamp)

        Returns:
            list of validated alerts:
                [{
                    'track_id': int,
                    'violation_type': str,
                    'confidence': float,
                    'duration_seconds': float,
                    'telemetry': dict,
                    'timestamp': float
                }]
        """
        now = current_time or time.time()
        validated_alerts = []

        # Group by (track_id, violation_type)
        signal_map = {(s['track_id'], s['violation_type']): s for s in track_signals}

        # Update all active keys
        all_keys = set(self.buffers.keys()) | set(signal_map.keys())

        for key in all_keys:
            track_id, violation_type = key
            signal = signal_map.get(key)
            is_active = signal['is_active'] if signal else False

            if key not in self.buffers:
                self.buffers[key] = deque(maxlen=self.window_size)

            self.buffers[key].append(1 if is_active else 0)

            # Track duration
            if is_active:
                if key not in self.active_start_times:
                    self.active_start_times[key] = now
            else:
                if key in self.active_start_times and sum(self.buffers[key]) == 0:
                    del self.active_start_times[key]

            # Calculate suspicion ratio S
            buf = self.buffers[key]
            if len(buf) >= int(self.window_size * 0.5): # Wait until at least half-full
                suspicion_ratio = sum(buf) / len(buf)
                if suspicion_ratio >= self.suspicion_threshold:
                    last_alert = self.cooldowns.get(key, 0)
                    if now - last_alert >= self.cooldown_seconds:
                        duration = now - self.active_start_times.get(key, now)
                        validated_alerts.append({
                            'track_id': track_id,
                            'violation_type': violation_type,
                            'confidence': signal.get('confidence', 0.85) if signal else 0.85,
                            'duration_seconds': max(2.0, duration),
                            'telemetry': signal.get('telemetry', {}) if signal else {},
                            'timestamp': now
                        })
                        self.cooldowns[key] = now

        return validated_alerts
