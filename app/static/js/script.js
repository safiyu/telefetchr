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

// Authentication helpers
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

        // Set button style
        confirmBtn.className = options.confirmClass || 'btn-danger';

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
    });
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
            showAlert('downloadAlert', 'Telegram session deleted successfully. Redirecting...', 'success');

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
                showAlert('downloadAlert', 'Session expired. Please log in again.', 'warning');
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
                    showAlert('downloadAlert', 'Download timeout. Large files may take longer. The download continues in the background.', 'warning');
                } else if (response.status === 503) {
                    showAlert('downloadAlert', 'Server is busy. Please wait and try again.', 'warning');
                } else {
                    showAlert('downloadAlert', `Server error (${response.status}). Please try again.`, 'error');
                }
            }
            return null;
        }

        return response;
    } catch (error) {
        console.error(`Network error for ${url}:`, error);
        // Don't show alert for background progress checks
        if (!url.includes('/download-progress')) {
            showAlert('downloadAlert', 'Network error. Please check your connection.', 'error');
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
            const completedCount = data.completed_count || 0;
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
                <div id="resumeNotification" class="${containerClasses} p-4 mb-4 rounded-r-lg">
                    <div class="flex items-center justify-between">
                        <div class="flex items-center">
                            ${statusIcon}
                            <div>
                                <p class="text-sm font-semibold ${textClasses}">${statusTitle}</p>
                                <p class="text-xs ${subTextClasses} mt-1">
                                    <strong>Channel:</strong> ${data.channel || 'Unknown'}<br>
                                    <strong>Progress:</strong> ${completedCount}/${totalCount} files completed
                                    ${remainingCount > 0 ? `(${remainingCount} remaining)` : '(All done!)'}
                                    ${wasCancelled ? '<br><strong>Status:</strong> Cancelled by user' : ''}
                                </p>
                                <p class="text-xs text-gray-500 mt-1">Session ID: ${data.session_id || 'N/A'}</p>
                            </div>
                        </div>
                        <div class="flex gap-2">
                            ${showResumeButton ? `
                                <button onclick="resumeDownload()" class="py-2 px-4 rounded bg-yellow-600 text-white font-semibold shadow hover:bg-yellow-500 focus:outline-none transition flex items-center gap-2">
                                    <i class="fa-solid fa-play"></i> Resume Download
                                </button>
                            ` : ''}
                            <button onclick="viewCompletedDownloads()" class="py-2 px-4 rounded bg-indigo-600 text-white font-semibold shadow hover:bg-indigo-500 focus:outline-none transition flex items-center gap-2">
                                <i class="fa-solid fa-eye"></i> View${completedCount > 0 ? ` (${completedCount})` : ''}
                            </button>
                            <button onclick="clearSavedState()" class="py-2 px-4 rounded bg-gray-700 text-gray-300 font-semibold shadow hover:bg-gray-600 hover:text-white focus:outline-none transition flex items-center gap-2">
                                <i class="fa-solid fa-xmark"></i> Clear
                            </button>
                        </div>
                    </div>
                </div>
            `;

            const downloadSection = document.getElementById("downloadSection");
            if (downloadSection && !document.getElementById("resumeNotification")) {
                downloadSection.insertAdjacentHTML("afterbegin", resumeHtml);
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
            document.getElementById("cancelBtn").classList.remove("hidden");
            startProgressMonitoring();
        }

        // Restore completed downloads to UI if they exist (whether active or not)
        if (data.completed_count > 0) {
            const progressResponse = await authFetch("/download-progress");
            const progressData = await progressResponse.json();

            if (progressData.completed_downloads) {
                const progressBarsContainer = document.getElementById("progressBarsContainer");
                if (progressBarsContainer) {
                    document.getElementById("downloadProgress").classList.remove("hidden");
                    document.getElementById("clearProgressBtn")?.classList.remove("hidden");

                    for (const [fileId, fileData] of Object.entries(progressData.completed_downloads)) {
                        completedDownloads.set(fileId, fileData.path);

                        // Add completed progress bar if not exists
                        if (!document.getElementById(`progress-${fileId}`)) {
                            const percentage = fileData.percentage || 100;
                            progressBarsContainer.insertAdjacentHTML('beforeend',
                                createProgressBar(fileId, fileData.name, true, percentage, fileData.size, fileData.size)
                            );
                        }
                    }
                }
            }
        }
    } catch (error) {
        console.error("Error checking saved state:", error);
    }
}

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
        const data = await response.json();

        const debugInfo = document.getElementById('debugInfo');
        if (debugInfo) {
            debugInfo.textContent = JSON.stringify(data, null, 2);
        }

        // Also log to console
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
        const progressData = await progressResponse.json();

        // Show progress section
        document.getElementById("downloadProgress").classList.remove("hidden");
        document.getElementById("clearProgressBtn")?.classList.remove("hidden");

        const progressBarsContainer = document.getElementById("progressBarsContainer");
        if (!progressBarsContainer) return;

        // Clear the container
        progressBarsContainer.innerHTML = '';

        const hasCompleted = progressData.completed_downloads && Object.keys(progressData.completed_downloads).length > 0;
        const hasCancelled = progressData.cancelled_files && Object.keys(progressData.cancelled_files).length > 0;

        if (hasCompleted || hasCancelled) {
            // Add all completed downloads
            if (hasCompleted) {
                for (const [fileId, fileData] of Object.entries(progressData.completed_downloads)) {
                    completedDownloads.set(fileId, fileData.path);
                    const percentage = fileData.percentage || 100;
                    progressBarsContainer.insertAdjacentHTML('beforeend',
                        createProgressBar(fileId, fileData.name, true, percentage, fileData.size, fileData.size)
                    );
                }
            }

            // Add all cancelled downloads
            if (hasCancelled) {
                // Add a divider if we have both
                if (hasCompleted) {
                    progressBarsContainer.insertAdjacentHTML('beforeend',
                        '<div class="border-t border-gray-700/50 my-4 pt-2"><p class="text-xs text-red-400 font-semibold mb-2">Cancelled Files</p></div>');
                }

                for (const [fileId, fileData] of Object.entries(progressData.cancelled_files)) {
                    const percentage = fileData.percentage || 0;
                    const size = fileData.total || 0;
                    // Use a modified progress bar for cancelled items
                    progressBarsContainer.insertAdjacentHTML('beforeend',
                        createCancelledProgressBar(fileId, fileData.name, percentage, fileData.progress, size)
                    );
                }
            }

            const count = (Object.keys(progressData.completed_downloads || {}).length) + (Object.keys(progressData.cancelled_files || {}).length);
            showAlert("downloadAlert", `Showing ${count} file${count !== 1 ? 's' : ''} in history`, "success");
        } else {
            // No completed downloads - show session info instead
            const total = progressData.total || 0;
            const channel = progressData.channel || 'Unknown';
            const cancelled = progressData.cancelled || false;

            const messageHtml = `
                <div class="text-center p-8 bg-gray-800/50 rounded-lg border border-gray-700/50">
                    <i class="fa-solid fa-${cancelled ? 'circle-xmark text-red-500' : 'circle-info text-blue-500'} text-5xl mb-4"></i>
                    <h3 class="text-lg font-semibold text-gray-200 mb-2">No Completed Downloads</h3>
                    <p class="text-sm text-gray-400">
                        ${cancelled ?
                    `The download session was cancelled before any files were completed.` :
                    `No files have been downloaded yet.`
                }
                    </p>
                    ${total > 0 ? `
                        <div class="mt-4 p-3 bg-gray-900/50 rounded-lg border border-gray-700">
                            <p class="text-xs text-gray-400 mb-1"><strong>Channel:</strong> ${channel}</p>
                            <p class="text-xs text-gray-400"><strong>Total files:</strong> ${total}</p>
                        </div>
                    ` : ''}
                </div>
            `;

            progressBarsContainer.innerHTML = messageHtml;

            showAlert("downloadAlert", cancelled ?
                "Session was cancelled with no completed downloads" :
                "No completed downloads found", "info");
        }
    } catch (error) {
        showAlert("downloadAlert", "Error loading completed downloads: " + error.message, "error");
    }
}

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
        const data = await response.json();

        const debugInfo = document.getElementById('debugInfo');
        if (debugInfo) {
            debugInfo.textContent = JSON.stringify(data, null, 2);
        }

        // Also log to console
        console.log('Debug State:', data);

    } catch (error) {
        console.error('Error fetching debug info:', error);
        const debugInfo = document.getElementById('debugInfo');
        if (debugInfo) {
            debugInfo.textContent = 'Error: ' + error.message;
        }
    }
}

async function clearSavedState() {
    try {
        const response = await authFetch("/download/clear-completed", {
            method: "POST",
        });

        const data = await response.json();

        if (response.ok) {
            // Clear local tracking
            completedDownloads.clear();

            // Clear UI
            const notification = document.getElementById("resumeNotification");
            if (notification) notification.remove();

            const progressBarsContainer = document.getElementById(
                "progressBarsContainer"
            );
            if (progressBarsContainer) {
                progressBarsContainer.innerHTML = "";
            }

            document.getElementById("downloadProgress").classList.add("hidden");

            showAlert("downloadAlert", "Saved state cleared", "info");
        }
    } catch (error) {
        showAlert("downloadAlert", "Error: " + error.message, "error");
    }
}

async function clearProgress() {
    try {
        const response = await authFetch("/download/clear-completed", {
            method: "POST",
        });

        const data = await response.json();

        if (response.ok) {
            completedDownloads.clear();

            const notification = document.getElementById("resumeNotification");
            if (notification) notification.remove();

            const progressBarsContainer = document.getElementById("progressBarsContainer");
            if (progressBarsContainer) {
                progressBarsContainer.innerHTML = "";
            }

            document.getElementById("downloadProgress").classList.add("hidden");
            document.getElementById("clearProgressBtn").classList.add("hidden");

            showAlert("downloadAlert", "Progress cleared", "info");
        }
    } catch (error) {
        showAlert("downloadAlert", "Error: " + error.message, "error");
    }
}

async function resumeDownload() {
    try {
        // First, load the existing completed downloads into UI
        const progressResponse = await authFetch("/download-progress");
        const progressData = await progressResponse.json();

        // Show progress section
        document.getElementById("downloadProgress").classList.remove("hidden");

        // Display all completed downloads
        if (progressData.completed_downloads) {
            const progressBarsContainer = document.getElementById("progressBarsContainer");
            if (progressBarsContainer) {
                // Clear existing progress bars
                progressBarsContainer.innerHTML = '';

                // Add all completed downloads
                for (const [fileId, fileData] of Object.entries(progressData.completed_downloads)) {
                    completedDownloads.set(fileId, fileData.path);
                    const percentage = fileData.percentage || 100;
                    progressBarsContainer.insertAdjacentHTML('beforeend',
                        createProgressBar(fileId, fileData.name, true, percentage, fileData.size, fileData.size)
                    );
                }

                // Show clear button if we have completed downloads
                if (Object.keys(progressData.completed_downloads).length > 0) {
                    document.getElementById('clearProgressBtn').classList.remove('hidden');
                }
            }
        }

        // Now resume the download
        const response = await authFetch("/download/resume", {
            method: "POST",
        });

        const data = await response.json();

        if (response.ok) {
            showAlert("downloadAlert", `${data.message}. Resuming ${data.remaining || 0} remaining files...`, "success");

            // Remove resume notification
            const notification = document.getElementById("resumeNotification");
            if (notification) notification.remove();

            // Show cancel button
            document.getElementById("cancelBtn").classList.remove("hidden");

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
                "inline-flex items-center px-3 py-1 rounded-full text-xs font-semibold bg-gradient-to-r from-green-400 to-green-500 text-white shadow-md animate-pulse";
            connectionStatus.innerHTML =
                '<i class="fa-solid fa-circle mr-2 text-white animate-pulse"></i><span class="font-bold tracking-wide">Connected</span>';
            userInfo.innerHTML = `<p><strong>User:</strong> ${data.user.first_name
                } (@${data.user.username || "N/A"})</p>`;
            loginSection.classList.add("hidden");
            downloadSection.classList.remove("hidden");
        } else if (data.status === "not_authenticated" || data.status === "disconnected") {
            connectionStatus.className =
                "inline-flex items-center px-3 py-1 rounded-full text-xs font-semibold bg-gradient-to-r from-yellow-400 to-orange-400 text-white shadow-md";
            connectionStatus.innerHTML =
                '<i class="fa-solid fa-circle-exclamation mr-2 text-white animate-pulse"></i><span class="font-bold tracking-wide">Authentication Required</span>';
            userInfo.innerHTML = "";
            loginSection.classList.remove("hidden");
            downloadSection.classList.add("hidden");
        } else {
            connectionStatus.className =
                "inline-flex items-center px-3 py-1 rounded-full text-xs font-semibold bg-gradient-to-r from-red-400 to-pink-500 text-white shadow-md";
            connectionStatus.innerHTML =
                '<i class="fa-solid fa-circle-xmark mr-2 text-white animate-pulse"></i><span class="font-bold tracking-wide">Error</span>';
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
        <div class="flex justify-between items-center mb-4">
            <h3 class="text-lg font-semibold text-white flex items-center gap-2">
                <i class="fa-solid fa-folder-open text-indigo-400"></i> Files Found
            </h3>
            <div class="flex gap-2">
                <button onclick="selectAllFiles()" class="py-1.5 px-3 rounded-lg bg-gray-700 text-gray-300 text-xs font-semibold hover:bg-indigo-600 hover:text-white transition flex items-center gap-1">
                    <i class="fa-solid fa-check-double"></i> Select All
                </button>
                <button onclick="deselectAllFiles()" class="py-1.5 px-3 rounded-lg bg-gray-700 text-gray-300 text-xs font-semibold hover:bg-gray-600 hover:text-white transition flex items-center gap-1">
                    <i class="fa-solid fa-xmark"></i> Deselect
                </button>
                <button onclick="downloadSelected('${currentChannel}')" id="downloadSelectedBtn" class="py-1.5 px-3 rounded-lg bg-indigo-600/20 border border-indigo-500/50 text-indigo-300 text-xs font-semibold hover:bg-indigo-500 hover:text-white transition flex items-center gap-1">
                    <i class="fa-solid fa-download"></i> Download (<span id="selectedCount">0</span>)
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

        // Dark theme styles for file item
        html += `
            <div class="bg-gray-800/50 rounded-lg border border-gray-700/50 flex items-center p-3 gap-4 hover:bg-gray-700/50 transition-colors group file-item" data-file-id="${file.file_id}">
                <div class="flex items-center">
                    <input type="checkbox"
                           id="file_${file.file_id}"
                           class="file-checkbox w-5 h-5 rounded border-gray-600 text-indigo-500 focus:ring-indigo-500 focus:ring-offset-gray-900 bg-gray-700 cursor-pointer"
                           onchange="toggleFileSelection(${file.file_id})"
                           ${isChecked}>
                </div>
                <div class="w-10 h-10 rounded-lg bg-indigo-500/10 flex items-center justify-center flex-shrink-0">
                    <i class="fa-solid ${icon} text-lg text-indigo-400"></i>
                </div>
                <div class="flex-1 min-w-0">
                    <div class="font-medium text-gray-200 truncate pr-4" title="${file.file_name}">${file.file_name}</div>
                    <div class="text-xs text-gray-500 mt-0.5 flex items-center gap-2">
                        <span class="bg-gray-700 px-1.5 rounded text-[10px] uppercase tracking-wide">${file.file_type}</span>
                        <span>${size}</span>
                        <span>•</span>
                        <span>${new Date(file.date).toLocaleDateString()}</span>
                    </div>
                </div>
                <button onclick="downloadSingle(${file.file_id}, '${currentChannel}')" 
                    class="opacity-0 group-hover:opacity-100 py-1.5 px-3 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-semibold shadow-lg shadow-indigo-500/20 transition-all flex items-center gap-1 transform translate-x-2 group-hover:translate-x-0">
                    <i class="fa-solid fa-download"></i> <span class="hidden sm:inline">Download</span>
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

            // If no more progress items, hide the entire progress section
            const progressBarsContainer = document.getElementById(
                "progressBarsContainer"
            );
            if (progressBarsContainer && progressBarsContainer.children.length === 0) {
                document.getElementById("downloadProgress").classList.add("hidden");
                document.getElementById("clearProgressBtn")?.classList.add("hidden");
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
    eta = 0
) {
    const progressId = `progress-${fileId}`;
    let retryBadge = '';
    let stallWarning = '';

    // Format speed and ETA
    const speedText = speed > 0 ? `${formatBytes(speed)}/s` : '';
    const etaText = eta > 0 ? `ETA: ${formatTime(eta)}` : '';

    if (retryAttempt && retryAttempt > 1) {
        retryBadge = `<span class="text-xs px-2 py-1 rounded bg-yellow-500/20 text-yellow-300 ml-2 border border-yellow-500/30">Retry ${retryAttempt}/3</span>`;
    }

    // Check if download appears stalled
    if (lastUpdate && !isComplete) {
        try {
            const lastUpdateTime = new Date(lastUpdate);
            const timeSinceUpdate = (Date.now() - lastUpdateTime) / 1000; // seconds
            if (timeSinceUpdate > 10) {
                stallWarning = `<span class="text-xs px-2 py-1 rounded bg-orange-500/20 text-orange-300 ml-2 border border-orange-500/30"><i class="fa-solid fa-triangle-exclamation"></i> Stalled ${Math.floor(timeSinceUpdate)}s</span>`;
            }
        } catch (e) {
            console.error('Error checking stall status:', e);
        }
    }

    let html = `
        <div id="${progressId}" class="file-progress-block relative overflow-hidden group">
            <!-- Glass effect background -->
            <div class="absolute inset-0 bg-gray-800/50 backdrop-blur-md border border-gray-700/50 rounded-xl"></div>
            
            <div class="relative p-4 z-10">
                <div class="flex justify-between items-start mb-3">
                    <div class="flex items-center gap-3 overflow-hidden">
                        <div class="w-10 h-10 rounded-lg bg-indigo-500/20 flex items-center justify-center flex-shrink-0 text-indigo-400">
                             <i class="fa-solid fa-file-arrow-down text-xl"></i>
                        </div>
                        <div class="min-w-0">
                            <div class="file-name text-white font-medium truncate" title="${fileName}">
                                ${fileName}
                            </div>
                            <div class="flex items-center gap-2 mt-0.5">
                                ${retryBadge}
                                ${stallWarning}
                                ${!isComplete && !retryBadge && !stallWarning ? `<span class="text-xs text-indigo-300 flex items-center gap-1"><i class="fa-solid fa-bolt text-[10px]"></i> ${speedText}</span>` : ''}
                            </div>
                        </div>
                    </div>
                    
                    ${isComplete ? `
                        <button onclick="clearIndividualProgress('${fileId}')" class="text-gray-400 hover:text-white transition-colors p-1">
                            <i class="fa-solid fa-xmark"></i>
                        </button>
                    ` : `
                        <div class="text-right">
                            <div class="text-lg font-bold text-white">${percentage}%</div>
                            <div class="text-xs text-gray-400 font-mono">${etaText}</div>
                        </div>
                    `}
                </div>

                <!-- Progress Track -->
                <div class="h-2 w-full bg-gray-700/50 rounded-full overflow-hidden mb-2">
                    <div class="h-full rounded-full transition-all duration-300 ease-out relative ${isComplete ? 'bg-green-500' : 'bg-gradient-to-r from-indigo-500 to-purple-500'}" 
                         style="width: ${percentage}%">
                         ${!isComplete ? '<div class="absolute inset-0 bg-white/20 animate-pulse"></div>' : ''}
                    </div>
                </div>

                <div class="flex justify-between items-center text-xs text-gray-400 font-medium">
                    <span>${formatBytes(current)} of ${formatBytes(total)}</span>
                    ${isComplete
            ? '<span class="text-green-400 flex items-center gap-1"><i class="fa-solid fa-check-circle"></i> Complete</span>'
            : retryAttempt > 1
                ? '<span class="text-yellow-400">Retrying connection...</span>'
                : '<span class="text-indigo-400">Downloading...</span>'
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
            <div class="absolute inset-0 bg-red-900/10 backdrop-blur-sm border border-red-500/20 rounded-xl"></div>
            
            <div class="relative p-4 z-10">
                <div class="flex justify-between items-start mb-3">
                    <div class="flex items-center gap-3 overflow-hidden">
                        <div class="w-10 h-10 rounded-lg bg-red-500/10 flex items-center justify-center flex-shrink-0 text-red-400">
                             <i class="fa-solid fa-ban text-xl"></i>
                        </div>
                        <div class="min-w-0">
                            <div class="file-name text-gray-300 font-medium truncate line-through decoration-red-500/50" title="${fileName}">
                                ${fileName}
                            </div>
                            <div class="flex items-center gap-2 mt-0.5">
                                <span class="text-xs text-red-400 flex items-center gap-1"><i class="fa-solid fa-circle-stop text-[10px]"></i> Cancelled</span>
                            </div>
                        </div>
                    </div>
                </div>

                <!-- Progress Track -->
                <div class="h-2 w-full bg-gray-700/50 rounded-full overflow-hidden mb-2">
                    <div class="h-full rounded-full bg-red-500/50" style="width: ${percentage}%"></div>
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

    try {
        const response = await authFetch("/files/download-selected", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                channel_username: channel,
                message_ids: Array.from(selectedFiles),
            }),
        });

        const data = await response.json();

        if (response.ok) {
            showAlert("downloadAlert", data.message, "success");
            document.getElementById("downloadProgress").classList.remove("hidden");
            document.getElementById("cancelBtn").classList.remove("hidden");

            startProgressMonitoring();
        } else {
            showAlert("downloadAlert", data.detail, "error");
        }
    } catch (error) {
        showAlert("downloadAlert", "Error: " + error.message, "error");
    }
}

async function downloadSingle(messageId, channel) {
    const fileId = `single_${messageId}`;

    try {
        showAlert("downloadAlert", "Starting download in background...", "info");
        document.getElementById("downloadProgress").classList.remove("hidden");
        document.getElementById("cancelBtn").classList.remove("hidden");

        const response = await authFetch(
            `/files/download/${messageId}?channel_username=${channel}`,
            {
                method: "POST",
            }
        );

        if (!response) {
            document.getElementById("cancelBtn").classList.add("hidden");
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
            document.getElementById("cancelBtn").classList.add("hidden");
        }
    } catch (error) {
        console.error("Download single error:", error);
        showAlert("downloadAlert", "Error: " + error.message, "error");
        document.getElementById("cancelBtn").classList.add("hidden");
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
            document.getElementById("cancelBtn").classList.remove("hidden");

            startProgressMonitoring();
        } else {
            showAlert("downloadAlert", data.detail, "error");
        }
    } catch (error) {
        showAlert("downloadAlert", "Error: " + error.message, "error");
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

            // Keep completed downloads, only remove active ones
            const progressBarsContainer = document.getElementById(
                "progressBarsContainer"
            );
            if (progressBarsContainer) {
                const activeDownloads = progressBarsContainer.querySelectorAll(
                    ".file-progress-block"
                );
                activeDownloads.forEach((block) => {
                    const fileId = block.id.replace("progress-", "");
                    if (!completedDownloads.has(fileId)) {
                        block.remove();
                    }
                });
            }

            const overallText = document.getElementById("overallText");
            if (overallText) {
                overallText.textContent = "";
            }

            selectedFiles.clear();
            updateSelectedCount();
            deselectAllFiles();

            document.getElementById("cancelBtn").classList.add("hidden");
        } else {
            showAlert("downloadAlert", data.detail || data.message, "error");
        }
    } catch (error) {
        showAlert("downloadAlert", "Error: " + error.message, "error");
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
            }

            // Ensure progress section is visible if we have data
            const progressSection = document.getElementById('downloadProgress');
            if (progressSection && (data.active || Object.keys(data.completed_downloads || {}).length > 0)) {
                progressSection.classList.remove('hidden');
            }

            // Handle completed downloads from state
            if (data.completed_downloads) {
                for (const [fileId, fileData] of Object.entries(data.completed_downloads)) {
                    const existingProgress = document.getElementById(`progress-${fileId}`);

                    if (!completedDownloads.has(fileId)) {
                        // This file just completed - update the tracking map
                        completedDownloads.set(fileId, fileData.path);
                        console.log(`File marked as completed: ${fileData.name}`);
                    }

                    // Always update/replace the progress bar to ensure it shows 100%
                    const progressBarsContainer = document.getElementById('progressBarsContainer');
                    if (progressBarsContainer) {
                        const percentage = fileData.percentage || 100;

                        if (existingProgress) {
                            // Replace existing progress bar with completed version
                            console.log(`Updating progress bar to complete (100%) for: ${fileData.name}`);
                            existingProgress.outerHTML = createProgressBar(fileId, fileData.name, true, percentage, fileData.size, fileData.size);
                        } else {
                            // Add new completed progress bar
                            console.log(`Adding completed progress bar for file: ${fileData.name}`);
                            progressBarsContainer.insertAdjacentHTML('beforeend',
                                createProgressBar(fileId, fileData.name, true, percentage, fileData.size, fileData.size)
                            );
                        }
                    }
                }

                // Show clear button if we have completed downloads
                if (Object.keys(data.completed_downloads).length > 0) {
                    document.getElementById('clearProgressBtn')?.classList.remove('hidden');
                }
            }

            // Update overall progress text (do this before checking completion)
            const overallText = document.getElementById('overallText');
            if (overallText && data.total > 0) {
                overallText.textContent = `${data.progress || 0}/${data.total} files`;
            }

            // Check if download is complete
            if (!data.active && hasStarted) {
                console.log('Download session completed, stopping progress monitoring');
                clearInterval(progressMonitoringInterval);
                progressMonitoringInterval = null;

                // Clear watchdog
                if (progressWatchdog) {
                    clearInterval(progressWatchdog);
                    progressWatchdog = null;
                }

                document.getElementById('cancelBtn')?.classList.add('hidden');

                const completedCount = Object.keys(data.completed_downloads || {}).length;
                if (completedCount > 0) {
                    showAlert("downloadAlert", `Download session complete! ${completedCount} files downloaded.`, "success");
                    document.getElementById('downloadProgress').classList.remove('hidden');
                    document.getElementById('clearProgressBtn')?.classList.remove('hidden');

                    // Update header to complete state
                    const title = document.getElementById("downloadProgressTitle");
                    if (title) title.innerHTML = '<i class="fa-solid fa-clock-rotate-left text-green-500"></i> Download History';
                }
                return;
            }

            // Handle active downloads
            const progressBarsContainer = document.getElementById('progressBarsContainer');
            if (!progressBarsContainer) {
                console.warn('progressBarsContainer not found');
                return;
            }

            if (data.active && data.concurrent_downloads) {
                for (const [fileId, fileData] of Object.entries(data.concurrent_downloads)) {
                    // Skip if already completed
                    if (completedDownloads.has(fileId)) continue;

                    const percentage = fileData.percentage || 0;
                    const existingProgress = document.getElementById(`progress-${fileId}`);

                    if (existingProgress) {
                        // Update existing progress bar
                        existingProgress.outerHTML = createProgressBar(
                            fileId,
                            fileData.name,
                            false,
                            percentage,
                            fileData.progress,
                            fileData.total,
                            fileData.retry_attempt,
                            fileData.last_update,
                            fileData.speed,
                            fileData.eta
                        );
                    } else {
                        // Add new progress bar
                        console.log(`Adding new progress bar for file: ${fileData.name} (${percentage}%)`);
                        progressBarsContainer.insertAdjacentHTML('beforeend', createProgressBar(
                            fileId,
                            fileData.name,
                            false,
                            percentage,
                            fileData.progress,
                            fileData.total,
                            fileData.retry_attempt,
                            fileData.last_update,
                            fileData.speed,
                            fileData.eta
                        ));
                    }
                }
            }
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
    let color = "bg-indigo-500";
    let icon = "fa-info-circle";
    if (type === "success") {
        color = "bg-green-500";
        icon = "fa-circle-check";
    } else if (type === "error") {
        color = "bg-red-500";
        icon = "fa-circle-xmark";
    } else if (type === "warning") {
        color = "bg-yellow-400 text-gray-900";
        icon = "fa-triangle-exclamation";
    } else if (type === "info") {
        color = "bg-blue-500";
        icon = "fa-circle-info";
    }
    const toast = document.createElement("div");
    toast.className = `flex items-center gap-4 px-6 py-4 rounded-xl shadow-2xl text-white font-medium ${color} animate-fade-in-up pointer-events-auto`;
    toast.style.width = "100%";
    toast.innerHTML = `<i class="fa-solid ${icon} text-2xl"></i><span class="text-base">${message}</span>`;
    toastContainer.appendChild(toast);
    setTimeout(() => {
        toast.classList.add("opacity-0");
        toast.style.transition = "opacity 0.5s";
        setTimeout(() => toast.remove(), 500);
    }, 4000);
}

// Toast fade-in animation
const style = document.createElement("style");
style.innerHTML = `
    @keyframes fade-in-up {
        from { opacity: 0; transform: translateY(20px);}
        to { opacity: 1; transform: translateY(0);}
    }
    .animate-fade-in-up {
        animation: fade-in-up 0.4s cubic-bezier(.39,.575,.565,1.000) both;
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
                document.getElementById('cancelBtn').classList.remove('hidden');

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
    const progressBarsContainer = document.getElementById('progressBarsContainer');
    if (!progressBarsContainer) return;

    const overallText = document.getElementById('overallText');
    if (overallText && data.total > 0) {
        overallText.textContent = `${data.progress || 0}/${data.total} files`;
    }

    // Update or add completed downloads
    if (data.completed_downloads) {
        for (const [fileId, fileData] of Object.entries(data.completed_downloads)) {
            if (!completedDownloads.has(fileId)) {
                completedDownloads.set(fileId, fileData.path);

                const existingProgress = document.getElementById(`progress-${fileId}`);
                if (!existingProgress) {
                    const percentage = fileData.percentage || 100;
                    progressBarsContainer.insertAdjacentHTML('beforeend',
                        createProgressBar(fileId, fileData.name, true, percentage, fileData.size, fileData.size)
                    );
                }
            }
        }
    }

    // Update active downloads
    if (data.active && data.concurrent_downloads) {
        for (const [fileId, fileData] of Object.entries(data.concurrent_downloads)) {
            if (completedDownloads.has(fileId)) continue;

            const percentage = fileData.percentage || 0;
            const existingProgress = document.getElementById(`progress-${fileId}`);

            if (existingProgress) {
                existingProgress.outerHTML = createProgressBar(
                    fileId,
                    fileData.name,
                    false,
                    percentage,
                    fileData.progress,
                    fileData.total,
                    fileData.retry_attempt,
                    fileData.last_update
                );
            } else {
                progressBarsContainer.insertAdjacentHTML('beforeend', createProgressBar(
                    fileId,
                    fileData.name,
                    false,
                    percentage,
                    fileData.progress,
                    fileData.total,
                    fileData.retry_attempt,
                    fileData.last_update
                ));
            }
        }
    }
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

        searchBtn.classList.add('border-indigo-500', 'text-white');
        searchBtn.classList.remove('border-transparent', 'text-gray-400');

        queueBtn.classList.remove('border-indigo-500', 'text-white');
        queueBtn.classList.add('border-transparent', 'text-gray-400');

        // Stop queue polling
        if (queuePollInterval) {
            clearInterval(queuePollInterval);
            queuePollInterval = null;
        }

    } else if (tab === 'queue') {
        searchTab.classList.add('hidden');
        queueTab.classList.remove('hidden');

        queueBtn.classList.add('border-indigo-500', 'text-white');
        queueBtn.classList.remove('border-transparent', 'text-gray-400');

        searchBtn.classList.remove('border-indigo-500', 'text-white');
        searchBtn.classList.add('border-transparent', 'text-gray-400');

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
        return;
    }

    list.innerHTML = queue.map((item, index) => `
        <div class="bg-gray-800/50 hover:bg-gray-800 border border-gray-700 hover:border-gray-600 rounded-lg p-4 transition-all flex items-center gap-4 group">
            <div class="text-gray-500 font-mono text-xs w-6 text-center">${index + 1}</div>
            
            <div class="flex-shrink-0 w-10 h-10 rounded bg-indigo-500/10 flex items-center justify-center text-indigo-400">
                <i class="fa-solid fa-file"></i>
            </div>
            
            <div class="flex-grow min-w-0">
                <div class="text-sm font-medium text-white truncate" title="${item.name}">${item.name}</div>
                <div class="text-xs text-gray-400 flex items-center gap-2">
                    <span class="bg-gray-700 px-1.5 rounded text-[10px]">${item.channel}</span>
                    <span class="${getStatusColor(item.status)} capitalize">${item.status}</span>
                </div>
            </div>
            
            <div class="flex items-center gap-2 opacity-0 group-hover:opacity-100 transition-opacity">
                <button onclick="reorderQueue(${index}, -1)" class="p-1.5 text-gray-400 hover:text-white hover:bg-gray-700 rounded" ${index === 0 ? 'disabled style="opacity:0.3;cursor:not-allowed"' : ''}>
                    <i class="fa-solid fa-arrow-up"></i>
                </button>
                <button onclick="reorderQueue(${index}, 1)" class="p-1.5 text-gray-400 hover:text-white hover:bg-gray-700 rounded" ${index === queue.length - 1 ? 'disabled style="opacity:0.3;cursor:not-allowed"' : ''}>
                    <i class="fa-solid fa-arrow-down"></i>
                </button>
                <button onclick="removeFromQueue('${item.id}')" class="p-1.5 text-red-400 hover:text-red-300 hover:bg-red-500/20 rounded ml-2">
                    <i class="fa-solid fa-xmark"></i>
                </button>
            </div>
        </div>
    `).join('');
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
    if (!currentQueue || currentQueue.length < 2) return;

    // Calculate new index
    const newIndex = index + direction;
    if (newIndex < 0 || newIndex >= currentQueue.length) return;

    // Swap locally for instant feedback
    const temp = currentQueue[index];
    currentQueue[index] = currentQueue[newIndex];
    currentQueue[newIndex] = temp;

    // Re-render immediately
    renderQueue(currentQueue);

    // Send update to server
    try {
        const ids = currentQueue.map(item => item.id);
        await authFetch('/queue/reorder', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ queue_ids: ids })
        });
    } catch (e) {
        console.error("Reorder failed", e);
        showAlert('downloadAlert', 'Failed to reorder queue', 'error');
        fetchQueue(); // Revert on error
    }
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
    if (!confirm("Are you sure you want to clear the entire queue?")) return;

    try {
        await authFetch('/queue/clear', { method: 'POST' });
        currentQueue = [];
        renderQueue([]);
        updateQueueBadge(0);
        showAlert('downloadAlert', 'Queue cleared', 'info');
    } catch (e) {
        showAlert('downloadAlert', 'Failed to clear queue', 'error');
    }
}

async function refreshQueue() {
    const btn = document.querySelector('button[title="Refresh Queue"] i');
    if (btn) btn.classList.add('fa-spin');
    await fetchQueue();
    if (btn) btn.classList.remove('fa-spin');
}