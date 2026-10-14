// Live Proctoring Dashboard WebSocket & Telemetry Controller
// Features: Dual-channel sync (WebSocket + high-frequency HTTP polling fallback)

let alertSocket = null;
let reconnectTimer = null;
let lastPolledTs = 0;
let pollTimer = null;
const seenIncidentIds = new Set();

function connectWebSocket() {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const wsUrl = `${protocol}//${window.location.host}/ws/alerts/`;

  try {
    alertSocket = new WebSocket(wsUrl);

    alertSocket.onopen = function () {
      console.log("WebSocket connected to Proctor Alert Bus.");
      setLiveStatus(true);
      if (reconnectTimer) clearTimeout(reconnectTimer);
    };

    alertSocket.onmessage = function (e) {
      try {
        const message = JSON.parse(e.data);
        if (message.type === 'violation_alert') {
          handleViolationAlert(message.data);
        } else if (message.type === 'telemetry_update') {
          handleTelemetryUpdate(message.data);
        }
      } catch (err) {
        console.warn("Error parsing WebSocket message:", err);
      }
    };

    alertSocket.onclose = function () {
      // If WebSocket closed (e.g. running standard WSGI runserver), polling keeps everything 100% alive
      reconnectTimer = setTimeout(connectWebSocket, 10000);
    };

    alertSocket.onerror = function (err) {
      // Graceful error handling - HTTP poller handles live updates
      if (alertSocket) alertSocket.close();
    };
  } catch (err) {
    reconnectTimer = setTimeout(connectWebSocket, 10000);
  }
}

function setLiveStatus(isLive) {
  const statusIndicator = document.getElementById('ws-status-indicator');
  const statusText = document.getElementById('ws-status-text');
  if (statusIndicator) {
    statusIndicator.style.backgroundColor = isLive ? "var(--accent-green)" : "var(--accent-orange)";
    statusIndicator.style.boxShadow = isLive ? "0 0 10px var(--accent-green)" : "0 0 10px var(--accent-orange)";
  }
  if (statusText) {
    statusText.textContent = isLive ? "CONNECTED (LIVE)" : "INITIALIZING...";
    statusText.style.color = isLive ? "var(--accent-green)" : "var(--accent-orange)";
  }
}

function startLivePolling() {
  // Mark existing rendered incidents as seen
  document.querySelectorAll('#incident-feed-list .incident-card').forEach(el => {
    if (el.dataset && el.dataset.incidentId) {
      seenIncidentIds.add(el.dataset.incidentId);
    }
  });

  // Initial sync to grab server timestamp
  pollIncidents(true);

  if (pollTimer) clearInterval(pollTimer);
  // Poll every 1.5 seconds for instant real-time snapshot display
  pollTimer = setInterval(() => {
    pollIncidents(false);
  }, 1500);
}

function pollIncidents(isInit = false) {
  const url = `/api/incidents/poll/?after=${encodeURIComponent(lastPolledTs)}`;
  fetch(url)
    .then(res => res.json())
    .then(data => {
      if (!data.success) return;

      setLiveStatus(true);

      // 1. Sync live metric counters
      if (typeof data.pending_reviews !== 'undefined') {
        const el = document.getElementById('metric-pending-count');
        if (el) el.textContent = data.pending_reviews;
      }
      if (typeof data.total_incidents !== 'undefined') {
        const el = document.getElementById('metric-total-count');
        if (el) el.textContent = data.total_incidents;
      }

      // If initial handshake, record server timestamp so only future incidents fire
      if (isInit || lastPolledTs === 0) {
        lastPolledTs = data.server_ts;
        return;
      }

      lastPolledTs = data.server_ts;

      // 2. Process real-time incidents
      if (Array.isArray(data.incidents) && data.incidents.length > 0) {
        data.incidents.forEach(inc => {
          handleViolationAlert(inc);
        });
      }

      // 3. Process active student posture telemetries
      if (Array.isArray(data.students)) {
        handleTelemetryUpdate(data.students);
      }
    })
    .catch(err => {
      // Network hiccup - poller will retry automatically
    });
}

