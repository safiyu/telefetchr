// telegram_downloader.js
// JavaScript logic for Telegram File Downloader UI

let statusCheckInterval;
let channelList = [];
let rawChannels = [];
let savePath = "";
let selectedFiles = new Set();
let progressMonitoringInterval = null;
let completedDownloads = new Map(); // Track completed downloads
let lastProgressUpdate = null; // Track last progress update time
let progressWatchdog = null; // Watchdog to detect stalled progress monitoring
let currentSessionId = null; // Track current active session ID
let currentActivityFilter = 'all'; // Current active filter: 'all' | 'active' | 'queued' | 'partial' | 'completed'
let lastKnownProgressData = null;
let diskCheckInterval = null;
let queueCurrentPage = 1;
const queueItemsPerPage = 10;

// Authentication helpers
function setButtonLoading(btnId, isLoading, loadingText = "Loading...") {
    const btn = document.getElementById(btnId);
    if (!btn) return;

    if (isLoading) {
        const originalContent = btn.innerHTML;
        if (!btn.hasAttribute('data-original-content')) {
            btn.setAttribute('data-original-content', originalContent);
        }
        btn.disabled = true;
        btn.innerHTML = `<i class="fa-solid fa-circle-notch fa-spin"></i> <span>${loadingText}</span>`;
    } else {
        btn.disabled = false;
        const originalContent = btn.getAttribute('data-original-content');
        if (originalContent) {
            btn.innerHTML = originalContent;
        }
    }
}

function getAuthToken() {
    return localStorage.getItem('access_token');
}

function getAuthHeaders() {
    const token = getAuthToken();
    return token ? { 'Authorization': `Bearer ${token}` } : {};
}

function getFileServeUrl(filePathOrName) {
    const token = getAuthToken();
    // Segment encode each part so slashes are preserved for FastAPI {filepath:path} parameter
    const safePath = String(filePathOrName || '')
        .split('/')
        .map(encodeURIComponent)
        .join('/');
    const base = `/files/serve/${safePath}`;
    return token ? `${base}?token=${encodeURIComponent(token)}` : base;
}

// --- XSS escaping helpers -------------------------------------------------
// Untrusted values (Telegram file names, channel names, user display names)
// must never be interpolated raw into HTML or inline event handlers. Use
// escapeHtml() for HTML text / attribute content and jsStr() for values that
// appear inside a single-quoted JavaScript string in an inline handler.

// Escape a value for safe use as HTML text content or inside an attribute.
function escapeHtml(value) {
    if (value === null || value === undefined) return "";
    return String(value)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
}

// Build a safe single-quoted JavaScript string literal (quotes included) from
// an untrusted value, for use inside an inline HTML event handler such as
// onclick="...". The HTML parser decodes the attribute before evaluating the
// JS, so we encode HTML metacharacters as JS unicode escapes (backslash
// first) which survive HTML decoding and stay literal in JS.
function jsStr(value) {
    const s = String(value === null || value === undefined ? "" : value);
    return "'" + s
        .replace(/\\/g, "\\\\")
        .replace(/&/g, "\\u0026")
        .replace(/</g, "\\u003c")
        .replace(/>/g, "\\u003e")
        .replace(/"/g, "\\u0022")
        .replace(/'/g, "\\u0027") + "'";
}

// Build the DOM id for a file's progress element. fileId can carry hostile
// characters (it derives from Telegram data), so it is restricted to a safe
// identifier character set. Every lookup of these elements MUST go through
// this same function (with the same prefix) or the lookup will miss the
// rendered node. Pass "cancelled" as prefix for the cancelled-progress bar.
function progressElId(fileId, prefix = "") {
    const safe = String(fileId).replace(/[^a-zA-Z0-9_-]/g, "_");
    return `progress${prefix ? `-${prefix}` : ""}-${safe}`;
}

async function logout() {
    // Check if user is on a trusted subnet
    try {
        const response = await fetch('/auth/is-trusted');
        if (response.ok) {
            const data = await response.json();
            if (data.is_trusted) {
                // Show info modal for trusted IP users
                await showConfirmModal({
                    title: 'Logout Disabled',
                    message: 'Logout is disabled because you are connected from a trusted subnet.',
                    details: 'Authentication is automatically bypassed for trusted IPs configured in the system. To logout, connect from a different network or use "Delete Session" to remove your Telegram session.',
                    icon: 'fa-info-circle',
                    iconType: 'warning',
                    confirmText: 'OK',
                    cancelText: '',
                    confirmClass: 'btn-primary'
                });
                return;
            }
        }
    } catch (error) {
        console.error('Error checking trusted IP:', error);
        // Continue with logout if check fails
    }

    // Show confirmation modal
    const confirmed = await showConfirmModal({
        title: 'Logout from TeleFetchr?',
        message: 'You will be logged out of the web interface. Your Telegram session will remain active.',
        details: 'You can log back in anytime with your credentials without re-authenticating with Telegram.',
        icon: 'fa-sign-out-alt',
        iconType: 'warning',
        confirmText: 'Logout',
        cancelText: 'Stay Logged In',
        confirmClass: 'btn-primary'
    });

    if (!confirmed) {
        return;
    }

    localStorage.removeItem('access_token');
    window.location.href = '/';
}

// Show confirmation modal
function showConfirmModal(options) {
    return new Promise((resolve) => {
        const modal = document.getElementById('confirmModal');
        const modalTitle = document.getElementById('modalTitle');
        const modalMessage = document.getElementById('modalMessage');
        const modalIcon = document.getElementById('modalIcon');
        const modalDetails = document.getElementById('modalDetails');
        const modalDetailsText = document.getElementById('modalDetailsText');
        const confirmBtn = document.getElementById('modalConfirmBtn');
        const cancelBtn = document.getElementById('modalCancelBtn');

        // Set content
        modalTitle.textContent = options.title || 'Confirm Action';
        modalMessage.textContent = options.message || 'Are you sure?';

        // Set icon
        modalIcon.className = 'modal-icon ' + (options.iconType || 'danger');
        modalIcon.innerHTML = `<i class="fa-solid ${options.icon || 'fa-trash-can'}"></i>`;

        // Set details if provided
        if (options.details) {
            modalDetailsText.textContent = options.details;
            modalDetails.classList.remove('hidden');
        } else {
            modalDetails.classList.add('hidden');
        }

        // Set button text
        confirmBtn.innerHTML = `<i class="fa-solid fa-check mr-2"></i>${options.confirmText || 'Confirm'}`;
        cancelBtn.innerHTML = `<i class="fa-solid fa-xmark mr-2"></i>${options.cancelText || 'Cancel'}`;

        // Hide cancel button if cancelText is empty
        if (options.cancelText === '') {
            cancelBtn.classList.add('hidden');
        } else {
            cancelBtn.classList.remove('hidden');
        }

        // Set button style - only override if confirmClass is explicitly provided
        if (options.confirmClass) {
            confirmBtn.className = options.confirmClass;
        }

        // Show modal
        modal.classList.remove('hidden');

        // Handle confirmation
        const handleConfirm = () => {
            cleanup();
            resolve(true);
        };

        // Handle cancel
        const handleCancel = () => {
            cleanup();
            resolve(false);
        };

        // Handle click outside modal
        const handleOutsideClick = (e) => {
            if (e.target === modal) {
                handleCancel();
            }
        };

        // Handle escape key
        const handleEscape = (e) => {
            if (e.key === 'Escape') {
                handleCancel();
            }
        };

        // Cleanup function
        const cleanup = () => {
            modal.classList.add('hidden');
            confirmBtn.removeEventListener('click', handleConfirm);
            cancelBtn.removeEventListener('click', handleCancel);
            modal.removeEventListener('click', handleOutsideClick);
            document.removeEventListener('keydown', handleEscape);
        };

        // Add event listeners
        confirmBtn.addEventListener('click', handleConfirm);
        cancelBtn.addEventListener('click', handleCancel);
        modal.addEventListener('click', handleOutsideClick);
        document.addEventListener('keydown', handleEscape);

        // Store resolve function on modal element for global closeConfirmModal
        modal._resolve = resolve;
        modal._cleanup = cleanup;
    });
}

function closeConfirmModal() {
    const modal = document.getElementById('confirmModal');
    if (modal && !modal.classList.contains('hidden')) {
        if (modal._resolve) {
            modal._resolve(false);
        }
        if (modal._cleanup) {
            modal._cleanup();
        } else {
            modal.classList.add('hidden');
        }
    }
}

async function logoutSession() {
    // Show confirmation modal
    const confirmed = await showConfirmModal({
        title: 'Delete Telegram Session?',
        message: 'This will permanently delete your Telegram session. You will need to re-authenticate with a verification code the next time you use this app.',
        details: 'This action cannot be undone. Your session file and all download progress will be cleared.',
        icon: 'fa-trash-can',
        iconType: 'danger',
        confirmText: 'Delete Session',
        cancelText: 'Cancel'
    });

    if (!confirmed) {
        return;
    }

    try {
        const response = await authFetch('/logout-session', {
            method: 'POST'
        });

        if (!response) return;

        const data = await response.json();

        if (response.ok) {
            showAlert('downloadAlert', 'Session deleted. Redirecting...', 'success');

            // Wait a moment for the user to see the message
            setTimeout(() => {
                // Clear local storage and redirect to login
                localStorage.removeItem('access_token');
                window.location.href = '/';
            }, 1500);
        } else {
            showAlert('downloadAlert', data.detail || 'Failed to delete session', 'error');
        }
    } catch (error) {
        console.error('Error deleting session:', error);
        showAlert('downloadAlert', 'Error: ' + error.message, 'error');
    }
}

// Safe JSON parser
async function safeJsonParse(response, context = 'request') {
    try {
        const text = await response.text();

        // Check if response is likely HTML (error page)
        if (text.trim().startsWith('<')) {
            console.error(`${context}: Server returned HTML instead of JSON. Likely a server error.`);
            console.error('Response preview:', text.substring(0, 200));
            throw new Error('Server error - received HTML instead of JSON');
        }

        // Try to parse as JSON
        return JSON.parse(text);
    } catch (error) {
        if (error.message.includes('Server error')) {
            throw error; // Re-throw our custom error
        }
        console.error(`${context}: Failed to parse response as JSON:`, error);
        throw new Error('Invalid response from server');
    }
}

// Authenticated fetch wrapper
async function authFetch(url, options = {}) {
    const headers = {
        ...getAuthHeaders(),
        ...(options.headers || {})
    };

    try {
        const response = await fetch(url, {
            ...options,
            headers
        });

        // If unauthorized, handle silently for background requests
        if (response.status === 401) {
            // Only show alert and redirect if this is NOT a background progress check
            if (!url.includes('/download-progress')) {
                showAlert('downloadAlert', 'Session expired. Login required.', 'warning');
                // Redirect to login after a short delay
                setTimeout(() => {
                    localStorage.removeItem('access_token');
                    window.location.href = '/';
                }, 2000);
            } else {
                // For progress checks, just log and return null silently
            }
            return null;
        }

        // IMPORTANT FIX: Allow login endpoints to return error responses
        // so they can be handled by the calling function
        if (url.includes('/login/')) {
            return response; // Return the response even if not ok
        }

        // Check for other error status codes
        if (!response.ok) {
            const errorText = await response.text();
            console.error(`HTTP ${response.status} error from ${url}:`, errorText);
            // Don't show alert for background progress checks
            if (!url.includes('/download-progress')) {
                // Special message for timeout errors
                if (response.status === 504 || response.status === 408) {
                    showAlert('downloadAlert', 'Timeout: Download continues in bg.', 'warning');
                } else if (response.status === 503) {
                    showAlert('downloadAlert', 'Server busy. Try again later.', 'warning');
                } else {
                    showAlert('downloadAlert', `Server error (${response.status}). Retry.`, 'error');
                }
            }
            return null;
        }

        return response;
    } catch (error) {
        console.error(`Network error for ${url}:`, error);
        // Don't show alert for background progress checks
        if (!url.includes('/download-progress')) {
            showAlert('downloadAlert', 'Network error. Check connection.', 'error');
        }
        return null;
    }
}

// Global error handler for unhandled promise rejections
window.addEventListener('unhandledrejection', function (event) {
    console.error('Unhandled promise rejection:', event.reason);

    // Prevent the default browser behavior (which might show console errors)
    event.preventDefault();

    // Check if it's a JSON parsing error
    if (event.reason && event.reason.message) {
        const message = event.reason.message;

        // Don't show toast for these expected errors (already logged)
        if (message.includes('Server error') ||
            message.includes('Invalid response') ||
            message.includes('Unexpected token')) {
            return;
        }
    }

    // For other errors, you might want to log them but not necessarily show a toast
});

// Check authentication on page load
window.addEventListener('DOMContentLoaded', () => {
    const token = getAuthToken();
    // Even if no token, we call checkStatus. 
    // If the server bypasses auth for this IP, checkStatus will work.
    checkStatus();
    fetchVersion();
    loadFolders();
    startProgressMonitoring();
});

async function fetchVersion() {
    try {
        const response = await authFetch('/about');
        if (!response) return;
        const data = await response.json();
        const versionText = `v${data.version}`;
        const el = document.getElementById('appVersion');
        if (el) el.textContent = versionText;
    } catch (e) {
        // silently fail — version is cosmetic
    }
}


// --- Disk Storage Metric in Top Bar and Controls ---
async function loadDiskInfo() {
    try {
        const response = await authFetch('/api/disk');
        if (!response || !response.ok) return;
        const data = await safeJsonParse(response, 'Load disk info');

        if (data.total > 0) {
            const freeStr = formatBytes(data.free);
            const totalStr = formatBytes(data.total);
            const usedPct = data.percentage || Math.round((data.used / data.total) * 100);

            const pathEl = document.getElementById('diskPathText');
            if (pathEl) {
                pathEl.textContent = data.path || '/downloads';
                pathEl.title = data.path;
            }

            const metricEl = document.getElementById('diskMetricText');
            if (metricEl) {
                metricEl.textContent = `${freeStr} free of ${totalStr}`;
            }

            const barEl = document.getElementById('diskProgressBar');
            if (barEl) {
                barEl.style.width = `${Math.min(100, Math.max(2, usedPct))}%`;
                // Color threshold based on usage
                if (usedPct > 90) {
                    barEl.style.backgroundColor = '#ef4444';
                    barEl.style.backgroundImage = 'none';
                } else if (usedPct > 75) {
                    barEl.style.backgroundColor = '#fbbf24';
                    barEl.style.backgroundImage = 'linear-gradient(90deg, #f59e0b, #fbbf24)';
                } else {
                    barEl.style.backgroundColor = '#10b981';
                    barEl.style.backgroundImage = 'linear-gradient(90deg, #059669, #34d399)';
                }
            }
        }
    } catch (err) {
        console.debug('Failed to load disk info:', err);
    }
}

// --- Searchable Channel Dropdown Combobox ---
async function loadChannels() {
    try {
        const response = await authFetch("/config/channels");
        if (!response) return;
        const data = await safeJsonParse(response, 'Load channels');

        rawChannels = data.channels || [];
        savePath = data.save_path || '/downloads';

        // Keep savePath updated and fetch disk info
        const savePathEl = document.getElementById("savePathText");
        if (savePathEl) savePathEl.textContent = savePath;
        loadDiskInfo();

        // Populate hidden select for full backward compatibility
        const channelSelect = document.getElementById("channelUsername");
        if (channelSelect) {
            channelSelect.innerHTML = '<option value="">Select a channel...</option>';
            rawChannels.forEach((channel) => {
                const option = document.createElement("option");
                option.value = channel.username || channel.id;
                option.textContent = channel.name;
                channelSelect.appendChild(option);
            });
        }

        renderChannelOptions(rawChannels);
        populateWatchRuleChannels();
        loadFolders();
    } catch (error) {
        console.error("Error loading channels:", error);
    }
}

function toggleChannelDropdown(e) {
    if (e) e.stopPropagation();
    const panel = document.getElementById("channelDropdownPanel");
    const chevron = document.getElementById("channelDropdownChevron");
    if (!panel) return;

    const isHidden = panel.classList.contains("hidden");
    if (isHidden) {
        panel.classList.remove("hidden");
        chevron?.classList.add("rotate-180");
        const searchInput = document.getElementById("channelSearchInput");
        if (searchInput) {
            searchInput.focus();
        }
    } else {
        closeChannelDropdown();
    }
}

function closeChannelDropdown() {
    const panel = document.getElementById("channelDropdownPanel");
    const chevron = document.getElementById("channelDropdownChevron");
    if (panel) panel.classList.add("hidden");
    if (chevron) chevron.classList.remove("rotate-180");
}

function renderChannelOptions(channelsToRender) {
    const list = document.getElementById("channelOptionsList");
    const matchCount = document.getElementById("channelMatchCount");
    if (!list) return;

    if (!channelsToRender || channelsToRender.length === 0) {
        list.innerHTML = `
            <div class="p-4 text-center text-xs text-gray-500">
                <i class="fa-solid fa-magnifying-glass mb-1.5 opacity-40"></i>
                <p>No matching channels</p>
            </div>
        `;
        if (matchCount) matchCount.textContent = "0 channels";
        return;
    }

    const currentVal = document.getElementById("channelUsername")?.value;
    const maxDisplay = 100;
    const sliced = channelsToRender.slice(0, maxDisplay);

    if (matchCount) {
        matchCount.textContent = channelsToRender.length > maxDisplay 
            ? `Showing 100 of ${channelsToRender.length}` 
            : `${channelsToRender.length} channels`;
    }

    list.innerHTML = sliced.map(channel => {
        const val = channel.username || channel.id;
        const isSelected = String(val) === String(currentVal);
        const usernameStr = channel.username ? `@${escapeHtml(channel.username)}` : `ID: ${channel.id}`;

        return `
            <div onclick="selectChannel(${jsStr(val)}, ${jsStr(channel.name)})"
                class="flex items-center justify-between p-2.5 rounded-lg cursor-pointer transition-all ${isSelected ? 'bg-amber-500/20 border border-amber-500/40 text-amber-300' : 'hover:bg-gray-800 text-gray-200 border border-transparent'}">
                <div class="flex items-center gap-2.5 min-w-0">
                    <div class="w-7 h-7 rounded-md bg-gray-800 flex items-center justify-center text-xs text-amber-400 flex-shrink-0">
                        <i class="fa-brands fa-telegram"></i>
                    </div>
                    <div class="min-w-0">
                        <p class="text-xs font-semibold truncate ${isSelected ? 'text-amber-300' : 'text-white'}" title="${escapeHtml(channel.name)}">${escapeHtml(channel.name)}</p>
                        <p class="text-[10px] text-gray-500 truncate font-mono">${usernameStr}</p>
                    </div>
                </div>
                ${isSelected ? '<i class="fa-solid fa-check text-amber-400 text-xs ml-2"></i>' : ''}
            </div>
        `;
    }).join('');
}

function filterChannels(query) {
    const clearBtn = document.getElementById("channelSearchClearBtn");
    if (clearBtn) {
        clearBtn.classList.toggle("hidden", !query);
    }

    const q = (query || "").trim().toLowerCase();
    if (!q) {
        renderChannelOptions(rawChannels);
        return;
    }

    const filtered = rawChannels.filter(c => {
        const name = (c.name || "").toLowerCase();
        const username = (c.username || "").toLowerCase();
        const idStr = String(c.id || "");
        return name.includes(q) || username.includes(q) || idStr.includes(q);
    });

    renderChannelOptions(filtered);
}

function clearChannelSearch() {
    const input = document.getElementById("channelSearchInput");
    if (input) {
        input.value = "";
        filterChannels("");
        input.focus();
    }
}

function selectChannel(val, label) {
    const selectEl = document.getElementById("channelUsername");
    if (selectEl) {
        selectEl.value = val;
        selectEl.dispatchEvent(new Event("change"));
    }

    const labelEl = document.getElementById("channelSelectedLabel");
    if (labelEl) {
        labelEl.textContent = label || "Select a channel...";
        labelEl.classList.remove("text-gray-400");
        labelEl.classList.add("text-white");
    }

    closeChannelDropdown();
    renderChannelOptions(rawChannels);
}

// Global click outside listener for channel picker
document.addEventListener("click", (e) => {
    const wrapper = document.getElementById("channelPickerWrapper");
    if (wrapper && !wrapper.contains(e.target)) {
        closeChannelDropdown();
    }
});

document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
        closeChannelDropdown();
    }
});

