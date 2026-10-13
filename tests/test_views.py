from django.test import TestCase, Client
from django.urls import reverse
from monitoring.models import ExamSession, CameraFeed, Student, ViolationIncident

class TestProctorViews(TestCase):
    def setUp(self):
        self.client = Client()
        self.session = ExamSession.objects.create(exam_name="Test Exam", room_number="Room 101", is_active=True)
        self.camera = CameraFeed.objects.create(name="Cam 1", source_type="WEBCAM", source_url="0", is_active=True)
        self.student = Student.objects.create(student_id="STU-999", name="Test Student", roll_number="CS-999")
        self.incident = ViolationIncident.objects.create(
            session=self.session,
            camera=self.camera,
            student=self.student,
            track_id=1,
            violation_type="PHONE_DETECTED",
            confidence_score=0.92,
            duration_seconds=3.5,
            snapshot_sha256="abc123hash"
        )

    def test_dashboard_view(self):
        response = self.client.get(reverse('monitoring:dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Test Exam")
        self.assertContains(response, "Prohibited Smartphone Detected")

    def test_incidents_view(self):
        response = self.client.get(reverse('monitoring:incidents'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Prohibited Smartphone Detected")
        self.assertContains(response, "abc123hash")

    def test_students_view(self):
        response = self.client.get(reverse('monitoring:students'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Test Student")

    def test_camera_config_view(self):
        response = self.client.get(reverse('monitoring:camera_config'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Cam 1")

    def test_review_incident_ajax(self):
        response = self.client.post(
            reverse('monitoring:review_incident', kwargs={'incident_id': self.incident.id}),
            {'status': 'CONFIRMED', 'notes': 'Verified phone in lap'}
        )
        self.assertEqual(response.status_code, 200)
        self.incident.refresh_from_db()
        self.assertEqual(self.incident.review_status, 'CONFIRMED')
        self.assertEqual(self.incident.notes, 'Verified phone in lap')
