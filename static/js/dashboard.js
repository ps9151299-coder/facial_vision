// Live Proctoring Dashboard WebSocket & Telemetry Controller

let alertSocket = null;
let reconnectTimer = null;

function connectWebSocket() {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const wsUrl = `${protocol}//${window.location.host}/ws/alerts/`;
  
  const statusIndicator = document.getElementById('ws-status-indicator');
  const statusText = document.getElementById('ws-status-text');

  try {
    alertSocket = new WebSocket(wsUrl);

    alertSocket.onopen = function () {
      console.log("WebSocket connected to Proctor Alert Bus.");
      if (statusIndicator) {
        statusIndicator.style.backgroundColor = "var(--accent-green)";
        statusIndicator.style.boxShadow = "0 0 10px var(--accent-green)";
      }
      if (statusText) statusText.textContent = "CONNECTED (LIVE)";
      if (reconnectTimer) clearTimeout(reconnectTimer);
    };

    alertSocket.onmessage = function (e) {
      const message = JSON.parse(e.data);
      if (message.type === 'violation_alert') {
        handleViolationAlert(message.data);
      } else if (message.type === 'telemetry_update') {
        handleTelemetryUpdate(message.data);
      }
    };

    alertSocket.onclose = function () {
      console.warn("WebSocket closed. Attempting reconnect in 3s...");
      if (statusIndicator) {
        statusIndicator.style.backgroundColor = "var(--accent-orange)";
        statusIndicator.style.boxShadow = "0 0 10px var(--accent-orange)";
      }
      if (statusText) statusText.textContent = "RECONNECTING...";
      reconnectTimer = setTimeout(connectWebSocket, 3000);
    };

    alertSocket.onerror = function (err) {
      console.error("WebSocket error:", err);
      alertSocket.close();
    };
  } catch (err) {
    console.error("Failed to initialize WebSocket:", err);
    reconnectTimer = setTimeout(connectWebSocket, 3000);
  }
}

function handleViolationAlert(alertData) {
  // 1. Play audio chime
  if (typeof playViolationChime === 'function') {
    playViolationChime();
  }

  // 2. Increment counters
  const pendingCountEl = document.getElementById('metric-pending-count');
  if (pendingCountEl) {
    let current = parseInt(pendingCountEl.textContent) || 0;
    pendingCountEl.textContent = current + 1;
  }
  const totalCountEl = document.getElementById('metric-total-count');
  if (totalCountEl) {
    let current = parseInt(totalCountEl.textContent) || 0;
    totalCountEl.textContent = current + 1;
  }

  // 3. Prepend into Live Incident Feed
  const feedList = document.getElementById('incident-feed-list');
  if (feedList) {
    const card = document.createElement('div');
    card.className = 'incident-card';
    card.style.borderLeft = '4px solid var(--accent-red)';
    card.style.animation = 'fadeIn 0.3s ease';

    const snapImg = alertData.snapshot_url
      ? `<img src="${alertData.snapshot_url}" style="width: 100%; border-radius: 6px; margin-top: 6px;" alt="Snapshot" />`
      : '';

    card.innerHTML = `
      <div style="display: flex; justify-content: space-between; align-items: center;">
        <span class="badge badge-red">🚨 ${alertData.violation_label || alertData.violation_type}</span>
        <small style="color: var(--text-secondary);">${alertData.timestamp || 'Just Now'}</small>
      </div>
      <div style="font-size: 0.9rem; font-weight: 600; margin-top: 4px;">
        Track #${alertData.track_id} &bull; Conf: ${alertData.confidence}% &bull; ${alertData.duration}s
      </div>
      ${snapImg}
      <div style="display: flex; gap: 8px; margin-top: 8px;">
        <a href="/incidents/" class="btn btn-outline" style="padding: 4px 8px; font-size: 0.75rem;">Review Incident</a>
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
  fetch(`/api/camera/${action}/`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ camera_id: 'default', source: '0' })
  })
  .then(res => res.json())
  .then(data => {
    console.log(`Stream ${action}:`, data);
    const feedImg = document.getElementById('live-stream-img');
    if (feedImg && action === 'start') {
      feedImg.src = `/stream/default/?t=${Date.now()}`;
    }
  })
  .catch(err => console.error(`Error toggling stream:`, err));
}

document.addEventListener('DOMContentLoaded', () => {
  connectWebSocket();
});
