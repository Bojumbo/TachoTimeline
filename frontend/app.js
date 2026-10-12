/**
 * TachoSmart - TruckerTimeline Frontend Application
 * EU Regulation 561/2006 + Mobility Package
 */

// Detect API URL (relative '/api' works seamlessly for Nginx on port 8975, 80, or FastAPI port 8000)
const API_BASE = window.location.protocol === 'file:' ? 'http://localhost:8000/api' : '/api';

let timeline = null;
let timelineItems = null;
let currentStatus = null;
let activitiesList = [];
let ongoingTimerInterval = null;
let activeActivityStartTime = null;

// ================= Clocks & Time Helpers =================
function updateClocks() {
    const now = new Date();
    
    // UTC
    const utcHours = String(now.getUTCHours()).padStart(2, '0');
    const utcMinutes = String(now.getUTCMinutes()).padStart(2, '0');
    const utcSeconds = String(now.getUTCSeconds()).padStart(2, '0');
    document.getElementById('utcClock').innerText = `${utcHours}:${utcMinutes}:${utcSeconds}`;

    // Local
    const localHours = String(now.getHours()).padStart(2, '0');
    const localMinutes = String(now.getMinutes()).padStart(2, '0');
    const localSeconds = String(now.getSeconds()).padStart(2, '0');
    document.getElementById('localClock').innerText = `${localHours}:${localMinutes}:${localSeconds}`;
}