// --- Retry, Resume & History Actions ---
async function retryIndividualDownload(fileId) {
    try {
        const response = await authFetch(`/download/retry/${encodeURIComponent(fileId)}`, {
            method: 'POST'
        });
        if (!response || !response.ok) {
            const errData = await response.json().catch(() => ({}));
            showAlert("downloadAlert", errData.detail || "Failed to retry download", "error");
            return;
        }
        showAlert("downloadAlert", "Re-queued file for download", "success");
        document.getElementById('stopAllBtn')?.classList.remove('hidden');
        startProgressMonitoring();
        refreshProgress();
    } catch (err) {
        showAlert("downloadAlert", "Error retrying download: " + err.message, "error");
    }
}

async function resumeAllUnfinished() {
    try {
        const response = await authFetch('/download/resume', { method: 'POST' });
        if (!response || !response.ok) {
            const errData = await response.json().catch(() => ({}));
            showAlert("downloadAlert", errData.detail || "Failed to resume downloads", "error");
            return;
        }
        const data = await response.json();
        showAlert("downloadAlert", data.message || "Resuming unfinished downloads...", "success");
        document.getElementById('stopAllBtn')?.classList.remove('hidden');
        startProgressMonitoring();
        refreshProgress();
    } catch (err) {
        showAlert("downloadAlert", "Error resuming downloads: " + err.message, "error");
    }
}

async function clearCompletedHistory() {
    try {
        const response = await authFetch('/download/clear-completed', { method: 'POST' });
        if (response && response.ok) {
            completedDownloads.clear();
            refreshProgress();
            loadDiskInfo();
            showAlert("downloadAlert", "Finished downloads history cleared", "info");
        }
    } catch (err) {
        showAlert("downloadAlert", "Error clearing history: " + err.message, "error");
    }
}

// Backward-compatible aliases
async function clearProgress() {
    await clearCompletedHistory();
}
async function clearSavedState() {
    await clearCompletedHistory();
}
async function resumeDownload() {
    await resumeAllUnfinished();
}

async function refreshProgress() {
    await pollProgress();
}

async function checkStatus() {
    try {
        const response = await authFetch("/status");
        if (!response) return;
        const data = await safeJsonParse(response, 'Check status');

        const connectionStatus = document.getElementById("connectionStatus");
        const userInfo = document.getElementById("userInfo");
        const loginSection = document.getElementById("loginSection");
        const downloadSection = document.getElementById("downloadSection");

        if (data.status === "connected") {
            connectionStatus.className = "flex items-center";
            connectionStatus.innerHTML =
                `<span class="relative flex h-2.5 w-2.5 sm:h-3 sm:w-3">
                    <span class="relative inline-flex rounded-full h-2.5 w-2.5 sm:h-3 sm:w-3 bg-green-500"></span>
                </span>
                <span class="hidden lg:inline ml-1.5 text-xs font-medium text-green-400">Connected</span>`;
            const userDisplay = data.user.username ? `@${data.user.username}` : (data.user.first_name || "Account");
            if (userInfo) userInfo.textContent = userDisplay;
            const userMenuHandle = document.getElementById("userMenuHandle");
            if (userMenuHandle) userMenuHandle.textContent = userDisplay;
            loginSection.classList.add("hidden");
            downloadSection.classList.remove("hidden");

            // Load data when authenticated
            loadChannels();
            loadDiskInfo();
            loadFolders();
            loadWatchRules();
            checkWebhookStatus();
            refreshProgress();
        } else if (data.status === "not_authenticated" || data.status === "disconnected") {
            connectionStatus.className = "flex items-center";
            connectionStatus.innerHTML =
                `<span class="relative flex h-3 w-3 sm:h-4 sm:w-4">
                    <span class="animate-ping absolute inline-flex h-full w-full rounded-full bg-yellow-500 opacity-75"></span>
                    <span class="relative inline-flex rounded-full h-3 w-3 sm:h-4 sm:w-4 bg-yellow-500"></span>
                </span>
                <span class="hidden md:inline ml-2 text-xs font-medium text-yellow-400">Auth Required</span>`;
            userInfo.innerHTML = "";
            loginSection.classList.remove("hidden");
            downloadSection.classList.add("hidden");
        } else {
            connectionStatus.className = "flex items-center";
            connectionStatus.innerHTML =
                `<span class="relative flex h-3 w-3 sm:h-4 sm:w-4">
                    <span class="animate-ping absolute inline-flex h-full w-full rounded-full bg-red-500 opacity-75"></span>
                    <span class="relative inline-flex rounded-full h-3 w-3 sm:h-4 sm:w-4 bg-red-500"></span>
                </span>
                <span class="hidden md:inline ml-2 text-xs font-medium text-red-400">Connection Error</span>`;
            userInfo.innerHTML = "";
            loginSection.classList.add("hidden");
            downloadSection.classList.add("hidden");
        }
    } catch (error) {
        console.error("Status check error:", error);
    }
}

async function requestCode() {
    try {
        const response = await authFetch("/login/request-code", {
            method: "POST",
        });

        if (!response) {
            showAlert("loginAlert", "No response from server", "error");
            return;
        }

        const data = await response.json();

        if (response.ok) {
            showAlert("loginAlert", data.message, "success");

            // Clear the verification code input when requesting a new code
            document.getElementById("verificationCode").value = "";

            document.getElementById("requestCodeBtn").classList.add("hidden");
            document.getElementById("codeForm").classList.remove("hidden");
        } else {
            showAlert("loginAlert", data.detail, "error");
        }
    } catch (error) {
        showAlert("loginAlert", "Error: " + error.message, "error");
    }
}

async function verifyCode() {
    const code = document.getElementById("verificationCode").value;

    if (!code) {
        showAlert("loginAlert", "Please enter the code", "error");
        return;
    }

    try {
        const response = await authFetch("/login/verify", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ code: code }),
        });

        const data = await response.json();

        if (response.ok) {
            showAlert("loginAlert", "Login successful!", "success");
            setTimeout(() => {
                checkStatus();
                loadChannels();
            }, 1000);
        } else {
            if (
                data.detail &&
                (data.detail.includes("2FA") || data.detail.includes("password"))
            ) {
                document.getElementById("codeForm").classList.add("hidden");
                document.getElementById("passwordForm").classList.remove("hidden");
                showAlert("loginAlert", "Please enter your 2FA password", "info");
            } else {
                showAlert("loginAlert", data.detail, "error");
            }
        }
    } catch (error) {
        showAlert("loginAlert", "Error: " + error.message, "error");
    }
}

async function verify2FA() {
    const password = document.getElementById("password2fa").value;

    if (!password) {
        showAlert("loginAlert", "Please enter your password", "error");
        return;
    }

    try {
        const response = await authFetch("/login/password", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ password: password }),
        });

        const data = await response.json();

        if (response.ok) {
            showAlert("loginAlert", "Login successful!", "success");
            setTimeout(() => {
                checkStatus();
                loadChannels();
            }, 1000);
        } else {
            showAlert("loginAlert", data.detail, "error");
        }
    } catch (error) {
        showAlert("loginAlert", "Error: " + error.message, "error");
    }
}

function toggleFilters() {
    const filtersPanel = document.getElementById("filtersPanel");
    const toggleBtn = document.getElementById("toggleFiltersBtn");

    if (filtersPanel.classList.contains("hidden")) {
        filtersPanel.classList.remove("hidden");
        toggleBtn.innerHTML = '<i class="fa-solid fa-chevron-up"></i> Hide';
    } else {
        filtersPanel.classList.add("hidden");
        toggleBtn.innerHTML = '<i class="fa-solid fa-chevron-down"></i> Show';
    }
}

function clearFilters() {
    document.getElementById("searchQuery").value = "";
    const extInput = document.getElementById("fileExtension");
    if (extInput) {
        extInput.value = "";
        extInput.classList.add("hidden");
    }
    const typeSelect = document.getElementById("fileTypeSelect");
    if (typeSelect) typeSelect.value = "";
    document.getElementById("minSize").value = "";
    document.getElementById("maxSize").value = "";
}

function onDashboardFileTypeChange(val) {
    const extInput = document.getElementById("fileExtension");
    if (!extInput) return;
    const map = {
        '': '',
        'video': 'mp4, mkv, avi, mov, webm',
        'audio': 'mp3, flac, wav, m4a, aac',
        'document': 'pdf, epub, docx, txt',
        'archive': 'zip, rar, 7z, tar, gz',
        'photo': 'jpg, png, webp, gif',
    };
    if (val === 'custom') {
        extInput.classList.remove('hidden');
        extInput.value = '';
        extInput.focus();
    } else {
        extInput.classList.add('hidden');
        extInput.value = map[val] !== undefined ? map[val] : '';
    }
}

