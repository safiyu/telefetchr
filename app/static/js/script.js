// telegram_downloader.js
// JavaScript logic for Telegram File Downloader UI

let statusCheckInterval;
let channelList = [];
let savePath = "";
let selectedFiles = new Set();
let progressMonitoringInterval = null;
let completedDownloads = new Map(); // Track completed downloads
let lastProgressUpdate = null; // Track last progress update time
let progressWatchdog = null; // Watchdog to detect stalled progress monitoring
let currentSessionId = null; // Track current active session ID
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

async function logout() {
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
            console.warn('Authentication token expired or invalid');
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
                console.log('Progress check failed due to expired token - monitoring will stop');
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
            console.log('Suppressing error toast for:', message);
            return;
        }
    }

    // For other errors, you might want to log them but not necessarily show a toast
    console.log('Non-JSON error caught:', event.reason);
});

// Check authentication on page load
window.addEventListener('DOMContentLoaded', () => {
    const token = getAuthToken();
    // Even if no token, we call checkStatus. 
    // If the server bypasses auth for this IP, checkStatus will work.
    checkStatus();
});

async function loadChannels() {
    try {
        const response = await authFetch("/config/channels");
        if (!response) return;
        const data = await safeJsonParse(response, 'Load channels');

        const channelList = data.channels;
        const savePath = data.save_path;

        document.getElementById("savePathText").textContent = savePath;

        const channelSelect = document.getElementById("channelUsername");
        channelSelect.innerHTML = '<option value="">Select a channel...</option>';

        channelList.forEach((channel) => {
            const option = document.createElement("option");
            option.value = channel.username || channel.id; // fallback to ID if username is missing
            const trimmedName =
                channel.name.length > 40 ?
                    channel.name.slice(0, 37) + "..." :
                    channel.name;

            option.textContent = trimmedName;
            channelSelect.appendChild(option);
        });
    } catch (error) {
        console.error("Error loading channels:", error);
    }
}

// Check for saved state on page load
async function checkSavedState() {
    try {
        console.log('Checking for saved state...');
        const response = await authFetch("/download/state");
        if (!response) return;
        const data = await safeJsonParse(response, 'Check saved state');

        console.log('Saved state response:', data);

        if (data.has_saved_state && !data.active) {
            const completedCount = data.progress || 0;
            const totalCount = data.total || 0;
            const remainingCount = totalCount - completedCount;
            const wasCancelled = data.cancelled || false;

            console.log(`Found saved state: ${completedCount}/${totalCount} completed, ${remainingCount} remaining, cancelled: ${wasCancelled}`);

            // Determine the status message and icon
            const statusIcon = wasCancelled ?
                '<i class="fa-solid fa-circle-xmark text-red-400 mr-3 text-2xl"></i>' :
                '<i class="fa-solid fa-circle-pause text-yellow-400 mr-3 text-2xl"></i>';

            const statusTitle = wasCancelled ?
                'Cancelled download session found' :
                'Previous download session found';

            // Dark theme colors
            const containerClasses = wasCancelled ?
                'bg-red-900/40 border-l-4 border-red-500' :
                'bg-yellow-900/40 border-l-4 border-yellow-500';

            const textClasses = wasCancelled ? 'text-red-200' : 'text-yellow-200';
            const subTextClasses = wasCancelled ? 'text-red-300/80' : 'text-yellow-300/80';

            // Show resume option only if there are remaining files AND it wasn't cancelled
            const showResumeButton = remainingCount > 0 && !wasCancelled;

            const resumeHtml = `
                <div id="resumeNotification" class="${containerClasses} p-4 mb-4 rounded-r-lg shadow-lg relative overflow-hidden">
                    <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
                        <div class="flex items-start gap-3">
                            <div class="flex-shrink-0 mt-0.5">${statusIcon}</div>
                            <div class="min-w-0">
                                <p class="text-sm font-semibold ${textClasses} break-words">${statusTitle}</p>
                                <div class="text-xs ${subTextClasses} mt-1 space-y-0.5">
                                    <p class="truncate"><span class="opacity-70">Channel:</span> ${data.channel || 'Unknown'}</p>
                                    <p><span class="opacity-70">Progress:</span> ${completedCount}/${totalCount} files ${remainingCount > 0 ? `(${remainingCount} left)` : ''}</p>
                                    ${wasCancelled ? '<p class="text-red-300"><i class="fa-solid fa-ban text-[10px] mr-1"></i>Cancelled by user</p>' : ''}
                                    <p class="text-xs opacity-50 font-mono mt-1">ID: ${data.session_id || 'N/A'}</p>
                                </div>
                            </div>
                        </div>
                        <div class="flex flex-col sm:flex-row gap-2 w-full sm:w-auto mt-2 sm:mt-0">
                            ${showResumeButton ? `
                                <button onclick="resumeDownload()" class="flex-1 sm:flex-none py-2 px-4 rounded-xl bg-yellow-500/20 text-yellow-500 font-medium shadow-lg shadow-yellow-900/10 hover:bg-yellow-600 hover:text-white transition-all flex items-center justify-center gap-2 text-xs sm:text-sm focus:outline-none">
                                    <i class="fa-solid fa-play"></i> Resume
                                </button>
                            ` : ''}
                            <button onclick="viewCompletedDownloads()" class="flex-1 sm:flex-none py-2 px-4 rounded-xl bg-indigo-500/20 text-indigo-400 font-medium shadow-lg shadow-indigo-900/10 hover:bg-indigo-600 hover:text-white transition-all flex items-center justify-center gap-2 text-xs sm:text-sm focus:outline-none">
                                <i class="fa-solid fa-eye"></i> View${completedCount > 0 ? ` (${completedCount})` : ''}
                            </button>
                            <button onclick="clearSavedState()" class="flex-1 sm:flex-none py-2 px-4 rounded-xl bg-gray-500/20 text-gray-400 font-medium hover:bg-gray-600 hover:text-white transition-all flex items-center justify-center gap-2 text-xs sm:text-sm focus:outline-none">
                                <i class="fa-solid fa-xmark"></i> Clear
                            </button>
                        </div>
                    </div>
                </div>
            `;

            const targetContainer = document.getElementById("downloadSection");
            if (targetContainer && !document.getElementById("resumeNotification")) {
                targetContainer.insertAdjacentHTML("afterbegin", resumeHtml);
            }

        } else if (!data.has_saved_state) {
            console.log('No saved state found');
        } else if (data.active) {
            console.log('Download is currently active');
        }

        // If download is active, restore progress monitoring
        if (data.active) {
            console.log('Restoring active download monitoring...');
            document.getElementById("downloadProgress").classList.remove("hidden");
            document.getElementById("stopAllBtn").classList.remove("hidden");
            startProgressMonitoring();
        }

        // Restore completed downloads to UI if they exist (whether active or not)
        if (data.completed_count > 0 || data.active) {
            const progressResponse = await authFetch("/download-progress");
            if (!progressResponse) return;
            const progressData = await progressResponse.json();
            updateProgressUI(progressData);
        }
    } catch (error) {
        console.error("Error checking saved state:", error);
    }
}

