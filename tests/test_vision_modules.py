import unittest
import numpy as np
import time

from vision_core.object_detector import ProhibitedObjectDetector
from vision_core.pose_estimator import PoseKinematicsAnalyzer
from vision_core.interaction_detector import InteractionAndAbsenceDetector
from vision_core.debouncer import TemporalDebounceEngine

class TestVisionModules(unittest.TestCase):
    def test_dynamic_object_association(self):
        """Test that prohibited objects are dynamically associated with student bounding box (No ROIs)."""
        detector = ProhibitedObjectDetector()
        detected_objects = [
            {'bbox': [100, 100, 150, 160], 'class_name': 'cell phone', 'conf': 0.92}
        ]
        student_tracks = [
            {'track_id': 1, 'bbox': [80, 80, 220, 300]}, # Student 1 overlaps phone
            {'track_id': 2, 'bbox': [400, 100, 550, 320]}  # Student 2 far away
        ]

        associations = detector.associate_objects_with_students(detected_objects, student_tracks)
        self.assertEqual(len(associations), 1)
        self.assertEqual(associations[0]['student_track_id'], 1)
        self.assertEqual(associations[0]['violation_type'], 'PHONE_DETECTED')

    def test_pose_kinematics_concealed_hand(self):
        """Test detection of concealed lap hand with downward head pitch."""
        analyzer = PoseKinematicsAnalyzer()
        # Create dummy 17 keypoints
        keypoints = np.zeros((17, 3))
        # Left Hip at y=200, Right Hip at y=200
        keypoints[analyzer.LEFT_HIP] = [100, 200, 0.9]
        keypoints[analyzer.RIGHT_HIP] = [150, 200, 0.9]
        # Left Wrist down at y=250 (below hip)
        keypoints[analyzer.LEFT_WRIST] = [110, 250, 0.9]
        keypoints[analyzer.RIGHT_WRIST] = [140, 150, 0.9]

        # Case 1: Head looking straight (pitch = 0) -> No alert
        flags = analyzer.analyze_pose(keypoints, head_pitch=0.0)
        self.assertNotIn('CONCEALED_HAND', flags)

        # Case 2: Head looking down into lap (pitch = -25 deg) -> Flagged
        flags = analyzer.analyze_pose(keypoints, head_pitch=-25.0)
        self.assertIn('CONCEALED_HAND', flags)

    def test_inter_person_intrusion(self):
        """Test dynamic inter-person proximity and spatial overlap (No static desk polygon)."""
        detector = InteractionAndAbsenceDetector(absence_timeout_seconds=5.0)
        
        # Two students sitting abnormally close / sharing paper
        tracks = [
            {'track_id': 10, 'bbox': [100, 100, 200, 250]},
            {'track_id': 11, 'bbox': [130, 100, 230, 250]} # Significant IoU overlap
        ]
        violations = detector.update_and_check(tracks, frame_timestamp=time.time())
        types = [v['violation_type'] for v in violations]
        self.assertIn('INTER_PERSON_INTRUSION', types)

    def test_temporal_debounce_and_cooldown(self):
        """Test rolling buffer filter and 15s cooldown hysteresis."""
        debouncer = TemporalDebounceEngine(window_size=10, suspicion_threshold=0.8, cooldown_seconds=15.0)
        
        # 1. Momentary noise (2 frames of looking left out of 10) -> Must NOT trigger
        signals_noise = [{'track_id': 1, 'violation_type': 'HEAD_LOOKING_LEFT', 'is_active': True, 'confidence': 0.9}]
        for i in range(2):
            alerts = debouncer.register_frame_signals(signals_noise, current_time=100.0 + i)
            self.assertEqual(len(alerts), 0)

        # 2. Sustained looking left (8 consecutive frames) -> MUST trigger
        triggered = False
        for i in range(2, 10):
            alerts = debouncer.register_frame_signals(signals_noise, current_time=100.0 + i)
            if len(alerts) > 0:
                triggered = True
                self.assertEqual(alerts[0]['violation_type'], 'HEAD_LOOKING_LEFT')
                break
        self.assertTrue(triggered)

        # 3. Subsequent frames within cooldown period (e.g. 5s later) -> MUST NOT spam another alert
        alerts_cooldown = debouncer.register_frame_signals(signals_noise, current_time=105.0)
        self.assertEqual(len(alerts_cooldown), 0)

if __name__ == '__main__':
    unittest.main()