async function listFiles() {
    const channel = document.getElementById("channelUsername").value;
    const limit = document.getElementById("fileLimit").value;
    const fileType = document.getElementById("fileType").value;
    const searchQuery = document.getElementById("searchQuery").value;
    const fileExtension = document.getElementById("fileExtension").value;
    const minSize = document.getElementById("minSize").value;
    const maxSize = document.getElementById("maxSize").value;

    if (!channel) {
        showAlert("downloadAlert", "Please select a channel", "error");
        return;
    }

    setButtonLoading("scanBtn", true, "Scanning...");

    try {
        const requestBody = {
            channel_username: channel,
            limit: parseInt(limit),
            filter_type: fileType || null,
        };

        if (searchQuery) requestBody.search_query = searchQuery;
        if (fileExtension) requestBody.file_extension = fileExtension;
        if (minSize) requestBody.min_size = parseFloat(minSize) * 1024 * 1024;
        if (maxSize) requestBody.max_size = parseFloat(maxSize) * 1024 * 1024;

        const response = await authFetch("/files/list", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(requestBody),
        });

        if (!response) return; // authFetch handles showing error

        const data = await response.json();

        if (response.ok) {
            window.currentScannedFiles = data.files;
            
            // Apply sorting if a sort option is already selected
            const sortOption = document.getElementById("sortOption");
            if (sortOption && sortOption.value) {
                sortFiles(window.currentScannedFiles, sortOption.value);
            } else {
                displayFiles(window.currentScannedFiles);
            }
            
            const filterMsg = (searchQuery || fileExtension || minSize || maxSize)
                ? " (with filters applied)"
                : "";
            showAlert("downloadAlert", `Found ${data.count} files${filterMsg}`, "success");
        } else {
            showAlert("downloadAlert", data.detail, "error");
        }
    } catch (error) {
        showAlert("downloadAlert", "Error: " + error.message, "error");
    } finally {
        setButtonLoading("scanBtn", false);
    }
}

function handleSortChange(val = null) {
    const sortVal = val || document.getElementById("sortOption")?.value || document.getElementById("sortOptionMobile")?.value;
    if (sortVal && window.currentScannedFiles) {
        sortFiles(window.currentScannedFiles, sortVal);
    }
}

function sortFiles(files, option) {
    if (!files || files.length === 0) return;
    
    // Sort array in place
    files.sort((a, b) => {
        switch (option) {
            case 'oldest':
                return new Date(a.date) - new Date(b.date);
            case 'newest':
                return new Date(b.date) - new Date(a.date);
            case 'largest':
                return b.file_size - a.file_size;
            case 'smallest':
                return a.file_size - b.file_size;
            case 'alpha':
                return a.file_name.localeCompare(b.file_name);
            case 'alpha_rev':
                return b.file_name.localeCompare(a.file_name);
            default:
                return 0; // maintain original order
        }
    });
    
    displayFiles(files);
    
    // Re-select the correct option in the newly rendered HTML
    setTimeout(() => {
        const select = document.getElementById("sortOption");
        if (select) select.value = option;
        const selectMobile = document.getElementById("sortOptionMobile");
        if (selectMobile) selectMobile.value = option;
    }, 10);
}

function displayFiles(files) {
    const filesList = document.getElementById("filesList");
    const currentChannel = document.getElementById("channelUsername").value;

    // Make sure the list is visible
    filesList.classList.remove('hidden');

    if (files.length === 0) {
        filesList.innerHTML = '<p class="text-gray-400 text-center py-4">No files found matching your criteria.</p>';
        return;
    }

    let html = `
        <div class="mb-4 pb-3 border-b border-gray-700/60">
            <!-- Row 1: Files Found header + Mobile Sort dropdown -->
            <div class="flex items-center justify-between gap-3 mb-2.5">
                <h3 class="text-base sm:text-lg font-bold text-white flex items-center gap-2 italic uppercase">
                    <i class="fa-solid fa-folder-open text-yellow-400"></i> Files Found
                    <span class="text-xs font-normal font-mono text-gray-400 normal-case">(${files.length})</span>
                </h3>
                <!-- Sort Dropdown on mobile sits in the top header row -->
                <div class="relative sm:hidden">
                    <select id="sortOptionMobile" onchange="handleSortChange(this.value)" class="appearance-none bg-gray-900 border border-gray-700 text-xs text-gray-300 py-1.5 pl-2.5 pr-7 rounded-xl focus:outline-none focus:border-yellow-400 cursor-pointer shadow-[1px_1px_0px_#000]">
                        <option value="newest">Newest</option>
                        <option value="oldest">Oldest</option>
                        <option value="largest">Largest</option>
                        <option value="smallest">Smallest</option>
                        <option value="alpha">A-Z</option>
                        <option value="alpha_rev">Z-A</option>
                    </select>
                    <i class="fa-solid fa-sort absolute right-2.5 top-1/2 -translate-y-1/2 text-[10px] text-gray-500 pointer-events-none"></i>
                </div>
            </div>

            <!-- Row 2: Clean action toolbar (Grouped selection buttons + Download Selected) -->
            <div class="flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-2">
                <!-- Grouped Selection Controls (Side-by-side 50/50 on mobile) -->
                <div class="flex items-center gap-2 flex-1 sm:flex-initial">
                    <button onclick="selectAllFiles()" type="button" 
                        class="flex-1 sm:flex-initial py-1.5 px-3 rounded-xl btn-orange-hover text-xs font-semibold flex items-center justify-center gap-1.5 focus:outline-none whitespace-nowrap shadow-[1.5px_1.5px_0px_#000]">
                        <i class="fa-solid fa-check-double text-[10px]"></i> <span>Select All</span>
                    </button>
                    <button onclick="deselectAllFiles()" type="button" 
                        class="flex-1 sm:flex-initial py-1.5 px-3 rounded-xl btn-orange-hover text-xs font-semibold flex items-center justify-center gap-1.5 focus:outline-none whitespace-nowrap shadow-[1.5px_1.5px_0px_#000]">
                        <i class="fa-solid fa-xmark text-[10px]"></i> <span>Deselect</span>
                    </button>
                </div>

                <div class="flex items-center gap-2 w-full sm:w-auto">
                    <!-- Sort Dropdown on desktop -->
                    <div class="relative hidden sm:block">
                        <select id="sortOption" onchange="handleSortChange(this.value)" class="appearance-none bg-gray-900 border border-gray-700 text-xs text-gray-300 py-1.5 pl-3 pr-8 rounded-xl focus:outline-none focus:border-yellow-400 cursor-pointer shadow-[1px_1px_0px_#000]">
                            <option value="newest">Newest First</option>
                            <option value="oldest">Oldest First</option>
                            <option value="largest">Largest First</option>
                            <option value="smallest">Smallest First</option>
                            <option value="alpha">A-Z</option>
                            <option value="alpha_rev">Z-A</option>
                        </select>
                        <i class="fa-solid fa-sort absolute right-2.5 top-1/2 -translate-y-1/2 text-[10px] text-gray-500 pointer-events-none"></i>
                    </div>

                    <!-- Download Selected Button: Never wraps, clean badge -->
                    <button onclick="downloadSelected(${jsStr(currentChannel)})" id="downloadSelectedBtn" type="button" 
                        class="w-full sm:w-auto py-2 sm:py-1.5 px-3.5 rounded-xl btn-yellow-hover text-xs font-bold flex items-center justify-center gap-2 focus:outline-none whitespace-nowrap shadow-[2px_2px_0px_#000]">
                        <i class="fa-solid fa-download text-xs"></i>
                        <span>Download Selected</span>
                        <span class="px-1.5 py-0.2 rounded bg-black bg-opacity-30 text-black font-mono font-bold text-[10px] ml-0.5"><span id="selectedCount">0</span></span>
                    </button>
                </div>
            </div>
        </div>
        <div class="grid grid-cols-1 gap-3">`;

    files.forEach((file) => {
        const size =
            file.file_size > 0 ?
                (file.file_size / 1024 / 1024).toFixed(2) + " MB" :
                "N/A";
        let icon = "fa-file-lines";
        if (file.file_type === "photo") icon = "fa-file-image";
        else if (file.file_type === "video") icon = "fa-file-video";
        else if (file.file_type === "audio") icon = "fa-file-music";
        else if (file.file_type === "document") icon = "fa-file-lines";

        const isChecked = selectedFiles.has(file.file_id) ? "checked" : "";

        // Comic style file item: ink borders + hard shadow
        html += `
            <div class="bg-gray-800 bg-opacity-60 rounded-xl border-1.5 border-black flex items-center p-3 gap-2 sm:gap-4 hover:bg-yellow-500 hover:bg-opacity-10 hover:border-yellow-400 hover:shadow-[3px_3px_0px_rgba(0,0,0,1)] hover:-translate-y-0.5 shadow-[2px_2px_0px_rgba(0,0,0,0.5)] transition-all group file-item" data-file-id="${file.file_id}">
                <div class="flex items-center">
                    <input type="checkbox"
                           id="file_${file.file_id}"
                           class="file-checkbox w-5 h-5 rounded border-black text-yellow-500 focus:ring-yellow-500 focus:ring-offset-gray-900 bg-gray-900 cursor-pointer"
                           onchange="toggleFileSelection(${file.file_id})"
                           ${isChecked}>
                </div>
                <div class="w-8 h-8 sm:w-10 sm:h-10 rounded-lg bg-yellow-400 bg-opacity-10 flex items-center justify-center flex-shrink-0 border border-black shadow-[2px_2px_0px_rgba(0,0,0,0.2)]">
                    <i class="fa-solid ${icon} text-sm sm:text-lg text-yellow-400"></i>
                </div>
                <div class="flex-1 min-w-0 overflow-hidden">
                    <div class="font-medium text-gray-200 line-clamp-2 text-sm sm:text-base" title="${escapeHtml(file.file_name)}">${escapeHtml(file.file_name)}</div>
                    <div class="text-[10px] sm:text-xs text-gray-500 mt-0.5 flex items-center flex-wrap gap-x-2 gap-y-1">
                        <span class="bg-gray-700 bg-opacity-80 px-1 sm:px-1.5 rounded uppercase tracking-wide">${escapeHtml(file.file_type)}</span>
                        <span>${size}</span>
                        <span class="hidden sm:inline">•</span>
                        <span>${new Date(file.date).toLocaleDateString()}</span>
                    </div>
                </div>
                <button onclick="downloadSingle(${file.file_id}, ${jsStr(currentChannel)})" 
                    class="flex-shrink-0 p-2 sm:py-1.5 sm:px-4 rounded-xl btn-yellow-hover text-xs flex items-center gap-1.5 md:opacity-0 md:group-hover:opacity-100 md:translate-x-2 md:group-hover:translate-x-0 focus:outline-none">
                    <i class="fa-solid fa-download text-[10px]"></i> <span class="hidden sm:inline">Download</span>
                </button>
            </div>
        `;
    });

    html += "</div>";
    filesList.innerHTML = html;
    updateSelectedCount();
}

function toggleFileSelection(fileId) {
    if (selectedFiles.has(fileId)) {
        selectedFiles.delete(fileId);
    } else {
        selectedFiles.add(fileId);
    }
    updateSelectedCount();
}

function selectAllFiles() {
    const checkboxes = document.querySelectorAll(".file-checkbox");
    checkboxes.forEach((checkbox) => {
        checkbox.checked = true;
        const fileId = parseInt(checkbox.id.replace("file_", ""));
        selectedFiles.add(fileId);
    });
    updateSelectedCount();
}

function deselectAllFiles() {
    const checkboxes = document.querySelectorAll(".file-checkbox");
    checkboxes.forEach((checkbox) => {
        checkbox.checked = false;
    });
    selectedFiles.clear();
    updateSelectedCount();
}

function updateSelectedCount() {
    const countElement = document.getElementById("selectedCount");
    if (countElement) {
        countElement.textContent = selectedFiles.size;
    }
}

async function clearIndividualProgress(fileId) {
    try {
        // Clear from backend state
        const response = await authFetch(`/download/completed/${encodeURIComponent(fileId)}`, {
            method: "DELETE",
        });

        if (response && response.ok) {
            completedDownloads.delete(fileId);
            const element = document.getElementById(progressElId(fileId));
            if (element) {
                element.remove();
            }
            const cancelledElement = document.getElementById(progressElId(fileId, "cancelled"));
            if (cancelledElement) {
                cancelledElement.remove();
            }
            refreshProgress();
        } else {
            console.error("Failed to clear individual download from backend");
        }
    } catch (error) {
        console.error("Error clearing individual download:", error);
    }
}

function formatBytes(bytes) {
    if (!bytes || bytes === 0) return "0 B";
    const k = 1024;
    const sizes = ["B", "KB", "MB", "GB", "TB"];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + " " + sizes[i];
}

function formatTime(seconds) {
    if (!seconds || !isFinite(seconds) || seconds < 0) return "--:--";
    if (seconds < 60) return `${Math.floor(seconds)}s`;
    const m = Math.floor(seconds / 60);
    const s = Math.floor(seconds % 60);
    if (m < 60) return `${m}m ${s}s`;
    const h = Math.floor(m / 60);
    const remM = m % 60;
    return `${h}h ${remM}m`;
}

// Legacy progress bar creators removed - replaced by unified renderActivityRow

function getSubfolderAndOrganize() {
    const subfolderEl = document.getElementById("targetSubfolder");
    const organizeEl = document.getElementById("organizeBy");
    const subfolder = subfolderEl ? subfolderEl.value.trim() : "";
    const organize_by = organizeEl ? organizeEl.value : "";
    return {
        subfolder: subfolder || null,
        organize_by: organize_by || null
    };
}

async function downloadSelected(channel) {
    if (selectedFiles.size === 0) {
        showAlert(
            "downloadAlert",
            "Please select at least one file to download",
            "warning"
        );
        return;
    }

    setButtonLoading("downloadSelectedBtn", true, "Starting...");

    try {
        const { subfolder, organize_by } = getSubfolderAndOrganize();
        const response = await authFetch("/files/download-selected", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                channel_username: channel,
                message_ids: Array.from(selectedFiles),
                subfolder: subfolder,
                organize_by: organize_by,
            }),
        });

        if (!response) return; // authFetch handles showing error

        const data = await response.json();

        if (response.ok) {
            showAlert("downloadAlert", data.message, "success");
            document.getElementById("downloadProgress").classList.remove("hidden");
            document.getElementById("stopAllBtn").classList.remove("hidden");

            startProgressMonitoring();
        } else {
            showAlert("downloadAlert", data.detail, "error");
        }
    } catch (error) {
        showAlert("downloadAlert", "Error: " + error.message, "error");
    } finally {
        setButtonLoading("downloadSelectedBtn", false);
        // Reset selection AFTER button content is restored
        deselectAllFiles();
    }
}

async function cancelIndividualDownload(fileId) {
    try {
        const response = await authFetch(`/download/cancel/${encodeURIComponent(fileId)}`, {
            method: "POST",
        });

        if (!response) return;
        const data = await response.json();

        if (response.ok) {
            showAlert("downloadAlert", data.message, "info");

            // Removing the element immediately for better UX
            const element = document.getElementById(progressElId(fileId));
            if (element) {
                element.remove();
            }

            // We used to hide the section here, but that causes issues during transitions.
            // We'll trust updateProgressUI (which runs on a poll) to handle the global visibility.
        } else {
            showAlert("downloadAlert", data.detail || "Cancellation failed", "error");
        }
    } catch (error) {
        console.error("Cancel individual download error:", error);
        showAlert("downloadAlert", "Error: " + error.message, "error");
    }
}

