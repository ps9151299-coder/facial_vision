import cv2
import time
import threading
import logging
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from .detector import UnifiedExamVisionEngine

logger = logging.getLogger(__name__)

class CameraStreamManager:
    """
    Thread-Safe Multi-Source Video Stream Manager & Incident Dispatcher.
    Captures frames from USB webcam, RTSP, or test files, executes vision analysis,
    dispatches live WebSocket alerts to Django Channels, and saves incident snapshots.
    """
    _instances = {}

    @classmethod
    def get_instance(cls, camera_id="default", source="0"):
        if camera_id not in cls._instances:
            cls._instances[camera_id] = cls(camera_id, source)
        else:
            inst = cls._instances[camera_id]
            if str(source) != str(inst.source):
                inst.stop()
                inst.source = source
        return cls._instances[camera_id]

    def __init__(self, camera_id="default", source="0"):
        self.camera_id = camera_id
        self.source = source
        self.engine = UnifiedExamVisionEngine()
        
        self.cap = None
        self.running = False
        self.thread = None
        self.lock = threading.Lock()
        
        self.latest_raw_frame = None
        self.latest_annotated_frame = None
        self.latest_students = []
        self.latest_alerts = []

        self.session_id = None
        self.channel_layer = get_channel_layer()

    def start(self, session_id=None):
        if self.running:
            return
        self.session_id = session_id
        self.running = True
        self.thread = threading.Thread(target=self._capture_loop, daemon=True)
        self.thread.start()
        logger.info(f"CameraStreamManager started for camera '{self.camera_id}' (source: {self.source})")

    def stop(self):
        self.running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2.0)
        if self.cap:
            self.cap.release()
            self.cap = None
        logger.info(f"CameraStreamManager stopped for camera '{self.camera_id}'")

    def _open_capture(self):
        # Determine source type
        src = self.source
        if str(src).isdigit():
            src = int(src)

        # On Windows, OpenCV default backend (MSMF) often fails to grab frames (Error -1072875772).
        # cv2.CAP_DSHOW (DirectShow) is reliable and fast for webcams on Windows.
        if isinstance(src, int):
            cap = cv2.VideoCapture(src, cv2.CAP_DSHOW)
            if not cap.isOpened():
                cap = cv2.VideoCapture(src)
        else:
            cap = cv2.VideoCapture(src)

        if not cap.isOpened():
            logger.warning(f"Failed to open video source {self.source}. Trying fallback synthetic source.")
            return None
        return cap

    def _capture_loop(self):
        """
        Dedicated capture thread — reads frames at full camera FPS.
        AI processing happens in a SEPARATE background thread so
        slow CPU inference never blocks the video stream.
        """
        self.cap = self._open_capture()
        frame_count = 0

        # Start the AI processing thread
        self._ai_thread = threading.Thread(target=self._ai_loop, daemon=True)
        self._ai_thread.start()

        while self.running:
            frame = None
            if self.cap and self.cap.isOpened():
                ret, frame = self.cap.read()
                if not ret:
                    if isinstance(self.source, str) and self.source.endswith(('.mp4', '.avi', '.mkv')):
                        self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        continue
                    else:
                        time.sleep(1.0)
                        self.cap = self._open_capture()
                        continue
            else:
                # Synthetic test pattern when no camera hardware
                frame = self._generate_synthetic_frame(frame_count)
                time.sleep(0.033)

            if frame is None:
                continue

            frame_count += 1

            # Always store latest raw frame and overlay current detections/watermark
            annotated = self.engine.annotator.draw_telemetry(
                frame,
                self.latest_students,
                detected_objects=getattr(self, 'latest_objects', None),
                violations=self.latest_alerts
            )

            with self.lock:
                self.latest_raw_frame = frame
                self.latest_annotated_frame = annotated

            # Signal AI thread (non-blocking — drop frame if AI is still busy)
            if not hasattr(self, '_pending_frame_lock'):
                self._pending_frame_lock = threading.Lock()
                self._pending_frame = None
                self._pending_ts = None

            with self._pending_frame_lock:
                self._pending_frame = frame
                self._pending_ts = time.time()

    def _ai_loop(self):
        """
        Dedicated AI thread — runs YOLO + MediaPipe on latest available frame.
        Operates independently so capture FPS is never blocked.
        """
        if not hasattr(self, '_pending_frame_lock'):
            self._pending_frame_lock = threading.Lock()
            self._pending_frame = None
            self._pending_ts = None

        frame_count = 0
        while self.running:
            # Grab latest pending frame
            with self._pending_frame_lock:
                frame = self._pending_frame
                ts = self._pending_ts
                self._pending_frame = None

            if frame is None:
                time.sleep(0.01)
                continue

            frame_count += 1
            try:
                res = self.engine.process_frame(frame, ts)
                if len(res) == 4:
                    annotated_frame, alerts, active_students, detected_objs = res
                else:
                    annotated_frame, alerts, active_students = res[:3]
                    detected_objs = []
            except Exception as e:
                logger.error(f"AI process_frame error: {e}")
                annotated_frame, alerts, active_students, detected_objs = frame, [], [], []

            with self.lock:
                self.latest_annotated_frame = annotated_frame
                self.latest_students = active_students
                self.latest_alerts = alerts
                self.latest_objects = detected_objs

            if alerts:
                self._handle_alerts(frame, alerts)

            if frame_count % 5 == 0:
                self._broadcast_telemetry(active_students)

    def _generate_synthetic_frame(self, counter):
        """Generates a synthetic camera test frame with moving simulated student."""
        import numpy as np
        img = np.zeros((480, 640, 3), dtype=np.uint8)
        img[:] = (35, 30, 30) # Dark gray background
        
        # Grid lines simulating exam room
        cv2.line(img, (0, 380), (640, 380), (70, 70, 70), 2)
        cv2.line(img, (200, 380), (150, 480), (70, 70, 70), 2)
        cv2.line(img, (440, 380), (490, 480), (70, 70, 70), 2)

        # Simulated student sitting at desk
        x_shift = int(np.sin(counter * 0.05) * 15)
        # Head
        cv2.circle(img, (320 + x_shift, 200), 40, (180, 150, 130), -1)
        # Torso
        cv2.ellipse(img, (320 + x_shift, 310), (70, 80), 0, 0, 360, (60, 90, 180), -1)

        cv2.putText(img, "SYNTHETIC TEST CAMERA FEED (NO HARDWARE)", (40, 40),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
        return img

    @staticmethod
    def _sanitize_telemetry(telemetry):
        """
        Recursively convert telemetry dict to JSON-serializable types.
        Removes numpy arrays (rot_vec, trans_vec) that cannot be JSON-serialized.
        """
        if telemetry is None:
            return {}
        safe = {}
        for k, v in telemetry.items():
            if k in ('rot_vec', 'trans_vec', 'nose_point'):
                # Skip raw numpy / tuple values
                continue
            elif hasattr(v, 'tolist'):
                safe[k] = v.tolist()
            elif isinstance(v, (int, float, bool, str)):
                safe[k] = v
            elif isinstance(v, dict):
                safe[k] = CameraStreamManager._sanitize_telemetry(v)
            elif isinstance(v, (list, tuple)):
                safe[k] = [
                    x.tolist() if hasattr(x, 'tolist') else x
                    for x in v
                    if isinstance(x, (int, float, bool, str)) or hasattr(x, 'tolist')
                ]
            # else: skip non-serializable types silently
        return safe

    def _handle_alerts(self, raw_frame, alerts):
        """
        Saves snapshots, writes to database, and triggers WebSocket + Email alerts.
        STRICT CAP: Maximum 5 snapshots per student/track_id are ever captured.
        Once 5 violations are reached, the student is marked as CONFIRMED CHEATER.
        """
        for alert in alerts:
            track_id = alert['track_id']

            try:
                from monitoring.models import ViolationIncident, ExamSession, CameraFeed, Student

                session_obj = None
                if self.session_id:
                    session_obj = ExamSession.objects.filter(id=self.session_id).first()
                if not session_obj:
                    session_obj = ExamSession.objects.filter(is_active=True).first()

                # Check existing count for this specific student/track_id
                snap_filter = {'track_id': track_id}
                if session_obj:
                    snap_filter['session'] = session_obj

                existing_count = ViolationIncident.objects.filter(**snap_filter).count()

                # STRICT CAP: Never take more than 5 snapshots for the same person!
                if existing_count >= 5:
                    logger.info(
                        f"[CAP REACHED] Student Track #{track_id} already has {existing_count}/5 snapshots. "
                        "Maximum evidence cap reached. Skipping further snapshots."
                    )
                    continue

                # 1. Export forensic snapshot with SHA-256 burned-in evidence
                snapshot_rel_path, sha256 = self.engine.annotator.export_forensic_snapshot(
                    raw_frame, alert, camera_name=f"Camera {self.camera_id}"
                )
                if not snapshot_rel_path:
                    logger.warning("Snapshot export returned None, skipping DB write.")
                    continue

                # 2. Sanitize telemetry — remove numpy arrays for JSON serialization
                safe_telemetry = self._sanitize_telemetry(alert.get('telemetry', {}))

                # 3. Resolve Camera and Student records
                camera_obj = (
                    CameraFeed.objects.filter(name=str(self.camera_id)).first()
                    or CameraFeed.objects.filter(source_url=str(self.source)).first()
                    or CameraFeed.objects.filter(is_active=True).first()
                )

                # Link or create dynamic Student record for tracking
                student_id_str = f"STU-TRACK-{track_id}"
                student_obj = Student.objects.filter(student_id=student_id_str).first()
                if not student_obj:
                    student_obj = Student.objects.create(
                        student_id=student_id_str,
                        name=f"Candidate (Track #{track_id})",
                        roll_number=f"ROLL-T{track_id}"
                    )

                new_count = existing_count + 1
                is_cheater_confirmed = (new_count >= 5)

                review_status = 'CONFIRMED' if is_cheater_confirmed else 'PENDING'
                notes = (
                    f"FINAL CHEATING LIST: Student reached 5/5 violations threshold."
                    if is_cheater_confirmed
                    else f"Evidence snapshot {new_count} of 5 max allowed."
                )

                incident = ViolationIncident(
                    session=session_obj,
                    camera=camera_obj,
                    student=student_obj,
                    track_id=track_id,
                    violation_type=alert['violation_type'],
                    confidence_score=float(alert.get('confidence', 0.85)),
                    duration_seconds=float(alert.get('duration_seconds', 3.0)),
                    telemetry=safe_telemetry,
                    snapshot_sha256=sha256,
                    review_status=review_status,
                    notes=notes
                )
                incident.snapshot.name = snapshot_rel_path
                incident.save()

                if is_cheater_confirmed:
                    # Upgrade all 5 incidents for this track to CONFIRMED
                    ViolationIncident.objects.filter(**snap_filter).update(
                        review_status='CONFIRMED',
                        student=student_obj
                    )
                    logger.warning(
                        f"🚨 [FINAL CHEATING LIST] Student Track #{track_id} ({student_obj.name}) "
                        f"HAS REACHED 5 VIOLATIONS! Confirmed as cheating candidate."
                    )

                logger.info(
                    f"[ALERT] Track #{track_id} | Snap {new_count}/5 | {alert['violation_type']} "
                    f"| conf={alert.get('confidence', 0):.0%} | snap={snapshot_rel_path}"
                )

                # 4. Email dispatch
                try:
                    from monitoring.email_service import dispatch_violation_email_async
                    dispatch_violation_email_async(incident)
                except Exception:
                    pass

                # 5. WebSocket broadcast
                try:
                    if self.channel_layer:
                        payload = {
                            'incident_id': str(incident.id),
                            'track_id': alert['track_id'],
                            'violation_type': alert['violation_type'],
                            'violation_label': incident.get_violation_type_display(),
                            'confidence': round(float(alert.get('confidence', 0.85)) * 100, 1),
                            'duration': round(float(alert.get('duration_seconds', 0)), 1),
                            'snapshot_url': incident.snapshot.url if incident.snapshot else None,
                            'snapshot_sha256': sha256,
                            'review_status': incident.review_status,
                            'snap_count': new_count,
                            'is_confirmed_cheater': is_cheater_confirmed,
                            'timestamp': incident.created_at.strftime('%H:%M:%S UTC')
                        }
                        async_to_sync(self.channel_layer.group_send)(
                            "proctor_global",
                            {'type': 'violation_alert', 'data': payload}
                        )
                except Exception:
                    pass

            except Exception as e:
                logger.error(f"Error persisting incident to DB: {e}", exc_info=True)

    def _broadcast_telemetry(self, students):
        """Broadcasts live student pose telemetry to the monitoring dashboard."""
        if not self.channel_layer or not students:
            return
        
        telemetry_payload = []
        for s in students:
            pose = s.get('pose')
            telemetry_payload.append({
                'track_id': s['track_id'],
                'yaw': pose['yaw'] if pose else 0.0,
                'pitch': pose['pitch'] if pose else 0.0,
                'roll': pose['roll'] if pose else 0.0,
                'gaze': pose['eye_gaze_side'] if pose else 'CENTER'
            })

        try:
            async_to_sync(self.channel_layer.group_send)(
                "proctor_global",
                {
                    'type': 'telemetry_update',
                    'data': telemetry_payload
                }
            )
        except Exception:
            pass

    def get_latest_jpeg(self):
        """Returns the latest annotated JPEG frame bytes for MJPEG streaming."""
        with self.lock:
            frame = self.latest_annotated_frame
            if frame is None:
                frame = self.latest_raw_frame

        if frame is None:
            # Generate instant initializing HUD frame so browser never receives empty bytes
            import numpy as np
            frame = np.zeros((480, 640, 3), dtype=np.uint8)
            frame[:] = (25, 25, 35) # Clean dark blue background
            cv2.rectangle(frame, (10, 8), (420, 38), (20, 20, 30), -1)
            cv2.rectangle(frame, (10, 8), (420, 38), (6, 182, 212), 1)
            cv2.circle(frame, (25, 23), 6, (0, 255, 255), -1)
            cv2.putText(frame, "CONNECTING TO CAMERA FEED...", (38, 28),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.48, (255, 255, 255), 1, cv2.LINE_AA)
            cv2.putText(frame, "Camera Stream Manager: Initializing OpenCV Capture", (50, 250),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (200, 200, 200), 1, cv2.LINE_AA)

        ret, jpeg = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        return jpeg.tobytes() if ret else None
