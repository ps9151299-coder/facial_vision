import numpy as np

class ProhibitedObjectDetector:
    """
    Dynamic Prohibited Object Detection & Spatial Association.
    Eliminates fixed desk ROIs by associating detected contraband directly
    with dynamic student bounding boxes and skeletal keypoints.
    """
    def __init__(self, confidence_threshold=0.45):
        self.conf_threshold = confidence_threshold
        # COCO class IDs: 67 is cell phone, 76 is scissors, 77 is teddy bear, etc.
        # Can also load fine-tuned custom weights
        self.prohibited_class_names = {
            'cell phone': 'PHONE_DETECTED',
            'phone': 'PHONE_DETECTED',
            'smartphone': 'PHONE_DETECTED',
            'book': 'CHIT_DETECTED',
            'paper': 'CHIT_DETECTED',
            'notes': 'CHIT_DETECTED',
            'remote': 'SMARTWATCH_EARPHONE',
            'watch': 'SMARTWATCH_EARPHONE',
            'earphone': 'SMARTWATCH_EARPHONE',
            'headphones': 'SMARTWATCH_EARPHONE'
        }

    def associate_objects_with_students(self, detected_objects, student_tracks):
        """
        Dynamically matches detected prohibited items to active student tracks
        using Intersection-over-Union (IoU) and Euclidean proximity.

        Args:
            detected_objects: list of dicts [{'bbox': [x1, y1, x2, y2], 'class_name': str, 'conf': float}]
            student_tracks: list of dicts [{'track_id': int, 'bbox': [x1, y1, x2, y2], 'keypoints': ...}]

        Returns:
            list of dicts [{'student_track_id': int, 'violation_type': str, 'conf': float, 'obj_bbox': [...]}]
        """
        associations = []
        if not detected_objects or not student_tracks:
            return associations

        for obj in detected_objects:
            obj_box = obj['bbox']
            obj_name = obj['class_name'].lower()
            obj_conf = obj['conf']
            
            violation_type = self.prohibited_class_names.get(obj_name, 'PHONE_DETECTED')

            best_track_id = None
            best_iou = 0.0
            min_dist = float('inf')

            ox1, oy1, ox2, oy2 = obj_box
            ocx = (ox1 + ox2) / 2.0
            ocy = (oy1 + oy2) / 2.0

            for student in student_tracks:
                track_id = student['track_id']
                sx1, sy1, sx2, sy2 = student['bbox']
                
                # Check bounding box IoU / Overlap
                ix1 = max(ox1, sx1)
                iy1 = max(oy1, sy1)
                ix2 = min(ox2, sx2)
                iy2 = min(oy2, sy2)
                
                iw = max(0, ix2 - ix1)
                ih = max(0, iy2 - iy1)
                intersection = iw * ih
                
                obj_area = (ox2 - ox1) * (oy2 - oy1)
                if obj_area > 0 and intersection / obj_area > 0.3:
                    # Object is directly held or overlapping with the student
                    best_track_id = track_id
                    break

                # Spatial proximity: center distance relative to student size
                scx = (sx1 + sx2) / 2.0
                scy = (sy1 + sy2) / 2.0
                s_height = max(1, sy2 - sy1)
                
                dist = np.sqrt((ocx - scx)**2 + (ocy - scy)**2)
                if dist < 1.2 * s_height and dist < min_dist:
                    min_dist = dist
                    best_track_id = track_id

            if best_track_id is not None:
                associations.append({
                    'student_track_id': best_track_id,
                    'violation_type': violation_type,
                    'confidence': float(obj_conf),
                    'obj_bbox': [int(c) for c in obj_box],
                    'label': obj_name
                })

        return associations