async function downloadSingle(messageId, channel) {
    const fileId = `single_${messageId}`;

    try {
        showAlert("downloadAlert", "Starting download...", "info");
        document.getElementById("downloadProgress").classList.remove("hidden");
        document.getElementById("stopAllBtn").classList.remove("hidden");

        const { subfolder, organize_by } = getSubfolderAndOrganize();
        let url = `/files/download/${encodeURIComponent(messageId)}?channel_username=${encodeURIComponent(channel)}`;
        if (subfolder) {
            url += `&subfolder=${encodeURIComponent(subfolder)}`;
        }
        if (organize_by) {
            url += `&organize_by=${encodeURIComponent(organize_by)}`;
        }

        const response = await authFetch(
            url,
            {
                method: "POST",
            }
        );

        if (!response) {
            document.getElementById("stopAllBtn").classList.add("hidden");
            return;
        }

        const data = await safeJsonParse(response, 'Download single file');

        if (response.ok) {
            showAlert("downloadAlert", data.message, "success");

            // Start progress monitoring if not already running
            if (!progressMonitoringInterval) {
                startProgressMonitoring();
            }

            // The progress will be shown automatically by the progress monitoring interval
        } else {
            showAlert("downloadAlert", data.detail || "Download failed", "error");
            document.getElementById("stopAllBtn").classList.add("hidden");
        }
    } catch (error) {
        console.error("Download single error:", error);
        showAlert("downloadAlert", "Error: " + error.message, "error");
        document.getElementById("stopAllBtn").classList.add("hidden");
    }
}

async function downloadAll() {
    const channel = document.getElementById("channelUsername").value;
    const limit = document.getElementById("fileLimit").value;
    const fileType = document.getElementById("fileType").value;

    if (!channel) {
        showAlert("downloadAlert", "Please select a channel", "error");
        return;
    }

    setButtonLoading("addAllBtn", true, "Queuing...");

    try {
        const { subfolder, organize_by } = getSubfolderAndOrganize();
        const response = await authFetch("/files/download-all", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                channel_username: channel,
                limit: parseInt(limit),
                filter_type: fileType || null,
                subfolder: subfolder,
                organize_by: organize_by,
            }),
        });

        const data = await response.json();

        if (response.ok) {
            showAlert("downloadAlert", data.message, "success");

            startProgressMonitoring();
        } else {
            showAlert("downloadAlert", data.detail, "error");
        }
    } catch (error) {
        showAlert("downloadAlert", "Error: " + error.message, "error");
    } finally {
        setButtonLoading("addAllBtn", false);
        // Reset selection AFTER button content is restored
        deselectAllFiles();
    }
}

async function cleanupState() {
    try {
        const response = await authFetch("/debug/cleanup-state", {
            method: "POST",
        });

        const data = await response.json();
        if (response.ok) {
            showAlert("downloadAlert", data.message, "info");
        } else {
            showAlert("downloadAlert", data.detail || data.message, "error");
        }
    } catch (error) {
        showAlert("downloadAlert", "Error: " + error.message, "error");
    }
}

async function resetState() {
    try {
        const response = await authFetch("/debug/reset-state", {
            method: "POST",
        });

        const data = await response.json();
        if (response.ok) {
            showAlert("downloadAlert", data.message, "info");
        } else {
            showAlert("downloadAlert", data.detail || data.message, "error");
        }
    } catch (error) {
        showAlert("downloadAlert", "Error: " + error.message, "error");
    }
}

async function cancelDownload() {
    setButtonLoading("stopAllBtn", true, "Stopping...");
    try {
        const response = await authFetch("/download/cancel", {
            method: "POST",
        });

        const data = await response.json();

        if (response.ok) {
            showAlert("downloadAlert", data.message, "info");

            if (progressMonitoringInterval) {
                clearInterval(progressMonitoringInterval);
                progressMonitoringInterval = null;
            }

            selectedFiles.clear();
            updateSelectedCount();
            deselectAllFiles();

            document.getElementById("stopAllBtn")?.classList.add("hidden");
            await refreshProgress();
        } else {
            showAlert("downloadAlert", data.detail || data.message, "error");
        }
    } catch (error) {
        showAlert("downloadAlert", "Error: " + error.message, "error");
    } finally {
        setButtonLoading("stopAllBtn", false);
    }
}

let isProgressActive = false;
let wasPreviouslyActive = false;
let isPollingInProgress = false;
let lastKnownActiveFiles = new Set();

function scheduleNextProgressPoll(delayMs = null) {
    if (progressMonitoringInterval) {
        clearTimeout(progressMonitoringInterval);
        progressMonitoringInterval = null;
    }

    let delay = delayMs;
    if (delay === null) {
        if (isProgressActive) {
            delay = 1000; // Active downloading: 1s fast refresh
        } else if (document.hidden) {
            delay = 5000; // Background tab: 5s heartbeat
        } else {
            delay = 2500; // Idle visible tab: 2.5s heartbeat
        }
    }

    progressMonitoringInterval = setTimeout(pollProgress, delay);
}

async function pollProgress() {
    if (isPollingInProgress) return;
    isPollingInProgress = true;

    try {
        const response = await authFetch('/download-progress');
        if (!response) {
            scheduleNextProgressPoll(5000);
            return;
        }

        let data;
        try {
            data = await safeJsonParse(response, 'Progress monitoring');
        } catch (jsonErr) {
            scheduleNextProgressPoll(5000);
            return;
        }

        if (!data) {
            scheduleNextProgressPoll(5000);
            return;
        }

        const isScanning = Boolean(data.scanning);
        const hasQueued = Boolean(data.queue && data.queue.length > 0);
        const concurrent = data.concurrent_downloads || {};
        const concurrentCount = Object.keys(concurrent).length;
        const isCurrentlyActive = Boolean(data.active || isScanning || hasQueued || concurrentCount > 0);

        isProgressActive = isCurrentlyActive;

        // Transition: Idle -> Active (e.g. background watch rule matched incoming message)
        if (isCurrentlyActive && !wasPreviouslyActive) {
            wasPreviouslyActive = true;
            document.getElementById('downloadProgress')?.classList.remove('hidden');
            document.getElementById('stopAllBtn')?.classList.remove('hidden');
            if (data.started_at) {
                updateSessionTimer(data.started_at);
            }
        }
        // Transition: Active -> Idle (Session finished)
        else if (!isCurrentlyActive && wasPreviouslyActive) {
            wasPreviouslyActive = false;
            document.getElementById('stopAllBtn')?.classList.add('hidden');
            const completedCount = Object.keys(data.completed_downloads || {}).length;
            if (completedCount > 0) {
                showAlert("downloadAlert", `Download session complete! ${completedCount} file(s) downloaded.`, "success");
                loadDiskInfo();
            }
        }

        // Track new background downloads starting to notify user
        if (concurrentCount > 0) {
            const currentActiveIds = Object.keys(concurrent);
            const newActiveIds = currentActiveIds.filter(id => !lastKnownActiveFiles.has(id));
            if (newActiveIds.length > 0 && lastKnownActiveFiles.size > 0) {
                const newName = concurrent[newActiveIds[0]]?.name || "new file";
                showAlert("downloadAlert", `Active download started: ${escapeHtml(newName)}`, "info");
            }
            lastKnownActiveFiles = new Set(currentActiveIds);
        } else {
            lastKnownActiveFiles.clear();
        }

        // Always update the unified activity UI with live status
        updateProgressUI(data);

        // Schedule next check
        scheduleNextProgressPoll();
    } catch (error) {
        console.error('Progress check error:', error);
        scheduleNextProgressPoll(5000);
    } finally {
        isPollingInProgress = false;
    }
}

function startProgressMonitoring() {
    isProgressActive = true;
    if (progressMonitoringInterval) {
        clearTimeout(progressMonitoringInterval);
        progressMonitoringInterval = null;
    }
    pollProgress();
}

document.addEventListener('visibilitychange', () => {
    if (!document.hidden) {
        pollProgress();
    }
});

function showAlert(_elementId, message, type) {
    const toastContainer = document.getElementById("toastContainer");
    if (!toastContainer) return;

    let borderClass = "border-black";
    let typeClass = "toast-info";
    let icon = "fa-circle-info";
    let iconColor = "text-white";
    let title = "Info";

    if (type === "success") {
        typeClass = "toast-success";
        icon = "fa-circle-check";
        iconColor = "text-emerald-300";
        title = "Success";
    } else if (type === "error") {
        typeClass = "toast-error";
        icon = "fa-circle-xmark";
        iconColor = "text-rose-300";
        title = "Error";
    } else if (type === "warning") {
        typeClass = "toast-info";
        icon = "fa-triangle-exclamation";
        iconColor = "text-amber-300";
        title = "Warning";
    }

    const isDesktop = window.innerWidth >= 640;
    const animationClass = isDesktop ? 'animate-fade-in-down' : 'animate-fade-in-up';

    const toast = document.createElement("div");
    // Comic Speech Bubble design: ink borders + hard shadow + kapow animation + color coding
    toast.className = `flex flex-col px-5 py-4 comic-toast animate-kapow text-white ${typeClass} ${animationClass} pointer-events-auto mt-4 transition-all duration-300 transform z-[99999] flex-shrink-0 relative`;
    toast.style.setProperty("width", "min(400px, calc(100vw - 2rem))", "important");
    toast.style.setProperty("min-width", "min(320px, calc(100vw - 2rem))", "important");
    toast.style.setProperty("max-width", "calc(100vw - 2rem)", "important");
    toast.style.setProperty("margin-bottom", "15px", "important");

    toast.innerHTML = `
        <div class="flex items-start gap-4">
            <div class="flex-shrink-0 flex items-center justify-center w-8 h-8 rounded-full bg-white/5">
                <i class="fa-solid ${icon} ${iconColor} text-lg"></i>
            </div>
            <div class="min-w-0 flex-1">
                <div class="flex items-center justify-between mb-0.5">
                    <span class="text-[10px] font-bold ${iconColor} uppercase tracking-[0.2em] opacity-80">${title}</span>
                </div>
                <p class="toast-message text-sm text-white/90 leading-relaxed font-medium break-all"></p>
            </div>
            <button class="flex-shrink-0 ml-2 p-1 text-white/20 hover:text-white transition-colors" onclick="this.closest('.relative').remove()">
                <i class="fa-solid fa-xmark text-xs"></i>
            </button>
        </div>
    `;
    // Message text may contain untrusted server/error data — set it as text,
    // not HTML.
    toast.querySelector(".toast-message").textContent = message;

    toastContainer.appendChild(toast);

    // Auto remove after 5s
    setTimeout(() => {
        if (toast.isConnected) {
            toast.classList.add("opacity-0", "-translate-y-4");
            setTimeout(() => toast.remove(), 300);
        }
    }, 5000);
}

// Toast fade-in animations
const style = document.createElement("style");
style.innerHTML = `
    @keyframes fade-in-up {
        from { opacity: 0; transform: translateY(20px) scale(0.95); }
        to { opacity: 1; transform: translateY(0) scale(1); }
    }
    @keyframes fade-in-down {
        from { opacity: 0; transform: translateY(-20px) scale(0.95); }
        to { opacity: 1; transform: translateY(0) scale(1); }
    }
    .animate-fade-in-up {
        animation: fade-in-up 0.3s cubic-bezier(.39,.575,.565,1.000) both;
    }
    .animate-fade-in-down {
        animation: fade-in-down 0.3s cubic-bezier(.39,.575,.565,1.000) both;
    }
    .progress-fill.bg-green-500 {
        background: linear-gradient(90deg, #10b981 0%, #059669 100%) !important;
    }
`;
document.head.appendChild(style);

// Handle page visibility changes to restore progress monitoring and save battery/CPU
document.addEventListener('visibilitychange', function () {
    if (!document.hidden) {
        refreshProgress();
        loadDiskInfo();
    }
});

// --- Unified Activity & History Display ---

function setActivityFilter(filter) {
    currentActivityFilter = filter;

    const filterBtns = document.querySelectorAll('.activity-chip');
    filterBtns.forEach(btn => {
        const isCurrent = btn.id === `filterBtn-${filter}`;
        if (isCurrent) {
            btn.className = "activity-chip px-3 py-1.5 rounded-xl text-xs font-bold transition-all border border-black bg-amber-400 text-black shadow-[2px_2px_0px_#000]";
        } else {
            btn.className = "activity-chip px-2.5 py-1.5 rounded-xl text-xs font-bold transition-all border border-transparent text-gray-400 hover:text-white hover:bg-gray-800";
        }
    });

    if (lastKnownProgressData) {
        renderUnifiedActivity(lastKnownProgressData);
    }
}

function getFileIconHtml(fileName) {
    const ext = (fileName || "").split('.').pop().toLowerCase();
    if (['mp4', 'mkv', 'avi', 'mov', 'webm', 'flv'].includes(ext)) {
        return '<i class="fa-solid fa-file-video text-amber-400"></i>';
    }
    if (['mp3', 'm4a', 'flac', 'wav', 'ogg', 'opus'].includes(ext)) {
        return '<i class="fa-solid fa-file-audio text-emerald-400"></i>';
    }
    if (['jpg', 'jpeg', 'png', 'gif', 'webp'].includes(ext)) {
        return '<i class="fa-solid fa-file-image text-purple-400"></i>';
    }
    if (['zip', 'rar', '7z', 'tar', 'gz'].includes(ext)) {
        return '<i class="fa-solid fa-file-zipper text-indigo-400"></i>';
    }
    if (['pdf', 'doc', 'docx', 'txt', 'epub'].includes(ext)) {
        return '<i class="fa-solid fa-file-lines text-blue-400"></i>';
    }
    return '<i class="fa-solid fa-file text-gray-400"></i>';
}

