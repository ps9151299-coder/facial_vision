import cv2
import numpy as np
import math
import os
import logging

logger = logging.getLogger(__name__)

# Path to MediaPipe face landmarker model (MediaPipe 1.x Tasks API)
_MODEL_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "face_landmarker.task"
)


class HeadPoseEstimator:
    """
    Ego-Centric Head Pose Estimation & Iris Gaze Tracking.
    Uses MediaPipe 1.x Tasks API (FaceLandmarker) for face mesh landmarks.
    Calculates 3D Euler angles (Yaw, Pitch, Roll) using solvePnP.
    """

    def __init__(self, yaw_threshold=30.0, pitch_lap_threshold=-22.0):
        self.yaw_threshold = yaw_threshold
        self.pitch_lap_threshold = pitch_lap_threshold

        # 6-point 3D canonical face model (mm)
        self.model_points = np.array([
            (0.0,    0.0,    0.0),       # Nose tip         → landmark 1
            (0.0,  -330.0,  -65.0),      # Chin             → landmark 152
            (-225.0, 170.0, -135.0),     # Left eye outer   → landmark 263
            (225.0,  170.0, -135.0),     # Right eye outer  → landmark 33
            (-150.0,-150.0, -125.0),     # Left mouth corner→ landmark 291
            (150.0, -150.0, -125.0),     # Right mouth corner→ landmark 61
        ], dtype=np.float64)

        self.landmark_indices = [1, 152, 263, 33, 291, 61]

        self._landmarker = None   # lazy-init

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _init_landmarker(self):
        """Lazy-initialize MediaPipe FaceLandmarker (Tasks API)."""
        if self._landmarker is not None:
            return

        try:
            import mediapipe as mp
            BaseOptions   = mp.tasks.BaseOptions
            FaceLandmarker        = mp.tasks.vision.FaceLandmarker
            FaceLandmarkerOptions = mp.tasks.vision.FaceLandmarkerOptions
            RunningMode           = mp.tasks.vision.RunningMode

            if not os.path.exists(_MODEL_PATH):
                logger.error(
                    f"face_landmarker.task not found at {_MODEL_PATH}. "
                    "Download it from: https://storage.googleapis.com/mediapipe-models/"
                    "face_landmarker/face_landmarker/float16/1/face_landmarker.task"
                )
                self._landmarker = False
                return

            options = FaceLandmarkerOptions(
                base_options=BaseOptions(model_asset_path=_MODEL_PATH),
                running_mode=RunningMode.IMAGE,
                num_faces=10,
                min_face_detection_confidence=0.5,
                min_face_presence_confidence=0.5,
                min_tracking_confidence=0.5,
                output_face_blendshapes=False,
                output_facial_transformation_matrixes=False,
            )
            self._landmarker = FaceLandmarker.create_from_options(options)
            logger.info("MediaPipe FaceLandmarker (Tasks API) initialized.")

        except Exception as e:
            logger.warning(f"Could not initialize MediaPipe FaceLandmarker: {e}")
            self._landmarker = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def estimate(self, frame_bgr, face_bbox=None):
        """
        Estimates yaw, pitch, roll, and eye gaze for a face in the frame.
        face_bbox: optional [x1, y1, x2, y2] to crop ROI for speed.

        Returns dict with yaw/pitch/roll/flags, or None if no face found.
        """
        self._init_landmarker()
        if not self._landmarker:
            return None

        import mediapipe as mp

        h, w = frame_bgr.shape[:2]

        # --- Optional crop to face bounding box ---
        if face_bbox is not None:
            bx1, by1, bx2, by2 = [int(v) for v in face_bbox]
            pad_x = int((bx2 - bx1) * 0.2)
            pad_y = int((by2 - by1) * 0.2)
            crop_x1 = max(0, bx1 - pad_x)
            crop_y1 = max(0, by1 - pad_y)
            crop_x2 = min(w,  bx2 + pad_x)
            crop_y2 = min(h,  by2 + pad_y)
            sub_img = frame_bgr[crop_y1:crop_y2, crop_x1:crop_x2]
            if sub_img.size == 0:
                return None
            offset_x, offset_y   = crop_x1, crop_y1
            target_h, target_w   = sub_img.shape[:2]
            work_img = sub_img
        else:
            offset_x, offset_y   = 0, 0
            target_h, target_w   = h, w
            work_img = frame_bgr

        # Convert to MediaPipe Image (RGB)
        rgb = cv2.cvtColor(work_img, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)

        try:
            result = self._landmarker.detect(mp_image)
        except Exception as e:
            logger.debug(f"FaceLandmarker detect error: {e}")
            return None

        if not result.face_landmarks:
            return None

        landmarks = result.face_landmarks[0]   # first face

        # --- Build 6 2-D image points ---
        image_points = []
        for idx in self.landmark_indices:
            lm = landmarks[idx]
            px = int(lm.x * target_w) + offset_x
            py = int(lm.y * target_h) + offset_y
            image_points.append([px, py])
        image_points = np.array(image_points, dtype=np.float64)

        # --- Camera intrinsics (approximate) ---
        focal_length  = float(w)
        center        = (w / 2.0, h / 2.0)
        camera_matrix = np.array([
            [focal_length, 0,            center[0]],
            [0,            focal_length, center[1]],
            [0,            0,            1         ],
        ], dtype=np.float64)
        dist_coeffs = np.zeros((4, 1), dtype=np.float64)

        # --- solvePnP ---
        success, rot_vec, trans_vec = cv2.solvePnP(
            self.model_points, image_points,
            camera_matrix, dist_coeffs,
            flags=cv2.SOLVEPNP_ITERATIVE
        )
        if not success:
            return None

        # --- Euler angles ---
        rot_mat, _ = cv2.Rodrigues(rot_vec)
        sy = math.sqrt(rot_mat[0, 0] ** 2 + rot_mat[1, 0] ** 2)
        singular = sy < 1e-6

        if not singular:
            pitch = math.atan2(rot_mat[2, 1], rot_mat[2, 2])
            yaw   = math.atan2(-rot_mat[2, 0], sy)
            roll  = math.atan2(rot_mat[1, 0], rot_mat[0, 0])
        else:
            pitch = math.atan2(-rot_mat[1, 2], rot_mat[1, 1])
            yaw   = math.atan2(-rot_mat[2, 0], sy)
            roll  = 0.0

        pitch_deg = math.degrees(pitch)
        yaw_deg   = math.degrees(yaw)
        roll_deg  = math.degrees(roll)

        # --- Iris gaze (requires ≥478 landmarks) ---
        gaze_side = "CENTER"
        if len(landmarks) >= 478:
            r_outer = landmarks[33].x
            r_inner = landmarks[133].x
            r_iris  = landmarks[473].x
            span = abs(r_inner - r_outer)
            if span > 1e-4:
                r_ratio = (r_iris - min(r_outer, r_inner)) / span
                if r_ratio < 0.35:
                    gaze_side = "RIGHT"
                elif r_ratio > 0.65:
                    gaze_side = "LEFT"

        is_looking_left  = yaw_deg >  self.yaw_threshold or (yaw_deg >  18.0 and gaze_side == "LEFT")
        is_looking_right = yaw_deg < -self.yaw_threshold or (yaw_deg < -18.0 and gaze_side == "RIGHT")
        is_looking_lap   = pitch_deg < self.pitch_lap_threshold

        nose_px = int(image_points[0][0])
        nose_py = int(image_points[0][1])

        return {
            'yaw':             round(yaw_deg,   2),
            'pitch':           round(pitch_deg, 2),
            'roll':            round(roll_deg,  2),
            'is_looking_left': bool(is_looking_left),
            'is_looking_right':bool(is_looking_right),
            'is_looking_lap':  bool(is_looking_lap),
            'eye_gaze_side':   gaze_side,
            'nose_point':      (nose_px, nose_py),
            'rot_vec':         rot_vec,
            'trans_vec':       trans_vec,
        }
