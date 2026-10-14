import cv2
import hashlib
import time
import numpy as np
import math
from datetime import datetime
from pathlib import Path


class ForensicAnnotator:
    """
    Forensic HUD Overlay, Telemetry Visualizer, and Cryptographic Snapshot Exporter.
    Renders:
      - Stable ROI bounding box with tactical corner brackets
      - COCO skeleton (bones + joints) from pose keypoints
      - 3D head pose direction arrow from nose tip
      - Yaw/Pitch/Roll HUD values per person
      - Gaze direction indicator
      - Prohibited object highlight boxes
      - Top-bar live LIVE/ALERT status HUD
      - Forensic snapshot with SHA-256 watermark
    """

    # Standard COCO 17-keypoint skeleton pairs
    SKELETON_PAIRS = [
        (5, 6),   # Left shoulder → Right shoulder
        (5, 7),   # Left shoulder → Left elbow
        (7, 9),   # Left elbow → Left wrist
        (6, 8),   # Right shoulder → Right elbow
        (8, 10),  # Right elbow → Right wrist
        (5, 11),  # Left shoulder → Left hip
        (6, 12),  # Right shoulder → Right hip
        (11, 12), # Left hip → Right hip
        (11, 13), # Left hip → Left knee
        (13, 15), # Left knee → Left ankle
        (12, 14), # Right hip → Right knee
        (14, 16), # Right knee → Right ankle
        (0, 5),   # Nose → Left shoulder
        (0, 6),   # Nose → Right shoulder
    ]

    # Joint colors for visual clarity (BGR)
    JOINT_COLORS = {
        0:  (0, 255, 255),    # Nose         — cyan
        1:  (0, 200, 255),    # Left eye     — light blue
        2:  (0, 200, 255),    # Right eye
        3:  (0, 180, 255),    # Left ear
        4:  (0, 180, 255),    # Right ear
        5:  (0, 255, 128),    # Left shoulder  — green
        6:  (0, 255, 128),    # Right shoulder
        7:  (0, 230, 80),     # Left elbow
        8:  (0, 230, 80),     # Right elbow
        9:  (50, 255, 50),    # Left wrist   — bright green
        10: (50, 255, 50),    # Right wrist
        11: (255, 180, 0),    # Left hip     — orange
        12: (255, 180, 0),    # Right hip
        13: (255, 120, 0),    # Left knee
        14: (255, 120, 0),    # Right knee
        15: (255, 60, 0),     # Left ankle   — deep orange
        16: (255, 60, 0),     # Right ankle
    }

    def __init__(self, output_dir="media/snapshots"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    # ──────────────────────────────────────────────────────────
    # Public: draw HUD on live frame
    # ──────────────────────────────────────────────────────────

    def draw_telemetry(self, frame_bgr, student_tracks, detected_objects=None, violations=None):
        """
        Draws full real-time HUD telemetry on the live video frame.
        Does NOT modify the original frame — works on a copy.
        """
        annotated = frame_bgr.copy()
        h, w = annotated.shape[:2]

        # Map violation by track_id for quick lookup
        violation_map = {}
        for v in (violations or []):
            tid = v['track_id']
            # Keep worst/latest violation per track
            violation_map[tid] = v

        # ── Draw prohibited objects ──────────────────────────────
        if detected_objects:
            for obj in detected_objects:
                x1, y1, x2, y2 = [int(v) for v in obj['bbox']]
                label = f"⚠ {obj['class_name'].upper()} {obj['conf']:.0%}"
                # Red fill banner then red border
                cv2.rectangle(annotated, (x1, y1), (x2, y2), (0, 0, 220), 2)
                (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.48, 1)
                cv2.rectangle(annotated, (x1, max(0, y1-th-6)), (x1+tw+4, y1), (0, 0, 180), -1)
                cv2.putText(annotated, label, (x1+2, max(10, y1-3)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.48, (255, 255, 255), 1, cv2.LINE_AA)

        # ── Draw each tracked student ────────────────────────────
        for s in student_tracks:
            track_id = s['track_id']
            x1, y1, x2, y2 = [int(v) for v in s['bbox']]
            is_violating = track_id in violation_map

            box_color = (0, 0, 220) if is_violating else (0, 220, 80)
            bracket_color = (0, 80, 255) if is_violating else (6, 182, 212)

            # 1. ROI bounding box
            cv2.rectangle(annotated, (x1, y1), (x2, y2), box_color, 2)

            # 2. Tactical corner brackets (L-shapes)
            b = max(14, min(28, int((x2-x1) * 0.18)))
            lw = 3
            # Top-left
            cv2.line(annotated, (x1, y1), (x1+b, y1), bracket_color, lw)
            cv2.line(annotated, (x1, y1), (x1, y1+b), bracket_color, lw)
            # Top-right
            cv2.line(annotated, (x2, y1), (x2-b, y1), bracket_color, lw)
            cv2.line(annotated, (x2, y1), (x2, y1+b), bracket_color, lw)
            # Bottom-left
            cv2.line(annotated, (x1, y2), (x1+b, y2), bracket_color, lw)
            cv2.line(annotated, (x1, y2), (x1, y2-b), bracket_color, lw)
            # Bottom-right
            cv2.line(annotated, (x2, y2), (x2-b, y2), bracket_color, lw)
            cv2.line(annotated, (x2, y2), (x2, y2-b), bracket_color, lw)

            # 3. Skeleton (bones + joints)
            kpts = s.get('keypoints')
            if kpts is not None and len(kpts) >= 13:
                self._draw_skeleton(annotated, kpts)

            # 4. Head pose 3D arrow + angle readout
            pose = s.get('pose')
            yaw_str = ""
            pitch_str = ""
            gaze_str = "CENTER"
            if pose:
                yaw_str = f"{pose['yaw']:+.0f}°"
                pitch_str = f"{pose['pitch']:+.0f}°"
                gaze_str = pose.get('eye_gaze_side', 'CENTER')
                self._draw_head_pose_arrow(annotated, pose, s['bbox'])

            # 5. Track label tag
            if is_violating:
                v_info = violation_map[track_id]
                vtype = v_info['violation_type'].replace('_', ' ')
                conf = v_info.get('confidence', 0.85)
                tag = f"ALERT: {vtype} ({conf:.0%})"
                tag_bg = (0, 0, 180)
                tag_fg = (255, 255, 255)
            else:
                tag = f"ID #{track_id}  Y:{yaw_str} P:{pitch_str}  {gaze_str}"
                tag_bg = (20, 20, 30)
                tag_fg = (200, 255, 200)

            (tw, th), _ = cv2.getTextSize(tag, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)
            tag_y = max(0, y1 - th - 10)
            cv2.rectangle(annotated, (x1, tag_y), (x1 + tw + 8, y1), tag_bg, -1)
            cv2.putText(annotated, tag, (x1 + 4, y1 - 4),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, tag_fg, 1, cv2.LINE_AA)

        # ── Top-left LIVE HUD banner ─────────────────────────────
        time_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
        n_students = len(student_tracks)
        n_violations = len(violation_map)
        status_txt = f"LIVE | {time_str} | Tracks:{n_students} | Alerts:{n_violations}"
        dot_color = (0, 0, 220) if n_violations > 0 else (0, 220, 80)

        (bw, bh), _ = cv2.getTextSize(status_txt, cv2.FONT_HERSHEY_SIMPLEX, 0.46, 1)
        cv2.rectangle(annotated, (8, 6), (18 + bw + 16, 36), (18, 18, 28), -1)
        cv2.rectangle(annotated, (8, 6), (18 + bw + 16, 36), (6, 182, 212), 1)
        cv2.circle(annotated, (23, 21), 6, dot_color, -1)
        cv2.putText(annotated, status_txt, (34, 26),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.46, (240, 240, 240), 1, cv2.LINE_AA)

        return annotated

    # ──────────────────────────────────────────────────────────
    # Private helpers
    # ──────────────────────────────────────────────────────────

    def _draw_skeleton(self, img, kpts):
        """Draw COCO skeleton bones and colour-coded joint circles."""
        # Bones
        for (i, j) in self.SKELETON_PAIRS:
            if i >= len(kpts) or j >= len(kpts):
                continue
            p1, p2 = kpts[i], kpts[j]
            c1 = float(p1[2]) if len(p1) > 2 else 1.0
            c2 = float(p2[2]) if len(p2) > 2 else 1.0
            if c1 > 0.35 and c2 > 0.35:
                pt1 = (int(p1[0]), int(p1[1]))
                pt2 = (int(p2[0]), int(p2[1]))
                # Skip zero-coords (undetected keypoints)
                if pt1 == (0, 0) or pt2 == (0, 0):
                    continue
                cv2.line(img, pt1, pt2, (255, 200, 0), 2, cv2.LINE_AA)

        # Joints
        for idx in range(min(17, len(kpts))):
            kp = kpts[idx]
            c = float(kp[2]) if len(kp) > 2 else 1.0
            if c > 0.35 and not (kp[0] == 0 and kp[1] == 0):
                color = self.JOINT_COLORS.get(idx, (0, 255, 255))
                cv2.circle(img, (int(kp[0]), int(kp[1])), 4, color, -1, cv2.LINE_AA)
                cv2.circle(img, (int(kp[0]), int(kp[1])), 4, (255, 255, 255), 1, cv2.LINE_AA)

    def _draw_head_pose_arrow(self, img, pose, bbox):
        """
        Draw a 3D-style head orientation arrow from the nose tip.
        Arrow length and direction encode yaw + pitch visually.
        """
        nose = pose.get('nose_point')
        if not nose or (nose[0] == 0 and nose[1] == 0):
            return

        yaw   = pose.get('yaw', 0.0)
        pitch = pose.get('pitch', 0.0)

        # Arrow length proportional to bbox width
        x1, y1, x2, y2 = [int(v) for v in bbox]
        box_w = max(1, x2 - x1)
        arrow_len = int(box_w * 0.28)

        # Convert yaw/pitch to 2D direction (approximate screen projection)
        # Yaw positive → person looking left on screen → arrow goes left
        # Pitch negative → person looking down → arrow goes down
        rad_yaw   = math.radians(-yaw)      # negate: yaw+ is face-left on screen
        rad_pitch = math.radians(pitch)

        dx = int(arrow_len * math.sin(rad_yaw))
        dy = int(arrow_len * math.sin(rad_pitch))

        nx, ny = nose
        end_x = np.clip(nx + dx, 0, img.shape[1] - 1)
        end_y = np.clip(ny + dy, 0, img.shape[0] - 1)

        # Color: green when looking forward, orange when suspicious
        if abs(yaw) > 25 or pitch < -20:
            arrow_color = (0, 100, 255)   # orange-red
        else:
            arrow_color = (0, 230, 100)   # green

        # Draw arrow
        cv2.arrowedLine(img, (nx, ny), (int(end_x), int(end_y)),
                        arrow_color, 2, cv2.LINE_AA, tipLength=0.35)
        # Nose anchor dot
        cv2.circle(img, (nx, ny), 4, (0, 220, 255), -1, cv2.LINE_AA)

    # ──────────────────────────────────────────────────────────
    # Public: forensic snapshot export
    # ──────────────────────────────────────────────────────────

    def export_forensic_snapshot(self, frame_bgr, alert_info, camera_name="Camera 1"):
        """
        Burn forensic evidence metadata into a copy of the frame and save to disk.
        Returns:
            (relative_filepath_str, sha256_hex_string)
        """
        evidence = frame_bgr.copy()
        h, w = evidence.shape[:2]

        # ── Top red alert banner ─────────────────────────────────
        banner_h = 52
        cv2.rectangle(evidence, (0, 0), (w, banner_h), (0, 0, 170), -1)
        cv2.rectangle(evidence, (0, 0), (w, banner_h), (0, 0, 220), 2)

        ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] + " UTC"
        v_type  = alert_info.get('violation_type', 'UNKNOWN').replace('_', ' ')
        track_id = alert_info.get('track_id', 0)
        conf     = alert_info.get('confidence', 0.85)
        duration = alert_info.get('duration_seconds', 0.0)

        line1 = f"VIOLATION: {v_type}   Track #{track_id}   Conf: {conf:.0%}   Duration: {duration:.1f}s"
        line2 = f"Camera: {camera_name}   {ts}"

        cv2.putText(evidence, line1, (12, 22),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.58, (255, 255, 255), 2, cv2.LINE_AA)
        cv2.putText(evidence, line2, (12, 44),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (220, 220, 220), 1, cv2.LINE_AA)

        # ── Compute SHA-256 of raw frame bytes ───────────────────
        raw_bytes = frame_bgr.tobytes()
        sha256 = hashlib.sha256(raw_bytes).hexdigest()

        # ── Bottom audit footer ───────────────────────────────────
        footer_h = 28
        cv2.rectangle(evidence, (0, h - footer_h), (w, h), (18, 18, 18), -1)
        footer = f"SHA-256: {sha256[:20]}...{sha256[-12:]}  |  AI Proctor System"
        cv2.putText(evidence, footer, (10, h - 8),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.40, (160, 160, 160), 1, cv2.LINE_AA)

        # ── Encode & save ─────────────────────────────────────────
        ok, encoded = cv2.imencode('.jpg', evidence, [cv2.IMWRITE_JPEG_QUALITY, 92])
        if not ok:
            return None, sha256

        date_dir = self.output_dir / datetime.utcnow().strftime("%Y/%m/%d")
        date_dir.mkdir(parents=True, exist_ok=True)

        fname = f"incident_{track_id}_{int(time.time())}_{sha256[:8]}.jpg"
        full_path = date_dir / fname
        with open(full_path, 'wb') as f:
            f.write(encoded.tobytes())

        rel_path = f"snapshots/{datetime.utcnow().strftime('%Y/%m/%d')}/{fname}"
        return rel_path, sha256