function updateProgressUI(data) {
    if (!data) return;
    lastKnownProgressData = data;

    // Check for session change
    if (data.session_id && data.session_id !== currentSessionId) {
        currentSessionId = data.session_id;
        completedDownloads.clear();
    }

    const concurrent = data.concurrent_downloads || {};
    const queue = data.queue || [];
    const completed = data.completed_downloads || {};
    const cancelled = data.cancelled_files || {};
    const failed = data.failed_files || {};

    const activeList = Object.entries(concurrent).map(([id, item]) => ({ ...item, id, type: 'active' }));
    const queuedList = queue.filter(q => q.status === 'queued').map((q, idx) => ({ ...q, type: 'queued', queueIndex: idx }));
    const partialList = [
        ...Object.entries(cancelled).map(([id, item]) => ({ ...item, id, type: 'partial' })),
        ...queue.filter(q => q.status === 'failed').map(q => ({ ...q, type: 'failed' })),
        ...Object.entries(failed).map(([id, item]) => ({ ...item, id, type: 'failed' }))
    ];
    const completedList = Object.entries(completed)
        .map(([id, item]) => ({ ...item, id, type: 'completed' }))
        .sort((a, b) => new Date(b.completed_at || 0) - new Date(a.completed_at || 0));

    const activeCount = activeList.length;
    const queuedCount = queuedList.length;
    const partialCount = partialList.length;
    const completedCount = completedList.length;
    const totalCount = activeCount + queuedCount + partialCount + completedCount;

    // Update filter counts
    const countAll = document.getElementById('count-all');
    if (countAll) countAll.textContent = totalCount;
    const countActive = document.getElementById('count-active');
    if (countActive) countActive.textContent = activeCount;
    const countQueued = document.getElementById('count-queued');
    if (countQueued) countQueued.textContent = queuedCount;
    const countPartial = document.getElementById('count-partial');
    if (countPartial) countPartial.textContent = partialCount;
    const countCompleted = document.getElementById('count-completed');
    if (countCompleted) countCompleted.textContent = completedCount;

    const summaryEl = document.getElementById('activityTotalSummary');
    if (summaryEl) {
        summaryEl.textContent = `${totalCount} item${totalCount === 1 ? '' : 's'} (${completedCount} completed)`;
    }

    // Live indicators & Session timer
    const liveIndicator = document.getElementById('activityLiveIndicator');
    const timerWrapper = document.getElementById('sessionTimerWrapper');
    const statusIcon = document.getElementById('activityStatusIcon');
    const stopAllBtn = document.getElementById('stopAllBtn');

    const isRunning = Boolean(data.active && (activeCount > 0 || queuedCount > 0 || data.scanning));
    if (isRunning) {
        liveIndicator?.classList.remove('hidden');
        timerWrapper?.classList.remove('hidden');
        timerWrapper?.classList.add('flex');
        if (statusIcon) statusIcon.innerHTML = '<i class="fa-solid fa-spinner fa-spin text-lg text-amber-400"></i>';
        stopAllBtn?.classList.remove('hidden');
        if (data.started_at) {
            updateSessionTimer(data.started_at);
        }
    } else {
        liveIndicator?.classList.add('hidden');
        timerWrapper?.classList.add('hidden');
        timerWrapper?.classList.remove('flex');
        if (statusIcon) statusIcon.innerHTML = '<i class="fa-solid fa-list-check text-lg text-amber-400"></i>';
        stopAllBtn?.classList.add('hidden');
    }

    // Dedicated Scanning container
    const scanningContainer = document.getElementById('scanningContainer');
    if (data.scanning && scanningContainer) {
        scanningContainer.classList.remove('hidden');
        const progressBar = document.getElementById('scanningProgressBar');
        const progressText = document.getElementById('scanningProgressText');
        const scanPct = data.scan_progress || 0;
        if (progressBar) progressBar.style.width = `${scanPct}%`;
        if (progressText) progressText.textContent = `${scanPct}%`;
    } else if (scanningContainer) {
        scanningContainer.classList.add('hidden');
    }

    // Buttons visibility
    const resumeAllBtn = document.getElementById('resumeAllBtn');
    if (resumeAllBtn) {
        resumeAllBtn.classList.toggle('hidden', !(partialCount > 0 && !data.active));
    }
    const clearHistoryBtn = document.getElementById('clearHistoryBtn');
    if (clearHistoryBtn) {
        clearHistoryBtn.classList.toggle('hidden', !(completedCount > 0 || partialCount > 0));
    }
    const clearQueueBtn = document.getElementById('clearQueueBtn');
    if (clearQueueBtn) {
        clearQueueBtn.classList.toggle('hidden', !(queuedCount > 0));
    }

    renderUnifiedActivity(data, { activeList, queuedList, partialList, completedList });
}

function renderUnifiedActivity(data, preparedLists = null) {
    const container = document.getElementById('activityContainer');
    if (!container) return;

    let { activeList, queuedList, partialList, completedList } = preparedLists || {};
    if (!activeList) {
        const concurrent = data.concurrent_downloads || {};
        const queue = data.queue || [];
        const completed = data.completed_downloads || {};
        const cancelled = data.cancelled_files || {};
        const failed = data.failed_files || {};

        activeList = Object.entries(concurrent).map(([id, item]) => ({ ...item, id, type: 'active' }));
        queuedList = queue.filter(q => q.status === 'queued').map((q, idx) => ({ ...q, type: 'queued', queueIndex: idx }));
        partialList = [
            ...Object.entries(cancelled).map(([id, item]) => ({ ...item, id, type: 'partial' })),
            ...queue.filter(q => q.status === 'failed').map(q => ({ ...q, type: 'failed' })),
            ...Object.entries(failed).map(([id, item]) => ({ ...item, id, type: 'failed' }))
        ];
        completedList = Object.entries(completed)
            .map(([id, item]) => ({ ...item, id, type: 'completed' }))
            .sort((a, b) => new Date(b.completed_at || 0) - new Date(a.completed_at || 0));
    }

    // Filter items based on currentActivityFilter
    let itemsToRender = [];
    if (currentActivityFilter === 'all') {
        itemsToRender = [...activeList, ...queuedList, ...partialList, ...completedList];
    } else if (currentActivityFilter === 'active') {
        itemsToRender = activeList;
    } else if (currentActivityFilter === 'queued') {
        itemsToRender = queuedList;
    } else if (currentActivityFilter === 'partial') {
        itemsToRender = partialList;
    } else if (currentActivityFilter === 'completed') {
        itemsToRender = completedList;
    }

    if (itemsToRender.length === 0) {
        let msg = "No download activity yet";
        let sub = "Select a channel and scan files to begin downloading";
        if (currentActivityFilter === 'active') {
            msg = "No active downloads";
            sub = "Nothing is downloading right now";
        } else if (currentActivityFilter === 'queued') {
            msg = "Download queue is empty";
            sub = "Scan files and add them to download";
        } else if (currentActivityFilter === 'partial') {
            msg = "No partial or interrupted downloads";
            sub = "All interrupted files will appear here with a resume option";
        } else if (currentActivityFilter === 'completed') {
            msg = "No completed downloads in history";
            sub = "Finished downloads will be recorded here";
        }

        container.innerHTML = `
            <div id="activityEmptyState" class="text-center text-gray-500 py-12">
                <i class="fa-solid fa-inbox text-4xl mb-3 opacity-30"></i>
                <p class="text-sm font-semibold text-gray-400">${escapeHtml(msg)}</p>
                <p class="text-xs text-gray-500 mt-1">${escapeHtml(sub)}</p>
            </div>
        `;
        return;
    }

    // Remove empty state if present
    const emptyState = document.getElementById('activityEmptyState');
    if (emptyState) {
        emptyState.remove();
    }

    // Surgical reconcile to avoid flashing or resetting active transitions
    const desiredIds = new Set(itemsToRender.map(item => progressElId(item.id)));

    // 1. Remove rows no longer present
    const existingRows = container.querySelectorAll('.activity-row');
    existingRows.forEach(row => {
        if (!desiredIds.has(row.id)) {
            row.remove();
        }
    });

    // 2. Insert or update rows in order
    let prevRow = null;
    itemsToRender.forEach(item => {
        const fileId = item.id;
        const rowId = progressElId(fileId);
        let existingRow = document.getElementById(rowId);

        if (!existingRow) {
            // New row
            const tempDiv = document.createElement('div');
            tempDiv.innerHTML = renderActivityRow(item).trim();
            const newRow = tempDiv.firstElementChild;
            if (prevRow) {
                prevRow.after(newRow);
            } else {
                container.prepend(newRow);
            }
            prevRow = newRow;
        } else {
            // Row exists: check if status type changed
            const currentType = existingRow.getAttribute('data-type');
            if (currentType !== item.type) {
                const tempDiv = document.createElement('div');
                tempDiv.innerHTML = renderActivityRow(item).trim();
                const replacement = tempDiv.firstElementChild;
                existingRow.replaceWith(replacement);
                existingRow = replacement;
            } else {
                // Same type: update in-place without touching outer DOM
                updateActivityRowInPlace(existingRow, item);
            }

            // Sync DOM order
            if (prevRow) {
                if (existingRow.previousElementSibling !== prevRow) {
                    prevRow.after(existingRow);
                }
            } else {
                if (container.firstElementChild !== existingRow) {
                    container.prepend(existingRow);
                }
            }
            prevRow = existingRow;
        }
    });
}

function updateActivityRowInPlace(row, item) {
    if (item.type === 'active') {
        const pct = item.percentage || 0;
        const cur = item.progress || 0;
        const tot = item.total || 0;
        const speedText = item.speed > 0 ? `${formatBytes(item.speed)}/s` : '';
        const etaText = item.eta > 0 ? `ETA: ${formatTime(item.eta)}` : '';

        // Smooth progress bar update
        const bar = row.querySelector('.progress-bar-inner');
        if (bar) {
            bar.style.width = `${pct}%`;
        }

        // Badge update
        const badge = row.querySelector('.row-badge');
        if (badge) {
            badge.innerHTML = `<span class="px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-wider bg-yellow-500 bg-opacity-20 text-yellow-300 border border-yellow-500 border-opacity-40 flex items-center gap-1"><i class="fa-solid fa-spinner fa-spin text-[8px]"></i> Downloading ${pct}%</span>`;
        }

        // Details update (current/total bytes, speed, ETA)
        const details = row.querySelector('.row-details');
        if (details) {
            details.innerHTML = `
                <span class="text-yellow-400 font-mono font-medium">${formatBytes(cur)} / ${formatBytes(tot)}</span>
                ${speedText ? `<span class="text-gray-400 font-mono">· ${speedText}</span>` : ''}
                ${etaText ? `<span class="text-gray-400 font-mono">· ${etaText}</span>` : ''}
            `;
        }
    } else if (item.type === 'queued') {
        const qIdx = (item.queueIndex !== undefined) ? item.queueIndex + 1 : 1;
        const badge = row.querySelector('.row-badge');
        if (badge) {
            badge.innerHTML = `<span class="px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-wider bg-yellow-500 bg-opacity-20 text-yellow-300 border border-yellow-500 border-opacity-30">Queued #${qIdx}</span>`;
        }
    }
}