function formatMinutes(mins) {
    if (isNaN(mins) || mins < 0) mins = 0;
    const h = Math.floor(mins / 60);
    const m = Math.floor(mins % 60);
    return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`;
}

function formatDateTimeInput(date) {
    const d = new Date(date);
    const offset = d.getTimezoneOffset() * 60000;
    const local = new Date(d.getTime() - offset);
    return local.toISOString().slice(0, 16);
}

// ================= Authentication =================
async function getAuthToken() {
    let token = localStorage.getItem('tacho_token');
    if (!token) {
        try {
            const res = await fetch(`${API_BASE}/auth/guest`, { method: 'POST' });
            if (res.ok) {
                const data = await res.json();
                token = data.access_token;
                localStorage.setItem('tacho_token', token);
                document.getElementById('userNameDisplay').innerText = `${data.username} (Гість)`;
            }
        } catch (err) {
            console.warn('Backend not responding or guest login fallback:', err);
        }
    }
    return token;
}

async function apiFetch(endpoint, options = {}) {
    const token = await getAuthToken();
    const headers = {
        'Content-Type': 'application/json',
        ...(token ? { 'Authorization': `Bearer ${token}` } : {}),
        ...options.headers
    };
    const res = await fetch(`${API_BASE}${endpoint}`, { ...options, headers });
    if (res.status === 401) {
        localStorage.removeItem('tacho_token');
        await getAuthToken();
    }
    return res;
}

// ================= Vis-Timeline Setup =================
function initTimeline() {
    const container = document.getElementById('visTimelineContainer');
    if (!container || !window.vis) return;

    timelineItems = new vis.DataSet([]);

    const now = new Date();
    const startWindow = new Date(now.getTime() - 12 * 60 * 60 * 1000);
    const endWindow = new Date(now.getTime() + 12 * 60 * 60 * 1000);

    const options = {
        start: startWindow,
        end: endWindow,
        zoomMin: 1000 * 60 * 15, // 15 minutes minimum zoom
        zoomMax: 1000 * 60 * 60 * 24 * 14, // 14 days maximum zoom
        orientation: 'top',
        stack: false,
        height: '210px',
        showCurrentTime: true,
        selectable: true,
        editable: false,
        margin: { item: 4, axis: 5 }
    };

    timeline = new vis.Timeline(container, timelineItems, options);

    timeline.on('select', function (properties) {
        if (properties.items.length > 0) {
            const selectedId = properties.items[0];
            const act = activitiesList.find(a => a.id === selectedId);
            if (act) openEditModal(act);
        }
    });

    // View filter buttons
    document.getElementById('view24h').addEventListener('click', () => {
        setActiveViewBtn('view24h');
        const curr = new Date();
        timeline.setWindow(new Date(curr.getTime() - 12 * 3600 * 1000), new Date(curr.getTime() + 12 * 3600 * 1000));
    });

    document.getElementById('view48h').addEventListener('click', () => {
        setActiveViewBtn('view48h');
        const curr = new Date();
        timeline.setWindow(new Date(curr.getTime() - 24 * 3600 * 1000), new Date(curr.getTime() + 24 * 3600 * 1000));
    });

    document.getElementById('view7d').addEventListener('click', () => {
        setActiveViewBtn('view7d');
        const curr = new Date();
        timeline.setWindow(new Date(curr.getTime() - 3.5 * 24 * 3600 * 1000), new Date(curr.getTime() + 3.5 * 24 * 3600 * 1000));
    });

    document.getElementById('btnZoomFit').addEventListener('click', () => {
        timeline.fit();
    });
}

function setActiveViewBtn(btnId) {
    ['view24h', 'view48h', 'view7d'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.classList.remove('active');
    });
    const target = document.getElementById(btnId);
    if (target) target.classList.add('active');
}

// ================= Data Sync & UI Updates =================
async function refreshDashboard() {
    try {
        // Fetch status & activities
        const [statusRes, actRes] = await Promise.all([
            apiFetch('/tachograph/status'),
            apiFetch('/activities')
        ]);

        if (statusRes.ok) {
            currentStatus = await statusRes.json();
            renderMetrics(currentStatus);
            renderAlerts(currentStatus.warnings || []);
        }

        if (actRes.ok) {
            activitiesList = await actRes.json();
            renderTimelineData(activitiesList);
            renderActivityTable(activitiesList);
        }
    } catch (err) {
        console.error('Error refreshing dashboard:', err);
    }
}

function renderMetrics(status) {
    if (!status) return;

    // 1. Continuous Driving
    const contVal = document.getElementById('valContinuousTime');
    const contLeft = document.getElementById('valContinuousLeft');
    const contBar = document.getElementById('barContinuous');
    const contCard = document.getElementById('cardContinuous');

    const contMins = status.current_drive_streak_min || 0;
    const contLeftMins = status.drive_left_before_pause_min || 0;
    const contPct = Math.min(100, Math.round((contMins / 270) * 100));

    contVal.innerText = formatMinutes(contMins);
    contLeft.innerText = formatMinutes(contLeftMins);
    contBar.style.width = `${contPct}%`;

    // Dynamic warning colors
    if (status.is_continuous_violation) {
        contBar.style.backgroundColor = 'var(--color-danger)';
        contCard.style.borderColor = 'var(--color-danger)';
    } else if (status.is_continuous_warning) {
        contBar.style.backgroundColor = 'var(--color-warning)';
        contCard.style.borderColor = 'var(--color-warning)';
    } else {
        contBar.style.backgroundColor = 'var(--color-drive)';
        contCard.style.borderColor = 'var(--border-color)';
    }

    const splitText = status.split_15_taken 
        ? `<i class="fa-solid fa-clock-rotate-left text-warning"></i> Спліт 15хв взято. Наступна пауза: <strong>30 хв</strong>` 
        : `<i class="fa-regular fa-circle-check text-success"></i> Обов'язкова пауза: <strong>${status.must_take_pause_minutes} хв</strong>`;
    document.getElementById('valSplitStatus').innerHTML = splitText;

    // 2. Daily Driving
    const dailyMins = status.daily_drive_min || 0;
    const dailyLeftMins = status.daily_drive_left_min || 0;
    const dailyMaxHours = status.effective_daily_max_hours || 9;
    const dailyMaxMins = dailyMaxHours * 60;
    const dailyPct = Math.min(100, Math.round((dailyMins / dailyMaxMins) * 100));

    document.getElementById('valDailyDrive').innerText = formatMinutes(dailyMins);
    document.getElementById('valDailyLeft').innerText = formatMinutes(dailyLeftMins);
    document.getElementById('barDaily').style.width = `${dailyPct}%`;
    document.getElementById('valDailyLimitLabel').innerText = `${dailyMaxHours}:00 год (макс)`;
    document.getElementById('valExtensionsStatus').innerHTML = `Подовжень 10 год використано: <strong>${status.extensions_used_this_week} з 2</strong> (Залишок: ${status.extensions_remaining})`;

    // 3. Daily Shift
    const shiftElapsed = status.shift_elapsed_min || 0;
    const shiftLeft = status.current_shift_left_min || 0;
    const allowedShiftHours = status.allowed_shift_hours || 13;
    const shiftMaxMins = allowedShiftHours * 60;
    const shiftPct = Math.min(100, Math.round((shiftElapsed / shiftMaxMins) * 100));

    document.getElementById('valShiftElapsed').innerText = formatMinutes(shiftElapsed);
    document.getElementById('valShiftLeft').innerText = formatMinutes(shiftLeft);
    document.getElementById('barShift').style.width = `${shiftPct}%`;
    document.getElementById('valShiftLimitLabel').innerText = `${allowedShiftHours}:00 год (макс)`;
    document.getElementById('valReducedRestStatus').innerHTML = `Скорочених відпочинків (9h): <strong>${status.reduced_rests_used_this_week} з 3</strong> (Залишок: ${status.reduced_rests_remaining})`;

    // 4. Weekly Limits
    const weekDriveHours = status.week_drive_hours || 0;
    const weekPct = Math.min(100, Math.round((weekDriveHours / 56) * 100));
    document.getElementById('valWeekDrive').innerText = `${weekDriveHours} / 56h`;
    document.getElementById('valTwoWeekDrive').innerText = `${status.two_week_drive_hours || 0} / 90h`;
    document.getElementById('barWeekly').style.width = `${weekPct}%`;
    document.getElementById('valWeeklyRestCountdown').innerHTML = `<i class="fa-solid fa-bed"></i> До щотижневого відпочинку: <strong>${status.hours_until_weekly_rest} год</strong> (144h ліміт)`;

    // Update Controller Active Status
    updateActiveController(status.current_activity);
}

