import threading
import logging
from email.mime.image import MIMEImage
from django.core.mail import EmailMultiAlternatives
from django.conf import settings
from django.utils import timezone
from pathlib import Path

logger = logging.getLogger(__name__)

def _send_email_worker(subject, text_content, html_content, recipient_list, image_path=None):
    """Worker function executed inside a non-blocking background thread."""
    try:
        msg = EmailMultiAlternatives(
            subject=subject,
            body=text_content,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=recipient_list
        )
        msg.attach_alternative(html_content, "text/html")
        
        # Attach embedded inline image if available
        if image_path and Path(image_path).exists():
            with open(image_path, 'rb') as f:
                img_data = f.read()
                mime_img = MIMEImage(img_data)
                mime_img.add_header('Content-ID', '<snapshot_evidence>')
                mime_img.add_header('Content-Disposition', 'inline', filename=Path(image_path).name)
                msg.attach(mime_img)
                
        msg.send(fail_silently=False)
        logger.info(f"Proctor alert email dispatched successfully to {recipient_list}")
    except Exception as e:
        logger.error(f"Failed to dispatch proctor alert email: {e}")


def dispatch_violation_email_async(incident, recipient_email=None):
    """
    Non-blocking async dispatcher for violation alerts.
    Spawns an independent thread to prevent blocking frame processing.
    """
    if not recipient_email:
        recipient_email = getattr(settings, 'PROCTOR_NOTIFICATION_EMAIL', 'proctor@exam-system.local')
    
    subject = f"🚨 [EXAM ALERT] Violation Detected: {incident.get_violation_type_display()} (Track #{incident.track_id})"
    
    text_content = f"""
EXAMINATION INTEGRITY ALERT
----------------------------------------
Violation: {incident.get_violation_type_display()}
Student Track ID: #{incident.track_id}
Confidence Score: {incident.confidence_score * 100:.1f}%
Duration: {incident.duration_seconds:.1f}s
Timestamp: {incident.created_at.strftime('%Y-%m-%d %H:%M:%S UTC')}
Room: {incident.session.room_number if incident.session else 'Main Exam Hall'}
----------------------------------------
Review evidence in the Proctoring Control Room.
    """
    
    image_cid_html = """
    <div style="margin-top: 20px; text-align: center;">
        <p style="font-weight: bold; color: #4b5563;">Forensic Snapshot Evidence:</p>
        <img src="cid:snapshot_evidence" style="max-width: 100%; border-radius: 8px; border: 2px solid #ef4444;" alt="Forensic Evidence" />
    </div>
    """ if incident.snapshot else ""

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #f3f4f6; margin: 0; padding: 20px; }}
            .card {{ max-width: 600px; margin: 0 auto; background: #ffffff; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1); border-top: 6px solid #ef4444; }}
            .header {{ background: #111827; color: #ffffff; padding: 24px; text-align: center; }}
            .badge {{ display: inline-block; background: #dc2626; color: #ffffff; padding: 4px 12px; border-radius: 9999px; font-weight: bold; font-size: 13px; text-transform: uppercase; }}
            .content {{ padding: 24px; color: #1f2937; }}
            .metric-table {{ width: 100%; border-collapse: collapse; margin-top: 16px; }}
            .metric-table td {{ padding: 10px 12px; border-bottom: 1px solid #e5e7eb; }}
            .metric-table td.label {{ font-weight: 600; color: #4b5563; width: 40%; }}
            .footer {{ background: #f9fafb; padding: 16px 24px; text-align: center; font-size: 13px; color: #6b7280; }}
        </style>
    </head>
    <body>
        <div class="card">
            <div class="header">
                <span class="badge">Immediate Attention Required</span>
                <h2 style="margin: 12px 0 0 0; font-size: 20px;">AI Proctoring Violation Detected</h2>
            </div>
            <div class="content">
                <table class="metric-table">
                    <tr><td class="label">Violation Type</td><td style="color: #dc2626; font-weight: bold;">{incident.get_violation_type_display()}</td></tr>
                    <tr><td class="label">Student Track ID</td><td>#{incident.track_id}</td></tr>
                    <tr><td class="label">Confidence Score</td><td>{incident.confidence_score * 100:.1f}%</td></tr>
                    <tr><td class="label">Sustained Duration</td><td>{incident.duration_seconds:.1f} seconds</td></tr>
                    <tr><td class="label">Timestamp</td><td>{incident.created_at.strftime('%Y-%m-%d %H:%M:%S UTC')}</td></tr>
                </table>
                {image_cid_html}
            </div>
            <div class="footer">
                Automated AI Proctoring System &bull; Tamper-evident Audit Trail
            </div>
        </div>
    </body>
    </html>
    """

    img_path = incident.snapshot.path if incident.snapshot else None

    # Spawn thread to guarantee non-blocking execution
    worker_thread = threading.Thread(
        target=_send_email_worker,
        args=(subject, text_content, html_content, [recipient_email], img_path),
        daemon=True
    )
    worker_thread.start()