function renderActivityRow(item) {
    const fileId = item.id;
    const progressId = progressElId(fileId);
    const fileName = item.name || `file_${item.message_id || fileId}`;
    const fileIcon = getFileIconHtml(fileName);
    const channelName = item.channel || '';

    let badgeHtml = '';
    let detailsHtml = '';
    let actionsHtml = '';
    let trackHtml = '';

    if (item.type === 'active') {
        const pct = item.percentage || 0;
        const cur = item.progress || 0;
        const tot = item.total || 0;
        const speedText = item.speed > 0 ? `${formatBytes(item.speed)}/s` : '';
        const etaText = item.eta > 0 ? `ETA: ${formatTime(item.eta)}` : '';

        badgeHtml = `<span class="px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-wider bg-yellow-500 bg-opacity-20 text-yellow-300 border border-yellow-500 border-opacity-40 flex items-center gap-1"><i class="fa-solid fa-spinner fa-spin text-[8px]"></i> Downloading ${pct}%</span>`;

        detailsHtml = `
            <span class="text-yellow-400 font-mono font-medium">${formatBytes(cur)} / ${formatBytes(tot)}</span>
            ${speedText ? `<span class="text-gray-400 font-mono">· ${speedText}</span>` : ''}
            ${etaText ? `<span class="text-gray-400 font-mono">· ${etaText}</span>` : ''}
        `;

        actionsHtml = `
            <button onclick="cancelIndividualDownload(${jsStr(fileId)})" 
                class="w-8 h-8 rounded-lg btn-red flex items-center justify-center text-xs focus:outline-none" 
                title="Cancel Download">
                <i class="fa-solid fa-stop"></i>
            </button>
        `;

        trackHtml = `
            <div class="mt-2.5 h-3 w-full bg-gray-950 border border-black/80 rounded-full overflow-hidden relative shadow-inner">
                <div class="progress-bar-inner h-full rounded-full transition-all duration-300 ease-out" 
                     style="width: ${pct}%; background-color: #fbbf24; background-image: linear-gradient(90deg, #f59e0b, #fbbf24); box-shadow: 0 0 10px rgba(251, 191, 36, 0.45);"></div>
            </div>
        `;
    } else if (item.type === 'queued') {
        const qIdx = (item.queueIndex !== undefined) ? item.queueIndex + 1 : 1;
        badgeHtml = `<span class="px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-wider bg-yellow-500 bg-opacity-20 text-yellow-300 border border-yellow-500 border-opacity-30">Queued #${qIdx}</span>`;
        detailsHtml = `<span class="text-gray-400 italic">Waiting in line...</span>`;
        actionsHtml = `
            <button onclick="removeFromQueue(${jsStr(fileId)})" 
                class="w-8 h-8 rounded-lg btn-red flex items-center justify-center text-xs focus:outline-none" 
                title="Remove from Queue">
                <i class="fa-solid fa-trash-can"></i>
            </button>
        `;
    } else if (item.type === 'partial') {
        const pct = item.percentage || 0;
        const cur = item.progress || 0;
        const tot = item.total || 0;

        badgeHtml = `<span class="px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-wider bg-red-500 bg-opacity-20 text-red-300 border border-red-500 border-opacity-30 flex items-center gap-1"><i class="fa-solid fa-pause text-[8px]"></i> Partial ${pct}%</span>`;

        detailsHtml = `
            <span class="text-red-400 font-mono">${formatBytes(cur)} of ${formatBytes(tot)}</span>
            <span class="text-gray-500 text-[10px]">· Interrupted</span>
        `;

        actionsHtml = `
            <button onclick="retryIndividualDownload(${jsStr(fileId)})" 
                class="px-2.5 py-1 rounded-lg btn-yellow-hover text-xs font-bold flex items-center gap-1.5 focus:outline-none" 
                title="Resume download from where it stopped">
                <i class="fa-solid fa-rotate text-[10px]"></i> <span>Resume</span>
            </button>
            <button onclick="clearIndividualProgress(${jsStr(fileId)})" 
                class="w-7 h-7 rounded-lg btn-red flex items-center justify-center text-xs focus:outline-none" 
                title="Dismiss from history">
                <i class="fa-solid fa-xmark"></i>
            </button>
        `;

        trackHtml = `
            <div class="mt-2.5 h-2 w-full bg-gray-950 border border-black/80 rounded-full overflow-hidden relative shadow-inner">
                <div class="h-full rounded-full" style="width: ${pct}%; background-color: #f43f5e;"></div>
            </div>
        `;
    } else if (item.type === 'failed') {
        badgeHtml = `<span class="px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-wider bg-red-500 bg-opacity-20 text-red-300 border border-red-500 border-opacity-30 flex items-center gap-1"><i class="fa-solid fa-triangle-exclamation text-[8px]"></i> Failed</span>`;
        detailsHtml = `<span class="text-red-400 font-mono text-[10px]">Error occurred</span>`;
        actionsHtml = `
            <button onclick="retryIndividualDownload(${jsStr(fileId)})" 
                class="px-2.5 py-1 rounded-lg btn-yellow-hover text-xs font-bold flex items-center gap-1.5 focus:outline-none" 
                title="Retry download">
                <i class="fa-solid fa-rotate text-[10px]"></i> <span>Retry</span>
            </button>
            <button onclick="clearIndividualProgress(${jsStr(fileId)})" 
                class="w-7 h-7 rounded-lg btn-red flex items-center justify-center text-xs focus:outline-none" 
                title="Dismiss from history">
                <i class="fa-solid fa-xmark"></i>
            </button>
        `;
    } else if (item.type === 'completed') {
        const sizeStr = item.size ? formatBytes(item.size) : '';
        const timeStr = item.completed_at ? new Date(item.completed_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '';

        badgeHtml = `<span class="px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-wider bg-green-500 bg-opacity-20 text-green-300 border border-green-500 border-opacity-30 flex items-center gap-1"><i class="fa-solid fa-check text-[8px]"></i> Done</span>`;

        detailsHtml = `
            ${sizeStr ? `<span class="text-green-400 font-mono font-medium">${sizeStr}</span>` : ''}
            ${timeStr ? `<span class="text-gray-500 text-[10px]">· Finished at ${timeStr}</span>` : ''}
        `;

        const servePath = item.relative_path || fileName;
        const serveUrl = getFileServeUrl(servePath);
        actionsHtml = `
            <a href="${serveUrl}" onclick="this.href=getFileServeUrl(${jsStr(servePath)})" target="_blank" download="${escapeHtml(fileName)}"
                class="w-7 h-7 rounded-lg bg-gray-700/50 hover:bg-yellow-400 hover:text-black text-gray-300 flex items-center justify-center text-xs transition-all border border-black/50" 
                title="Download to Device">
                <i class="fa-solid fa-download text-[10px]"></i>
            </a>
            <button onclick="clearIndividualProgress(${jsStr(fileId)})" 
                class="w-7 h-7 rounded-lg btn-red flex items-center justify-center text-xs focus:outline-none" 
                title="Clear from history">
                <i class="fa-solid fa-xmark"></i>
            </button>
        `;
    }

    return `
        <div id="${progressId}" class="activity-row border-2 border-black rounded-xl p-3 sm:p-3.5 bg-gray-800/80 hover:bg-gray-800 transition-colors shadow-[2px_2px_0px_rgba(0,0,0,0.3)]" data-type="${item.type}">
            <div class="flex items-start justify-between gap-3">
                <div class="flex items-start gap-2.5 sm:gap-3 min-w-0 flex-1">
                    <div class="w-8 h-8 sm:w-9 sm:h-9 rounded-lg bg-gray-900 border border-black flex items-center justify-center flex-shrink-0 text-sm shadow-[1px_1px_0px_#000]">
                        ${fileIcon}
                    </div>
                    <div class="min-w-0 flex-1">
                        <div class="flex flex-wrap items-center gap-1.5 mb-1">
                            <span class="text-xs sm:text-sm font-semibold text-white line-clamp-2" title="${escapeHtml(fileName)}">
                                ${escapeHtml(fileName)}
                            </span>
                        </div>
                        <div class="flex flex-wrap items-center gap-2 text-xs text-gray-400">
                            ${channelName ? `<span class="bg-gray-900 px-1.5 py-0.5 rounded text-[10px] font-mono text-gray-400 border border-black/40 truncate max-w-[120px]">${escapeHtml(channelName)}</span>` : ''}
                            ${item.source === 'auto_watch' ? `<span class="px-1.5 py-0.5 rounded text-[10px] font-bold uppercase tracking-wider bg-blue-500/20 text-blue-300 border border-blue-500/30 flex items-center gap-1" title="Automated background download triggered by Watch Rule"><i class="fa-solid fa-tower-broadcast text-[8px]"></i> Auto-Watch</span>` : ''}
                            ${item.source === 'quick_fetch' ? `<span class="px-1.5 py-0.5 rounded text-[10px] font-bold uppercase tracking-wider bg-amber-500/20 text-amber-300 border border-amber-500/30 flex items-center gap-1" title="Direct link queued via Quick Fetch"><i class="fa-solid fa-bolt text-[8px]"></i> Quick Fetch</span>` : ''}
                            ${item.subfolder ? `<span class="px-1.5 py-0.5 rounded text-[10px] font-mono text-yellow-300 bg-yellow-500/10 border border-yellow-500/20 flex items-center gap-1" title="Saved to subfolder"><i class="fa-solid fa-folder text-[8px]"></i> ${escapeHtml(item.subfolder)}</span>` : ''}
                            <span class="row-badge">${badgeHtml}</span>
                            <span class="row-details flex items-center gap-2">${detailsHtml}</span>
                        </div>
                    </div>
                </div>

                <div class="row-actions flex items-center gap-1.5 flex-shrink-0">
                    ${actionsHtml}
                </div>
            </div>

            <div class="row-track">
                ${trackHtml}
            </div>
        </div>
    `;
}

async function removeFromQueue(id) {
    try {
        await authFetch(`/queue/${encodeURIComponent(id)}`, { method: 'DELETE' });
        refreshProgress();
    } catch (e) {
        console.error("Remove from queue failed", e);
        refreshProgress();
    }
}

async function clearQueue() {
    const confirmed = await showConfirmModal({
        title: 'Clear Queue?',
        message: 'Are you sure you want to remove all pending items from the download queue?',
        icon: 'fa-trash-can',
        iconType: 'danger',
        confirmText: 'Clear Queue',
        cancelText: 'Cancel'
    });

    if (!confirmed) return;

    try {
        await authFetch('/queue/clear', { method: 'POST' });
        refreshProgress();
        showAlert('downloadAlert', 'Queue cleared', 'info');
    } catch (e) {
        showAlert('downloadAlert', 'Failed to clear queue', 'error');
    }
}

function updateSessionTimer(startedAt) {
    const timerEl = document.getElementById('sessionTimer');
    if (!timerEl || !startedAt) return;

    try {
        const startTime = new Date(startedAt);
        const now = new Date();
        const elapsedMs = now - startTime;

        if (elapsedMs < 0) {
            timerEl.textContent = '00:00';
            return;
        }

        const totalSeconds = Math.floor(elapsedMs / 1000);
        const hours = Math.floor(totalSeconds / 3600);
        const minutes = Math.floor((totalSeconds % 3600) / 60);
        const seconds = totalSeconds % 60;

        if (hours > 0) {
            timerEl.textContent = `${hours}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
        } else {
            timerEl.textContent = `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
        }
    } catch (e) {
        console.error('Error updating session timer:', e);
    }
}

// Debug helper
window.debugRender = function () {
    const fakeData = {
        active: true,
        session_id: 'debug-session',
        total: 100,
        progress: 50,
        concurrent_downloads: {
            'debug_file_1': {
                name: 'Debug File 1.mp4',
                channel: 'Debug Channel',
                progress: 50000000,
                total: 100000000,
                percentage: 50,
                speed: 1000000, // 1 MB/s
                eta: 50,
                last_update: new Date().toISOString()
            }
        },
        completed_downloads: {},
        cancelled_files: {},
        queue: []
    };
    updateProgressUI(fakeData);
};
// About Modal Logic
async function showAbout() {
    const modal = document.getElementById('aboutModal');
    const changelogContainer = document.getElementById('changelogContent');

    // Show modal first with loading state
    modal.classList.remove('hidden');
    document.body.style.overflow = 'hidden'; // Prevent scroll

    try {
        const response = await authFetch('/about');
        if (!response) return;
        const data = await response.json();

        // Update version if needed (already set in HTML but good for sync)
        document.getElementById('aboutVersion').textContent = `v${data.version}`;
        document.getElementById('appVersion').textContent = `v${data.version}`;

        // Parse markdown-ish changelog to HTML
        const htmlContent = parseChangelog(data.changelog);
        changelogContainer.innerHTML = htmlContent;

    } catch (error) {
        console.error('Error fetching about info:', error);
        changelogContainer.innerHTML = '<p class="text-red-400">Failed to load release notes.</p>';
    }
}

function closeAboutModal() {
    const modal = document.getElementById('aboutModal');
    modal.classList.add('hidden');
    document.body.style.overflow = ''; // Restore scroll
}

function parseChangelog(markdown) {
    if (!markdown) return 'No release notes available.';

    // Very simple markdown to HTML parser for the changelog. Escape the source
    // text first so any literal HTML in the file is rendered as text.
    return escapeHtml(markdown)
        .replace(/### (.*)/g, '<h5 class="text-amber-500/80 font-bold text-xs uppercase tracking-widest mt-4 mb-2">$1</h5>')
        .replace(/## \[(.*)\] - (.*)/g, '<div class="border-l-2 border-amber-500 pl-4 mb-6"><h4 class="text-white font-bold text-lg">Version $1</h4><p class="text-[10px] text-gray-500 font-mono uppercase">Released on $2</p></div>')
        .replace(/^- (.*)/gm, '<div class="flex items-start gap-2 mb-1.5"><i class="fa-solid fa-caret-right text-amber-500/50 mt-1 text-[10px]"></i><p class="text-sm text-gray-300">$1</p></div>')
        .replace(/#(.*)/g, '') // Hide main title
        .trim();
}

// ==========================================
// Folder Picker & Management
// ==========================================

let availableFolders = [];
let currentFolderSelectTarget = 'targetSubfolder';

async function loadFolders(selectedFolderToSet = null, targetSelectId = null) {
    try {
        const response = await authFetch("/api/folders");
        if (!response || !response.ok) return;
        const data = await response.json();
        availableFolders = data.folders || [];
        populateFolderSelects(availableFolders, selectedFolderToSet, targetSelectId);
    } catch (err) {
        console.error("Error loading folders:", err);
    }
}

function populateFolderSelects(folders, selectedFolderToSet = null, targetSelectId = null) {
    const selectIds = ["targetSubfolder", "quickFetchSubfolder", "watchRuleSubfolder"];
    selectIds.forEach(id => {
        const select = document.getElementById(id);
        if (!select) return;

        const prevValue = select.value;
        const targetValue = (targetSelectId && targetSelectId === id && selectedFolderToSet !== null)
            ? selectedFolderToSet
            : (selectedFolderToSet !== null && !targetSelectId ? selectedFolderToSet : prevValue);

        select.innerHTML = '<option value="">📁 Root (/)</option>';
        folders.forEach(f => {
            const opt = document.createElement("option");
            opt.value = f;
            opt.textContent = `📁 ${f}`;
            select.appendChild(opt);
        });

        if (targetValue && !folders.includes(targetValue)) {
            const opt = document.createElement("option");
            opt.value = targetValue;
            opt.textContent = `📁 ${targetValue}`;
            select.appendChild(opt);
        }

        if (targetValue) {
            select.value = targetValue;
        } else {
            select.value = "";
        }
    });
    updateAllPathPreviews();
}

function promptCreateSubfolder(targetSelectId) {
    currentFolderSelectTarget = targetSelectId || 'targetSubfolder';
    const modal = document.getElementById('newFolderModal');
    const input = document.getElementById('newFolderNameInput');
    if (input) input.value = '';
    if (modal) modal.classList.remove('hidden');
    if (input) setTimeout(() => input.focus(), 60);
}

function closeNewFolderModal() {
    const modal = document.getElementById('newFolderModal');
    if (modal) modal.classList.add('hidden');
}

async function submitCreateSubfolder() {
    const input = document.getElementById('newFolderNameInput');
    const name = input ? input.value.trim() : '';
    if (!name) {
        showAlert('downloadAlert', 'Please enter a folder name.', 'warning');
        return;
    }

    try {
        const response = await authFetch('/api/folders', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: name })
        });
        if (!response) return;
        const data = await response.json();
        if (response.ok && data.status === 'success') {
            closeNewFolderModal();
            showAlert('downloadAlert', data.message || `Folder '${data.folder}' created.`, 'success');
            await loadFolders(data.folder, currentFolderSelectTarget);
            updateAllPathPreviews();
        } else {
            showAlert('downloadAlert', data.detail || 'Failed to create folder', 'error');
        }
    } catch (err) {
        showAlert('downloadAlert', 'Error creating folder: ' + err.message, 'error');
    }
}

// ==========================================
// Live Path Previews
// ==========================================

function updatePathPreview(subfolderSelectId, organizeSelectId, previewElId) {
    const subfolderEl = document.getElementById(subfolderSelectId);
    const organizeEl = document.getElementById(organizeSelectId);
    const previewEl = document.getElementById(previewElId);
    if (!previewEl) return;

    const sub = subfolderEl ? subfolderEl.value.trim() : '';
    const org = organizeEl ? organizeEl.value : '';

    let path = '/downloads';
    if (sub) {
        path += '/' + sub.replace(/^\/+|\/+$/g, '');
    }
    if (org === 'channel') {
        path += '/@channel';
    } else if (org === 'media_type') {
        path += '/videos';
    }
    path += '/filename.mp4';
    previewEl.textContent = path;
}

function updateAllPathPreviews() {
    updatePathPreview('targetSubfolder', 'organizeBy', 'dashboardPathPreview');
    updatePathPreview('quickFetchSubfolder', 'quickFetchOrganize', 'quickFetchPathPreview');
    updatePathPreview('watchRuleSubfolder', 'watchRuleOrganize', 'watchRulePathPreview');
}

// ==========================================
// View Switcher & Header Navigation
// ==========================================

function switchMainView(viewName) {
    const searchContent = document.getElementById("searchTabContent");
    const watchView = document.getElementById("watchRulesView");
    const navBtnSearch = document.getElementById("navBtnSearch");
    const navBtnWatch = document.getElementById("navBtnWatch");

    if (viewName === "watch") {
        searchContent?.classList.add("hidden");
        watchView?.classList.remove("hidden");

        if (navBtnWatch) {
            navBtnWatch.className = "px-3.5 sm:px-4 py-2 rounded-lg text-xs font-bold transition-all flex items-center gap-2 border border-black bg-blue-500 text-white shadow-[2px_2px_0px_#000]";
        }
        if (navBtnSearch) {
            navBtnSearch.className = "px-3.5 sm:px-4 py-2 rounded-lg text-xs font-bold transition-all flex items-center gap-2 border border-transparent text-gray-400 hover:text-white hover:bg-gray-800";
        }
        loadWatchRules();
        checkWebhookStatus();
    } else {
        watchView?.classList.add("hidden");
        searchContent?.classList.remove("hidden");

        if (navBtnSearch) {
            navBtnSearch.className = "px-3.5 sm:px-4 py-2 rounded-lg text-xs font-bold transition-all flex items-center gap-2 border border-black bg-amber-400 text-black shadow-[2px_2px_0px_#000]";
        }
        if (navBtnWatch) {
            navBtnWatch.className = "px-3.5 sm:px-4 py-2 rounded-lg text-xs font-bold transition-all flex items-center gap-2 border border-transparent text-gray-400 hover:text-white hover:bg-gray-800";
        }
    }
}

// User Menu Dropdown
function toggleUserMenu(event) {
    if (event) event.stopPropagation();
    const panel = document.getElementById("userMenuPanel");
    const chevron = document.getElementById("userMenuChevron");
    if (!panel) return;
    const isHidden = panel.classList.contains("hidden");
    if (isHidden) {
        panel.classList.remove("hidden");
        if (chevron) chevron.style.transform = "rotate(180deg)";
    } else {
        closeUserMenu();
    }
}

function closeUserMenu() {
    const panel = document.getElementById("userMenuPanel");
    const chevron = document.getElementById("userMenuChevron");
    if (panel) panel.classList.add("hidden");
    if (chevron) chevron.style.transform = "";
}

window.addEventListener("click", (e) => {
    const userMenuContainer = document.getElementById("userInfoActions");
    if (userMenuContainer && !userMenuContainer.contains(e.target)) {
        closeUserMenu();
    }
});

// ==========================================
// FEATURE 3: Direct Link / Quick Fetch Modal
// ==========================================

function updateQuickFetchLinkCount() {
    const textarea = document.getElementById("quickFetchLinks");
    const countEl = document.getElementById("quickFetchLinkCount");
    if (!textarea || !countEl) return;
    const lines = textarea.value.split("\n").map(l => l.trim()).filter(Boolean);
    countEl.textContent = `${lines.length} link${lines.length === 1 ? '' : 's'} entered`;
}

function openQuickFetchModal() {
    const modal = document.getElementById("quickFetchModal");
    if (!modal) return;
    modal.classList.remove("hidden");
    document.body.style.overflow = "hidden";
    loadFolders();
    updateAllPathPreviews();
    const resultBox = document.getElementById("quickFetchResult");
    if (resultBox) {
        resultBox.classList.add("hidden");
        resultBox.textContent = "";
    }
    const input = document.getElementById("quickFetchLinks");
    if (input) {
        input.focus();
        updateQuickFetchLinkCount();
    }
}

function closeQuickFetchModal() {
    const modal = document.getElementById("quickFetchModal");
    if (!modal) return;
    modal.classList.add("hidden");
    document.body.style.overflow = "";
}

async function submitQuickFetch() {
    const linksInput = document.getElementById("quickFetchLinks");
    const subfolderInput = document.getElementById("quickFetchSubfolder");
    const organizeInput = document.getElementById("quickFetchOrganize");
    const resultBox = document.getElementById("quickFetchResult");

    const linksText = (linksInput ? linksInput.value : "").trim();
    if (!linksText) {
        showAlert("downloadAlert", "Please enter at least one Telegram link.", "warning");
        return;
    }

    setButtonLoading("quickFetchSubmitBtn", true, "Fetching...");

    try {
        const response = await authFetch("/files/quick-fetch", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                links: linksText,
                subfolder: (subfolderInput && subfolderInput.value.trim()) || null,
                organize_by: (organizeInput && organizeInput.value) || null,
            }),
        });

        if (!response) return;
        const data = await response.json();

        if (response.ok && data.status === "success") {
            showAlert("downloadAlert", data.message, "success");
            if (resultBox) {
                resultBox.classList.remove("hidden");
                resultBox.className = "p-3.5 rounded-xl border border-green-500/40 bg-green-500/10 text-white text-xs space-y-2 animate-fade-in";
                
                let details = `<div class="font-bold text-green-300 flex items-center gap-1.5"><i class="fa-solid fa-circle-check"></i> ${escapeHtml(data.message)}</div>`;
                if (data.queued && data.queued.length > 0) {
                    details += `<div class="mt-2 space-y-1"><div class="text-[10px] uppercase font-bold text-gray-400 tracking-wider">Queued for Download:</div>`;
                    data.queued.forEach(q => {
                        details += `<div class="font-mono text-[11px] text-green-200 bg-gray-900/80 px-2.5 py-1 rounded-lg border border-green-500/20 flex items-center gap-1.5"><i class="fa-solid fa-check text-[9px] text-green-400"></i> ${escapeHtml(q)}</div>`;
                    });
                    details += `</div>`;
                }
                if (data.skipped_count > 0) {
                    details += `<div class="text-[11px] text-gray-400 mt-1"><i class="fa-solid fa-info-circle mr-1 text-amber-400"></i> ${data.skipped_count} link(s) skipped (already active or queued).</div>`;
                }
                resultBox.innerHTML = details;
            }
            if (linksInput) linksInput.value = "";
            updateQuickFetchLinkCount();
            document.getElementById("downloadProgress")?.classList.remove("hidden");
            startProgressMonitoring();
            setTimeout(() => {
                closeQuickFetchModal();
                switchMainView("search");
                document.getElementById("downloadProgress")?.scrollIntoView({ behavior: 'smooth' });
            }, 1800);
        } else {
            const err = data.detail || data.message || "Failed to process links";
            showAlert("downloadAlert", err, "error");
            if (resultBox) {
                resultBox.classList.remove("hidden");
                resultBox.className = "p-3.5 rounded-xl border border-red-500/40 bg-red-500/10 text-red-200 text-xs font-mono";
                resultBox.innerHTML = `<div class="font-bold text-red-300 flex items-center gap-1.5"><i class="fa-solid fa-circle-exclamation"></i> Error</div><div class="mt-1">${escapeHtml(err)}</div>`;
            }
        }
    } catch (e) {
        showAlert("downloadAlert", "Network error: " + e.message, "error");
    } finally {
        setButtonLoading("quickFetchSubmitBtn", false);
    }
}