function updateActiveController(currAct) {
    const badge = document.getElementById('activeStateBadge');
    const badgeText = document.getElementById('activeStateText');
    const timerText = document.getElementById('activeTimerText');

    // Reset button active states
    ['btnModeDrive', 'btnModeWork', 'btnModePoa', 'btnModeRest'].forEach(id => {
        document.getElementById(id).classList.remove('active');
    });

    if (currAct && currAct.is_ongoing) {
        badgeText.innerText = currAct.type;
        activeActivityStartTime = new Date(currAct.start);

        const btnMap = {
            'DRIVE': 'btnModeDrive',
            'WORK': 'btnModeWork',
            'POA': 'btnModePoa',
            'REST': 'btnModeRest'
        };
        const activeBtn = document.getElementById(btnMap[currAct.type]);
        if (activeBtn) activeBtn.classList.add('active');

        // Start live counting timer
        if (ongoingTimerInterval) clearInterval(ongoingTimerInterval);
        ongoingTimerInterval = setInterval(() => {
            const diffSeconds = Math.max(0, Math.floor((new Date() - activeActivityStartTime) / 1000));
            const h = Math.floor(diffSeconds / 3600);
            const m = Math.floor((diffSeconds % 3600) / 60);
            const s = diffSeconds % 60;
            timerText.innerText = `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
        }, 1000);
    } else {
        badgeText.innerText = 'НЕАКТИВНО';
        timerText.innerText = '00:00';
        if (ongoingTimerInterval) clearInterval(ongoingTimerInterval);
    }
}

function renderAlerts(warnings) {
    const container = document.getElementById('alertsContainer');
    container.innerHTML = '';
    if (!warnings || warnings.length === 0) return;

    warnings.forEach(w => {
        const div = document.createElement('div');
        div.className = `alert-box alert-${w.level}`;
        const icon = w.level === 'danger' ? 'fa-triangle-exclamation' : 'fa-circle-exclamation';
        div.innerHTML = `<i class="fa-solid ${icon}"></i> <span>${w.message}</span>`;
        container.appendChild(div);
    });
}

function renderTimelineData(activities) {
    if (!timeline || !timelineItems) return;

    const items = activities.map(act => {
        const start = new Date(act.start_time);
        const end = act.end_time ? new Date(act.end_time) : new Date();
        const durationMin = Math.round((end - start) / 60000);
        const actType = act.type.toUpperCase();

        const labels = {
            'DRIVE': 'Кермо (Drive)',
            'WORK': 'Робота (Work)',
            'POA': 'Готовність (POA)',
            'REST': 'Відпочинок (Rest)'
        };

        const classNameMap = {
            'DRIVE': 'item-drive',
            'WORK': 'item-work',
            'POA': 'item-poa',
            'REST': 'item-rest'
        };

        return {
            id: act.id,
            content: `<strong>${labels[actType] || actType}</strong> (${formatMinutes(durationMin)})`,
            start: start,
            end: end,
            className: classNameMap[actType] || 'item-drive',
            title: `Тип: ${labels[actType]}\nПочаток: ${start.toLocaleTimeString()}\nКінець: ${end.toLocaleTimeString()}\nТривалість: ${formatMinutes(durationMin)}\n${act.note || ''}`
        };
    });

    timelineItems.clear();
    timelineItems.add(items);
}

function renderActivityTable(activities) {
    const tbody = document.getElementById('activityTableBody');
    if (!activities || activities.length === 0) {
        tbody.innerHTML = `<tr><td colspan="6" class="text-center text-muted py-4">Журнал порожній. Натисніть кнопку режиму або завантажте зразок рейсу.</td></tr>`;
        return;
    }

    const typeIcons = {
        'DRIVE': '<span class="type-pill pill-drive"><i class="fa-solid fa-steering-wheel"></i> Кермо</span>',
        'WORK': '<span class="type-pill pill-work"><i class="fa-solid fa-hammer"></i> Робота</span>',
        'POA': '<span class="type-pill pill-poa"><i class="fa-regular fa-square"></i> Готовність</span>',
        'REST': '<span class="type-pill pill-rest"><i class="fa-solid fa-bed"></i> Відпочинок</span>'
    };

    // Sort descending for recent items first
    const sorted = [...activities].reverse();

    tbody.innerHTML = sorted.map(act => {
        const start = new Date(act.start_time);
        const end = act.end_time ? new Date(act.end_time) : null;
        const durationMin = end ? Math.round((end - start) / 60000) : Math.round((new Date() - start) / 60000);
        const isOngoing = !act.end_time;

        return `
            <tr>
                <td>${typeIcons[act.type] || act.type}</td>
                <td class="font-mono">${start.toLocaleDateString()} ${start.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</td>
                <td class="font-mono">${end ? `${end.toLocaleDateString()} ${end.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}` : '<span class="pulse-dot inline-block"></span> Триває зараз'}</td>
                <td class="font-mono"><strong>${formatMinutes(durationMin)}</strong></td>
                <td>${act.note ? act.note : '<span class="text-muted">—</span>'}</td>
                <td style="text-align: right">
                    <button class="btn btn-secondary btn-sm" onclick="editActivityById(${act.id})" title="Редагувати">
                        <i class="fa-solid fa-pen"></i>
                    </button>
                    <button class="btn btn-danger btn-sm" onclick="deleteActivityById(${act.id})" title="Видалити">
                        <i class="fa-solid fa-trash"></i>
                    </button>
                </td>
            </tr>
        `;
    }).join('');
}

// ================= Tachograph Button Switches =================
async function switchActivityMode(type) {
    try {
        const res = await apiFetch('/activities/switch', {
            method: 'POST',
            body: JSON.stringify({ type: type })
        });
        if (res.ok) {
            await refreshDashboard();
        }
    } catch (err) {
        console.error('Failed to switch activity:', err);
    }
}

// ================= Modal Management =================
const modal = document.getElementById('activityModal');
const activityForm = document.getElementById('activityForm');

function openAddModal() {
    document.getElementById('modalTitle').innerHTML = '<i class="fa-solid fa-plus-circle"></i> Додати запис тахографа';
    document.getElementById('editActivityId').value = '';
    document.getElementById('modalStartTime').value = formatDateTimeInput(new Date());
    document.getElementById('modalEndTime').value = '';
    document.getElementById('modalNote').value = '';
    modal.classList.remove('hidden');
}

function openEditModal(act) {
    document.getElementById('modalTitle').innerHTML = '<i class="fa-solid fa-pen-to-square"></i> Редагувати запис';
    document.getElementById('editActivityId').value = act.id;
    
    const radios = document.getElementsByName('modalType');
    radios.forEach(r => {
        r.checked = (r.value === act.type);
    });

    document.getElementById('modalStartTime').value = formatDateTimeInput(act.start_time);
    document.getElementById('modalEndTime').value = act.end_time ? formatDateTimeInput(act.end_time) : '';
    document.getElementById('modalNote').value = act.note || '';
    modal.classList.remove('hidden');
}

function closeModal() {
    modal.classList.add('hidden');
}

window.editActivityById = function(id) {
    const act = activitiesList.find(a => a.id === id);
    if (act) openEditModal(act);
};

window.deleteActivityById = async function(id) {
    if (!confirm('Видалити цей запис тахографа?')) return;
    try {
        const res = await apiFetch(`/activities/${id}`, { method: 'DELETE' });
        if (res.ok) {
            await refreshDashboard();
        }
    } catch (err) {
        console.error('Failed to delete activity:', err);
    }
};

// ================= Event Listeners =================
document.addEventListener('DOMContentLoaded', async () => {
    // Start live clock
    updateClocks();
    setInterval(updateClocks, 1000);

    // Initialize timeline
    initTimeline();

    // Authenticate / Init session
    await getAuthToken();

    // Load initial data
    await refreshDashboard();

    // Auto-poll status every 15 seconds
    setInterval(refreshDashboard, 15000);

    // Hardware button listeners
    document.getElementById('btnModeDrive').addEventListener('click', () => switchActivityMode('DRIVE'));
    document.getElementById('btnModeWork').addEventListener('click', () => switchActivityMode('WORK'));
    document.getElementById('btnModePoa').addEventListener('click', () => switchActivityMode('POA'));
    document.getElementById('btnModeRest').addEventListener('click', () => switchActivityMode('REST'));

    // Keyboard shortcuts (1: Drive, 2: Work, 3: POA, 4: Rest)
    window.addEventListener('keydown', (e) => {
        if (['INPUT', 'TEXTAREA'].includes(e.target.tagName)) return;
        if (e.key === '1') switchActivityMode('DRIVE');
        if (e.key === '2') switchActivityMode('WORK');
        if (e.key === '3') switchActivityMode('POA');
        if (e.key === '4') switchActivityMode('REST');
    });

    // Seed Demo Shift
    document.getElementById('btnSeedDemo').addEventListener('click', async () => {
        try {
            const res = await apiFetch('/activities/seed-demo', { method: 'POST' });
            if (res.ok) {
                await refreshDashboard();
                if (timeline) timeline.fit();
            }
        } catch (err) {
            alert('Помилка завантаження демо: ' + err.message);
        }
    });

    // Clear activities
    document.getElementById('btnClearActivities').addEventListener('click', async () => {
        if (!confirm('Ви дійсно бажаєте очистити всі записи тахографа?')) return;
        try {
            const res = await apiFetch('/activities/clear', { method: 'POST' });
            if (res.ok) await refreshDashboard();
        } catch (err) {
            alert('Помилка: ' + err.message);
        }
    });

    // Modal controls
    document.getElementById('btnAddManual').addEventListener('click', openAddModal);
    document.getElementById('btnCloseModal').addEventListener('click', closeModal);
    document.getElementById('btnCancelModal').addEventListener('click', closeModal);

    activityForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const editId = document.getElementById('editActivityId').value;
        const selectedType = document.querySelector('input[name="modalType"]:checked').value;
        const startVal = document.getElementById('modalStartTime').value;
        const endVal = document.getElementById('modalEndTime').value;
        const noteVal = document.getElementById('modalNote').value;

        const payload = {
            type: selectedType,
            start_time: new Date(startVal).toISOString(),
            end_time: endVal ? new Date(endVal).toISOString() : null,
            note: noteVal || null
        };

        try {
            let res;
            if (editId) {
                res = await apiFetch(`/activities/${editId}`, {
                    method: 'PUT',
                    body: JSON.stringify(payload)
                });
            } else {
                res = await apiFetch('/activities', {
                    method: 'POST',
                    body: JSON.stringify(payload)
                });
            }

            if (res.ok) {
                closeModal();
                await refreshDashboard();
            } else {
                const errData = await res.json();
                alert('Помилка: ' + (errData.detail || 'Не вдалося зберегти запис'));
            }
        } catch (err) {
            alert('Мережева помилка: ' + err.message);
        }
    });
});
