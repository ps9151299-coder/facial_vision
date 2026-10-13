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
        
        cap = cv2.VideoCapture(src)
        if not cap.isOpened():
            logger.warning(f"Failed to open video source {self.source}. Trying fallback synthetic source.")
            return None
        return cap

    def _capture_loop(self):
        self.cap = self._open_capture()

        fps_timer = time.time()
        frame_count = 0

        while self.running:
            frame = None
            if self.cap and self.cap.isOpened():
                ret, frame = self.cap.read()
                if not ret:
                    # Loop video file or attempt reconnect
                    if isinstance(self.source, str) and self.source.endswith(('.mp4', '.avi', '.mkv')):
                        self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        continue
                    else:
                        time.sleep(1.0)
                        self.cap = self._open_capture()
                        continue
            else:
                # Generate synthetic test pattern if camera hardware is unavailable
                frame = self._generate_synthetic_frame(frame_count)
                time.sleep(0.033)

            if frame is None:
                continue

            frame_count += 1
            now = time.time()

            # Process every frame or every 2nd frame for maximum FPS
            annotated_frame, alerts, active_students = self.engine.process_frame(frame, now)

            with self.lock:
                self.latest_raw_frame = frame
                self.latest_annotated_frame = annotated_frame
                self.latest_students = active_students
                self.latest_alerts = alerts

            # If alerts triggered, persist and dispatch immediately
            if alerts:
                self._handle_alerts(frame, alerts)

            # Broadcast live telemetry update every 5 frames
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

    def _handle_alerts(self, raw_frame, alerts):
        """Saves snapshots, writes to database, and triggers WebSocket + Email alerts."""
        for alert in alerts:
            # 1. Export forensic snapshot with SHA-256
            snapshot_path, sha256 = self.engine.annotator.export_forensic_snapshot(
                raw_frame, alert, camera_name=f"Camera {self.camera_id}"
            )

            # 2. Persist to Database asynchronously
            try:
                from monitoring.models import ViolationIncident, ExamSession, CameraFeed
                session_obj = None
                if self.session_id:
                    session_obj = ExamSession.objects.filter(id=self.session_id).first()
                if not session_obj:
                    session_obj = ExamSession.objects.filter(is_active=True).first()

                camera_obj = CameraFeed.objects.filter(name=str(self.camera_id)).first()

                incident = ViolationIncident.objects.create(
                    session=session_obj,
                    camera=camera_obj,
                    track_id=alert['track_id'],
                    violation_type=alert['violation_type'],
                    confidence_score=alert['confidence'],
                    duration_seconds=alert['duration_seconds'],
                    telemetry=alert['telemetry'],
                    snapshot=snapshot_path,
                    snapshot_sha256=sha256
                )

                # 3. Trigger Email Dispatch in background thread
                from monitoring.email_service import dispatch_violation_email_async
                dispatch_violation_email_async(incident)

                # 4. Broadcast to WebSocket clients
                if self.channel_layer:
                    payload = {
                        'incident_id': str(incident.id),
                        'track_id': alert['track_id'],
                        'violation_type': alert['violation_type'],
                        'violation_label': incident.get_violation_type_display(),
                        'confidence': round(alert['confidence'] * 100, 1),
                        'duration': round(alert['duration_seconds'], 1),
                        'snapshot_url': incident.snapshot.url if incident.snapshot else None,
                        'timestamp': incident.created_at.strftime('%H:%M:%S UTC')
                    }
                    async_to_sync(self.channel_layer.group_send)(
                        "proctor_global",
                        {
                            'type': 'violation_alert',
                            'data': payload
                        }
                    )
            except Exception as e:
                logger.error(f"Error persisting/broadcasting incident: {e}")

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
            # Blank black frame
            import numpy as np
            frame = np.zeros((480, 640, 3), dtype=np.uint8)

        ret, jpeg = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        return jpeg.tobytes() if ret else None