// Debug Panel Logic
function toggleDebugPanel() {
    const panel = document.getElementById('debugPanel');
    if (panel) {
        if (panel.classList.contains('hidden')) {
            panel.classList.remove('hidden');
            refreshDebugInfo();
        } else {
            panel.classList.add('hidden');
        }
    }
}

async function refreshDebugInfo() {
    try {
        const response = await authFetch('/debug/state');
        if (!response) return;
        const data = await response.json();

        const debugInfo = document.getElementById('debugInfo');
        if (debugInfo) {
            debugInfo.textContent = JSON.stringify(data, null, 2);
        }

        console.log('Debug State:', data);
    } catch (error) {
        console.error('Error fetching debug info:', error);
        const debugInfo = document.getElementById('debugInfo');
        if (debugInfo) {
            debugInfo.textContent = 'Error: ' + error.message;
        }
    }
}

async function viewCompletedDownloads() {
    try {
        const progressResponse = await authFetch("/download-progress");
        if (!progressResponse) return;
        const data = await progressResponse.json();
        updateProgressUI(data);

        const hasHistory = (data.completed_downloads && Object.keys(data.completed_downloads).length > 0) ||
            (data.cancelled_files && Object.keys(data.cancelled_files).length > 0);

        if (hasHistory) {
            const count = (Object.keys(data.completed_downloads || {}).length) + (Object.keys(data.cancelled_files || {}).length);
            showAlert("downloadAlert", `History: ${count} files.`, "success");
        } else {
            showAlert("downloadAlert", "No completed downloads found", "info");
        }
    } catch (error) {
        showAlert("downloadAlert", "Error loading completed downloads: " + error.message, "error");
    }
}



async function clearSavedState() {
    try {
        const response = await authFetch("/download/clear-completed", {
            method: "POST",
        });

        if (response && response.ok) {
            // Clear local tracking
            completedDownloads.clear();

            // Clear UI
            const notification = document.getElementById("resumeNotification");
            if (notification) notification.remove();

            const historyContainer = document.getElementById("historyContainer");
            const historySection = document.getElementById("historySection");
            if (historyContainer) {
                historyContainer.innerHTML = "";
                historySection?.classList.add('hidden');
            }

            // check if overall progress section should be hidden
            const statusResponse = await authFetch("/download-progress");
            if (statusResponse) {
                const status = await statusResponse.json();
                if (!status.active && (!status.queue || status.queue.length === 0)) {
                    document.getElementById("downloadProgress").classList.add("hidden");
                }
            }

            showAlert("downloadAlert", "Saved state cleared", "info");
        }
    } catch (error) {
        showAlert("downloadAlert", "Error: " + error.message, "error");
    }
}

async function clearProgress() {
    try {
        const response = await authFetch("/download/clear-completed", { method: "POST" });
        if (!response || !response.ok) throw new Error("Failed to clear progress on server");

        completedDownloads.clear();

        const historyContainer = document.getElementById("historyContainer");
        const historySection = document.getElementById("historySection");
        if (historyContainer) {
            historyContainer.innerHTML = '';
            historySection?.classList.add('hidden');
        }

        // If session not active and no other sections shown, hide the whole progress section
        const statusResponse = await authFetch("/download-progress");
        if (statusResponse) {
            const status = await statusResponse.json();
            if (!status.active && (!status.queue || status.queue.length === 0)) {
                document.getElementById("downloadProgress").classList.add("hidden");
            }
        }

        // Immediately hide the resume notification if it exists
        const resumeNotification = document.getElementById("resumeNotification");
        if (resumeNotification) {
            resumeNotification.classList.add("hidden");
            resumeNotification.remove(); // Also remove it to be sure
        }

        // Reset local session trackers
        currentSessionId = null;

        showAlert("downloadAlert", "Progress history cleared", "success");
    } catch (error) {
        showAlert("downloadAlert", "Error clearing progress: " + error.message, "error");
    }
}

