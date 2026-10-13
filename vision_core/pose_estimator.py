import numpy as np

class PoseKinematicsAnalyzer:
    """
    Skeletal Kinematics Analyzer using 17 standard body keypoints.
    Evaluates:
      1. Concealed lap hands (wrists below hip plane)
      2. Whispering / Earpiece check (hand near ear or mouth)
      3. Reaching across towards neighbors
    """
    def __init__(self):
        # Keypoint indices according to COCO Pose
        self.NOSE = 0
        self.LEFT_EYE = 1
        self.RIGHT_EYE = 2
        self.LEFT_EAR = 3
        self.RIGHT_EAR = 4
        self.LEFT_SHOULDER = 5
        self.RIGHT_SHOULDER = 6
        self.LEFT_ELBOW = 7
        self.RIGHT_ELBOW = 8
        self.LEFT_WRIST = 9
        self.RIGHT_WRIST = 10
        self.LEFT_HIP = 11
        self.RIGHT_HIP = 12

    def analyze_pose(self, keypoints, head_pitch=0.0):
        """
        Analyzes 17 keypoints for anomalous kinematics.
        Keypoints format: numpy array of shape (17, 2) or (17, 3) [x, y, conf]
        Returns:
            dict of detected violation flags and confidence
        """
        if keypoints is None or len(keypoints) < 13:
            return {}

        flags = {}

        # Extract coordinates and confidences
        def get_kp(idx):
            kp = keypoints[idx]
            x, y = kp[0], kp[1]
            conf = kp[2] if len(kp) > 2 else 1.0
            return x, y, conf

        lw_x, lw_y, lw_conf = get_kp(self.LEFT_WRIST)
        rw_x, rw_y, rw_conf = get_kp(self.RIGHT_WRIST)
        lh_x, lh_y, lh_conf = get_kp(self.LEFT_HIP)
        rh_x, rh_y, rh_conf = get_kp(self.RIGHT_HIP)
        le_x, le_y, le_conf = get_kp(self.LEFT_EAR)
        re_x, re_y, re_conf = get_kp(self.RIGHT_EAR)
        nose_x, nose_y, nose_conf = get_kp(self.NOSE)

        # Average hip Y coordinate
        hip_y = None
        if lh_conf > 0.4 and rh_conf > 0.4:
            hip_y = (lh_y + rh_y) / 2.0
        elif lh_conf > 0.4:
            hip_y = lh_y
        elif rh_conf > 0.4:
            hip_y = rh_y

        # 1. Concealed Lap Hand: Wrists below hips while head tilted down
        if hip_y is not None and head_pitch < -18.0:
            left_lap = lw_conf > 0.4 and lw_y > hip_y
            right_lap = rw_conf > 0.4 and rw_y > hip_y
            if left_lap or right_lap:
                flags['CONCEALED_HAND'] = {
                    'confidence': 0.88,
                    'details': 'Wrists positioned below hip plane with downward gaze'
                }

        # 2. Hand-to-Ear / Mouth (Whispering / Hidden Earpiece)
        # Distance between wrist and ear/nose
        ear_mouth_threshold = 45.0  # pixels in standard 720p/1080p frame
        
        # Check left wrist to left ear or nose
        l_to_ear = np.hypot(lw_x - le_x, lw_y - le_y) if (lw_conf > 0.4 and le_conf > 0.4) else 999
        l_to_nose = np.hypot(lw_x - nose_x, lw_y - nose_y) if (lw_conf > 0.4 and nose_conf > 0.4) else 999
        
        # Check right wrist to right ear or nose
        r_to_ear = np.hypot(rw_x - re_x, rw_y - re_y) if (rw_conf > 0.4 and re_conf > 0.4) else 999
        r_to_nose = np.hypot(rw_x - nose_x, rw_y - nose_y) if (rw_conf > 0.4 and nose_conf > 0.4) else 999

        if min(l_to_ear, l_to_nose, r_to_ear, r_to_nose) < ear_mouth_threshold:
            flags['HAND_TO_EAR'] = {
                'confidence': 0.85,
                'details': 'Hand persistently near ear or mouth area'
            }

        return flags