function handleViolationAlert(alertData) {
  const incId = alertData.id || alertData.incident_id || String(Date.now());
  if (seenIncidentIds.has(incId)) {
    return;
  }
  seenIncidentIds.add(incId);

  // 1. Play audio chime
  if (typeof playViolationChime === 'function') {
    playViolationChime();
  }

  // 2. Remove "No active violations" placeholder
  const emptyPlaceholder = document.getElementById('no-incidents-msg');
  if (emptyPlaceholder) {
    emptyPlaceholder.remove();
  }

  // 3. Prepend into Live Incident Feed
  const feedList = document.getElementById('incident-feed-list');
  if (feedList) {
    const card = document.createElement('div');
    card.id = `feed-incident-${incId}`;
    card.dataset.incidentId = incId;
    card.className = 'incident-card';
    card.style.borderLeft = '4px solid var(--accent-red)';
    card.style.animation = 'fadeIn 0.3s ease';

    const snapImg = alertData.snapshot_url
      ? `<div style="margin-top: 6px;">
           <img src="${alertData.snapshot_url}" 
                style="width: 100%; border-radius: 6px; cursor: pointer; border: 1px solid rgba(255,255,255,0.1);" 
                onclick="openEvidenceModal('${alertData.snapshot_url}', '${alertData.violation_label || alertData.violation_type}', '${alertData.track_id}', '${alertData.snapshot_sha256 || ''}')" 
                alt="Incident Snapshot" />
         </div>`
      : '';

    card.innerHTML = `
      <div style="display: flex; justify-content: space-between; align-items: center;">
        <span class="badge badge-red">🚨 ${alertData.violation_label || alertData.violation_type}</span>
        <small style="color: var(--text-secondary);">${alertData.created_at_str || alertData.timestamp || 'Just Now'}</small>
      </div>
      <div style="font-size: 0.9rem; font-weight: 600; margin-top: 4px;">
        Track #${alertData.track_id} &bull; Conf: ${alertData.confidence}% &bull; ${alertData.duration}s
      </div>
      ${snapImg}
      <div style="display: flex; justify-content: space-between; align-items: center; margin-top: 6px;">
        <span class="badge badge-orange" id="status-badge-${incId}" style="font-size: 0.65rem;">
          ${alertData.review_status || 'PENDING'}
        </span>
        <a href="/incidents/" class="btn btn-outline" style="padding: 2px 8px; font-size: 0.7rem;">Review</a>
      </div>
    `;

    feedList.insertBefore(card, feedList.firstChild);
  }

  // 4. Highlight student card
  const studentCard = document.getElementById(`student-card-${alertData.track_id}`);
  if (studentCard) {
    studentCard.className = 'student-card alert';
    const flagEl = studentCard.querySelector('.student-flag');
    if (flagEl) {
      flagEl.textContent = `ALERT: ${alertData.violation_label || alertData.violation_type}`;
      flagEl.style.color = 'var(--accent-red)';
    }
  }
}

function handleTelemetryUpdate(studentsList) {
  const container = document.getElementById('students-cards-container');
  if (!container || !Array.isArray(studentsList)) return;

  if (studentsList.length > 0) {
    const placeholder = document.getElementById('student-card-placeholder');
    if (placeholder) placeholder.remove();
  }

  studentsList.forEach(s => {
    let card = document.getElementById(`student-card-${s.track_id}`);
    const isSuspicious = Math.abs(s.yaw) > 25 || s.pitch < -20;
    const cardStatusClass = isSuspicious ? 'suspicious' : 'normal';

    if (!card) {
      card = document.createElement('div');
      card.id = `student-card-${s.track_id}`;
      card.className = `student-card ${cardStatusClass}`;
      container.appendChild(card);
    } else if (!card.classList.contains('alert')) {
      card.className = `student-card ${cardStatusClass}`;
    }

    card.innerHTML = `
      <div style="display: flex; justify-content: space-between; align-items: center;">
        <span style="font-weight: 700; font-size: 0.85rem;">Track #${s.track_id}</span>
        <span class="badge ${isSuspicious ? 'badge-orange' : 'badge-green'}" style="font-size: 0.65rem;">
          ${isSuspicious ? 'FLAGGED' : 'NORMAL'}
        </span>
      </div>
      <div style="font-size: 0.75rem; color: var(--text-secondary); margin-top: 6px; line-height: 1.4;">
        <div>Yaw: <strong style="color: ${Math.abs(s.yaw) > 25 ? 'var(--accent-orange)' : '#ffffff'};">${s.yaw}°</strong></div>
        <div>Pitch: <strong style="color: ${s.pitch < -20 ? 'var(--accent-orange)' : '#ffffff'};">${s.pitch}°</strong></div>
        <div>Gaze: <strong>${s.gaze}</strong></div>
      </div>
      <div class="student-flag" style="font-size: 0.7rem; font-weight: 600; margin-top: 4px; color: ${isSuspicious ? 'var(--accent-orange)' : 'var(--text-secondary)'};">
        ${isSuspicious ? 'Unusual head rotation' : 'Posture Nominal'}
      </div>
    `;
  });
}

