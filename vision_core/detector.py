import cv2
import numpy as np
import time
import logging
from .head_pose import HeadPoseEstimator
from .object_detector import ProhibitedObjectDetector
from .pose_estimator import PoseKinematicsAnalyzer
from .interaction_detector import InteractionAndAbsenceDetector
from .debouncer import TemporalDebounceEngine
from .annotator import ForensicAnnotator

logger = logging.getLogger(__name__)


class StableCentroidTracker:
    """
    Robust centroid tracker with IoU + distance hybrid matching.
    Gives stable persistent IDs even without supervision/ByteTrack.
    Uses a 'lost' buffer so IDs don't jump on temporary occlusions.
    """
    def __init__(self, max_lost_frames=8, max_distance=120, iou_threshold=0.3):
        self.next_id = 1
        self.tracks = {}        # id -> {'bbox', 'centroid', 'lost_frames'}
        self.max_lost = max_lost_frames
        self.max_dist = max_distance
        self.iou_threshold = iou_threshold

    @staticmethod
    def _iou(b1, b2):
        ix1 = max(b1[0], b2[0]); iy1 = max(b1[1], b2[1])
        ix2 = min(b1[2], b2[2]); iy2 = min(b1[3], b2[3])
        iw = max(0, ix2 - ix1); ih = max(0, iy2 - iy1)
        inter = iw * ih
        a1 = (b1[2]-b1[0])*(b1[3]-b1[1])
        a2 = (b2[2]-b2[0])*(b2[3]-b2[1])
        union = a1 + a2 - inter
        return inter / union if union > 0 else 0.0

    def update(self, bboxes):
        """Update tracker with new bboxes. Returns list of (track_id, bbox)."""
        if not bboxes:
            # Increment lost frames for all tracks
            for tid in list(self.tracks.keys()):
                self.tracks[tid]['lost_frames'] += 1
                if self.tracks[tid]['lost_frames'] > self.max_lost:
                    del self.tracks[tid]
            return []

        # Compute centroids for new detections
        new_centroids = [((b[0]+b[2])/2, (b[1]+b[3])/2) for b in bboxes]
        matched_det = set()
        matched_track = set()
        results = {}

        track_ids = list(self.tracks.keys())

        # Step 1: IoU matching
        for det_idx, bbox in enumerate(bboxes):
            best_iou = self.iou_threshold
            best_tid = None
            for tid in track_ids:
                if tid in matched_track:
                    continue
                iou = self._iou(bbox, self.tracks[tid]['bbox'])
                if iou > best_iou:
                    best_iou = iou
                    best_tid = tid
            if best_tid is not None:
                results[best_tid] = bbox
                matched_det.add(det_idx)
                matched_track.add(best_tid)

        # Step 2: Centroid distance matching for unmatched
        for det_idx, bbox in enumerate(bboxes):
            if det_idx in matched_det:
                continue
            cx, cy = new_centroids[det_idx]
            best_dist = self.max_dist
            best_tid = None
            for tid in track_ids:
                if tid in matched_track:
                    continue
                tc = self.tracks[tid]['centroid']
                d = np.hypot(cx - tc[0], cy - tc[1])
                if d < best_dist:
                    best_dist = d
                    best_tid = tid
            if best_tid is not None:
                results[best_tid] = bbox
                matched_det.add(det_idx)
                matched_track.add(best_tid)

        # Step 3: Unmatched detections → new track IDs
        for det_idx, bbox in enumerate(bboxes):
            if det_idx not in matched_det:
                results[self.next_id] = bbox
                self.next_id += 1

        # Step 4: Update track state
        for tid in track_ids:
            if tid in results:
                b = results[tid]
                self.tracks[tid] = {
                    'bbox': b,
                    'centroid': ((b[0]+b[2])/2, (b[1]+b[3])/2),
                    'lost_frames': 0
                }
            else:
                self.tracks[tid]['lost_frames'] += 1
                if self.tracks[tid]['lost_frames'] > self.max_lost:
                    del self.tracks[tid]

        for tid, bbox in results.items():
            if tid not in self.tracks:
                self.tracks[tid] = {
                    'bbox': bbox,
                    'centroid': ((bbox[0]+bbox[2])/2, (bbox[1]+bbox[3])/2),
                    'lost_frames': 0
                }

        return [(tid, self.tracks[tid]['bbox']) for tid in results if tid in self.tracks]


class BBoxSmoother:
    """
    Exponential moving average smoother for bounding boxes.
    Prevents flickering ROI boxes on screen.
    """
    def __init__(self, alpha=0.4):
        self.alpha = alpha
        self._smooth = {}

    def smooth(self, track_id, bbox):
        if track_id not in self._smooth:
            self._smooth[track_id] = list(bbox)
            return bbox
        s = self._smooth[track_id]
        smoothed = [
            self.alpha * bbox[i] + (1 - self.alpha) * s[i]
            for i in range(4)
        ]
        self._smooth[track_id] = smoothed
        return smoothed

    def remove(self, track_id):
        self._smooth.pop(track_id, None)


