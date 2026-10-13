import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'proctor_system.settings')
django.setup()

from monitoring.models import ExamSession, CameraFeed, Student

def seed():
    print("Seeding initial exam session and camera data...")
    # 1. Create or get Active Exam Session
    session, created = ExamSession.objects.get_or_create(
        exam_name="Mid-Term Computer Architecture Examination",
        defaults={
            'room_number': "Room 101",
            'is_active': True
        }
    )
    if created:
        print(f"Created Exam Session: {session.exam_name}")

    # 2. Create Default Camera Feed
    camera, c_created = CameraFeed.objects.get_or_create(
        name="Primary Classroom Camera",
        defaults={
            'source_type': "WEBCAM",
            'source_url': "0",
            'room_number': "Room 101",
            'is_active': True
        }
    )
    if c_created:
        print(f"Created Default Camera: {camera.name}")

    # 3. Create Sample Students
    students_data = [
        {"student_id": "STU-101", "name": "Alex Mercer", "roll_number": "CS-2026-001"},
        {"student_id": "STU-102", "name": "Elena Rostova", "roll_number": "CS-2026-002"},
        {"student_id": "STU-103", "name": "David Kim", "roll_number": "CS-2026-003"},
        {"student_id": "STU-104", "name": "Sophia Zhang", "roll_number": "CS-2026-004"},
    ]
    for s in students_data:
        student, s_created = Student.objects.get_or_create(
            student_id=s['student_id'],
            defaults={
                'name': s['name'],
                'roll_number': s['roll_number']
            }
        )
        if s_created:
            print(f"Registered Student: {student.name} ({student.roll_number})")

    print("Data seeding completed successfully!")

if __name__ == '__main__':
    seed()
