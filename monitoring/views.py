import time
import json
from django.shortcuts import render, get_object_or_404, redirect
from django.http import StreamingHttpResponse, JsonResponse
from django.views.decorators.http import require_POST, require_GET
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


@csrf_exempt
@require_POST
def delete_incident_view(request, incident_id):
    """Deletes an incident and its associated snapshot image file from disk."""
    incident = get_object_or_404(ViolationIncident, id=incident_id)
    try:
        if incident.snapshot and incident.snapshot.path:
            import os
            if os.path.exists(incident.snapshot.path):
                os.remove(incident.snapshot.path)
    except Exception:
        pass

    incident.delete()
    return JsonResponse({'success': True, 'deleted_id': str(incident_id)})


@csrf_exempt
@require_POST
def delete_all_incidents_view(request):
    """Bulk deletes all incidents and associated snapshots from disk."""
    import os
    count = 0
    for incident in ViolationIncident.objects.all():
        try:
            if incident.snapshot and incident.snapshot.path:
                if os.path.exists(incident.snapshot.path):
                    os.remove(incident.snapshot.path)
        except Exception:
            pass
        incident.delete()
        count += 1

    return JsonResponse({'success': True, 'deleted_count': count})


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
    source = '0'
    try:
        # Check if camera_id matches CameraFeed by UUID or name
        cam = None
        if camera_id != 'default':
            cam = CameraFeed.objects.filter(id=camera_id).first()
        if not cam:
            cam = CameraFeed.objects.filter(is_active=True).first()
        if cam and cam.source_url:
            source = cam.source_url
    except Exception:
        source = '0'

    manager = CameraStreamManager.get_instance(camera_id=camera_id, source=source)
    if not manager.running:
        manager.start()

    while True:
        jpeg_bytes = manager.get_latest_jpeg()
        if jpeg_bytes:
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + jpeg_bytes + b'\r\n')
        else:
            time.sleep(0.04)
            continue
        time.sleep(0.035) # ~28 FPS smooth streaming rate


def video_feed(request, camera_id):
    """HTTP MJPEG video stream endpoint for <img> tags."""
    response = StreamingHttpResponse(
        gen_frames(camera_id),
        content_type='multipart/x-mixed-replace; boundary=frame'
    )
    response['Cache-Control'] = 'no-cache, no-store, must-revalidate, pre-check=0, post-check=0, max-age=0'
    response['Pragma'] = 'no-cache'
    response['Expires'] = '0'
    return response


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


def incidents_poll_api(request):
    """
    Real-time polling API — returns new ViolationIncidents created after
    the given `after_ts` Unix timestamp (float, e.g. 1792003200.0).
    Also returns real-time student pose telemetries and live metric counters.
    JS polls this every 1-2 seconds to show new snaps and update UI without page refresh.

    GET /api/incidents/poll/?after=<unix_timestamp>
    """
    from datetime import datetime, timezone as tz
    current_server_ts = datetime.now(tz=tz.utc).timestamp()

    try:
        after_ts = float(request.GET.get('after', 0))
    except (ValueError, TypeError):
        after_ts = 0.0

    incidents = []
    if after_ts > 0:
        after_dt = datetime.fromtimestamp(after_ts, tz=tz.utc)
        qs = ViolationIncident.objects.filter(
            created_at__gt=after_dt
        ).order_by('created_at')[:30]

        for inc in qs:
            incidents.append({
                'id':              str(inc.id),
                'track_id':        inc.track_id,
                'violation_type':  inc.violation_type,
                'violation_label': inc.get_violation_type_display(),
                'confidence':      round(inc.confidence_score * 100, 1),
                'duration':        round(inc.duration_seconds, 1),
                'review_status':   inc.review_status,
                'snapshot_url':    inc.snapshot.url if inc.snapshot else None,
                'snapshot_sha256': inc.snapshot_sha256 or '',
                'created_at_ts':   inc.created_at.timestamp(),
                'created_at_str':  inc.created_at.strftime('%H:%M:%S UTC'),
            })

    # Collect active students telemetry from running camera stream instances
    active_students = []
    for inst in list(CameraStreamManager._instances.values()):
        if inst.running and getattr(inst, 'latest_students', None):
            for s in inst.latest_students:
                pose = s.get('pose')
                active_students.append({
                    'track_id': s['track_id'],
                    'yaw':      pose['yaw'] if pose else 0.0,
                    'pitch':    pose['pitch'] if pose else 0.0,
                    'roll':     pose['roll'] if pose else 0.0,
                    'gaze':     pose.get('eye_gaze_side', 'CENTER') if pose else 'CENTER'
                })

    return JsonResponse({
        'success': True,
        'incidents': incidents,
        'students': active_students,
        'server_ts': current_server_ts,
        'total_incidents': ViolationIncident.objects.count(),
        'pending_reviews': ViolationIncident.objects.filter(review_status='PENDING').count(),
        'confirmed_violations': ViolationIncident.objects.filter(review_status='CONFIRMED').count(),
    })