async function resumeDownload() {
    try {
        // First, load the existing completed downloads into UI
        const progressResponse = await authFetch("/download-progress");
        const data = await progressResponse.json();
        updateProgressUI(data);

        // Now resume the download
        const response = await authFetch("/download/resume", {
            method: "POST",
        });

        const resumeData = await response.json();

        if (response.ok) {
            showAlert("downloadAlert", `Resuming ${resumeData.remaining || 0} files...`, "success");

            // Remove resume notification
            const notification = document.getElementById("resumeNotification");
            if (notification) notification.remove();

            // Show cancel button
            document.getElementById("stopAllBtn").classList.remove("hidden");

            // Start monitoring for new downloads
            startProgressMonitoring();
        } else {
            showAlert("downloadAlert", data.detail || data.message, "error");
        }
    } catch (error) {
        showAlert("downloadAlert", "Error: " + error.message, "error");
    }
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
            connectionStatus.className =
                "flex items-center gap-2 px-2 sm:px-3 py-1 sm:py-1.5 rounded-full bg-green-500 bg-opacity-10 border border-green-500 border-opacity-20 text-green-400 text-[10px] sm:text-sm font-medium shadow-md";
            connectionStatus.innerHTML =
                `<span class="relative flex h-1.5 w-1.5 sm:h-2 sm:w-2">
                    <span class="animate-ping absolute inline-flex h-full w-full rounded-full bg-green-400 opacity-75"></span>
                    <span class="relative inline-flex rounded-full h-1.5 w-1.5 sm:h-2 sm:w-2 bg-green-500"></span>
                </span>
                <span class="hidden md:inline">Connected</span>`;
            userInfo.innerHTML = `<p><strong>User:</strong> ${data.user.first_name
                } (@${data.user.username || "N/A"})</p>`;
            loginSection.classList.add("hidden");
            downloadSection.classList.remove("hidden");
        } else if (data.status === "not_authenticated" || data.status === "disconnected") {
            connectionStatus.className =
                "flex items-center gap-2 px-2 sm:px-3 py-1 sm:py-1.5 rounded-full bg-yellow-500 bg-opacity-10 border border-yellow-500 border-opacity-20 text-yellow-400 text-[10px] sm:text-sm font-medium shadow-md";
            connectionStatus.innerHTML =
                '<i class="fa-solid fa-circle-exclamation mr-1 sm:mr-2 text-yellow-500 animate-pulse"></i><span class="hidden md:inline">Authentication Required</span><span class="md:hidden">Auth</span>';
            userInfo.innerHTML = "";
            loginSection.classList.remove("hidden");
            downloadSection.classList.add("hidden");
        } else {
            connectionStatus.className =
                "flex items-center gap-2 px-2 sm:px-3 py-1 sm:py-1.5 rounded-full bg-red-500 bg-opacity-10 border border-red-500 border-opacity-20 text-red-400 text-[10px] sm:text-sm font-medium shadow-md";
            connectionStatus.innerHTML =
                '<i class="fa-solid fa-circle-xmark mr-1 sm:mr-2 text-red-500 animate-pulse"></i><span class="hidden md:inline">Error</span><span class="md:hidden">Err</span>';
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
    document.getElementById("fileExtension").value = "";
    document.getElementById("minSize").value = "";
    document.getElementById("maxSize").value = "";
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
            displayFiles(data.files);
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
        <div class="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-3 mb-4">
            <h3 class="text-lg font-bold text-white flex items-center gap-2 italic uppercase">
                <i class="fa-solid fa-folder-open text-amber-400"></i> Files Found
            </h3>
            <div class="flex flex-wrap gap-2 w-full sm:w-auto">
                <button onclick="selectAllFiles()" class="flex-1 sm:flex-none py-1.5 px-3 rounded-xl btn-orange-hover flex items-center justify-center gap-1.5 focus:outline-none">
                    <i class="fa-solid fa-check-double text-[10px]"></i> Select All
                </button>
                <button onclick="deselectAllFiles()" class="flex-1 sm:flex-none py-1.5 px-3 rounded-xl btn-orange-hover flex items-center justify-center gap-1.5 focus:outline-none">
                    <i class="fa-solid fa-xmark text-[10px]"></i> Deselect
                </button>
                <button onclick="downloadSelected('${currentChannel}')" id="downloadSelectedBtn" class="flex-1 sm:flex-none py-1.5 px-3 rounded-xl btn-yellow-hover text-[11px] flex items-center justify-center gap-1.5 focus:outline-none">
                    <i class="fa-solid fa-download text-[10px]"></i> Download (<span id="selectedCount">0</span>)
                </button>
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
                    <div class="font-medium text-gray-200 line-clamp-2 text-sm sm:text-base" title="${file.file_name}">${file.file_name}</div>
                    <div class="text-[10px] sm:text-xs text-gray-500 mt-0.5 flex items-center flex-wrap gap-x-2 gap-y-1">
                        <span class="bg-gray-700 bg-opacity-80 px-1 sm:px-1.5 rounded uppercase tracking-wide">${file.file_type}</span>
                        <span>${size}</span>
                        <span class="hidden sm:inline">•</span>
                        <span>${new Date(file.date).toLocaleDateString()}</span>
                    </div>
                </div>
                <button onclick="downloadSingle(${file.file_id}, '${currentChannel}')" 
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
        const response = await authFetch(`/download/completed/${fileId}`, {
            method: "DELETE",
        });

        if (response && response.ok) {
            // Clear from frontend
            completedDownloads.delete(fileId);
            const element = document.getElementById(`progress-${fileId}`);
            if (element) {
                element.remove();
            }

            // Also try removing the cancelled variant if it exists
            const cancelledElement = document.getElementById(`progress-cancelled-${fileId}`);
            if (cancelledElement) {
                cancelledElement.remove();
            }

            // If no more progress items in any section, hide the entire progress section
            const activeContainer = document.getElementById("activeDownloadContainer");
            const queueContainer = document.getElementById("queueContainer");
            const historyContainer = document.getElementById("historyContainer");

            const hasItems = (activeContainer?.children.length > 0) ||
                (queueContainer?.children.length > 0) ||
                (historyContainer?.children.length > 0);

            if (!hasItems) {
                document.getElementById("downloadProgress").classList.add("hidden");
            }

            // Update overall progress text if visible
            const overallText = document.getElementById('overallText');
            if (overallText) {
                // Fetch updated progress from backend
                const progressResponse = await authFetch("/download-progress");
                if (progressResponse && progressResponse.ok) {
                    const progressData = await progressResponse.json();
                    if (progressData.total > 0) {
                        overallText.textContent = `${progressData.progress || 0}/${progressData.total} files`;
                    }
                }
            }
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

function createProgressBar(
    fileId,
    fileName,
    isComplete = false,
    percentage = 0,
    current = 0,
    total = 0,
    retryAttempt = null,
    lastUpdate = null,
    speed = 0,
    eta = 0,
    isQueued = false
) {
    const progressId = `progress-${fileId}`;
    let retryBadge = '';
    let stallWarning = '';

    // Format speed and ETA
    const speedText = speed > 0 ? `${formatBytes(speed)}/s` : '';
    const etaText = eta > 0 ? `ETA: ${formatTime(eta)}` : '';

    if (retryAttempt && retryAttempt > 1) {
        retryBadge = `<span class="text-xs px-2 py-1 rounded bg-yellow-500 bg-opacity-20 text-yellow-300 ml-2 border border-black shadow-[2px_2px_0px_rgba(0,0,0,0.2)]">Retry ${retryAttempt}/3</span>`;
    }

    // Check if download appears stalled
    if (lastUpdate && !isComplete) {
        try {
            const lastUpdateTime = new Date(lastUpdate);
            const timeSinceUpdate = (Date.now() - lastUpdateTime) / 1000; // seconds
            if (timeSinceUpdate > 10) {
                stallWarning = `<span class="text-xs px-2 py-1 rounded bg-yellow-600 bg-opacity-20 text-yellow-600 ml-2 border border-black shadow-[2px_2px_0px_rgba(0,0,0,0.2)]"><i class="fa-solid fa-triangle-exclamation"></i> Stalled ${Math.floor(timeSinceUpdate)}s</span>`;
            }
        } catch (e) {
            console.error('Error checking stall status:', e);
        }
    }

    let html = `
        <div id="${progressId}" class="file-progress-block relative overflow-hidden group">
            <!-- Comic Panel effect background -->
            <div class="absolute inset-0 bg-gray-800 border-2 border-black rounded-xl shadow-[4px_4px_0px_rgba(0,0,0,0.4)]"></div>
            
            <div class="relative p-5 z-10">
                <div class="flex justify-between items-start mb-4">
                    <div class="flex items-center gap-4 overflow-hidden">
                        <div class="w-12 h-12 rounded-lg bg-yellow-400 bg-opacity-10 flex items-center justify-center flex-shrink-0 text-yellow-400 border-2 border-black shadow-[2px_2px_0px_rgba(0,0,0,0.2)]">
                             <i class="fa-solid fa-file-arrow-down text-xl"></i>
                        </div>
                        <div class="min-w-0">
                            <div class="file-name text-white font-medium line-clamp-2" title="${fileName}">
                                ${fileName}
                            </div>
                            <div class="flex items-center gap-2 mt-0.5">
                                ${retryBadge}
                                ${stallWarning}
                                ${isQueued ? `<span class="text-xs text-yellow-500 flex items-center gap-1 font-bold uppercase italic"><i class="fa-solid fa-hourglass-start text-[10px]"></i> Queued!</span>` : ''}
                                ${!isComplete && !isQueued && !retryBadge && !stallWarning ? `<span class="text-xs text-yellow-300 font-bold flex items-center gap-1 uppercase"><i class="fa-solid fa-bolt text-[10px]"></i> ${speedText}</span>` : ''}
                            </div>
                        </div>
                    </div>
                    
                    ${isComplete ? `
                        <button onclick="clearIndividualProgress('${fileId}')" class="w-8 h-8 flex items-center justify-center btn-red rounded-xl focus:outline-none" title="Clear History">
                            <i class="fa-solid fa-xmark text-sm"></i>
                        </button>
                    ` : `
                        <div class="flex flex-col items-end gap-1">
                            <div class="flex items-center gap-2">
                                <span class="text-lg font-bold text-white header-gradient">${isQueued ? '0' : percentage}%</span>
                                <button onclick="cancelIndividualDownload('${fileId}')" 
                                    class="w-8 h-8 flex items-center justify-center ${isQueued ? 'btn-orange-hover' : 'btn-red'} rounded-xl focus:outline-none" 
                                    title="${isQueued ? 'Remove from Queue' : 'Cancel Download'}">
                                    <i class="fa-solid ${isQueued ? 'fa-trash-can' : 'fa-stop'} text-xs"></i>
                                </button>
                            </div>
                            <div class="text-xs text-gray-400 font-medium tracking-wide">${etaText}</div>
                        </div>
                    `}
                </div>

                <!-- Progress Track -->
                <div class="h-4 w-full bg-gray-900 border-2 border-black rounded-full overflow-hidden mb-3 relative shadow-[inset_2px_2px_4px_rgba(0,0,0,0.5)]">
                    <div class="h-full transition-all duration-500 ease-out relative ${isComplete ? 'bg-green-500' : 'bg-yellow-400 progress-bar-glow'}" 
                         style="width: ${percentage}%; background-color: ${isComplete ? '' : '#fbbf24'}; border-right: ${isComplete ? 'none' : '2px solid black'};">
                    </div>
                </div>

                <div class="flex justify-between items-center text-xs text-gray-400 font-medium">
                    <span>${isQueued ? 'Awaiting start...' : `${formatBytes(current)} <span class="opacity-50 mx-1">/</span> ${formatBytes(total)}`}</span>
                    ${isComplete
            ? '<span class="text-green-400 font-bold flex items-center gap-1.5"><i class="fa-solid fa-circle-check"></i> Complete</span>'
            : isQueued
                ? '<span class="text-yellow-400 text-opacity-70 font-medium flex items-center gap-1.5"><i class="fa-solid fa-clock"></i> Queued</span>'
                : retryAttempt > 1
                    ? '<span class="text-yellow-400 font-bold flex items-center gap-1.5"><i class="fa-solid fa-spinner fa-spin"></i> Retrying...</span>'
                    : '<span class="text-blue-400 font-bold flex items-center gap-1.5"><i class="fa-solid fa-spinner fa-spin"></i> Downloading</span>'
        }
                </div>
            </div>
        </div>
    `;
    return html;
}

function createCancelledProgressBar(
    fileId,
    fileName,
    percentage = 0,
    current = 0,
    total = 0
) {
    const progressId = `progress-cancelled-${fileId}`;

    let html = `
        <div id="${progressId}" class="relative overflow-hidden group opacity-80">
            <!-- Glass effect background with red tint -->
            <div class="absolute inset-0 bg-red-900 bg-opacity-10 backdrop-blur-sm border-2 border-red-500 border-opacity-20 rounded-xl shadow-[3px_3px_0px_rgba(0,0,0,0.2)]"></div>
            
            <div class="relative p-4 z-10">
                <div class="flex justify-between items-start mb-3">
                    <div class="flex items-center gap-3 overflow-hidden">
                        <div class="w-10 h-10 rounded-lg bg-red-500 bg-opacity-10 flex items-center justify-center flex-shrink-0 text-red-400 border border-black shadow-[2px_2px_0px_rgba(0,0,0,0.2)]">
                             <i class="fa-solid fa-ban text-xl"></i>
                        </div>
                        <div class="min-w-0">
                            <div class="file-name text-gray-300 font-medium line-clamp-2 line-through decoration-red-500 decoration-opacity-50" title="${fileName}">
                                ${fileName}
                            </div>
                            <div class="flex items-center gap-2 mt-0.5">
                                <span class="text-xs text-red-400 flex items-center gap-1 font-bold uppercase"><i class="fa-solid fa-circle-stop text-[10px]"></i> Cancelled</span>
                            </div>
                        </div>
                    </div>
                    
                    <button onclick="clearIndividualProgress('${fileId}')" class="w-7 h-7 flex items-center justify-center btn-red rounded-xl focus:outline-none" title="Dismiss">
                        <i class="fa-solid fa-xmark text-xs"></i>
                    </button>
                </div>

                <!-- Progress Track -->
                <div class="h-2 w-full bg-gray-700 bg-opacity-50 border border-black rounded-full overflow-hidden mb-2">
                    <div class="h-full rounded-full bg-red-500 bg-opacity-50" style="width: ${percentage}%; border-right: 1.5px solid black;"></div>
                </div>

                <div class="flex justify-between items-center text-xs text-gray-400 font-medium">
                    <span>${formatBytes(current)} of ${formatBytes(total)}</span>
                    <span class="text-red-400">Stopped at ${percentage}%</span>
                </div>
            </div>
        </div>
    `;
    return html;
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
        const response = await authFetch("/files/download-selected", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                channel_username: channel,
                message_ids: Array.from(selectedFiles),
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
    }
}

async function cancelIndividualDownload(fileId) {
    try {
        const response = await authFetch(`/download/cancel/${fileId}`, {
            method: "POST",
        });

        if (!response) return;
        const data = await response.json();

        if (response.ok) {
            showAlert("downloadAlert", data.message, "info");

            // Removing the element immediately for better UX
            const element = document.getElementById(`progress-${fileId}`);
            if (element) {
                element.remove();
            }

            // check if no more progress items
            const activeContainer = document.getElementById("activeDownloadContainer");
            const queueContainer = document.getElementById("queueContainer");
            const historyContainer = document.getElementById("historyContainer");

            const hasItems = (activeContainer?.children.length > 0) ||
                (queueContainer?.children.length > 0) ||
                (historyContainer?.children.length > 0);

            if (!hasItems) {
                document.getElementById("downloadProgress").classList.add("hidden");
            }
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

        const response = await authFetch(
            `/files/download/${messageId}?channel_username=${channel}`,
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
        const response = await authFetch("/files/download-all", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                channel_username: channel,
                limit: parseInt(limit),
                filter_type: fileType || null,
            }),
        });

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
        setButtonLoading("addAllBtn", false);
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

            // Clear active and queue containers
            const activeContainer = document.getElementById("activeDownloadContainer");
            const queueContainer = document.getElementById("queueContainer");
            if (activeContainer) activeContainer.innerHTML = '';
            if (queueContainer) queueContainer.innerHTML = '';

            const overallText = document.getElementById("overallText");
            if (overallText) {
                overallText.textContent = "";
            }

            // Hide sections
            document.getElementById("activeSection")?.classList.add("hidden");
            document.getElementById("queueSection")?.classList.add("hidden");

            // Reset title and icon to cancelled state
            const progressTitle = document.getElementById("activeTitle");
            if (progressTitle) {
                progressTitle.innerHTML = '<i id="activeIcon" class="fa-solid fa-circle-xmark text-red-500"></i> Download Session Cancelled';
            }

            // Hide progress section if no history exists
            const historyContainer = document.getElementById("historyContainer");
            if (!historyContainer || historyContainer.children.length === 0) {
                document.getElementById("downloadProgress").classList.add("hidden");
            }

            selectedFiles.clear();
            updateSelectedCount();
            deselectAllFiles();

            document.getElementById("stopAllBtn").classList.add("hidden");
        } else {
            showAlert("downloadAlert", data.detail || data.message, "error");
        }
    } catch (error) {
        showAlert("downloadAlert", "Error: " + error.message, "error");
    } finally {
        setButtonLoading("stopAllBtn", false);
    }
}

