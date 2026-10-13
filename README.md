# Automated AI-Powered Exam Monitoring & Proctoring System

An edge-capable, multi-camera, **100% ROI-Free** examination surveillance system built with **Django 5, Channels (WebSockets), MediaPipe 3D Face Mesh, YOLOv8/11, and ByteTrack**.

---

## Key Features

1. **ROI-Free Dynamic Person Tracking**:
   - Eliminates rigid desk polygon ROIs that fail across different classroom orientations and camera angles.
   - Detects seated students dynamically anywhere in the camera feed and maintains persistent IDs across occlusions via **ByteTrack**.

2. **Multi-Modal Academic Dishonesty Detection**:
   - **Head Pose & Eye-Gaze**: Perspective-n-Point (`cv2.solvePnP`) calculates 3D Euler angles (Yaw, Pitch, Roll) and Iris gaze relative to the student's face coordinate frame.
   - **Prohibited Objects**: Dynamic IoU and proximity association for smartphones, paper notes/chits, smartwatches, and earphones.
   - **Skeletal Kinematics**: YOLOv8-Pose detects concealed lap hands and hands near ears/mouth (whispering / earpieces).
   - **Inter-Person Collaboration**: Flags spatial overlap, leaning, and absence/roaming.

3. **Temporal Debounce Engine**:
   - Rolling sliding window buffer ($N=45$ frames, $\tau \ge 0.75$) with 15-second cooldown hysteresis eliminates transient false positives from normal movements (sneezing, blinking, stretching).

4. **Real-Time Proctor Dashboard (Django UI)**:
   - Live video stream with burned-in telemetry HUD.
   - Real-time WebSocket alert feed with synthesized Web Audio API warning chime.
   - Dynamic telemetry cards showing real-time Yaw, Pitch, Roll, and Gaze for each tracked student.
   - Forensic incident review table with tamper-proof SHA-256 signatures and modal snapshot inspection.

5. **Asynchronous SMTP Alert Pipeline**:
   - Non-blocking background email dispatch with CID-embedded forensic snapshot images.

---

## Getting Started

### 1. Virtual Environment
The virtual environment has been created and configured in `.venv`:

```powershell
# Activate the environment in PowerShell:
.\.venv\Scripts\Activate.ps1
```

### 2. Database & Sample Data
The SQLite database has been migrated and seeded with sample data:
- Superuser: `admin` | Password: `adminpassword2026`
- Active Exam Session: *Mid-Term Computer Architecture Examination* (Room 101)
- Default Camera Feed: *Primary Classroom Camera* (Device `0` / Webcam)

### 3. Run the Development Server

Run with Django Channels / Daphne ASGI server:

```powershell
.\.venv\Scripts\python.exe manage.py runserver 127.0.0.1:8000
```

Access the control center:
- **Live Monitoring Dashboard**: [http://127.0.0.1:8000/](http://127.0.0.1:8000/)
- **Forensic Incident Logs**: [http://127.0.0.1:8000/incidents/](http://127.0.0.1:8000/incidents/)
- **Student Roster**: [http://127.0.0.1:8000/students/](http://127.0.0.1:8000/students/)
- **Camera Setup**: [http://127.0.0.1:8000/cameras/](http://127.0.0.1:8000/cameras/)
- **Admin Portal**: [http://127.0.0.1:8000/admin/](http://127.0.0.1:8000/admin/)

---

## Running Unit Tests

Run the test suite to verify the vision algorithms, spatial association, and debounce engine:

```powershell
.\.venv\Scripts\python.exe -m unittest tests/test_vision_modules.py
```
