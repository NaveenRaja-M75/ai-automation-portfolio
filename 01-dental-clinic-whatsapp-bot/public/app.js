// ============================================
// DENTAL CLINIC ADMIN DASHBOARD — app.js
// Step 4: Connected to Supabase via admin API
// ============================================

(function () {
  'use strict';

  // ---------- Constants ----------
  const SESSION_KEY = 'dental_admin_session';
  const SESSION_DURATION_MS = 24 * 60 * 60 * 1000; // 24 hours
  const AUTO_REFRESH_INTERVAL_MS = 60 * 1000; // 60 seconds
  const API_BASE = '/api/admin';

  // Treatment icon map (backend doesn't store icons)
  const TREATMENT_ICONS = {
    'General checkup & cleaning': '🪥',
    'Tooth filling': '🔧',
    'Root canal treatment': '⚙️',
    'Tooth extraction': '🔩',
    'Braces / alignment': '😁',
    'Teeth whitening': '✨'
  };

  // ---------- DOM References ----------
  const loginPage = document.getElementById('login-page');
  const dashboard = document.getElementById('dashboard');
  const loginForm = document.getElementById('login-form');
  const loginError = document.getElementById('login-error');
  const passwordInput = document.getElementById('password-input');
  const loginBtn = document.getElementById('login-btn');
  const btnLogout = document.getElementById('btn-logout');
  const clinicNameEl = document.getElementById('clinic-name');
  const headerDateEl = document.getElementById('header-date');
  const statToday = document.getElementById('stat-today');
  const statMonth = document.getElementById('stat-month');
  const statNoshow = document.getElementById('stat-noshow');
  const tabButtons = document.querySelectorAll('.tab-btn');
  const btnExport = document.getElementById('btn-export');
  const btnRefresh = document.getElementById('btn-manual-refresh');
  const appointmentsBody = document.getElementById('appointments-body');
  const emptyState = document.getElementById('empty-state');
  const tableCount = document.getElementById('table-count');
  const lastUpdated = document.getElementById('last-updated');
  const modalOverlay = document.getElementById('status-modal');
  const modalTitle = document.getElementById('modal-title');
  const modalMessage = document.getElementById('modal-message');
  const modalCancel = document.getElementById('modal-cancel');
  const modalConfirm = document.getElementById('modal-confirm');
  const toastContainer = document.getElementById('toast-container');

  // ---------- State ----------
  let currentTab = 'today';
  let appointments = [];
  let refreshTimer = null;
  let pendingStatusChange = null; // { id, newStatus }

  // ---------- API Helper ----------
  /**
   * Make an authenticated API call to the admin backend
   * Automatically includes the session token in the Authorization header
   * Handles 401 (expired session) by redirecting to login
   */
  async function apiCall(method, path, body = null) {
    const session = getSession();
    const headers = { 'Content-Type': 'application/json' };

    if (session) {
      headers['Authorization'] = 'Bearer ' + session.token;
    }

    const options = { method, headers };
    if (body) {
      options.body = JSON.stringify(body);
    }

    const response = await fetch(API_BASE + path, options);

    // Session expired — redirect to login
    if (response.status === 401) {
      destroySession();
      showLogin();
      throw new Error('Session expired. Please sign in again.');
    }

    if (!response.ok) {
      const err = await response.json().catch(() => ({}));
      throw new Error(err.error || 'API error: ' + response.status);
    }

    return response.json();
  }

  // ---------- Utility Functions ----------
  function formatDate(date) {
    const y = date.getFullYear();
    const m = String(date.getMonth() + 1).padStart(2, '0');
    const d = String(date.getDate()).padStart(2, '0');
    return y + '-' + m + '-' + d;
  }

  function displayDate(dateStr) {
    const date = new Date(dateStr + 'T00:00:00');
    const options = { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' };
    return date.toLocaleDateString('en-IN', options);
  }

  function getToday() {
    return formatDate(new Date());
  }

  function isToday(dateStr) {
    return dateStr === getToday();
  }

  function formatHeaderDate() {
    const options = { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' };
    return new Date().toLocaleDateString('en-IN', options);
  }

  function formatTimestamp() {
    return new Date().toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' });
  }

  function compareTimes(a, b) {
    return convertTo24(a) - convertTo24(b);
  }

  function convertTo24(timeStr) {
    const match = timeStr.match(/(\d+):(\d+)\s*(AM|PM)/i);
    if (!match) return 0;
    let hours = parseInt(match[1]);
    const mins = parseInt(match[2]);
    const period = match[3].toUpperCase();
    if (period === 'PM' && hours !== 12) hours += 12;
    if (period === 'AM' && hours === 12) hours = 0;
    return hours * 60 + mins;
  }

  function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  }

  // ---------- Session Management ----------
  function getSession() {
    try {
      const raw = localStorage.getItem(SESSION_KEY);
      if (!raw) return null;
      const session = JSON.parse(raw);
      // Client-side expiry check (server is the real authority)
      if (Date.now() - session.timestamp > SESSION_DURATION_MS) {
        localStorage.removeItem(SESSION_KEY);
        return null;
      }
      return session;
    } catch {
      localStorage.removeItem(SESSION_KEY);
      return null;
    }
  }

  function createSession(token) {
    const session = { token: token, timestamp: Date.now() };
    localStorage.setItem(SESSION_KEY, JSON.stringify(session));
  }

  function destroySession() {
    localStorage.removeItem(SESSION_KEY);
  }

  // ---------- Login / Logout ----------
  async function handleLogin(e) {
    e.preventDefault();
    const password = passwordInput.value.trim();
    if (!password) return;

    loginBtn.disabled = true;
    loginBtn.textContent = 'Signing in...';

    try {
      const result = await apiCall('POST', '/login', { password: password });
      loginError.classList.remove('visible');
      createSession(result.token);
      showDashboard();
    } catch (err) {
      loginError.textContent = err.message || 'Incorrect password. Please try again.';
      loginError.classList.add('visible');
      passwordInput.value = '';
      passwordInput.focus();
    } finally {
      loginBtn.disabled = false;
      loginBtn.textContent = 'Sign In';
    }
  }

  async function handleLogout() {
    try {
      await apiCall('POST', '/logout');
    } catch {
      // Ignore logout errors — we clear the session regardless
    }
    destroySession();
    stopAutoRefresh();
    showLogin();
  }

  function showLogin() {
    dashboard.classList.remove('active');
    loginPage.style.display = '';
    passwordInput.value = '';
    loginError.classList.remove('visible');
    passwordInput.focus();
  }

  // ---------- Dashboard Initialization ----------
  async function showDashboard() {
    loginPage.style.display = 'none';
    dashboard.classList.add('active');
    headerDateEl.textContent = formatHeaderDate();

    // Load data from API
    await Promise.all([loadAppointments(), loadStats()]);
    startAutoRefresh();
  }

  // ---------- Load Appointments from API ----------
  async function loadAppointments() {
    try {
      const data = await apiCall('GET', '/appointments?filter=' + currentTab);

      // Add treatment icons (backend doesn't store them)
      appointments = data.map(function (appt) {
        appt.treatment_icon = TREATMENT_ICONS[appt.treatment_type] || '🦷';
        return appt;
      });

      renderTable();
      lastUpdated.textContent = 'Last updated: ' + formatTimestamp();
    } catch (err) {
      if (err.message !== 'Session expired. Please sign in again.') {
        showToast('Failed to load appointments', '❌');
        console.error('loadAppointments error:', err);
      }
    }
  }

  // ---------- Load Stats from API ----------
  async function loadStats() {
    try {
      const stats = await apiCall('GET', '/stats');
      animateCounter(statToday, stats.todayCount);
      animateCounter(statMonth, stats.totalBookings);
      animateCounter(statNoshow, stats.noShows);
    } catch (err) {
      // Stats are non-critical — fail silently
      console.error('loadStats error:', err);
    }
  }

  // ---------- Animated Counter ----------
  function animateCounter(element, target) {
    const current = parseInt(element.textContent) || 0;
    if (current === target) return;

    const duration = 400;
    const startTime = performance.now();

    function step(timestamp) {
      var elapsed = timestamp - startTime;
      var progress = Math.min(elapsed / duration, 1);
      var eased = 1 - Math.pow(1 - progress, 3);
      var value = Math.round(current + (target - current) * eased);
      element.textContent = value;
      if (progress < 1) requestAnimationFrame(step);
    }
    requestAnimationFrame(step);
  }

  // ---------- Table Rendering ----------
  function getFilteredAppointments() {
    // Data is already filtered by the API based on currentTab
    // Just sort it for display
    var sorted = appointments.slice();
    if (currentTab === 'today') {
      sorted.sort(function (a, b) { return compareTimes(a.appt_time, b.appt_time); });
    } else if (currentTab === 'upcoming') {
      sorted.sort(function (a, b) {
        return a.appt_date.localeCompare(b.appt_date) || compareTimes(a.appt_time, b.appt_time);
      });
    } else {
      // 'all' — most recent first (already sorted by API), then by time
      sorted.sort(function (a, b) {
        return b.appt_date.localeCompare(a.appt_date) || compareTimes(a.appt_time, b.appt_time);
      });
    }
    return sorted;
  }

  function renderTable() {
    var filtered = getFilteredAppointments();
    appointmentsBody.innerHTML = '';

    if (filtered.length === 0) {
      emptyState.style.display = '';
      tableCount.textContent = 'Showing 0 appointments';
      return;
    }

    emptyState.style.display = 'none';

    filtered.forEach(function (appt, index) {
      var tr = document.createElement('tr');
      tr.setAttribute('data-id', appt.id);
      var displayPhone = appt.patient_phone ? appt.patient_phone.replace('whatsapp:', '') : '—';
      tr.innerHTML =
        '<td style="color: var(--gray-400); font-size: 0.75rem;">' + (index + 1) + '</td>' +
        '<td class="patient-name">' + escapeHtml(appt.patient_name) + '</td>' +
        '<td style="font-family: monospace; font-size: 0.85rem;">' + escapeHtml(displayPhone) + '</td>' +
        '<td><span class="treatment-tag">' + appt.treatment_icon + ' ' + escapeHtml(appt.treatment_type) + '</span></td>' +
        '<td>' + (isToday(appt.appt_date) ? '<strong>Today</strong>' : displayDate(appt.appt_date)) + '</td>' +
        '<td>' + appt.appt_time + '</td>' +
        '<td>' + statusBadge(appt.status) + '</td>' +
        '<td>' + actionButtons(appt) + '</td>';
      appointmentsBody.appendChild(tr);
    });

    tableCount.textContent = 'Showing ' + filtered.length + ' appointment' + (filtered.length !== 1 ? 's' : '');
  }

  function statusBadge(status) {
    var labels = {
      'pending': '⏳ Pending',
      'confirmed': '✅ Confirmed',
      'completed': '☑️ Completed',
      'cancelled': '❌ Cancelled',
      'no-show': '⚠️ No-show'
    };
    var cssClass = status === 'no-show' ? 'no-show' : status;
    return '<span class="status-badge ' + cssClass + '">' + (labels[status] || status) + '</span>';
  }

  function actionButtons(appt) {
    if (['completed', 'cancelled', 'no-show'].includes(appt.status)) {
      return '<span style="color: var(--gray-300); font-size: 0.75rem;">—</span>';
    }

    var buttons = [];

    if (appt.status === 'pending') {
      buttons.push('<button class="action-btn confirm" onclick="window.DentalApp.changeStatus(' + appt.id + ', \'confirmed\')" title="Mark as confirmed">✅ Confirm</button>');
    }
    if (appt.status === 'confirmed' || appt.status === 'pending') {
      buttons.push('<button class="action-btn complete" onclick="window.DentalApp.changeStatus(' + appt.id + ', \'completed\')" title="Mark as completed">☑️ Complete</button>');
      buttons.push('<button class="action-btn cancel" onclick="window.DentalApp.changeStatus(' + appt.id + ', \'cancelled\')" title="Mark as cancelled">❌ Cancel</button>');
      buttons.push('<button class="action-btn noshow" onclick="window.DentalApp.changeStatus(' + appt.id + ', \'no-show\')" title="Mark as no-show">⚠️ No-show</button>');
    }

    return '<div class="action-group">' + buttons.join('') + '</div>';
  }

  // ---------- Status Change (with confirmation modal) ----------
  function changeStatus(id, newStatus) {
    var appt = appointments.find(function (a) { return a.id === id; });
    if (!appt) return;

    var statusLabels = {
      'confirmed': 'Confirmed',
      'completed': 'Completed',
      'cancelled': 'Cancelled',
      'no-show': 'No-show'
    };

    pendingStatusChange = { id: id, newStatus: newStatus };
    modalTitle.textContent = 'Mark as ' + statusLabels[newStatus] + '?';
    modalMessage.textContent = 'Change ' + appt.patient_name + '\'s appointment (' + appt.appt_time + ', ' + displayDate(appt.appt_date) + ') to "' + statusLabels[newStatus] + '"?';
    modalOverlay.classList.add('active');
  }

  async function confirmStatusChange() {
    if (!pendingStatusChange) return;
    var id = pendingStatusChange.id;
    var newStatus = pendingStatusChange.newStatus;

    // Disable confirm button to prevent double-click
    modalConfirm.disabled = true;
    modalConfirm.textContent = 'Updating...';

    try {
      await apiCall('PATCH', '/appointments/' + id, { status: newStatus });
      closeModal();
      // Reload data from server to get fresh state
      await Promise.all([loadAppointments(), loadStats()]);
      showToast('Status updated to "' + newStatus + '"');
    } catch (err) {
      closeModal();
      showToast('Failed to update status', '❌');
      console.error('confirmStatusChange error:', err);
    }

    pendingStatusChange = null;
  }

  function closeModal() {
    modalOverlay.classList.remove('active');
    modalConfirm.disabled = false;
    modalConfirm.textContent = 'Confirm';
    pendingStatusChange = null;
  }

  // ---------- CSV Export ----------
  function exportCSV() {
    var filtered = getFilteredAppointments();
    if (filtered.length === 0) {
      showToast('No appointments to export');
      return;
    }

    var headers = ['#', 'Patient Name', 'Phone', 'Treatment', 'Date', 'Time', 'Status'];
    var rows = filtered.map(function (appt, i) {
      var displayPhone = appt.patient_phone ? appt.patient_phone.replace('whatsapp:', '') : '';
      return [
        i + 1,
        '"' + appt.patient_name + '"',
        '"' + displayPhone + '"',
        '"' + appt.treatment_type + '"',
        appt.appt_date,
        appt.appt_time,
        appt.status
      ];
    });

    var csvContent = [
      headers.join(',')
    ].concat(rows.map(function (r) { return r.join(','); })).join('\n');

    var blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
    var url = URL.createObjectURL(blob);
    var link = document.createElement('a');
    link.href = url;
    link.download = 'appointments_' + currentTab + '_' + getToday() + '.csv';
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);

    showToast('Exported ' + filtered.length + ' appointments as CSV');
  }

  // ---------- Tab Switching ----------
  function switchTab(tab) {
    currentTab = tab;
    tabButtons.forEach(function (btn) {
      btn.classList.toggle('active', btn.dataset.tab === tab);
    });
    // Fetch fresh data for the new tab
    loadAppointments();
  }

  // ---------- Auto Refresh ----------
  function startAutoRefresh() {
    stopAutoRefresh();
    refreshTimer = setInterval(function () {
      loadAppointments();
      loadStats();
    }, AUTO_REFRESH_INTERVAL_MS);
  }

  function stopAutoRefresh() {
    if (refreshTimer) {
      clearInterval(refreshTimer);
      refreshTimer = null;
    }
  }

  // ---------- Toast Notifications ----------
  function showToast(message, icon) {
    var toast = document.createElement('div');
    toast.className = 'toast';
    toast.innerHTML = (icon || '✓') + ' ' + escapeHtml(message);
    toastContainer.appendChild(toast);

    setTimeout(function () {
      if (toast.parentNode) toast.parentNode.removeChild(toast);
    }, 3200);
  }

  // ---------- Event Listeners ----------
  loginForm.addEventListener('submit', handleLogin);
  btnLogout.addEventListener('click', handleLogout);
  btnExport.addEventListener('click', exportCSV);
  btnRefresh.addEventListener('click', function () {
    Promise.all([loadAppointments(), loadStats()]);
    showToast('Data refreshed', '🔄');
  });

  tabButtons.forEach(function (btn) {
    btn.addEventListener('click', function () { switchTab(btn.dataset.tab); });
  });

  modalCancel.addEventListener('click', closeModal);
  modalConfirm.addEventListener('click', confirmStatusChange);

  modalOverlay.addEventListener('click', function (e) {
    if (e.target === modalOverlay) closeModal();
  });

  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && modalOverlay.classList.contains('active')) {
      closeModal();
    }
  });

  // ---------- Expose for inline onclick handlers ----------
  window.DentalApp = { changeStatus: changeStatus };

  // ---------- Initialize ----------
  async function init() {
    var session = getSession();
    if (session) {
      // Verify session with server before showing dashboard
      try {
        await apiCall('GET', '/verify');
        showDashboard();
      } catch {
        // Session invalid — show login
        destroySession();
        showLogin();
      }
    } else {
      showLogin();
    }
  }

  init();
})();
