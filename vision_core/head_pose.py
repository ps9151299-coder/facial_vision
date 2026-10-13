import cv2
import numpy as np
import math

class HeadPoseEstimator:
    """
    Ego-Centric Head Pose Estimation & Iris Gaze Tracking using MediaPipe Face Mesh.
    Calculates 3D Euler angles (Yaw, Pitch, Roll) using Perspective-n-Point (solvePnP).
    Works in any classroom layout or camera orientation because calculations are performed
    relative to the student's local 3D facial coordinate frame.
    """
    def __init__(self, yaw_threshold=30.0, pitch_lap_threshold=-22.0):
        self.yaw_threshold = yaw_threshold
        self.pitch_lap_threshold = pitch_lap_threshold

        # Canonical 3D facial model points (in mm)
        self.model_points = np.array([
            (0.0, 0.0, 0.0),          # Nose tip (index 1)
            (0.0, -330.0, -65.0),     # Chin (index 152)
            (-225.0, 170.0, -135.0),  # Left eye outer corner (index 263)
            (225.0, 170.0, -135.0),   # Right eye outer corner (index 33)
            (-150.0, -150.0, -125.0), # Left mouth corner (index 291)
            (150.0, -150.0, -125.0)   # Right mouth corner (index 61)
        ], dtype=np.float64)

        # Standard landmark indices in MediaPipe Face Mesh
        self.landmark_indices = [1, 152, 263, 33, 291, 61]
        
        # Lazy-loaded MediaPipe Face Mesh
        self.face_mesh = None

    def _init_mediapipe(self):
        if self.face_mesh is None:
            try:
                import mediapipe as mp
                self.mp_face_mesh = mp.solutions.face_mesh
                self.face_mesh = self.mp_face_mesh.FaceMesh(
                    max_num_faces=10,
                    refine_landmarks=True, # enables 478 landmarks with iris
                    min_detection_confidence=0.5,
                    min_tracking_confidence=0.5
                )
            except Exception as e:
                self.face_mesh = False

    def estimate(self, frame_bgr, face_bbox=None):
        """
        Estimates yaw, pitch, roll, and eye gaze for a face in the frame.
        If face_bbox is provided [x1, y1, x2, y2], crops ROI for speed.
        Returns:
            dict with:
                - yaw: float (degrees, negative = left, positive = right)
                - pitch: float (degrees, negative = looking down, positive = looking up)
                - roll: float (degrees)
                - is_looking_left: bool
                - is_looking_right: bool
                - is_looking_lap: bool
                - eye_gaze_side: str ("CENTER", "LEFT", "RIGHT")
                - nose_2d: (x, y)
        """
        self._init_mediapipe()
        if not self.face_mesh:
            return None

        h, w = frame_bgr.shape[:2]
        
        # Optionally crop to face bounding box with padding
        if face_bbox is not None:
            bx1, by1, bx2, by2 = face_bbox
            pad_x = int((bx2 - bx1) * 0.2)
            pad_y = int((by2 - by1) * 0.2)
            crop_x1 = max(0, bx1 - pad_x)
            crop_y1 = max(0, by1 - pad_y)
            crop_x2 = min(w, bx2 + pad_x)
            crop_y2 = min(h, by2 + pad_y)
            
            sub_img = frame_bgr[crop_y1:crop_y2, crop_x1:crop_x2]
            if sub_img.size == 0:
                return None
            sub_h, sub_w = sub_img.shape[:2]
            rgb = cv2.cvtColor(sub_img, cv2.COLOR_BGR2RGB)
            results = self.face_mesh.process(rgb)
            offset_x, offset_y = crop_x1, crop_y1
            target_h, target_w = sub_h, sub_w
        else:
            rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
            results = self.face_mesh.process(rgb)
            offset_x, offset_y = 0, 0
            target_h, target_w = h, w

        if not results.multi_face_landmarks:
            return None

        # Take first detected face inside this crop
        landmarks = results.multi_face_landmarks[0].landmark

        # 2D image coordinates for the 6 canonical points
        image_points = []
        for idx in self.landmark_indices:
            lm = landmarks[idx]
            px = int(lm.x * target_w) + offset_x
            py = int(lm.y * target_h) + offset_y
            image_points.append([px, py])
        image_points = np.array(image_points, dtype=np.float64)

        # Approximate camera intrinsic matrix K
        focal_length = w
        center = (w / 2.0, h / 2.0)
        camera_matrix = np.array([
            [focal_length, 0, center[0]],
            [0, focal_length, center[1]],
            [0, 0, 1]
        ], dtype=np.float64)

        dist_coeffs = np.zeros((4, 1), dtype=np.float64)

        # Solve Perspective-n-Point
        success, rot_vec, trans_vec = cv2.solvePnP(
            self.model_points,
            image_points,
            camera_matrix,
            dist_coeffs,
            flags=cv2.SOLVEPNP_ITERATIVE
        )

        if not success:
            return None

        # Convert rotation vector to 3x3 matrix
        rot_mat, _ = cv2.Rodrigues(rot_vec)

        # Decompose rotation matrix into Euler angles
        # pitch (X), yaw (Y), roll (Z)
        sy = math.sqrt(rot_mat[0, 0] * rot_mat[0, 0] + rot_mat[1, 0] * rot_mat[1, 0])
        singular = sy < 1e-6

        if not singular:
            pitch = math.atan2(rot_mat[2, 1], rot_mat[2, 2])
            yaw = math.atan2(-rot_mat[2, 0], sy)
            roll = math.atan2(rot_mat[1, 0], rot_mat[0, 0])
        else:
            pitch = math.atan2(-rot_mat[1, 2], rot_mat[1, 1])
            yaw = math.atan2(-rot_mat[2, 0], sy)
            roll = 0

        # Convert radians to degrees
        pitch_deg = pitch * 180.0 / math.pi
        yaw_deg = yaw * 180.0 / math.pi
        roll_deg = roll * 180.0 / math.pi

        # Iris tracking for lateral glance
        gaze_side = "CENTER"
        if len(landmarks) >= 478:
            # MediaPipe refined iris landmarks:
            # 468: left iris center; 473: right iris center
            # 33: right eye outer, 133: right eye inner
            # 362: left eye inner, 263: left eye outer
            r_outer = landmarks[33].x
            r_inner = landmarks[133].x
            r_iris = landmarks[473].x
            if abs(r_inner - r_outer) > 1e-4:
                r_ratio = (r_iris - min(r_outer, r_inner)) / abs(r_inner - r_outer)
                if r_ratio < 0.35:
                    gaze_side = "RIGHT"
                elif r_ratio > 0.65:
                    gaze_side = "LEFT"

        is_looking_left = yaw_deg > self.yaw_threshold or (yaw_deg > 18.0 and gaze_side == "LEFT")
        is_looking_right = yaw_deg < -self.yaw_threshold or (yaw_deg < -18.0 and gaze_side == "RIGHT")
        is_looking_lap = pitch_deg < self.pitch_lap_threshold

        nose_px = int(image_points[0][0])
        nose_py = int(image_points[0][1])

        return {
            'yaw': round(yaw_deg, 2),
            'pitch': round(pitch_deg, 2),
            'roll': round(roll_deg, 2),
            'is_looking_left': bool(is_looking_left),
            'is_looking_right': bool(is_looking_right),
            'is_looking_lap': bool(is_looking_lap),
            'eye_gaze_side': gaze_side,
            'nose_point': (nose_px, nose_py),
            'rot_vec': rot_vec,
            'trans_vec': trans_vec
        }
