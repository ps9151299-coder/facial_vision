import uuid
from django.db import models

class Student(models.Model):
    student_id = models.CharField(max_length=50, unique=True, help_text="e.g. STU-101")
    name = models.CharField(max_length=150)
    roll_number = models.CharField(max_length=50, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} ({self.roll_number})"

    class Meta:
        ordering = ['roll_number']


class ExamSession(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    exam_name = models.CharField(max_length=200)
    room_number = models.CharField(max_length=50, default="Room 101")
    is_active = models.BooleanField(default=True)
    start_time = models.DateTimeField(auto_now_add=True)
    end_time = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        status = "Active" if self.is_active else "Finished"
        return f"{self.exam_name} ({self.room_number}) - {status}"

    class Meta:
        ordering = ['-start_time']


class CameraFeed(models.Model):
    SOURCE_TYPES = [
        ('WEBCAM', 'Local USB / Built-in Webcam'),
        ('RTSP', 'Network IP Camera (RTSP)'),
        ('VIDEO_FILE', 'Test Video File'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100, default="Primary Camera")
    source_type = models.CharField(max_length=20, choices=SOURCE_TYPES, default='WEBCAM')
    source_url = models.CharField(max_length=500, default="0", help_text="Device index '0' or RTSP URL or path to video file")
    room_number = models.CharField(max_length=50, default="Room 101")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} [{self.source_type}] ({self.source_url})"


class ViolationIncident(models.Model):
    VIOLATION_TYPES = [
        ('HEAD_LOOKING_LEFT', 'Looking Left (Neighbor Paper)'),
        ('HEAD_LOOKING_RIGHT', 'Looking Right (Neighbor Paper)'),
        ('SUSPICIOUS_LAP_GAZE', 'Suspicious Downward Lap Gaze'),
        ('PHONE_DETECTED', 'Prohibited Smartphone Detected'),
        ('CHIT_DETECTED', 'Paper Notes / Chit Detected'),
        ('SMARTWATCH_EARPHONE', 'Smartwatch / Earphone Detected'),
        ('CONCEALED_HAND', 'Concealed Lap Hand Kinematics'),
        ('HAND_TO_EAR', 'Suspicious Hand Near Ear / Mouth'),
        ('INTER_PERSON_INTRUSION', 'Inter-Person Collaboration / Peeking'),
        ('UNAUTHORIZED_ABSENCE', 'Absence / Desk Roaming Detected'),
    ]

    REVIEW_STATUS = [
        ('PENDING', 'Pending Proctor Review'),
        ('CONFIRMED', 'Confirmed Violation'),
        ('DISMISSED', 'False Alarm / Dismissed'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(ExamSession, on_delete=models.CASCADE, null=True, blank=True, related_name='incidents')
    camera = models.ForeignKey(CameraFeed, on_delete=models.SET_NULL, null=True, blank=True, related_name='incidents')
    student = models.ForeignKey(Student, on_delete=models.SET_NULL, null=True, blank=True, related_name='incidents')
    
    # Dynamic tracking identifier (no fixed desk ROI needed)
    track_id = models.IntegerField(help_text="Dynamic ByteTrack ID of student")
    violation_type = models.CharField(max_length=50, choices=VIOLATION_TYPES)
    confidence_score = models.FloatField(default=0.85)
    duration_seconds = models.FloatField(default=3.0)
    
    # Telemetry details (Euler angles, detected objects, keypoints)
    telemetry = models.JSONField(default=dict, blank=True)
    
    # Forensic evidence
    snapshot = models.ImageField(upload_to='snapshots/%Y/%m/%d/', null=True, blank=True)
    snapshot_sha256 = models.CharField(max_length=64, blank=True, help_text="Forensic SHA-256 hash")
    
    # Email alert status
    email_dispatched = models.BooleanField(default=False)
    dispatched_at = models.DateTimeField(null=True, blank=True)
    
    # Proctor review
    review_status = models.CharField(max_length=20, choices=REVIEW_STATUS, default='PENDING')
    notes = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Track #{self.track_id} - {self.get_violation_type_display()} ({self.confidence_score:.2f})"

    class Meta:
        ordering = ['-created_at']