// ==========================================
// FEATURE 1: Watch Rules View & Modal CRUD
// ==========================================

let currentEditingRuleId = null;
let currentRulesCache = [];

function populateWatchRuleChannels() {
    const select = document.getElementById("watchRuleChannel");
    if (!select) return;

    const currentVal = select.value;
    select.innerHTML = '<option value="">Select a channel to watch...</option>';
    if (Array.isArray(rawChannels)) {
        rawChannels.forEach(c => {
            const val = c.username ? `@${c.username}` : String(c.id);
            const opt = document.createElement("option");
            opt.value = val;
            opt.textContent = `${c.name || c.username || c.id} (${val})`;
            select.appendChild(opt);
        });
    }
    const customOpt = document.createElement("option");
    customOpt.value = "__custom__";
    customOpt.textContent = "Custom Channel (@username or Chat ID)...";
    select.appendChild(customOpt);

    if (currentVal) select.value = currentVal;
}

function onWatchRuleChannelChange(val) {
    const customInput = document.getElementById("watchRuleCustomChannel");
    if (!customInput) return;
    if (val === "__custom__") {
        customInput.classList.remove("hidden");
        customInput.focus();
    } else {
        customInput.classList.add("hidden");
    }
}

function onWatchRuleFileTypeChange(val) {
    const extInput = document.getElementById("watchRuleExtensions");
    if (!extInput) return;
    const map = {
        'all': '',
        'video': 'mp4, mkv, avi, mov, webm',
        'audio': 'mp3, flac, wav, m4a, aac',
        'document': 'pdf, zip, rar, 7z, epub',
        'photo': 'jpg, jpeg, png, webp',
    };
    if (val === 'custom') {
        extInput.classList.remove('hidden');
        extInput.value = '';
        extInput.focus();
    } else {
        extInput.classList.add('hidden');
        extInput.value = map[val] !== undefined ? map[val] : '';
    }
}

function openWatchRulesModal() {
    switchMainView("watch");
}

function closeWatchRulesModal() {
    switchMainView("search");
}

function openWatchRuleModal(ruleId = null) {
    const modal = document.getElementById("watchRuleModal");
    if (!modal) return;

    currentEditingRuleId = ruleId;
    const editIdInput = document.getElementById("watchRuleEditId");
    if (editIdInput) editIdInput.value = ruleId || "";
    
    populateWatchRuleChannels();
    loadFolders();

    const titleEl = document.getElementById("watchRuleModalTitle");
    const btnTextEl = document.getElementById("saveWatchRuleBtnText");

    if (ruleId && currentRulesCache.length > 0) {
        const rule = currentRulesCache.find(r => r.id === ruleId);
        if (rule) {
            if (titleEl) titleEl.textContent = "Edit Channel Watch Rule";
            if (btnTextEl) btnTextEl.textContent = "Update Watch Rule";

            const channelSelect = document.getElementById("watchRuleChannel");
            const customChannelInput = document.getElementById("watchRuleCustomChannel");
            const subfolderInput = document.getElementById("watchRuleSubfolder");
            const fileTypeSelect = document.getElementById("watchRuleFileType");
            const extensionsInput = document.getElementById("watchRuleExtensions");
            const keywordsInput = document.getElementById("watchRuleKeywords");
            const minSizeInput = document.getElementById("watchRuleMinSize");
            const maxSizeInput = document.getElementById("watchRuleMaxSize");
            const organizeInput = document.getElementById("watchRuleOrganize");

            let matchedOpt = false;
            if (channelSelect) {
                for (let i = 0; i < channelSelect.options.length; i++) {
                    if (channelSelect.options[i].value === rule.channel) {
                        channelSelect.value = rule.channel;
                        matchedOpt = true;
                        break;
                    }
                }
                if (!matchedOpt) {
                    channelSelect.value = "__custom__";
                    if (customChannelInput) {
                        customChannelInput.value = rule.channel;
                        customChannelInput.classList.remove("hidden");
                    }
                } else if (customChannelInput) {
                    customChannelInput.classList.add("hidden");
                }
            }

            if (subfolderInput) subfolderInput.value = rule.subfolder || "";
            if (organizeInput) organizeInput.value = rule.organize_by || "";
            if (keywordsInput) keywordsInput.value = rule.keywords || "";
            if (minSizeInput) minSizeInput.value = rule.min_size_mb !== null && rule.min_size_mb !== undefined ? rule.min_size_mb : "";
            if (maxSizeInput) maxSizeInput.value = rule.max_size_mb !== null && rule.max_size_mb !== undefined ? rule.max_size_mb : "";

            if (fileTypeSelect && extensionsInput) {
                const ext = (rule.file_extensions || "").toLowerCase().trim();
                if (!ext) {
                    fileTypeSelect.value = "all";
                    extensionsInput.classList.add("hidden");
                } else if (ext.includes("mp4") && ext.includes("mkv")) {
                    fileTypeSelect.value = "video";
                    extensionsInput.classList.add("hidden");
                } else if (ext.includes("mp3") && ext.includes("flac")) {
                    fileTypeSelect.value = "audio";
                    extensionsInput.classList.add("hidden");
                } else if (ext.includes("pdf") && ext.includes("zip")) {
                    fileTypeSelect.value = "document";
                    extensionsInput.classList.add("hidden");
                } else if (ext.includes("jpg") && ext.includes("png")) {
                    fileTypeSelect.value = "photo";
                    extensionsInput.classList.add("hidden");
                } else {
                    fileTypeSelect.value = "custom";
                    extensionsInput.value = rule.file_extensions;
                    extensionsInput.classList.remove("hidden");
                }
            }
        }
    } else {
        if (titleEl) titleEl.textContent = "Add Channel Watch Rule";
        if (btnTextEl) btnTextEl.textContent = "Save Watch Rule";

        const channelSelect = document.getElementById("watchRuleChannel");
        if (channelSelect) channelSelect.value = "";
        const customChannelInput = document.getElementById("watchRuleCustomChannel");
        if (customChannelInput) {
            customChannelInput.value = "";
            customChannelInput.classList.add("hidden");
        }
        const fileTypeSelect = document.getElementById("watchRuleFileType");
        if (fileTypeSelect) fileTypeSelect.value = "all";
        const extensionsInput = document.getElementById("watchRuleExtensions");
        if (extensionsInput) {
            extensionsInput.value = "";
            extensionsInput.classList.add("hidden");
        }
        const keywordsInput = document.getElementById("watchRuleKeywords");
        if (keywordsInput) keywordsInput.value = "";
        const minSizeInput = document.getElementById("watchRuleMinSize");
        if (minSizeInput) minSizeInput.value = "";
        const maxSizeInput = document.getElementById("watchRuleMaxSize");
        if (maxSizeInput) maxSizeInput.value = "";
        const subfolderInput = document.getElementById("watchRuleSubfolder");
        if (subfolderInput) subfolderInput.value = "";
        const organizeInput = document.getElementById("watchRuleOrganize");
        if (organizeInput) organizeInput.value = "";
    }

    updatePathPreview('watchRuleSubfolder', 'watchRuleOrganize', 'watchRulePathPreview');
    modal.classList.remove("hidden");
    document.body.style.overflow = "hidden";
}

function closeWatchRuleModal() {
    const modal = document.getElementById("watchRuleModal");
    if (!modal) return;
    modal.classList.add("hidden");
    document.body.style.overflow = "";
    currentEditingRuleId = null;
}

