// Incidents Review & Evidence Modal Controller

function openEvidenceModal(imgSrc, title, trackId, sha256) {
  const modal = document.getElementById('evidence-modal');
  const modalImg = document.getElementById('modal-img');
  const modalTitle = document.getElementById('modal-title');
  const modalSha = document.getElementById('modal-sha256');

  if (modal && modalImg) {
    modalImg.src = imgSrc;
    if (modalTitle) modalTitle.textContent = `${title} (Track #${trackId})`;
    if (modalSha) modalSha.textContent = `SHA-256 Hash: ${sha256}`;
    modal.classList.add('active');
  }
}

function closeEvidenceModal() {
  const modal = document.getElementById('evidence-modal');
  if (modal) {
    modal.classList.remove('active');
  }
}

function reviewIncident(incidentId, status) {
  const csrfToken = document.querySelector('[name=csrfmiddlewaretoken]')?.value || '';
  const formData = new FormData();
  formData.append('status', status);
  formData.append('notes', document.getElementById(`notes-${incidentId}`)?.value || '');

  fetch(`/incidents/${incidentId}/review/`, {
    method: 'POST',
    headers: {
      'X-CSRFToken': csrfToken
    },
    body: formData
  })
  .then(res => res.json())
  .then(data => {
    if (data.success) {
      const badge = document.getElementById(`status-badge-${incidentId}`);
      if (badge) {
        if (status === 'CONFIRMED') {
          badge.className = 'badge badge-red';
          badge.textContent = 'CONFIRMED';
        } else if (status === 'DISMISSED') {
          badge.className = 'badge badge-green';
          badge.textContent = 'DISMISSED';
        }
      }
    } else {
      alert('Error updating review status: ' + (data.error || 'Unknown error'));
    }
  })
  .catch(err => console.error('Error submitting review:', err));
}