// Camera stream controls
function toggleStream(action) {
  const feedImg = document.getElementById('live-stream-img');
  const placeholder = document.getElementById('stream-placeholder');

  if (action === 'start') {
    if (placeholder) {
      placeholder.style.display = 'flex';
      placeholder.innerHTML = `
        <div style="font-size:2rem;">⏳</div>
        <div style="color:#06b6d4; font-size:0.95rem; font-weight:600;">Starting Camera...</div>
        <div style="color:#555; font-size:0.8rem;">Initializing Video Stream</div>
      `;
    }
    if (feedImg) {
      feedImg.style.display = 'none';
      feedImg.src = '';
    }
  }

  const camSelect = document.getElementById('camera-select');
  const selectedSource = camSelect ? camSelect.value : '0';
  const selectedOption = camSelect ? camSelect.options[camSelect.selectedIndex] : null;
  const selectedCamId = (selectedOption && selectedOption.dataset.camId) ? selectedOption.dataset.camId : 'default';

  fetch(`/api/camera/${action}/`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ camera_id: selectedCamId, source: selectedSource })
  })
  .then(res => res.json())
  .then(data => {
    console.log(`Stream ${action} response:`, data);

    if (action === 'start' && data.success) {
      if (!feedImg) return;

      const streamUrl = `/stream/${selectedCamId}/?t=${Date.now()}`;

      // Immediately display image and hide placeholder
      feedImg.style.display = 'block';
      if (placeholder) {
        placeholder.style.display = 'none';
      }

      feedImg.onerror = function() {
        console.warn("Stream connection interrupted or failed to load.");
        if (feedImg.src && feedImg.src.includes('/stream/')) {
          feedImg.style.display = 'none';
          if (placeholder) {
            placeholder.style.display = 'flex';
            placeholder.innerHTML = `
              <div style="font-size:2rem;">⚠️</div>
              <div style="color:#ef4444; font-size:0.95rem; font-weight:600;">Stream Connection Lost</div>
              <div style="color:#555; font-size:0.8rem;">Check camera source & click Start Camera again</div>
            `;
          }
        }
      };

      // Set src to start the MJPEG HTTP stream
      feedImg.src = streamUrl;

    } else if (action === 'stop') {
      if (feedImg) {
        feedImg.onerror = null;
        feedImg.onload = null;
        feedImg.src = '';
        feedImg.style.display = 'none';
      }
      if (placeholder) {
        placeholder.style.display = 'flex';
        placeholder.innerHTML = `
          <div style="font-size:3rem;">📷</div>
          <div style="color:#888; font-size:0.95rem; font-weight:600;">Camera Offline</div>
          <div style="color:#555; font-size:0.8rem;">Click <strong style="color:#06b6d4;">Start Camera</strong> to begin live stream</div>
        `;
      }
    } else if (!data.success) {
      if (placeholder) {
        placeholder.style.display = 'flex';
        placeholder.innerHTML = `
          <div style="font-size:2rem;">❌</div>
          <div style="color:#ef4444; font-size:0.95rem; font-weight:600;">Failed to Start Camera</div>
          <div style="color:#555; font-size:0.8rem;">${data.error || 'Server returned failure'}</div>
        `;
      }
    }
  })
  .catch(err => {
    console.error(`Error toggling stream:`, err);
    if (placeholder) {
      placeholder.style.display = 'flex';
      placeholder.innerHTML = `
        <div style="font-size:2rem;">❌</div>
        <div style="color:#ef4444; font-size:0.95rem; font-weight:600;">Connection Error</div>
        <div style="color:#555; font-size:0.8rem;">Django server unreachable</div>
      `;
    }
  });
}

document.addEventListener('DOMContentLoaded', () => {
  connectWebSocket();
  startLivePolling();
});