async function loadWatchRules() {
    const container = document.getElementById("watchRulesContainer");
    if (!container) return;

    container.innerHTML = '<div class="text-center py-8 text-gray-500 text-xs"><i class="fa-solid fa-spinner fa-spin text-lg text-amber-400 mb-2 block"></i>Loading watch rules...</div>';

    try {
        const response = await authFetch("/api/watch-rules");
        if (!response) return;
        const data = await response.json();
        const rules = data.rules || [];
        currentRulesCache = rules;

        const activeRulesCount = rules.filter(r => r.enabled).length;
        const totalMatchesCount = rules.reduce((acc, r) => acc + (r.match_count || 0), 0);

        const countBadges = ["watchRulesCountBadge", "navWatchCountBadge", "watchRulesCount", "headerWatchersCount"];
        countBadges.forEach(id => {
            const el = document.getElementById(id);
            if (el) el.textContent = id === "headerWatchersCount" || id === "navWatchCountBadge" ? activeRulesCount : rules.length;
        });

        const activeRulesMetrics = document.getElementById("metricsActiveRules");
        if (activeRulesMetrics) activeRulesMetrics.textContent = activeRulesCount;

        const totalMatchesMetrics = document.getElementById("metricsTotalMatches");
        if (totalMatchesMetrics) totalMatchesMetrics.textContent = totalMatchesCount;

        const headerBeacon = document.getElementById("headerWatchersBadge");
        if (headerBeacon) {
            headerBeacon.classList.toggle("hidden", activeRulesCount === 0);
        }

        if (rules.length === 0) {
            container.innerHTML = `
                <div class="p-8 text-center bg-gray-900/40 rounded-2xl border-2 border-dashed border-gray-800 space-y-3">
                    <div class="w-12 h-12 mx-auto rounded-2xl bg-blue-500/10 border border-blue-500/20 text-blue-400 flex items-center justify-center text-xl">
                        <i class="fa-solid fa-tower-broadcast"></i>
                    </div>
                    <div class="max-w-md mx-auto">
                        <p class="text-sm font-bold text-white">No active channel watchers yet</p>
                        <p class="text-xs text-gray-400 mt-1">Add a Telegram channel rule to automatically monitor and download incoming media in real-time as soon as it is posted.</p>
                    </div>
                    <button type="button" onclick="openWatchRuleModal()" class="px-4 py-2 rounded-xl btn-yellow-hover text-xs font-bold inline-flex items-center gap-2 mt-2">
                        <i class="fa-solid fa-plus-circle"></i> Create Your First Rule
                    </button>
                </div>
            `;
            return;
        }

        container.innerHTML = rules.map(rule => {
            const lastMatchStr = rule.last_matched_at ? new Date(rule.last_matched_at).toLocaleString() : 'Never';

            return `
            <div class="p-4 bg-gray-900/90 border-2 border-black rounded-xl hover:border-gray-700 transition-all shadow-[2px_2px_0px_#000] space-y-3">
                <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
                    <!-- Channel & Status -->
                    <div class="flex items-center gap-3 min-w-0">
                        <div class="w-10 h-10 rounded-xl bg-gray-950 border border-black flex items-center justify-center text-base text-blue-400 shadow-[1px_1px_0px_#000] flex-shrink-0">
                            <i class="fa-brands fa-telegram"></i>
                        </div>
                        <div class="min-w-0">
                            <div class="flex items-center gap-2 flex-wrap">
                                <span class="font-bold text-sm text-white font-mono truncate max-w-[220px]" title="${escapeHtml(rule.channel)}">${escapeHtml(rule.channel)}</span>
                                <span class="px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-wider ${rule.enabled ? 'bg-green-500/20 text-green-400 border border-green-500/30' : 'bg-gray-800 text-gray-400 border border-gray-700'}">
                                    ${rule.enabled ? 'Active' : 'Paused'}
                                </span>
                            </div>
                            <p class="text-[11px] text-gray-400 truncate mt-0.5">Automated 24/7 Channel Watcher</p>
                        </div>
                    </div>

                    <!-- Actions -->
                    <div class="flex items-center gap-2 self-end sm:self-center flex-shrink-0">
                        <button onclick="scanWatchRule('${escapeHtml(rule.id)}')"
                            class="px-2.5 py-1.5 bg-amber-500/15 border border-black text-amber-400 hover:bg-amber-500 hover:text-black rounded-lg text-xs font-bold transition-all shadow-[1px_1px_0px_#000] flex items-center gap-1.5"
                            title="Retroactively scan recent 50 messages now">
                            <i class="fa-solid fa-magnifying-glass text-[10px]"></i>
                            <span>Scan (50)</span>
                        </button>
                        <button onclick="openWatchRuleModal('${escapeHtml(rule.id)}')"
                            class="px-2.5 py-1.5 bg-gray-800 hover:bg-gray-700 text-gray-300 hover:text-white border border-black rounded-lg text-xs font-bold transition-all shadow-[1px_1px_0px_#000] flex items-center gap-1.5"
                            title="Edit rule settings">
                            <i class="fa-solid fa-pen text-[10px]"></i>
                            <span>Edit</span>
                        </button>
                        <button onclick="toggleWatchRule('${escapeHtml(rule.id)}', ${!rule.enabled})"
                            class="px-2.5 py-1.5 ${rule.enabled ? 'bg-yellow-500/15 text-yellow-400 hover:bg-yellow-500 hover:text-black' : 'bg-green-500/15 text-green-400 hover:bg-green-500 hover:text-black'} border border-black rounded-lg text-xs font-bold transition-all shadow-[1px_1px_0px_#000]"
                            title="${rule.enabled ? 'Pause' : 'Enable'} rule">
                            <i class="fa-solid ${rule.enabled ? 'fa-pause' : 'fa-play'} text-[10px]"></i>
                        </button>
                        <button onclick="deleteWatchRule('${escapeHtml(rule.id)}')"
                            class="w-8 h-8 rounded-lg btn-red flex items-center justify-center text-xs focus:outline-none"
                            title="Delete rule">
                            <i class="fa-solid fa-trash-can text-[10px]"></i>
                        </button>
                    </div>
                </div>

                <!-- Criteria Tag Chips -->
                <div class="flex flex-wrap items-center gap-1.5 pt-2 border-t border-gray-800 text-xs">
                    ${rule.subfolder ? `<span class="bg-yellow-500/10 border border-yellow-500/20 text-yellow-300 px-2 py-0.5 rounded text-[10px] font-mono"><i class="fa-solid fa-folder mr-1"></i>${escapeHtml(rule.subfolder)}</span>` : '<span class="bg-gray-800 text-gray-400 px-2 py-0.5 rounded text-[10px] font-mono">📁 Root</span>'}
                    ${rule.organize_by ? `<span class="bg-blue-500/10 border border-blue-500/20 text-blue-300 px-2 py-0.5 rounded text-[10px] font-mono"><i class="fa-solid fa-sitemap mr-1"></i>${escapeHtml(rule.organize_by)}</span>` : ''}
                    ${rule.file_extensions ? `<span class="bg-purple-500/10 border border-purple-500/20 text-purple-300 px-2 py-0.5 rounded text-[10px] font-mono"><i class="fa-solid fa-file mr-1"></i>${escapeHtml(rule.file_extensions)}</span>` : '<span class="bg-gray-800 text-gray-400 px-2 py-0.5 rounded text-[10px] font-mono">Any File</span>'}
                    ${rule.keywords ? `<span class="bg-green-500/10 border border-green-500/20 text-green-300 px-2 py-0.5 rounded text-[10px] font-mono"><i class="fa-solid fa-tag mr-1"></i>${escapeHtml(rule.keywords)}</span>` : ''}
                    ${rule.min_size_mb || rule.max_size_mb ? `<span class="bg-gray-950 border border-gray-800 text-gray-300 px-2 py-0.5 rounded text-[10px] font-mono"><i class="fa-solid fa-weight-hanging mr-1"></i>${rule.min_size_mb || 0} - ${rule.max_size_mb || '∞'} MB</span>` : ''}
                </div>

                <!-- Activity Stats Footer -->
                <div class="flex items-center justify-between text-[11px] text-gray-400 pt-1 border-t border-gray-800/60 font-mono">
                    <span class="flex items-center gap-1 text-gray-300">
                        <i class="fa-solid fa-circle-check text-green-400 text-[10px]"></i>
                        <span>${rule.match_count || 0} auto-download${rule.match_count === 1 ? '' : 's'}</span>
                    </span>
                    <span class="text-gray-500">Last triggered: ${escapeHtml(lastMatchStr)}</span>
                </div>
            </div>
            `;
        }).join("");
    } catch (e) {
        console.error("Error loading watch rules:", e);
        container.innerHTML = '<div class="text-center py-8 text-red-400 text-xs">Failed to load watch rules.</div>';
    }
}

async function createWatchRule() {
    const channelSelect = document.getElementById("watchRuleChannel");
    const customChannelInput = document.getElementById("watchRuleCustomChannel");
    const subfolderInput = document.getElementById("watchRuleSubfolder");
    const fileTypeSelect = document.getElementById("watchRuleFileType");
    const extensionsInput = document.getElementById("watchRuleExtensions");
    const keywordsInput = document.getElementById("watchRuleKeywords");
    const minSizeInput = document.getElementById("watchRuleMinSize");
    const maxSizeInput = document.getElementById("watchRuleMaxSize");
    const organizeInput = document.getElementById("watchRuleOrganize");

    let channel = (channelSelect ? channelSelect.value : "").trim();
    if (channel === "__custom__") {
        channel = (customChannelInput ? customChannelInput.value : "").trim();
    }
    if (!channel) {
        showAlert("downloadAlert", "Target channel is required.", "warning");
        return;
    }

    let file_extensions = "";
    if (fileTypeSelect && fileTypeSelect.value === "custom") {
        file_extensions = (extensionsInput ? extensionsInput.value : "").trim();
    } else if (fileTypeSelect && fileTypeSelect.value !== "all") {
        const map = {
            'video': 'mp4, mkv, avi, mov, webm',
            'audio': 'mp3, flac, wav, m4a, aac',
            'document': 'pdf, zip, rar, 7z, epub',
            'photo': 'jpg, jpeg, png, webp',
        };
        file_extensions = map[fileTypeSelect.value] || "";
    }

    const payload = {
        channel: channel,
        subfolder: (subfolderInput && subfolderInput.value.trim()) || null,
        file_extensions: file_extensions || null,
        keywords: (keywordsInput && keywordsInput.value.trim()) || null,
        min_size_mb: minSizeInput && minSizeInput.value ? parseFloat(minSizeInput.value) : null,
        max_size_mb: maxSizeInput && maxSizeInput.value ? parseFloat(maxSizeInput.value) : null,
        organize_by: (organizeInput && organizeInput.value) || null,
        enabled: true
    };

    setButtonLoading("saveWatchRuleBtn", true, currentEditingRuleId ? "Updating..." : "Saving...");

    try {
        const url = currentEditingRuleId 
            ? `/api/watch-rules/${encodeURIComponent(currentEditingRuleId)}`
            : "/api/watch-rules";
        const method = currentEditingRuleId ? "PUT" : "POST";

        const response = await authFetch(url, {
            method: method,
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });
        if (!response) return;
        const data = await response.json();
        if (response.ok && data.status === "success") {
            showAlert("downloadAlert", currentEditingRuleId ? "Watch rule updated successfully!" : "Watch rule added successfully!", "success");
            closeWatchRuleModal();
            loadWatchRules();
        } else {
            showAlert("downloadAlert", data.detail || data.message || "Failed to save watch rule", "error");
        }
    } catch (e) {
        showAlert("downloadAlert", "Error: " + e.message, "error");
    } finally {
        setButtonLoading("saveWatchRuleBtn", false);
    }
}

async function toggleWatchRule(ruleId, enabled) {
    try {
        const response = await authFetch(`/api/watch-rules/${encodeURIComponent(ruleId)}`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ enabled: enabled })
        });
        if (!response) return;
        loadWatchRules();
    } catch (e) {
        showAlert("downloadAlert", "Error: " + e.message, "error");
    }
}

async function deleteWatchRule(ruleId) {
    showConfirmModal(
        "Delete Watch Rule",
        "Are you sure you want to delete this channel watch rule? Background monitoring for this channel will stop immediately.",
        async () => {
            try {
                const response = await authFetch(`/api/watch-rules/${encodeURIComponent(ruleId)}`, {
                    method: "DELETE"
                });
                if (!response) return;
                showAlert("downloadAlert", "Watch rule deleted.", "info");
                loadWatchRules();
            } catch (e) {
                showAlert("downloadAlert", "Error: " + e.message, "error");
            }
        }
    );
}

async function scanWatchRule(ruleId) {
    showAlert("downloadAlert", "Scanning channel for matching files...", "info");
    try {
        const response = await authFetch(`/api/watch-rules/${encodeURIComponent(ruleId)}/scan?limit=50`, {
            method: "POST"
        });
        if (!response) return;
        const data = await response.json();
        if (response.ok && data.status === "success") {
            showAlert("downloadAlert", data.message, "success");
            loadWatchRules();
            startProgressMonitoring();
        } else {
            showAlert("downloadAlert", data.detail || data.message || "Scan failed", "error");
        }
    } catch (e) {
        showAlert("downloadAlert", "Error: " + e.message, "error");
    }
}

// ==========================================
// Webhook Notifications Modal & Testing
// ==========================================

async function checkWebhookStatus() {
    try {
        const response = await authFetch("/api/webhook/status");
        if (!response || !response.ok) return;
        const data = await response.json();
        const badge = document.getElementById("metricsWebhookStatus");
        if (badge) {
            badge.textContent = data.configured ? "Configured" : "Not Set";
            badge.className = data.configured ? "text-xs font-semibold text-green-400" : "text-xs font-semibold text-gray-500";
        }
    } catch (err) {
        console.error("Error checking webhook status:", err);
    }
}

async function openWebhookModal() {
    const modal = document.getElementById("webhookModal");
    if (!modal) return;
    modal.classList.remove("hidden");
    document.body.style.overflow = "hidden";

    const badge = document.getElementById("webhookStatusBadge");
    const display = document.getElementById("webhookUrlDisplay");
    const resultBox = document.getElementById("webhookTestResult");
    if (resultBox) {
        resultBox.classList.add("hidden");
        resultBox.textContent = "";
    }

    try {
        const response = await authFetch("/api/webhook/status");
        if (!response) return;
        const data = await response.json();
        if (data.configured) {
            if (badge) {
                badge.textContent = "Active / Configured";
                badge.className = "px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-green-500/20 text-green-400 border border-green-500/30";
            }
            if (display) {
                display.textContent = data.url;
            }
        } else {
            if (badge) {
                badge.textContent = "Not Configured";
                badge.className = "px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-yellow-500/20 text-yellow-400 border border-yellow-500/30";
            }
            if (display) {
                display.textContent = "No COMPLETION_WEBHOOK_URL set in environment variables.";
            }
        }
    } catch (err) {
        if (display) display.textContent = "Error checking status: " + err.message;
    }
}

function closeWebhookModal() {
    const modal = document.getElementById("webhookModal");
    if (!modal) return;
    modal.classList.add("hidden");
    document.body.style.overflow = "";
}

async function testWebhook() {
    const resultBox = document.getElementById("webhookTestResult");
    setButtonLoading("webhookTestBtn", true, "Sending...");

    try {
        const response = await authFetch("/api/webhook/test", { method: "POST" });
        if (!response) return;
        const data = await response.json();

        if (resultBox) {
            resultBox.classList.remove("hidden");
            if (response.ok && data.status === "success") {
                resultBox.className = "p-3 rounded-xl border border-green-500/30 bg-green-500/10 text-green-300 text-xs font-mono animate-fade-in";
                resultBox.innerHTML = `✓ ${escapeHtml(data.message)}`;
            } else {
                resultBox.className = "p-3 rounded-xl border border-red-500/30 bg-red-500/10 text-red-300 text-xs font-mono animate-fade-in";
                resultBox.innerHTML = `✕ Error: ${escapeHtml(data.detail || data.message || "Webhook test failed")}`;
            }
        }
    } catch (err) {
        if (resultBox) {
            resultBox.classList.remove("hidden");
            resultBox.className = "p-3 rounded-xl border border-red-500/30 bg-red-500/10 text-red-300 text-xs font-mono animate-fade-in";
            resultBox.textContent = `✕ Network error: ${err.message}`;
        }
    } finally {
        setButtonLoading("webhookTestBtn", false);
    }
}