class UnifiedExamVisionEngine:
    """
    Unified, Camera-Agnostic Computer Vision Engine.
    Executes:
      1. Person Detection & Stable Centroid Tracking (with smooth bbox)
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

        # Our stable centroid tracker (used if ByteTrack unavailable)
        self._centroid_tracker = StableCentroidTracker(
            max_lost_frames=8,
            max_distance=120,
            iou_threshold=0.30
        )
        self._bbox_smoother = BBoxSmoother(alpha=0.35)

        self._init_models(weights_person, weights_pose)

    def _init_models(self, weights_person, weights_pose):
        """Initializes YOLO models and ByteTrack."""
        try:
            from ultralytics import YOLO
            self.yolo_model = YOLO(weights_person)
            try:
                self.pose_model = YOLO(weights_pose)
                logger.info("YOLOv8 pose model loaded successfully.")
            except Exception as e:
                logger.warning(f"Could not load pose model: {e}")
                self.pose_model = None
        except Exception as e:
            logger.warning(f"Ultralytics YOLO not yet ready or loading failed: {e}")
            self.yolo_model = None

        # Try Supervision ByteTrack first
        try:
            import supervision as sv
            self.bytetracker = sv.ByteTrack(
                track_thresh=0.25,
                track_buffer=30,
                match_thresh=0.8,
                frame_rate=30
            )
            logger.info("Supervision ByteTrack loaded.")
        except Exception as e:
            logger.info(f"ByteTrack unavailable ({e}), using StableCentroidTracker fallback.")
            self.bytetracker = None

    def _run_tracking(self, raw_person_bboxes, person_confs):
        """Run ByteTrack or fallback centroid tracker. Returns list of (track_id, bbox)."""
        if not raw_person_bboxes:
            # Still update centroid tracker so lost-frame logic runs
            self._centroid_tracker.update([])
            return []

        if self.bytetracker is not None:
            try:
                import supervision as sv
                detections = sv.Detections(
                    xyxy=np.array(raw_person_bboxes),
                    confidence=np.array(person_confs),
                    class_id=np.zeros(len(raw_person_bboxes), dtype=int)
                )
                tracked = self.bytetracker.update_with_detections(detections)
                result = []
                for i in range(len(tracked)):
                    tid = int(tracked.tracker_id[i]) if tracked.tracker_id is not None else (i + 1)
                    bbox = tracked.xyxy[i].tolist()
                    result.append((tid, bbox))
                return result
            except Exception as e:
                logger.warning(f"ByteTrack update error: {e}")

        # Fallback: stable centroid tracker
        return self._centroid_tracker.update(raw_person_bboxes)

    def _match_pose_to_student(self, student_bbox, pose_boxes, kpts_data):
        """
        Best-IoU matching between a student bbox and pose model detections.
        Returns keypoints array or None.
        """
        sx1, sy1, sx2, sy2 = student_bbox
        best_iou = 0.20  # minimum IoU threshold for a match
        best_kpts = None

        for p_idx, p_box in enumerate(pose_boxes):
            ix1 = max(sx1, p_box[0]); iy1 = max(sy1, p_box[1])
            ix2 = min(sx2, p_box[2]); iy2 = min(sy2, p_box[3])
            iw = max(0, ix2 - ix1); ih = max(0, iy2 - iy1)
            inter = iw * ih
            a1 = (sx2 - sx1) * (sy2 - sy1)
            a2 = (p_box[2] - p_box[0]) * (p_box[3] - p_box[1])
            union = a1 + a2 - inter
            iou = inter / union if union > 0 else 0.0
            if iou > best_iou:
                best_iou = iou
                best_kpts = kpts_data[p_idx]

        return best_kpts

    def process_frame(self, frame_bgr, frame_timestamp=None):
        """
        Processes a single video frame end-to-end.
        Returns:
            annotated_frame: np.ndarray (with HUD telemetry)
            validated_alerts: list of alert dicts
            active_students: list of active student telemetry dicts
            detected_objects: list of detected prohibited objects
        """
        h, w = frame_bgr.shape[:2]
        raw_person_bboxes = []
        person_confs = []
        detected_objects = []

        # ── 1. YOLO Object + Person Detection ──────────────────────────────
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
                        person_confs.append(conf)
                    elif cls_name in self.object_detector.prohibited_class_names:
                        detected_objects.append({
                            'bbox': xyxy,
                            'class_name': cls_name,
                            'conf': conf
                        })
        else:
            # Haar cascade fallback (no YOLO)
            face_cascade = cv2.CascadeClassifier(
                cv2.data.haarcascades + 'haarcascade_frontalface_default.xml'
            )
            gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
            faces = face_cascade.detectMultiScale(gray, 1.2, 5, minSize=(60, 60))
            for (fx, fy, fw, fh) in faces:
                raw_person_bboxes.append([
                    max(0, fx - int(fw * 0.4)),
                    fy,
                    min(w, fx + int(fw * 1.4)),
                    min(h, fy + int(fh * 2.8))
                ])
                person_confs.append(0.75)

        # ── 2. Stable Tracking ──────────────────────────────────────────────
        tracked = self._run_tracking(raw_person_bboxes, person_confs)

        # Build active_students with smoothed bboxes
        active_students = []
        for (tid, raw_bbox) in tracked:
            smooth_bbox = self._bbox_smoother.smooth(tid, raw_bbox)
            active_students.append({
                'track_id': tid,
                'bbox': smooth_bbox,
                'raw_bbox': raw_bbox,
            })

        # Clean up smoother for lost tracks
        alive_ids = {s['track_id'] for s in active_students}
        for lost_id in list(self._bbox_smoother._smooth.keys()):
            if lost_id not in alive_ids:
                self._bbox_smoother.remove(lost_id)

        # ── 3. YOLOv8-Pose Keypoints ───────────────────────────────────────
        pose_boxes_raw = []
        kpts_data_raw = []
        if self.pose_model is not None and active_students:
            try:
                pose_results = self.pose_model(frame_bgr, verbose=False, conf=0.35)[0]
                if pose_results.keypoints is not None and pose_results.boxes is not None:
                    kpts_data_raw = pose_results.keypoints.data.cpu().numpy()   # (N, 17, 3)
                    pose_boxes_raw = pose_results.boxes.xyxy.cpu().numpy()       # (N, 4)
            except Exception as e:
                logger.debug(f"Pose model error: {e}")

        # Match keypoints to students via IoU
        for s in active_students:
            if len(pose_boxes_raw) > 0:
                kpts = self._match_pose_to_student(s['raw_bbox'], pose_boxes_raw, kpts_data_raw)
                if kpts is not None:
                    s['keypoints'] = kpts

        # ── 4. Head Pose & Gaze per Student ───────────────────────────────
        frame_signals = []
        for s in active_students:
            tid = s['track_id']

            # Use the raw (non-smoothed) bbox for head pose crop
            pose = self.head_pose.estimate(frame_bgr, face_bbox=s['raw_bbox'])
            s['pose'] = pose

            if pose:
                yaw = pose['yaw']
                pitch = pose['pitch']

                # Head turning left (yaw > threshold)
                frame_signals.append({
                    'track_id': tid,
                    'violation_type': 'HEAD_LOOKING_LEFT',
                    'is_active': pose['is_looking_left'],
                    'confidence': min(0.99, 0.6 + abs(yaw) / 100.0),
                    'telemetry': pose
                })
                # Head turning right
                frame_signals.append({
                    'track_id': tid,
                    'violation_type': 'HEAD_LOOKING_RIGHT',
                    'is_active': pose['is_looking_right'],
                    'confidence': min(0.99, 0.6 + abs(yaw) / 100.0),
                    'telemetry': pose
                })
                # Looking at lap
                frame_signals.append({
                    'track_id': tid,
                    'violation_type': 'SUSPICIOUS_LAP_GAZE',
                    'is_active': pose['is_looking_lap'],
                    'confidence': min(0.99, 0.6 + abs(pitch) / 100.0),
                    'telemetry': pose
                })

            # Skeletal kinematics
            if 'keypoints' in s:
                pitch_val = pose['pitch'] if pose else 0.0
                kinematics = self.pose_analyzer.analyze_pose(s['keypoints'], head_pitch=pitch_val)
                for v_type, details in kinematics.items():
                    frame_signals.append({
                        'track_id': tid,
                        'violation_type': v_type,
                        'is_active': True,
                        'confidence': details.get('confidence', 0.80),
                        'telemetry': details
                    })

        # ── 5. Prohibited Object Association ──────────────────────────────
        obj_violations = self.object_detector.associate_objects_with_students(
            detected_objects, active_students
        )
        for ov in obj_violations:
            frame_signals.append({
                'track_id': ov['student_track_id'],
                'violation_type': ov['violation_type'],
                'is_active': True,
                'confidence': ov['confidence'],
                'telemetry': {'object': ov['label'], 'box': ov['obj_bbox']}
            })

        # ── 6. Inter-Person Collaboration & Absence ───────────────────────
        interactions = self.interaction_detector.update_and_check(active_students, frame_timestamp)
        for inter in interactions:
            frame_signals.append({
                'track_id': inter['track_id'],
                'violation_type': inter['violation_type'],
                'is_active': True,
                'confidence': inter['confidence'],
                'telemetry': {'details': inter['details']}
            })

        # ── 7. Temporal Debouncing ─────────────────────────────────────────
        validated_alerts = self.debouncer.register_frame_signals(frame_signals, frame_timestamp)

        # ── 8. Annotate ────────────────────────────────────────────────────
        annotated_frame = self.annotator.draw_telemetry(
            frame_bgr,
            active_students,
            detected_objects=detected_objects,
            violations=validated_alerts
        )

        return annotated_frame, validated_alerts, active_students, detected_objects
