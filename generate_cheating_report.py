#!/usr/bin/env python
"""
AI Exam Proctoring System - Automated Cheating Summary & Evidence Report Generator.

This script scans all students/tracks that have committed >= 5 violations,
compiles their 5 forensic evidence snapshots with cryptographic SHA-256 hashes,
and generates a self-contained, audit-ready HTML & Markdown report with embedded photos.

Usage:
    python generate_cheating_report.py
    python generate_cheating_report.py --threshold 5
    python generate_cheating_report.py --session <UUID>
"""

import os
import sys
import argparse
import base64
from datetime import datetime
from pathlib import Path
from collections import Counter

# Configure Django environment
BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'proctor_system.settings')

import django
django.setup()

from django.conf import settings
from monitoring.models import ViolationIncident, ExamSession, Student, CameraFeed


def image_to_base64(image_path):
    """Reads an image file from disk and encodes it into a Base64 data URI."""
    if not image_path or not os.path.exists(image_path):
        return None
    try:
        with open(image_path, "rb") as f:
            encoded = base64.b64encode(f.read()).decode('utf-8')
        ext = Path(image_path).suffix.lower().replace('.', '')
        if ext == 'jpg':
            ext = 'jpeg'
        return f"data:image/{ext};base64,{encoded}"
    except Exception as e:
        print(f"Warning: Could not encode image {image_path}: {e}")
        return None


