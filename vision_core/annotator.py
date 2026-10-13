import cv2
import hashlib
import time
from datetime import datetime
from pathlib import Path

class ForensicAnnotator:
    """
    Forensic HUD Overlay, Telemetry Visualizer, and Cryptographic Snapshot Exporter.
    Generates evidence images with burned-in telemetry, student track details,
    timestamps, and SHA-256 tamper-evident digital signatures.
    """
    def __init__(self, output_dir="media/snapshots"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def draw_telemetry(self, frame_bgr, student_tracks, detected_objects=None, violations=None):
        """
        Draws dynamic real-time telemetry on the live frame.
        """
        annotated = frame_bgr.copy()
        h, w = annotated.shape[:2]

        violation_track_ids = {v['track_id']: v for v in (violations or [])}

        # Draw detected objects
        if detected_objects:
            for obj in detected_objects:
                x1, y1, x2, y2 = [int(v) for v in obj['bbox']]
                label = f"{obj['class_name']} {obj['conf']:.2f}"
                cv2.rectangle(annotated, (x1, y1), (x2, y2), (0, 0, 255), 2)
                cv2.putText(annotated, label, (x1, max(15, y1 - 6)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 2)

        # Draw student tracks
        for s in student_tracks:
            track_id = s['track_id']
            x1, y1, x2, y2 = [int(v) for v in s['bbox']]
            
            is_violating = track_id in violation_track_ids
            box_color = (0, 0, 255) if is_violating else (0, 255, 0)

            # Draw bounding box
            cv2.rectangle(annotated, (x1, y1), (x2, y2), box_color, 2)

            # Build status tag
            pose = s.get('pose')
            pose_str = ""
            if pose:
                pose_str = f" | Y:{pose['yaw']:+.0f} P:{pose['pitch']:+.0f}"

            tag = f"Track #{track_id}{pose_str}"
            if is_violating:
                v_type = violation_track_ids[track_id]['violation_type']
                tag = f"ALERT: {v_type} (Track #{track_id})"

            # Tag background
            (tw, th), _ = cv2.getTextSize(tag, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)
            cv2.rectangle(annotated, (x1, max(0, y1 - th - 8)), (x1 + tw + 6, y1), box_color, -1)
            cv2.putText(annotated, tag, (x1 + 3, max(12, y1 - 4)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 0) if not is_violating else (255, 255, 255), 1)

        # Draw Live System Watermark in top-left
        time_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
        cv2.putText(annotated, f"AI PROCTOR ACTIVE | {time_str}", (15, 25),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 200), 2)

        return annotated

    def export_forensic_snapshot(self, frame_bgr, alert_info, camera_name="Camera 1"):
        """
        Burn forensic evidence metadata into the frame and save to disk.
        Returns:
            (relative_filepath, sha256_hash)
        """
        evidence_frame = frame_bgr.copy()
        h, w = evidence_frame.shape[:2]

        # Top Red Banner
        banner_h = 50
        cv2.rectangle(evidence_frame, (0, 0), (w, banner_h), (0, 0, 180), -1)
        
        timestamp_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] + " UTC"
        v_title = alert_info.get('violation_type', 'ACADEMIC_DISHONESTY')
        track_id = alert_info.get('track_id', 0)
        conf = alert_info.get('confidence', 0.85)

        banner_text = f"VIOLATION: {v_title} | Track #{track_id} | Conf: {conf:.2f}"
        cv2.putText(evidence_frame, banner_text, (15, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 2)

        # Compute SHA-256 hash of original frame data
        success, encoded_img = cv2.imencode('.jpg', evidence_frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
        if not success:
            return None, ""
        
        sha256 = hashlib.sha256(encoded_img.tobytes()).hexdigest()

        # Bottom Audit Footer with Hash
        cv2.rectangle(evidence_frame, (0, h - 30), (w, h), (20, 20, 20), -1)
        footer_text = f"{camera_name} | {timestamp_str} | SHA-256: {sha256[:16]}...{sha256[-8:]}"
        cv2.putText(evidence_frame, footer_text, (15, h - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (200, 200, 200), 1)

        # Re-encode final annotated image
        _, final_encoded = cv2.imencode('.jpg', evidence_frame, [cv2.IMWRITE_JPEG_QUALITY, 90])

        date_dir = self.output_dir / datetime.utcnow().strftime("%Y/%m/%d")
        date_dir.mkdir(parents=True, exist_ok=True)

        filename = f"incident_{track_id}_{int(time.time())}_{sha256[:8]}.jpg"
        full_path = date_dir / filename

        with open(full_path, 'wb') as f:
            f.write(final_encoded.tobytes())

        # Path relative to MEDIA_ROOT
        rel_path = f"snapshots/{datetime.utcnow().strftime('%Y/%m/%d')}/{filename}"
        return rel_path, sha256