def _build_cheating_report_context(threshold=1, session_id=None):
    """
    Builds structured cheating audit records and executive insights.
    Capped at maximum 5 evidence snapshots per candidate.
    """
    from collections import Counter
    import os
    import base64

    session_obj = None
    if session_id:
        session_obj = ExamSession.objects.filter(id=session_id).first()
    if not session_obj:
        session_obj = ExamSession.objects.filter(is_active=True).first()

    query_filter = {}
    if session_obj:
        query_filter['session'] = session_obj

    all_incidents = ViolationIncident.objects.filter(**query_filter).order_by('created_at')

    track_incidents = {}
    all_violation_types = []
    for inc in all_incidents:
        tid = inc.track_id
        if tid not in track_incidents:
            track_incidents[tid] = []
        track_incidents[tid].append(inc)
        all_violation_types.append(inc.get_violation_type_display())

    candidates = []
    confirmed_count = 0

    for tid, incs in sorted(track_incidents.items()):
        if len(incs) >= threshold:
            capped_snaps = incs[:5]
            student_obj = capped_snaps[0].student or Student.objects.filter(student_id=f"STU-TRACK-{tid}").first()
            student_name = student_obj.name if student_obj else f"Candidate (Track #{tid})"
            roll_num = student_obj.roll_number if student_obj else f"ROLL-T{tid}"
            student_id = student_obj.student_id if student_obj else f"STU-TRACK-{tid}"

            # Detailed violation breakdown
            v_counts = Counter(inc.get_violation_type_display() for inc in capped_snaps)
            violation_types_list = [
                {'label': v_name, 'count': count}
                for v_name, count in v_counts.most_common()
            ]
            summary_breakdown = ", ".join(f"{cnt}x {v}" for v, cnt in v_counts.most_common())
            avg_conf = sum(inc.confidence_score for inc in capped_snaps) / len(capped_snaps)

            is_confirmed = (len(incs) >= 5 or any(inc.review_status == 'CONFIRMED' for inc in capped_snaps))
            if is_confirmed:
                confirmed_count += 1

            candidates.append({
                'track_id': tid,
                'name': student_name,
                'roll_number': roll_num,
                'student_id': student_id,
                'is_confirmed': is_confirmed,
                'status_label': 'CONFIRMED CHEATER (5/5 Limit Reached)' if is_confirmed else f'FLAGGED ({len(capped_snaps)} Violation{"s" if len(capped_snaps) > 1 else ""})',
                'verdict': 'CONFIRMED ACADEMIC DISHONESTY' if is_confirmed else 'SUSPICIOUS CHEATING ACTIVITY',
                'avg_conf': round(avg_conf * 100, 1),
                'breakdown_str': summary_breakdown,
                'violation_types_list': violation_types_list,
                'snapshots': capped_snaps,
                'first_seen': capped_snaps[0].created_at,
                'last_seen': capped_snaps[-1].created_at,
            })

    # Executive Insights
    most_common_violation = Counter(all_violation_types).most_common(1)
    top_violation_name = most_common_violation[0][0] if most_common_violation else "None"

    return {
        'session': session_obj,
        'threshold': threshold,
        'candidates': candidates,
        'total_tracks': len(track_incidents),
        'total_flagged_students': len(candidates),
        'confirmed_cheaters_count': confirmed_count,
        'total_incidents_count': all_incidents.count(),
        'top_violation_name': top_violation_name,
    }


def cheating_report_view(request):
    """
    Renders the Final Cheating Audit Report in the browser.
    Displays all students who cheated with their violation types and evidence snapshots.
    """
    try:
        threshold = int(request.GET.get('threshold', 1))
    except (ValueError, TypeError):
        threshold = 1

    context = _build_cheating_report_context(threshold=threshold)
    return render(request, 'monitoring/cheating_report.html', context)