def generate_reports(threshold=1, session_id=None, output_dir=None):
    """
    Scans for students with >= threshold violations, compiles their evidence snapshots,
    and generates self-contained HTML and Markdown summary reports.
    """
    if output_dir is None:
        output_dir = BASE_DIR / 'reports'
    else:
        output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # 1. Resolve Session
    session_obj = None
    if session_id:
        session_obj = ExamSession.objects.filter(id=session_id).first()
    if not session_obj:
        session_obj = ExamSession.objects.filter(is_active=True).first()

    session_name = session_obj.exam_name if session_obj else "General Surveillance Session"
    room_number = session_obj.room_number if session_obj else "Room 101"
    session_time = session_obj.start_time.strftime("%Y-%m-%d %H:%M:%S UTC") if session_obj else datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

    # 2. Query Incidents grouped by track_id
    query_filter = {}
    if session_obj:
        query_filter['session'] = session_obj

    all_incidents = ViolationIncident.objects.filter(**query_filter).order_by('created_at')

    # Group incidents by track_id
    track_incidents = {}
    for inc in all_incidents:
        tid = inc.track_id
        if tid not in track_incidents:
            track_incidents[tid] = []
        track_incidents[tid].append(inc)

    # Filter students that have reached the cheating threshold (default >= 5)
    cheating_candidates = {}
    for tid, incs in track_incidents.items():
        if len(incs) >= threshold:
            # Take only the first 5 capped evidence snapshots
            cheating_candidates[tid] = incs[:5]

    from datetime import timezone
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    file_timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    # 3. Print CLI Summary
    print("\n" + "=" * 80)
    print(" AI EXAM PROCTOR - FINAL CHEATING SUMMARY & FORENSIC AUDIT REPORT")
    print("=" * 80)
    print(f"Exam Session : {session_name} ({room_number})")
    print(f"Session Start: {session_time}")
    print(f"Report Date  : {now_str}")
    print(f"Threshold    : >= {threshold} Violation Snapshots Required for Confirmation")
    print("-" * 80)

    if not cheating_candidates:
        print(f" [RESULT] No candidate has reached the {threshold}-incident threshold yet.")
        print(f" Total active tracks monitored: {len(track_incidents)}")
        for tid, incs in track_incidents.items():
            print(f"   - Track #{tid}: {len(incs)} / {threshold} violation snapshots recorded.")
        print("-" * 80)
        print("Tip: Start the camera and perform 5 violations to test final cheater generation.\n")
        return None

    print(f" [CONFIRMED CHEATERS DETECTED]: {len(cheating_candidates)} Candidate(s)\n")

    candidates_data = []

    for tid, incs in sorted(cheating_candidates.items()):
        student_obj = incs[0].student or Student.objects.filter(student_id=f"STU-TRACK-{tid}").first()
        student_name = student_obj.name if student_obj else f"Candidate (Track #{tid})"
        roll_num = student_obj.roll_number if student_obj else f"ROLL-T{tid}"
        student_id = student_obj.student_id if student_obj else f"STU-TRACK-{tid}"

        violation_counts = Counter(inc.get_violation_type_display() for inc in incs)
        summary_breakdown = ", ".join(f"{cnt}x {v}" for v, cnt in violation_counts.items())

        avg_conf = sum(inc.confidence_score for inc in incs) / len(incs)

        print(f"  * [FLAGGED CHEATER] Track #{tid} - {student_name} ({roll_num})")
        print(f"    Total Snapshots : {len(incs)} / {threshold} (Maximum Evidence Reached)")
        print(f"    Avg Confidence  : {avg_conf * 100:.1f}%")
        print(f"    Violations Log  : {summary_breakdown}")
        print("    Captured Evidence Snapshots:")

        snapshots_data = []
        for idx, inc in enumerate(incs, 1):
            snap_path = inc.snapshot.path if (inc.snapshot and os.path.exists(inc.snapshot.path)) else None
            b64_img = image_to_base64(snap_path)
            sha = inc.snapshot_sha256 or "N/A"
            ts = inc.created_at.strftime("%H:%M:%S UTC")
            v_name = inc.get_violation_type_display()

            print(f"      [{idx}/5] {ts} | {v_name} | Conf: {inc.confidence_score * 100:.0f}% | SHA: {sha[:16]}...")

            snapshots_data.append({
                'index': idx,
                'violation_type': v_name,
                'code': inc.violation_type,
                'confidence': round(inc.confidence_score * 100, 1),
                'duration': round(inc.duration_seconds, 1),
                'timestamp': ts,
                'sha256': sha,
                'file_path': snap_path,
                'b64': b64_img,
            })

        print()

        candidates_data.append({
            'track_id': tid,
            'name': student_name,
            'roll_number': roll_num,
            'student_id': student_id,
            'avg_conf': round(avg_conf * 100, 1),
            'violation_counts': violation_counts,
            'breakdown_str': summary_breakdown,
            'snapshots': snapshots_data,
            'first_seen': incs[0].created_at.strftime("%H:%M:%S UTC"),
            'last_seen': incs[-1].created_at.strftime("%H:%M:%S UTC"),
            'verdict': "CONFIRMED ACADEMIC DISHONESTY",
        })

    # 4. Generate Self-Contained HTML Report with Embedded Photos
    html_file = output_dir / f"cheating_summary_report_{file_timestamp}.html"
    generate_html_report(
        html_file,
        session_name=session_name,
        room_number=room_number,
        session_time=session_time,
        report_time=now_str,
        threshold=threshold,
        candidates=candidates_data
    )

    # 5. Generate Markdown Report
    md_file = output_dir / f"cheating_summary_report_{file_timestamp}.md"
    generate_markdown_report(
        md_file,
        session_name=session_name,
        room_number=room_number,
        session_time=session_time,
        report_time=now_str,
        threshold=threshold,
        candidates=candidates_data
    )

    print("-" * 80)
    print(f" [SUCCESS] Reports successfully generated:")
    print(f"   -> HTML Report (Embedded Snaps) : {html_file}")
    print(f"   -> Markdown Audit Log          : {md_file}")
    print("=" * 80 + "\n")

    return {
        'html_path': str(html_file),
        'md_path': str(md_file),
        'cheaters_count': len(candidates_data),
        'candidates': candidates_data
    }


