import time
import json
from django.shortcuts import render, get_object_or_404, redirect
from django.http import StreamingHttpResponse, JsonResponse
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_exempt
from django.core.paginator import Paginator
from .models import ExamSession, CameraFeed, Student, ViolationIncident
from vision_core.camera_stream import CameraStreamManager

def dashboard_view(request):
    """Primary Live Proctoring Dashboard View."""
    active_session = ExamSession.objects.filter(is_active=True).first()
    cameras = CameraFeed.objects.filter(is_active=True)
    recent_incidents = ViolationIncident.objects.all().order_by('-created_at')[:10]
    
    total_incidents = ViolationIncident.objects.count()
    pending_reviews = ViolationIncident.objects.filter(review_status='PENDING').count()
    confirmed_violations = ViolationIncident.objects.filter(review_status='CONFIRMED').count()

    context = {
        'active_session': active_session,
        'cameras': cameras,
        'recent_incidents': recent_incidents,
        'total_incidents': total_incidents,
        'pending_reviews': pending_reviews,
        'confirmed_violations': confirmed_violations,
    }
    return render(request, 'monitoring/dashboard.html', context)


def incident_list_view(request):
    """Forensic Incident Logs with Filtering and Pagination."""
    status_filter = request.GET.get('status', '')
    type_filter = request.GET.get('type', '')

    queryset = ViolationIncident.objects.all().order_by('-created_at')
    if status_filter:
        queryset = queryset.filter(review_status=status_filter)
    if type_filter:
        queryset = queryset.filter(violation_type=type_filter)

    paginator = Paginator(queryset, 12)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    context = {
        'page_obj': page_obj,
        'status_filter': status_filter,
        'type_filter': type_filter,
        'violation_types': ViolationIncident.VIOLATION_TYPES,
        'review_statuses': ViolationIncident.REVIEW_STATUS,
    }
    return render(request, 'monitoring/incidents.html', context)


@require_POST
def review_incident_view(request, incident_id):
    """Allows proctor to Confirm or Dismiss an incident."""
    incident = get_object_or_404(ViolationIncident, id=incident_id)
    status = request.POST.get('status')
    notes = request.POST.get('notes', '')

    if status in ['CONFIRMED', 'DISMISSED', 'PENDING']:
        incident.review_status = status
        incident.notes = notes
        incident.save()
        return JsonResponse({'success': True, 'new_status': status})
    return JsonResponse({'success': False, 'error': 'Invalid status'}, status=400)


def student_list_view(request):
    """Renders student registry and recent incident counts."""
    students = Student.objects.all().prefetch_related('incidents')
    context = {'students': students}
    return render(request, 'monitoring/students.html', context)


def camera_config_view(request):
    """View and manage camera stream sources (Webcam, RTSP, Video File)."""
    if request.method == 'POST':
        name = request.POST.get('name', 'Camera')
        source_type = request.POST.get('source_type', 'WEBCAM')
        source_url = request.POST.get('source_url', '0')
        room = request.POST.get('room_number', 'Room 101')

        CameraFeed.objects.create(
            name=name,
            source_type=source_type,
            source_url=source_url,
            room_number=room,
            is_active=True
        )
        return redirect('monitoring:camera_config')

    cameras = CameraFeed.objects.all()
    context = {'cameras': cameras}
    return render(request, 'monitoring/camera_setup.html', context)


def gen_frames(camera_id):
    """MJPEG Frame Generator for streaming live video to web browsers."""
    manager = CameraStreamManager.get_instance(camera_id=camera_id)
    if not manager.running:
        manager.start()

    while True:
        jpeg_bytes = manager.get_latest_jpeg()
        if jpeg_bytes:
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + jpeg_bytes + b'\r\n')
        time.sleep(0.04) # ~25 FPS streaming rate


def video_feed(request, camera_id):
    """HTTP MJPEG video stream endpoint for <img> tags."""
    return StreamingHttpResponse(
        gen_frames(camera_id),
        content_type='multipart/x-mixed-replace; boundary=frame'
    )


@csrf_exempt
@require_POST
def start_camera_api(request):
    """API endpoint to start a camera stream."""
    try:
        data = json.loads(request.body)
        cam_id = data.get('camera_id', 'default')
        source = data.get('source', '0')
        
        manager = CameraStreamManager.get_instance(camera_id=cam_id, source=source)
        manager.start()
        return JsonResponse({'success': True, 'camera_id': cam_id, 'status': 'running'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@csrf_exempt
@require_POST
def stop_camera_api(request):
    """API endpoint to stop a camera stream."""
    try:
        data = json.loads(request.body)
        cam_id = data.get('camera_id', 'default')
        
        manager = CameraStreamManager.get_instance(camera_id=cam_id)
        manager.stop()
        return JsonResponse({'success': True, 'camera_id': cam_id, 'status': 'stopped'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)
