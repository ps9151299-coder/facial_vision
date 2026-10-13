from django.contrib import admin
from .models import Student, ExamSession, CameraFeed, ViolationIncident

@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    list_display = ('student_id', 'name', 'roll_number', 'created_at')
    search_fields = ('student_id', 'name', 'roll_number')

@admin.register(ExamSession)
class ExamSessionAdmin(admin.ModelAdmin):
    list_display = ('exam_name', 'room_number', 'is_active', 'start_time', 'end_time')
    list_filter = ('is_active', 'room_number')

@admin.register(CameraFeed)
class CameraFeedAdmin(admin.ModelAdmin):
    list_display = ('name', 'source_type', 'source_url', 'room_number', 'is_active')
    list_filter = ('source_type', 'is_active')

@admin.register(ViolationIncident)
class ViolationIncidentAdmin(admin.ModelAdmin):
    list_display = ('track_id', 'violation_type', 'confidence_score', 'duration_seconds', 'review_status', 'email_dispatched', 'created_at')
    list_filter = ('violation_type', 'review_status', 'email_dispatched', 'created_at')
    search_fields = ('track_id', 'violation_type')