function startProgressMonitoring() {
    if (progressMonitoringInterval) {
        console.log('Clearing existing progress monitoring interval');
        clearInterval(progressMonitoringInterval);
        progressMonitoringInterval = null;
    }

    // Clear existing watchdog
    if (progressWatchdog) {
        clearInterval(progressWatchdog);
        progressWatchdog = null;
    }

    let hasStarted = false;
    let errorCount = 0;
    const maxErrors = 5;

    console.log('Starting progress monitoring with 500ms interval');
    lastProgressUpdate = Date.now();

    // Start watchdog to detect stalled monitoring (check every 5 seconds)
    progressWatchdog = setInterval(() => {
        const timeSinceUpdate = Date.now() - lastProgressUpdate;
        if (timeSinceUpdate > 10000) { // 10 seconds without update
            console.warn(`Progress monitoring appears stalled (${timeSinceUpdate}ms since last update)`);

            // Check if download is still active
            authFetch('/download-progress').then(response => {
                if (!response) return;
                return response.json();
            }).then(data => {
                if (data && data.active && !progressMonitoringInterval) {
                    console.log('Watchdog: Restarting stalled progress monitoring');
                    startProgressMonitoring();
                }
            }).catch(err => {
                console.error('Watchdog check failed:', err);
            });
        }
    }, 5000);

    progressMonitoringInterval = setInterval(async () => {
        try {
            const response = await authFetch('/download-progress');

            if (!response) {
                console.error('No response from download-progress endpoint');
                errorCount++;
                if (errorCount >= maxErrors) {
                    console.error(`Too many errors (${errorCount}), stopping progress monitoring`);
                    clearInterval(progressMonitoringInterval);
                    progressMonitoringInterval = null;

                    // Clear watchdog
                    if (progressWatchdog) {
                        clearInterval(progressWatchdog);
                        progressWatchdog = null;
                    }

                    // Don't show error if it's likely a token issue (user will see session expired message)
                    // Only show if it seems like a genuine connection issue
                    const token = getAuthToken();
                    if (token) {
                        showAlert("downloadAlert", "Lost connection to server. Please refresh the page.", "error");
                    }
                }
                return;
            }

            let data;
            try {
                data = await safeJsonParse(response, 'Progress monitoring');
            } catch (jsonError) {
                console.error('Failed to parse progress response:', jsonError.message);
                errorCount++;
                if (errorCount >= maxErrors) {
                    console.error(`Too many JSON parsing errors (${errorCount}), stopping progress monitoring`);
                    clearInterval(progressMonitoringInterval);
                    progressMonitoringInterval = null;
                    if (progressWatchdog) {
                        clearInterval(progressWatchdog);
                        progressWatchdog = null;
                    }
                }
                return;
            }

            errorCount = 0; // Reset error count on success
            lastProgressUpdate = Date.now(); // Update last progress timestamp

            if (data.active) {
                hasStarted = true;
                updateSessionTimer(data.started_at);
            }

            // Centralized UI update
            updateProgressUI(data);

            // Stop monitoring if session is finished
            if (!data.active && hasStarted) {
                console.log('Download session finished, stopping monitor.');
                clearInterval(progressMonitoringInterval);
                progressMonitoringInterval = null;
                if (progressWatchdog) {
                    clearInterval(progressWatchdog);
                    progressWatchdog = null;
                }
                document.getElementById('stopAllBtn')?.classList.add('hidden');

                const completedCount = Object.keys(data.completed_downloads || {}).length;
                if (completedCount > 0) {
                    showAlert("downloadAlert", `Download session complete! ${completedCount} files downloaded.`, "success");
                }
                return;
            }

            // The rendering logic is now fully handled in updateProgressUI(data) above.
            // This prevents redundant DOM manipulation and ensures section-based organization.
        } catch (error) {
            console.error('Progress check error:', error);
            errorCount++;
            if (errorCount >= maxErrors) {
                console.error(`Too many errors (${errorCount}), stopping progress monitoring`);
                clearInterval(progressMonitoringInterval);
                progressMonitoringInterval = null;
                showAlert("downloadAlert", "Error monitoring download progress. Please refresh the page.", "error");
            }
        }
    }, 500);
}

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
    toast.style.setProperty("width", "400px", "important");
    toast.style.setProperty("min-width", "400px", "important");
    toast.style.setProperty("max-width", "400px", "important");
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
                <p class="text-sm text-white/90 leading-relaxed font-medium">${message}</p>
            </div>
            <button class="flex-shrink-0 ml-2 p-1 text-white/20 hover:text-white transition-colors" onclick="this.closest('.relative').remove()">
                <i class="fa-solid fa-xmark text-xs"></i>
            </button>
        </div>
    `;

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

// Handle page visibility changes to restore progress monitoring
document.addEventListener('visibilitychange', async function () {
    if (!document.hidden) {
        // Page became visible again
        console.log('Page became visible, checking download state...');

        try {
            const response = await authFetch('/download-progress');
            if (!response) return;

            const data = await response.json();

            // If there's an active download and no monitoring is running
            if (data.active && !progressMonitoringInterval) {
                console.log('Active download detected, restoring progress monitoring...');

                // Show progress section and cancel button
                document.getElementById('downloadProgress').classList.remove('hidden');
                document.getElementById('stopAllBtn').classList.remove('hidden');

                // Restore progress monitoring
                startProgressMonitoring();

                // Immediately update to show current state
                updateProgressUI(data);
            } else if (data.active && progressMonitoringInterval) {
                // Monitoring is running, just refresh the UI
                console.log('Active download and monitoring running, refreshing UI...');
                updateProgressUI(data);
            } else if (!data.active && data.completed_downloads && Object.keys(data.completed_downloads).length > 0) {
                // No active download but there are completed ones, show them
                console.log('Restoring completed downloads view...');
                document.getElementById('downloadProgress').classList.remove('hidden');
                document.getElementById('clearProgressBtn')?.classList.remove('hidden');
                updateProgressUI(data);
            }
        } catch (error) {
            console.error('Error restoring download state:', error);
        }
    } else {
        // Page became hidden
        console.log('Page became hidden, interval may be throttled by browser');
    }
});

// Helper function to update progress UI
function updateProgressUI(data) {
    if (!data) {
        console.warn('updateProgressUI: No data received');
        return;
    }
    console.log('updateProgressUI: Updating with data', {
        active: data.active,
        session_id: data.session_id,
        active_count: data.concurrent_downloads ? Object.keys(data.concurrent_downloads).length : 0,
        history_count: (data.completed_downloads ? Object.keys(data.completed_downloads).length : 0) + (data.cancelled_files ? Object.keys(data.cancelled_files).length : 0)
    });

    const activeContainer = document.getElementById('activeDownloadContainer');
    const queueList = document.getElementById('queueList'); // Replaced queueContainer
    const historyContainer = document.getElementById('historyContainer');

    const activeSection = document.getElementById('activeSection');
    const queueSection = document.getElementById('queueTabContent'); // Use tab content as section
    const historySection = document.getElementById('historySection');
    const progressSection = document.getElementById('downloadProgress');

    // Removed early return to allow partial updates even if some containers are missing

    // 1. Check for session change
    if (data.session_id && data.session_id !== currentSessionId) {
        console.log(`New session detected: ${data.session_id} (old: ${currentSessionId})`);
        currentSessionId = data.session_id;

        // Reset UI for new session
        completedDownloads.clear();
        if (activeContainer) activeContainer.innerHTML = '';
        if (queueList) queueList.innerHTML = '';
        if (historyContainer) historyContainer.innerHTML = '';

        if (progressSection) progressSection.classList.remove('hidden');
    }

    // 2. Update Header / Visibility based on active status
    const hasActive = data.active && data.concurrent_downloads && Object.keys(data.concurrent_downloads).length > 0;
    const hasHistory = (data.completed_downloads && Object.keys(data.completed_downloads).length > 0) ||
        (data.cancelled_files && Object.keys(data.cancelled_files).length > 0);

    if (hasActive || hasHistory || data.active) {
        if (progressSection) progressSection.classList.remove('hidden');
    }

    if (data.active) {
        document.getElementById('stopAllBtn')?.classList.remove('hidden');
        document.getElementById('activeIcon')?.classList.add('fa-spin');
    } else {
        document.getElementById('stopAllBtn')?.classList.add('hidden');
        document.getElementById('activeIcon')?.classList.remove('fa-spin');
    }

    // 4. Update or add completed downloads (History)
    if (data.completed_downloads && historyContainer) {
        const completedEntries = Object.entries(data.completed_downloads);
        if (completedEntries.length > 0) {
            historySection?.classList.remove('hidden');

            // Sort by completed_at descending and take last 10
            const last10 = completedEntries
                .sort((a, b) => new Date(b[1].completed_at) - new Date(a[1].completed_at))
                .slice(0, 10);

            // ALWAYS clear and re-render history to ensure correct sorted order (top 10)
            historyContainer.innerHTML = '';
            completedDownloads.clear();

            for (const [fileId, fileData] of last10) {
                completedDownloads.set(fileId, fileData.path);
                const percentage = fileData.percentage || 100;
                const html = createProgressBar(fileId, fileData.name, true, percentage, fileData.size, fileData.size);
                historyContainer.insertAdjacentHTML('beforeend', html);
            }

            // Add all cancelled downloads
            if (data.cancelled_files) {
                const cancelledEntries = Object.entries(data.cancelled_files);
                if (cancelledEntries.length > 0) {
                    // Add a divider if we also have completed ones in this same update
                    if (last10.length > 0) {
                        historyContainer.insertAdjacentHTML('beforeend',
                            '<div id="cancelled-divider" class="border-t border-gray-700/50 my-4 pt-2"><p class="text-xs text-red-400 font-semibold mb-2">Cancelled Files</p></div>');
                    }

                    for (const [fileId, fileData] of cancelledEntries) {
                        const percentage = fileData.percentage || 0;
                        const size = fileData.total || 0;
                        historyContainer.insertAdjacentHTML('beforeend',
                            createCancelledProgressBar(fileId, fileData.name, percentage, fileData.progress, size)
                        );
                    }
                }
            }
        } else if (data.cancelled_files && Object.keys(data.cancelled_files).length > 0) {
            // Only cancelled files
            historySection?.classList.remove('hidden');
            historyContainer.innerHTML = '';
            for (const [fileId, fileData] of Object.entries(data.cancelled_files)) {
                const percentage = fileData.percentage || 0;
                const size = fileData.total || 0;
                historyContainer.insertAdjacentHTML('beforeend',
                    createCancelledProgressBar(fileId, fileData.name, percentage, fileData.progress, size)
                );
            }
        } else {
            historySection?.classList.add('hidden');
        }
    }

    // 5. Update active downloads
    if (data.concurrent_downloads && activeContainer) {
        const activeIds = Object.keys(data.concurrent_downloads);

        // Remove stale active downloads that are no longer in concurrent_downloads
        const currentActiveBars = activeContainer.querySelectorAll('.file-progress-block');
        currentActiveBars.forEach(bar => {
            const id = bar.id.replace('progress-', '');
            if (!activeIds.includes(id)) {
                console.log(`Removing stale active bar: ${id}`);
                bar.remove();
            }
        });

        if (activeIds.length > 0) {
            console.log(`Rendering ${activeIds.length} active downloads`);
            activeSection?.classList.remove('hidden');

            for (const [fileId, fileData] of Object.entries(data.concurrent_downloads)) {
                // Skip if already in completed map
                if (completedDownloads.has(fileId)) continue;

                const percentage = fileData.percentage || 0;
                const existingProgress = document.getElementById(`progress-${fileId}`);
                const html = createProgressBar(
                    fileId,
                    fileData.name,
                    false,
                    percentage,
                    fileData.progress,
                    fileData.total,
                    fileData.retry_attempt,
                    fileData.last_update,
                    fileData.speed,
                    fileData.eta,
                    false // isQueued
                );

                if (existingProgress) {
                    // Update only if changed to avoid unnecessary DOM thrashing
                    if (existingProgress.outerHTML !== html) {
                        existingProgress.outerHTML = html;
                    }
                } else {
                    console.log(`Adding new active bar: ${fileId}`);
                    activeContainer.insertAdjacentHTML('beforeend', html);
                }
            }
            // Remove initializing placeholder if it exists
            const placeholder = document.getElementById('initializing-placeholder');
            if (placeholder) {
                console.log('Removing initializing placeholder');
                placeholder.remove();
            }
        } else {
            // No items in concurrent_downloads
            const hasQueuedItems = data.queue && data.queue.length > 0;
            const isFinished = data.total > 0 && data.progress >= data.total;

            if (data.active && !isFinished && hasQueuedItems) {
                console.log('Session active but no concurrent downloads. Showing placeholder.');
                activeSection?.classList.remove('hidden');
                if (!document.getElementById('initializing-placeholder')) {
                    activeContainer.innerHTML = `
                        <div id="initializing-placeholder" class="text-center py-8 animate-pulse bg-gray-800/20 rounded-2xl border border-gray-700/30">
                            <i class="fa-solid fa-circle-notch fa-spin text-indigo-400 text-3xl mb-3"></i>
                            <p class="text-white text-sm font-medium">Preparing next download...</p>
                        </div>
                    `;
                }
            } else {
                if (data.active && isFinished) {
                    console.log('Session is marked active but all files are finished. Hiding active section.');
                }
                activeSection?.classList.add('hidden');
            }
        }
    }

    // 6. Update Queue
    if (data.queue) {
        // Only sync badge and count, list rendering moved to Queue tab
        updateQueueBadge(data.queue.length);
    }

    // 7. Cleanup UI - Remove items from containers if they move between states
    // (Already handled by logic above for most cases)
}

// On page load, check status and load channels if connected
(async function () {
    console.log('TeleFetchr initializing...');
    console.log('Checking connection status...');
    await checkStatus();

    const connectionStatus = document.getElementById('connectionStatus');
    if (connectionStatus && connectionStatus.textContent.includes('Connected')) {
        console.log('Connected to Telegram, loading channels...');
        await loadChannels();
        console.log('Checking for saved download state...');
        await checkSavedState();
    } else {
        console.log('Not connected to Telegram');
    }

    console.log('TeleFetchr initialization complete');
})();

// --- Queue Management ---

let queuePollInterval = null;
let currentQueue = [];

function switchTab(tab) {
    const searchTab = document.getElementById('searchTabContent');
    const queueTab = document.getElementById('queueTabContent');
    const searchBtn = document.getElementById('tab-search');
    const queueBtn = document.getElementById('tab-queue');
    const downloadProgress = document.getElementById('downloadProgress');

    if (tab === 'search') {
        searchTab.classList.remove('hidden');
        queueTab.classList.add('hidden');

        searchBtn.classList.add('btn-gradient');
        searchBtn.classList.remove('text-gray-400', 'border-transparent');

        queueBtn.classList.remove('btn-gradient');
        queueBtn.classList.add('text-gray-400', 'border-transparent');

        // Stop queue polling
        if (queuePollInterval) {
            clearInterval(queuePollInterval);
            queuePollInterval = null;
        }

    } else if (tab === 'queue') {
        searchTab.classList.add('hidden');
        queueTab.classList.remove('hidden');

        queueBtn.classList.add('btn-gradient');
        queueBtn.classList.remove('text-gray-400', 'border-transparent');

        searchBtn.classList.remove('btn-gradient');
        searchBtn.classList.add('text-gray-400', 'border-transparent');

        // Fetch queue immediately
        fetchQueue();

        // Start polling queue
        if (!queuePollInterval) {
            queuePollInterval = setInterval(fetchQueue, 2000);
        }
    }
}

async function fetchQueue() {
    try {
        const response = await authFetch('/queue');
        if (!response) return;

        const data = await safeJsonParse(response);
        if (data.status === 'success') {
            currentQueue = data.queue || [];
            renderQueue(currentQueue);
            updateQueueBadge(currentQueue.length);
        }
    } catch (e) {
        console.error("Error fetching queue:", e);
    }
}

function updateQueueBadge(count) {
    const badge = document.getElementById('queueCountBadge');
    const totalCount = document.getElementById('queueTotalCount');

    if (badge) {
        badge.textContent = count;
        badge.classList.toggle('hidden', count === 0);
    }

    if (totalCount) {
        totalCount.textContent = `${count} items`;
    }
}

function renderQueue(queue) {
    const list = document.getElementById('queueList');
    if (!list) return;

    if (queue.length === 0) {
        list.innerHTML = `
            <div class="text-center text-gray-500 py-10">
                <i class="fa-solid fa-basket-shopping text-4xl mb-3 opacity-30"></i>
                <p>Queue is empty</p>
            </div>
        `;
        updatePaginationUI(0);
        return;
    }

    // Pagination logic
    const totalItems = queue.length;
    const totalPages = Math.ceil(totalItems / queueItemsPerPage);

    // Ensure current page is within bounds
    if (queueCurrentPage > totalPages) queueCurrentPage = Math.max(1, totalPages);

    const startIndex = (queueCurrentPage - 1) * queueItemsPerPage;
    const endIndex = Math.min(startIndex + queueItemsPerPage, totalItems);
    const paginatedItems = queue.slice(startIndex, endIndex);

    list.innerHTML = paginatedItems.map((item, index) => {
        const globalIndex = startIndex + index;
        return `
        <div class="bg-gray-800/50 hover:bg-gray-800 border border-gray-700 hover:border-gray-600 rounded-lg p-4 transition-all flex items-center gap-4 group">
            <div class="text-gray-500 font-mono text-xs w-6 text-center">${globalIndex + 1}</div>
            
            <div class="flex-shrink-0 w-10 h-10 rounded bg-indigo-500/10 flex items-center justify-center text-indigo-400">
                <i class="fa-solid fa-file"></i>
            </div>
            
            <div class="flex-grow min-w-0">
                <div class="text-sm font-medium text-white line-clamp-2" title="${item.name}">${item.name}</div>
                <div class="text-xs text-gray-400 flex items-center gap-2">
                    <span class="bg-gray-700 px-1.5 rounded text-[10px]">${item.channel}</span>
                    <span class="${getStatusColor(item.status)} capitalize">${item.status}</span>
                </div>
            </div>
            
            <div class="flex items-center gap-1.5 opacity-0 group-hover:opacity-100 transition-opacity">
                <button onclick="removeFromQueue('${item.id}')" class="ml-1 w-8 h-8 flex items-center justify-center bg-red-500/20 text-red-400 hover:bg-red-600 hover:text-white rounded-lg transition-all focus:outline-none">
                    <i class="fa-solid fa-trash-can text-[10px]"></i>
                </button>
            </div>
        </div>
    `}).join('');

    updatePaginationUI(totalItems);
}

function updatePaginationUI(totalItems) {
    const paginationEl = document.getElementById('queuePagination');
    if (!paginationEl) return;

    if (totalItems === 0) {
        paginationEl.classList.add('hidden');
        return;
    }

    paginationEl.classList.remove('hidden');

    const totalPages = Math.ceil(totalItems / queueItemsPerPage);
    const startIndex = (queueCurrentPage - 1) * queueItemsPerPage + 1;
    const endIndex = Math.min(queueCurrentPage * queueItemsPerPage, totalItems);

    document.getElementById('paginationRange').textContent = `${startIndex} - ${endIndex}`;
    document.getElementById('paginationTotal').textContent = totalItems;
    document.getElementById('currentPageNum').textContent = queueCurrentPage;
    document.getElementById('totalPageNum').textContent = totalPages;

    document.getElementById('prevPageBtn').disabled = queueCurrentPage === 1;
    document.getElementById('nextPageBtn').disabled = queueCurrentPage === totalPages;
}

function changeQueuePage(delta) {
    const totalPages = Math.ceil(currentQueue.length / queueItemsPerPage);
    const next = queueCurrentPage + delta;

    if (next >= 1 && next <= totalPages) {
        queueCurrentPage = next;
        renderQueue(currentQueue);
    }
}

function getStatusColor(status) {
    switch (status) {
        case 'downloading': return 'text-green-400 animate-pulse';
        case 'queued': return 'text-yellow-400';
        case 'failed': return 'text-red-400';
        default: return 'text-gray-400';
    }
}

async function reorderQueue(index, direction) {
    // Reorder disabled as per user request to remove drag/drop & simplify
    console.log("Reorder disabled");
}

async function removeFromQueue(id) {
    try {
        await authFetch(`/queue/${id}`, { method: 'DELETE' });
        // Optimistic update
        currentQueue = currentQueue.filter(item => item.id !== id);
        renderQueue(currentQueue);
        updateQueueBadge(currentQueue.length);
    } catch (e) {
        console.error("Remove failed", e);
        fetchQueue();
    }
}

async function clearQueue() {
    // Show confirmation modal
    const confirmed = await showConfirmModal({
        title: 'Clear Queue?',
        message: 'Are you sure you want to remove all items from the download queue?',
        icon: 'fa-trash-can',
        iconType: 'danger',
        confirmText: 'Clear Queue',
        cancelText: 'Cancel'
    });

    if (!confirmed) return;

    setButtonLoading("clearQueueBtn", true, "Clearing...");

    try {
        await authFetch('/queue/clear', { method: 'POST' });
        currentQueue = [];
        renderQueue([]);
        updateQueueBadge(0);
        showAlert('downloadAlert', 'Queue cleared', 'info');
    } catch (e) {
        showAlert('downloadAlert', 'Failed to clear queue', 'error');
    } finally {
        setButtonLoading("clearQueueBtn", false);
    }
}

async function refreshQueue() {
    const btn = document.querySelector('button[title="Refresh Queue"] i');
    if (btn) btn.classList.add('fa-spin');
    await fetchQueue();
    if (btn) btn.classList.remove('fa-spin');
}

// Session Timer
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
    console.log('Running debug render...');
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