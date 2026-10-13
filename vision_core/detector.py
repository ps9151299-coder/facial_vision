import cv2
import numpy as np
import logging
from .head_pose import HeadPoseEstimator
from .object_detector import ProhibitedObjectDetector
from .pose_estimator import PoseKinematicsAnalyzer
from .interaction_detector import InteractionAndAbsenceDetector
from .debouncer import TemporalDebounceEngine
from .annotator import ForensicAnnotator

logger = logging.getLogger(__name__)

class UnifiedExamVisionEngine:
    """
    Unified, Camera-Agnostic, ROI-Free Computer Vision Engine.
    Executes:
      1. Person Detection & ByteTrack Persistent Tracking
      2. 3D Head Pose & Iris Gaze Tracking (MediaPipe)
      3. Prohibited Object Detection with Dynamic Association
      4. Skeletal Kinematics & Lap Hand Analysis
      5. Inter-Person Collaboration & Roaming Detection
      6. Temporal Window Debouncing & Hysteresis Cooldown
      7. Forensic Snapshot Watermarking
    """
    def __init__(self, weights_person="yolov8n.pt", weights_pose="yolov8n-pose.pt"):
        self.head_pose = HeadPoseEstimator()
        self.object_detector = ProhibitedObjectDetector()
        self.pose_analyzer = PoseKinematicsAnalyzer()
        self.interaction_detector = InteractionAndAbsenceDetector()
        self.debouncer = TemporalDebounceEngine()
        self.annotator = ForensicAnnotator()

        self.yolo_model = None
        self.pose_model = None
        self.bytetracker = None
        self._init_models(weights_person, weights_pose)

    def _init_models(self, weights_person, weights_pose):
        """Initializes YOLO models and ByteTrack."""
        try:
            from ultralytics import YOLO
            self.yolo_model = YOLO(weights_person)
            try:
                self.pose_model = YOLO(weights_pose)
            except Exception as e:
                logger.warning(f"Could not load pose model: {e}")
                self.pose_model = None
        except Exception as e:
            logger.warning(f"Ultralytics YOLO not yet ready or loading failed: {e}")
            self.yolo_model = None

        # Supervision ByteTrack
        try:
            import supervision as sv
            self.bytetracker = sv.ByteTrack(
                track_thresh=0.25,
                track_buffer=30,
                match_thresh=0.8,
                frame_rate=30
            )
        except Exception as e:
            logger.warning(f"Supervision ByteTrack not loaded, using fallback tracker: {e}")
            self.bytetracker = None

        # Fallback simple centroid tracker if supervision is missing
        self._next_fallback_id = 1
        self._fallback_tracks = {}

    def _fallback_track(self, bboxes):
        """Lightweight centroid tracker when supervision ByteTrack is unavailable."""
        current_tracks = []
        if not bboxes:
            return current_tracks

        for bbox in bboxes:
            bx1, by1, bx2, by2 = bbox
            cx, cy = (bx1 + bx2) / 2.0, (by1 + by2) / 2.0
            
            matched_id = None
            min_dist = 80.0 # max pixel movement between frames

            for tid, prev_c in self._fallback_tracks.items():
                d = np.hypot(cx - prev_c[0], cy - prev_c[1])
                if d < min_dist:
                    min_dist = d
                    matched_id = tid
                    break

            if matched_id is None:
                matched_id = self._next_fallback_id
                self._next_fallback_id += 1

            self._fallback_tracks[matched_id] = (cx, cy)
            current_tracks.append({
                'track_id': matched_id,
                'bbox': bbox
            })
        return current_tracks

    def process_frame(self, frame_bgr, frame_timestamp=None):
        """
        Processes a single video frame end-to-end.
        Returns:
            annotated_frame: np.ndarray (with HUD telemetry)
            validated_alerts: list of alert dicts
            active_students: list of active student telemetry dicts
        """
        h, w = frame_bgr.shape[:2]
        raw_person_bboxes = []
        detected_objects = []
        detected_poses = {} # map track_id -> keypoints

        # 1. Object & Person Detection using YOLO
        if self.yolo_model is not None:
            results = self.yolo_model(frame_bgr, verbose=False, conf=0.35)[0]
            boxes = results.boxes
            if boxes is not None:
                for box in boxes:
                    cls_id = int(box.cls[0])
                    conf = float(box.conf[0])
                    cls_name = results.names[cls_id]
                    xyxy = box.xyxy[0].cpu().numpy().tolist()

                    if cls_name == 'person':
                        raw_person_bboxes.append(xyxy)
                    elif cls_name in self.object_detector.prohibited_class_names:
                        detected_objects.append({
                            'bbox': xyxy,
                            'class_name': cls_name,
                            'conf': conf
                        })
        else:
            # Simple Haar cascade fallback for local testing without weights
            face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
            gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
            faces = face_cascade.detectMultiScale(gray, 1.2, 5, minSize=(80, 80))
            for (fx, fy, fw, fh) in faces:
                # Expand face to approximate body bounding box
                raw_person_bboxes.append([
                    max(0, fx - int(fw * 0.5)),
                    fy,
                    min(w, fx + int(fw * 1.5)),
                    min(h, fy + int(fh * 3.0))
                ])

        # 2. ByteTrack Tracking
        active_students = []
        if self.bytetracker is not None and len(raw_person_bboxes) > 0:
            import supervision as sv
            detections = sv.Detections(
                xyxy=np.array(raw_person_bboxes),
                confidence=np.ones(len(raw_person_bboxes)),
                class_id=np.zeros(len(raw_person_bboxes), dtype=int)
            )
            tracked_detections = self.bytetracker.update_with_detections(detections)
            for i in range(len(tracked_detections)):
                tid = int(tracked_detections.tracker_id[i]) if tracked_detections.tracker_id is not None else (i + 1)
                bbox = tracked_detections.xyxy[i].tolist()
                active_students.append({'track_id': tid, 'bbox': bbox})
        else:
            active_students = self._fallback_track(raw_person_bboxes)

        # 3. Pose Keypoints (if model loaded)
        if self.pose_model is not None and active_students:
            try:
                pose_results = self.pose_model(frame_bgr, verbose=False, conf=0.4)[0]
                if pose_results.keypoints is not None:
                    kpts_data = pose_results.keypoints.data.cpu().numpy()
                    p_boxes = pose_results.boxes.xyxy.cpu().numpy()
                    # Match pose box to active student track
                    for s in active_students:
                        sx1, sy1, sx2, sy2 = s['bbox']
                        for p_idx, p_box in enumerate(p_boxes):
                            # IoU matching
                            if abs(sx1 - p_box[0]) < 50 and abs(sy1 - p_box[1]) < 50:
                                s['keypoints'] = kpts_data[p_idx]
                                break
            except Exception as e:
                pass

        # 4. Extract Head Pose & Cheating Signals per Student
        frame_signals = []
        for s in active_students:
            tid = s['track_id']
            bbox = s['bbox']
            
            # Head pose & gaze estimation
            pose = self.head_pose.estimate(frame_bgr, face_bbox=bbox)
            s['pose'] = pose

            if pose:
                # Signal 1: Looking Left
                frame_signals.append({
                    'track_id': tid,
                    'violation_type': 'HEAD_LOOKING_LEFT',
                    'is_active': pose['is_looking_left'],
                    'confidence': 0.88,
                    'telemetry': pose
                })
                # Signal 2: Looking Right
                frame_signals.append({
                    'track_id': tid,
                    'violation_type': 'HEAD_LOOKING_RIGHT',
                    'is_active': pose['is_looking_right'],
                    'confidence': 0.88,
                    'telemetry': pose
                })
                # Signal 3: Extreme Lap Gaze
                frame_signals.append({
                    'track_id': tid,
                    'violation_type': 'SUSPICIOUS_LAP_GAZE',
                    'is_active': pose['is_looking_lap'],
                    'confidence': 0.85,
                    'telemetry': pose
                })

            # Signal 4: Skeletal Kinematics
            if 'keypoints' in s:
                pitch = pose['pitch'] if pose else 0.0
                kinematics = self.pose_analyzer.analyze_pose(s['keypoints'], head_pitch=pitch)
                for v_type, details in kinematics.items():
                    frame_signals.append({
                        'track_id': tid,
                        'violation_type': v_type,
                        'is_active': True,
                        'confidence': details['confidence'],
                        'telemetry': details
                    })

        # 5. Prohibited Objects Dynamic Association
        obj_violations = self.object_detector.associate_objects_with_students(detected_objects, active_students)
        for ov in obj_violations:
            frame_signals.append({
                'track_id': ov['student_track_id'],
                'violation_type': ov['violation_type'],
                'is_active': True,
                'confidence': ov['confidence'],
                'telemetry': {'object': ov['label'], 'box': ov['obj_bbox']}
            })

        # 6. Inter-Person Collaboration & Roaming
        interactions = self.interaction_detector.update_and_check(active_students, frame_timestamp)
        for inter in interactions:
            frame_signals.append({
                'track_id': inter['track_id'],
                'violation_type': inter['violation_type'],
                'is_active': True,
                'confidence': inter['confidence'],
                'telemetry': {'details': inter['details']}
            })

        # 7. Debouncing (Hysteresis & Rolling Buffer Filter)
        validated_alerts = self.debouncer.register_frame_signals(frame_signals, frame_timestamp)

        # 8. Annotate Frame with HUD
        annotated_frame = self.annotator.draw_telemetry(
            frame_bgr,
            active_students,
            detected_objects=detected_objects,
            violations=validated_alerts
        )

        return annotated_frame, validated_alerts, active_students