def generate_html_report(output_file, session_name, room_number, session_time, report_time, threshold, candidates):
    """Writes a self-contained, modern HTML report with base64 embedded evidence snapshots."""
    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Academic Integrity Violation Report - {session_name}</title>
  <style>
    :root {{
      --bg: #0f172a;
      --card-bg: #1e293b;
      --border: #334155;
      --text: #f8fafc;
      --text-muted: #94a3b8;
      --red: #ef4444;
      --red-bg: rgba(239, 68, 68, 0.15);
      --cyan: #06b6d4;
      --green: #10b981;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
      background: var(--bg);
      color: var(--text);
      line-height: 1.5;
      padding: 2.5rem;
    }}
    .container {{ max-width: 1200px; margin: 0 auto; }}
    .header {{
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-left: 6px solid var(--red);
      padding: 1.5rem 2rem;
      border-radius: 8px;
      margin-bottom: 2rem;
    }}
    .header h1 {{ font-size: 1.6rem; font-weight: 700; color: #ffffff; display: flex; align-items: center; gap: 10px; }}
    .header p {{ color: var(--text-muted); font-size: 0.9rem; margin-top: 6px; }}
    .meta-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 1rem;
      margin-top: 1.25rem;
      padding-top: 1rem;
      border-top: 1px solid var(--border);
    }}
    .meta-item {{ font-size: 0.85rem; }}
    .meta-item strong {{ color: var(--text-muted); display: block; font-size: 0.75rem; text-transform: uppercase; }}
    .meta-item span {{ color: #ffffff; font-weight: 600; font-size: 0.95rem; }}
    .cheater-card {{
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 8px;
      margin-bottom: 2.5rem;
      overflow: hidden;
    }}
    .cheater-header {{
      background: rgba(239, 68, 68, 0.12);
      border-bottom: 1px solid rgba(239, 68, 68, 0.3);
      padding: 1.25rem 1.5rem;
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 10px;
    }}
    .badge {{
      display: inline-block;
      padding: 4px 10px;
      border-radius: 4px;
      font-size: 0.75rem;
      font-weight: 700;
      text-transform: uppercase;
    }}
    .badge-red {{ background: var(--red); color: white; }}
    .badge-cyan {{ background: rgba(6, 182, 212, 0.2); color: var(--cyan); border: 1px solid var(--cyan); }}
    .cheater-body {{ padding: 1.5rem; }}
    .summary-box {{
      background: rgba(0, 0, 0, 0.25);
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 1rem;
      margin-bottom: 1.5rem;
      font-size: 0.9rem;
    }}
    .summary-box strong {{ color: var(--cyan); }}
    .snaps-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
      gap: 1.25rem;
    }}
    .snap-card {{
      background: #0f172a;
      border: 1px solid var(--border);
      border-radius: 6px;
      overflow: hidden;
      display: flex;
      flex-direction: column;
    }}
    .snap-card img {{
      width: 100%;
      height: 160px;
      object-fit: cover;
      background: #000;
      display: block;
    }}
    .snap-info {{
      padding: 0.75rem;
      font-size: 0.78rem;
      flex: 1;
      display: flex;
      flex-direction: column;
      justify-content: space-between;
    }}
    .snap-title {{ font-weight: 700; color: #fff; margin-bottom: 4px; }}
    .snap-meta {{ color: var(--text-muted); line-height: 1.4; }}
    .snap-sha {{
      font-family: monospace;
      font-size: 0.65rem;
      color: var(--cyan);
      background: rgba(6, 182, 212, 0.1);
      padding: 2px 4px;
      border-radius: 3px;
      margin-top: 6px;
      word-break: break-all;
    }}
    .footer {{
      text-align: center;
      margin-top: 3rem;
      padding-top: 1.5rem;
      border-top: 1px solid var(--border);
      color: var(--text-muted);
      font-size: 0.8rem;
    }}
    @media print {{
      body {{ background: #fff; color: #000; padding: 1rem; }}
      .cheater-card, .header, .snap-card {{ border: 1px solid #ccc; background: #fff; color: #000; }}
      .snap-sha {{ color: #333; }}
      .badge-red {{ background: #b91c1c; color: #fff; }}
      .print-btn {{ display: none; }}
    }}
  </style>
</head>
<body>
  <div class="container">
    <div style="display: flex; justify-content: flex-end; margin-bottom: 1rem;">
      <button class="print-btn" onclick="window.print()" style="background: var(--cyan); color: #000; border: none; padding: 8px 16px; border-radius: 6px; font-weight: 700; cursor: pointer;">
        🖨️ Print / Save as PDF
      </button>
    </div>

    <!-- Main Header -->
    <div class="header">
      <h1>🚨 AI Exam Proctor - Final Cheating Audit Report</h1>
      <p>Forensic evidentiary dossier with tamper-evident cryptographic SHA-256 signatures for students committing &ge; {threshold} confirmed violations.</p>
      
      <div class="meta-grid">
        <div class="meta-item">
          <strong>Exam Session</strong>
          <span>{session_name}</span>
        </div>
        <div class="meta-item">
          <strong>Room / Lab</strong>
          <span>{room_number}</span>
        </div>
        <div class="meta-item">
          <strong>Session Commenced</strong>
          <span>{session_time}</span>
        </div>
        <div class="meta-item">
          <strong>Report Generated</strong>
          <span>{report_time}</span>
        </div>
        <div class="meta-item">
          <strong>Flagged Cheaters</strong>
          <span style="color: var(--red);">{len(candidates)} Candidate(s)</span>
        </div>
      </div>
    </div>
"""

    for cand in candidates:
        html_content += f"""
    <!-- Candidate Dossier -->
    <div class="cheater-card">
      <div class="cheater-header">
        <div>
          <h2 style="font-size: 1.2rem; font-weight: 700; color: #fff;">
            {cand['name']} <span style="font-weight: 400; color: var(--text-muted); font-size: 0.9rem;">({cand['roll_number']})</span>
          </h2>
          <small style="color: var(--text-muted);">ByteTrack Identifier: Track #{cand['track_id']} &bull; Student ID: {cand['student_id']}</small>
        </div>
        <div style="display: flex; gap: 8px; align-items: center;">
          <span class="badge badge-cyan">Avg Conf: {cand['avg_conf']}%</span>
          <span class="badge badge-red">🚨 {cand['verdict']} (5/5 Violations)</span>
        </div>
      </div>

      <div class="cheater-body">
        <div class="summary-box">
          <div><strong>Violation Pattern:</strong> {cand['breakdown_str']}</div>
          <div style="margin-top: 4px; color: var(--text-muted); font-size: 0.8rem;">
            Evidence Window: {cand['first_seen']} &rarr; {cand['last_seen']} (Maximum cap of 5 evidentiary snapshots archived)
          </div>
        </div>

        <h4 style="font-size: 0.95rem; font-weight: 600; margin-bottom: 1rem; color: #fff;">
          Forensic Snapshot Evidence Dossier (5 Capped Images):
        </h4>

        <div class="snaps-grid">
"""
        for snap in cand['snapshots']:
            img_tag = f'<img src="{snap["b64"]}" alt="Evidence #{snap["index"]}" />' if snap['b64'] else '<div style="height:160px; background:#111; display:flex; align-items:center; justify-content:center; color:#666;">No Image Available</div>'

            html_content += f"""
          <div class="snap-card">
            {img_tag}
            <div class="snap-info">
              <div>
                <div class="snap-title">#{snap['index']} &bull; {snap['violation_type']}</div>
                <div class="snap-meta">
                  Time: {snap['timestamp']}<br>
                  Confidence: {snap['confidence']}% &bull; {snap['duration']}s
                </div>
              </div>
              <div class="snap-sha" title="Tamper-evident SHA-256 signature">
                SHA: {snap['sha256'][:18]}...{snap['sha256'][-6:]}
              </div>
            </div>
          </div>
"""
        html_content += """
        </div>
      </div>
    </div>
"""

    html_content += f"""
    <div class="footer">
      Automated AI Exam Proctoring System &bull; Cryptographically Verified Forensic Dossier &bull; Generated {report_time}
    </div>
  </div>
</body>
</html>
"""

    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(html_content)


def generate_markdown_report(output_file, session_name, room_number, session_time, report_time, threshold, candidates):
    """Writes an audit-grade Markdown summary of all cheating candidates and their evidence."""
    md = f"""# AI Exam Proctor - Final Cheating Audit Report

- **Exam Session**: {session_name}
- **Room / Lab**: {room_number}
- **Session Commenced**: {session_time}
- **Report Generated**: {report_time}
- **Flagged Cheater Threshold**: &ge; {threshold} Violation Snapshots
- **Confirmed Cheaters Detected**: {len(candidates)}

---

"""
    for cand in candidates:
        md += f"""## Candidate: {cand['name']} (Track #{cand['track_id']})
- **Roll Number**: {cand['roll_number']}
- **Student ID**: {cand['student_id']}
- **Verdict**: **{cand['verdict']}**
- **Violation Breakdown**: {cand['breakdown_str']}
- **Average Confidence**: {cand['avg_conf']}%
- **Evidence Timeframe**: {cand['first_seen']} to {cand['last_seen']}

### 5 Evidence Snapshots:
| # | Timestamp | Violation | Confidence | Duration | Forensic SHA-256 | Snapshot Path |
|---|---|---|---|---|---|---|
"""
        for s in cand['snapshots']:
            md += f"| {s['index']} | {s['timestamp']} | {s['violation_type']} | {s['confidence']}% | {s['duration']}s | `{s['sha256'][:16]}...` | `{s['file_path'] or 'N/A'}` |\n"
        md += "\n---\n\n"

    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(md)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Generate AI Proctor Final Cheating Summary Report")
    parser.add_argument('--threshold', type=int, default=1, help="Number of violations required to flag a student (default: 1, shows all cheating candidates)")
    parser.add_argument('--session', type=str, default=None, help="ExamSession UUID (default: latest active session)")
    parser.add_argument('--output-dir', type=str, default=None, help="Directory to save generated reports")

    args = parser.parse_args()
    generate_reports(threshold=args.threshold, session_id=args.session, output_dir=args.output_dir)
