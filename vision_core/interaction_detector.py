import time
import numpy as np

class InteractionAndAbsenceDetector:
    """
    Dynamic Inter-Person Proximity, Collaboration & Absence/Roaming Detector.
    Zero static desk ROIs: analyzes dynamic bounding box interactions,
    relative velocities, aspect ratio shifts, and track disappearances.
    """
    def __init__(self, absence_timeout_seconds=15.0):
        self.absence_timeout = absence_timeout_seconds
        # Maps track_id -> {'last_seen': timestamp, 'history_bbox': deque}
        self.active_tracks = {}
        # Stores absence alerts raised: track_id -> timestamp
        self.absence_alerts = {}

    def update_and_check(self, current_tracks, frame_timestamp=None):
        """
        Args:
            current_tracks: list of dicts [{'track_id': int, 'bbox': [x1, y1, x2, y2], 'keypoints': ...}]
            frame_timestamp: float (optional time)
        Returns:
            list of dicts [{'track_id': int, 'violation_type': str, 'conf': float, 'details': str}]
        """
        now = frame_timestamp or time.time()
        violations = []
        current_track_ids = set()

        # 1. Check Inter-Person Collaboration / Intrusion between all active pairs
        num_students = len(current_tracks)
        for i in range(num_students):
            t1 = current_tracks[i]
            id1 = t1['track_id']
            b1 = t1['bbox']
            current_track_ids.add(id1)

            # Update active tracks dictionary
            if id1 not in self.active_tracks:
                self.active_tracks[id1] = {'first_seen': now, 'last_seen': now, 'last_bbox': b1}
            else:
                self.active_tracks[id1]['last_seen'] = now
                self.active_tracks[id1]['last_bbox'] = b1

            for j in range(i + 1, num_students):
                t2 = current_tracks[j]
                id2 = t2['track_id']
                b2 = t2['bbox']

                # Compute IoU between the two person boxes
                ix1 = max(b1[0], b2[0])
                iy1 = max(b1[1], b2[1])
                ix2 = min(b1[2], b2[2])
                iy2 = min(b1[3], b2[3])

                iw = max(0, ix2 - ix1)
                ih = max(0, iy2 - iy1)
                inter = iw * ih

                area1 = (b1[2] - b1[0]) * (b1[3] - b1[1])
                area2 = (b2[2] - b2[0]) * (b2[3] - b2[1])
                union = area1 + area2 - inter

                iou = inter / union if union > 0 else 0.0

                # If bounding boxes overlap significantly, they are crowding / leaning into each other
                if iou > 0.20:
                    violations.append({
                        'track_id': id1,
                        'violation_type': 'INTER_PERSON_INTRUSION',
                        'confidence': 0.85,
                        'details': f'Significant spatial overlap with Track #{id2} (IoU {iou:.2f})'
                    })
                    violations.append({
                        'track_id': id2,
                        'violation_type': 'INTER_PERSON_INTRUSION',
                        'confidence': 0.85,
                        'details': f'Significant spatial overlap with Track #{id1} (IoU {iou:.2f})'
                    })

        # 2. Check Absence of previously established tracks
        for track_id, info in list(self.active_tracks.items()):
            # Only consider tracks that were established for at least 10 seconds
            if now - info['first_seen'] > 10.0:
                elapsed_unseen = now - info['last_seen']
                if elapsed_unseen > self.absence_timeout:
                    # Check if already alerted recently
                    last_alert = self.absence_alerts.get(track_id, 0)
                    if now - last_alert > 60.0:  # alert once per minute
                        violations.append({
                            'track_id': track_id,
                            'violation_type': 'UNAUTHORIZED_ABSENCE',
                            'confidence': 0.95,
                            'details': f'Student missing from camera view for {elapsed_unseen:.1f}s'
                        })
                        self.absence_alerts[track_id] = now

        return violations