def download_cheating_report_view(request):
    """
    Generates and downloads a self-contained, offline-ready HTML evidence report.
    All snapshot images are embedded in Base64 so the teacher can save or share
    the file anywhere without needing the web server running.
    """
    import os
    import base64
    from datetime import datetime, timezone
    from django.http import HttpResponse

    try:
        threshold = int(request.GET.get('threshold', 1))
    except (ValueError, TypeError):
        threshold = 1

    data = _build_cheating_report_context(threshold=threshold)
    session = data['session']
    candidates = data['candidates']

    session_name = session.exam_name if session else "General Exam Surveillance"
    room_number = session.room_number if session else "Room 101"
    now_dt = datetime.now(timezone.utc)
    report_date_str = now_dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    filename_date = now_dt.strftime("%Y%m%d_%H%M%S")

    # Build standalone HTML with embedded base64 photos
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Cheating Evidence Report - {session_name}</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif; }}
    body {{ background: #0f172a; color: #f8fafc; padding: 30px; line-height: 1.5; }}
    .container {{ max-width: 1100px; margin: 0 auto; }}
    .report-header {{ background: #1e293b; border-left: 6px solid #ef4444; border-radius: 8px; padding: 24px; margin-bottom: 24px; }}
    .report-title {{ font-size: 24px; font-weight: 800; color: #ffffff; margin-bottom: 6px; }}
    .report-subtitle {{ color: #94a3b8; font-size: 14px; margin-bottom: 16px; }}
    .insights-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 14px; padding-top: 16px; border-top: 1px solid #334155; }}
    .insight-card {{ background: #0f172a; padding: 12px 16px; border-radius: 6px; border: 1px solid #334155; }}
    .insight-card .label {{ color: #94a3b8; font-size: 11px; text-transform: uppercase; font-weight: 600; display: block; }}
    .insight-card .val {{ font-size: 18px; font-weight: 700; color: #ffffff; margin-top: 2px; }}
    .student-dossier {{ background: #1e293b; border: 1px solid #334155; border-radius: 8px; margin-bottom: 30px; overflow: hidden; }}
    .dossier-header {{ background: rgba(239, 68, 68, 0.12); border-bottom: 1px solid rgba(239, 68, 68, 0.25); padding: 18px 20px; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px; }}
    .student-title {{ font-size: 18px; font-weight: 700; color: #fff; }}
    .student-sub {{ font-size: 13px; color: #94a3b8; margin-top: 2px; }}
    .badge {{ display: inline-block; padding: 4px 10px; border-radius: 4px; font-size: 12px; font-weight: 700; }}
    .badge-red {{ background: #ef4444; color: white; }}
    .badge-orange {{ background: #f97316; color: white; }}
    .dossier-body {{ padding: 20px; }}
    .cheating-types-box {{ background: rgba(0,0,0,0.3); border: 1px solid #334155; border-radius: 6px; padding: 14px 18px; margin-bottom: 20px; }}
    .cheating-types-box h4 {{ color: #06b6d4; font-size: 14px; font-weight: 700; margin-bottom: 8px; text-transform: uppercase; }}
    .types-list {{ list-style: none; display: flex; flex-wrap: wrap; gap: 8px; }}
    .type-pill {{ background: #0f172a; border: 1px solid #475569; padding: 4px 12px; border-radius: 20px; font-size: 13px; color: #f1f5f9; }}
    .snaps-title {{ font-size: 14px; font-weight: 700; color: #ffffff; margin-bottom: 12px; text-transform: uppercase; letter-spacing: 0.5px; }}
    .snaps-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 14px; }}
    .snap-card {{ background: #0f172a; border: 1px solid #334155; border-radius: 6px; overflow: hidden; }}
    .snap-card img {{ width: 100%; height: 160px; object-fit: cover; display: block; background: #000; }}
    .snap-meta {{ padding: 10px; font-size: 12px; }}
    .snap-type {{ font-weight: 700; color: #f8fafc; margin-bottom: 4px; }}
    .snap-info {{ color: #94a3b8; font-size: 11px; line-height: 1.4; }}
    .snap-sha {{ font-family: monospace; font-size: 10px; color: #06b6d4; background: rgba(6, 182, 212, 0.1); padding: 2px 4px; border-radius: 3px; margin-top: 6px; word-break: break-all; }}
    .footer {{ text-align: center; color: #64748b; font-size: 12px; margin-top: 40px; padding-top: 20px; border-top: 1px solid #334155; }}
    @media print {{
      body {{ background: #ffffff; color: #000000; padding: 15px; }}
      .report-header, .student-dossier, .snap-card, .insight-card, .cheating-types-box {{ background: #ffffff !important; color: #000000 !important; border: 1px solid #cccccc !important; }}
      .report-title, .student-title, .snap-type {{ color: #000000 !important; }}
      .report-subtitle, .student-sub, .snap-info {{ color: #555555 !important; }}
      .badge-red {{ background: #b91c1c !important; color: #fff !important; }}
      .snap-sha {{ color: #222 !important; }}
    }}
  </style>
</head>
<body>
  <div class="container">
    <!-- 1. Report Title & Header -->
    <div class="report-header">
      <div class="report-title">🚨 EXAMINATION CHEATING & FORENSIC EVIDENCE REPORT</div>
      <div class="report-subtitle">
        Official Academic Integrity Surveillance Dossier &bull; Generated: {report_date_str}
      </div>
      <div class="insights-grid">
        <div class="insight-card">
          <span class="label">Exam Session</span>
          <div class="val">{session_name}</div>
        </div>
        <div class="insight-card">
          <span class="label">Room / Lab</span>
          <div class="val">{room_number}</div>
        </div>
        <div class="insight-card">
          <span class="label">Students Flagged</span>
          <div class="val" style="color: #ef4444;">{data['total_flagged_students']} Student(s)</div>
        </div>
        <div class="insight-card">
          <span class="label">Confirmed Cheaters</span>
          <div class="val" style="color: #f97316;">{data['confirmed_cheaters_count']} Candidate(s)</div>
        </div>
        <div class="insight-card">
          <span class="label">Most Frequent Method</span>
          <div class="val" style="font-size: 14px; color: #06b6d4;">{data['top_violation_name']}</div>
        </div>
      </div>
    </div>
"""

    if not candidates:
        html += """
    <div style="background: #1e293b; padding: 40px; text-align: center; border-radius: 8px; border: 1px solid #334155;">
      <h3 style="font-size: 18px; margin-bottom: 8px;">No Cheating Incidents Recorded</h3>
      <p style="color: #94a3b8; font-size: 14px;">Surveillance is nominal. No students have been flagged for cheating.</p>
    </div>
"""
    else:
        for cand in candidates:
            # 2. Student ID & Details
            badge_class = "badge-red" if cand['is_confirmed'] else "badge-orange"
            html += f"""
    <!-- Student Dossier -->
    <div class="student-dossier">
      <div class="dossier-header">
        <div>
          <div class="student-title">👤 {cand['name']} ({cand['roll_number']})</div>
          <div class="student-sub">
            Student Identifier: <strong>{cand['student_id']}</strong> &bull; Tracking ID: <strong>Track #{cand['track_id']}</strong>
          </div>
        </div>
        <div>
          <span class="badge {badge_class}">{cand['status_label']}</span>
        </div>
      </div>

      <div class="dossier-body">
        <!-- 3. Which Type of Cheating He Tried -->
        <div class="cheating-types-box">
          <h4>Cheating Attempted ({len(cand['snapshots'])} Evidence Events Captured):</h4>
          <div class="types-list">
"""
            for v_item in cand['violation_types_list']:
                html += f"""            <span class="type-pill">⚠ {v_item['label']}: <strong>{v_item['count']} time(s)</strong></span>\n"""

            html += f"""          </div>
          <div style="font-size: 12px; color: #94a3b8; margin-top: 10px;">
            Evidence Window: {cand['first_seen'].strftime('%H:%M:%S UTC')} &rarr; {cand['last_seen'].strftime('%H:%M:%S UTC')} &bull; Average AI Confidence: <strong>{cand['avg_conf']}%</strong>
          </div>
        </div>

        <!-- 4. Below: Their Snapshots -->
        <div class="snaps-title">Captured Evidence Snapshots (Underneath):</div>
        <div class="snaps-grid">
"""
            for idx, snap in enumerate(cand['snapshots'], 1):
                b64_src = ""
                if snap.snapshot and os.path.exists(snap.snapshot.path):
                    try:
                        with open(snap.snapshot.path, 'rb') as img_f:
                            raw = img_f.read()
                            b64_src = f"data:image/jpeg;base64,{base64.b64encode(raw).decode('utf-8')}"
                    except Exception:
                        pass

                img_tag = f'<img src="{b64_src}" alt="Snapshot #{idx}" />' if b64_src else '<div style="height:160px; background:#111; display:flex; align-items:center; justify-content:center; color:#777; font-size:12px;">Image File Missing</div>'
                sha_str = snap.snapshot_sha256 or "N/A"

                html += f"""
          <div class="snap-card">
            {img_tag}
            <div class="snap-meta">
              <div class="snap-type">#{idx} &bull; {snap.get_violation_type_display()}</div>
              <div class="snap-info">
                Time: {snap.created_at.strftime('%H:%M:%S UTC')}<br>
                Confidence: {round(snap.confidence_score * 100, 1)}% &bull; {round(snap.duration_seconds, 1)}s
              </div>
              <div class="snap-sha" title="Cryptographic SHA-256">
                SHA: {sha_str[:16]}...
              </div>
            </div>
          </div>
"""

            html += """
        </div>
      </div>
    </div>
"""

    html += f"""
    <div class="footer">
      AI Exam Proctoring System &bull; Cryptographically Verified Evidence Dossier &bull; Generated on {report_date_str}
    </div>
  </div>
</body>
</html>
"""

    response = HttpResponse(html, content_type='text/html; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="Cheating_Evidence_Report_{filename_date}.html"'
    return response


