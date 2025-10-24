// telegram_downloader.js
// JavaScript logic for Telegram File Downloader UI

let statusCheckInterval;
let channelList = [];
let savePath = "";
let selectedFiles = new Set();
let progressMonitoringInterval = null;
let completedDownloads = new Map(); // Track completed downloads

// Authentication helpers
function getAuthToken() {
    return localStorage.getItem('access_token');
}

function getAuthHeaders() {
    const token = getAuthToken();
    return token ? { 'Authorization': `Bearer ${token}` } : {};
}

function logout() {
    localStorage.removeItem('access_token');
    window.location.href = '/';
}

// Authenticated fetch wrapper
async function authFetch(url, options = {}) {
    const headers = {
        ...getAuthHeaders(),
        ...(options.headers || {})
    };

    const response = await fetch(url, {
        ...options,
        headers
    });

    // If unauthorized, redirect to login
    if (response.status === 401) {
        logout();
        return null;
    }

    return response;
}

// Check authentication on page load
window.addEventListener('DOMContentLoaded', () => {
    const token = getAuthToken();
    if (!token) {
        window.location.href = '/';
        return;
    }
});

async function loadChannels() {
    try {
        const response = await authFetch("/config/channels");
        if (!response) return;
        const data = await response.json();

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
        const data = await response.json();

        console.log('Saved state response:', data);

        if (data.has_saved_state && !data.active) {
            const completedCount = data.completed_count || 0;
            const totalCount = data.total || 0;
            const remainingCount = totalCount - completedCount;

            console.log(`Found saved state: ${completedCount}/${totalCount} completed, ${remainingCount} remaining`);

            // Show resume option
            const resumeHtml = `
                <div id="resumeNotification" class="bg-yellow-50 border-l-4 border-yellow-400 p-4 mb-4">
                    <div class="flex items-center justify-between">
                        <div class="flex items-center">
                            <i class="fa-solid fa-circle-pause text-yellow-400 mr-3 text-2xl"></i>
                            <div>
                                <p class="text-sm font-semibold text-yellow-800">Previous download session found</p>
                                <p class="text-xs text-yellow-700 mt-1">
                                    <strong>Channel:</strong> ${data.channel || 'Unknown'}<br>
                                    <strong>Progress:</strong> ${completedCount}/${totalCount} files completed
                                    ${remainingCount > 0 ? `(${remainingCount} remaining)` : '(All done!)'}
                                </p>
                                <p class="text-xs text-gray-500 mt-1">Session ID: ${data.session_id || 'N/A'}</p>
                            </div>
                        </div>
                        <div class="flex gap-2">
                            ${remainingCount > 0 ? `
                                <button onclick="resumeDownload()" class="py-2 px-4 rounded bg-yellow-500 text-white font-semibold shadow hover:bg-yellow-600 focus:outline-none transition flex items-center gap-2">
                                    <i class="fa-solid fa-play"></i> Resume Download
                                </button>
                            ` : ''}
                            <button onclick="viewCompletedDownloads()" class="py-2 px-4 rounded bg-blue-500 text-white font-semibold shadow hover:bg-blue-600 focus:outline-none transition flex items-center gap-2">
                                <i class="fa-solid fa-eye"></i> View (${completedCount})
                            </button>
                            <button onclick="clearSavedState()" class="py-2 px-4 rounded bg-gray-500 text-white font-semibold shadow hover:bg-gray-600 focus:outline-none transition flex items-center gap-2">
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

            // Restore completed downloads to the map
            if (data.completed_count > 0) {
                const progressResponse = await authFetch("/download-progress");
                const progressData = await progressResponse.json();

                if (progressData.completed_downloads) {
                    const progressBarsContainer = document.getElementById("progressBarsContainer");
                    if (progressBarsContainer) {
                        for (const [fileId, fileData] of Object.entries(progressData.completed_downloads)) {
                            completedDownloads.set(fileId, fileData.path);

                            // Add completed progress bar if not exists
                            if (!document.getElementById(`progress-${fileId}`)) {
                                progressBarsContainer.insertAdjacentHTML('beforeend',
                                    createProgressBar(fileId, fileData.name, true, 100, fileData.size, fileData.size)
                                );
                            }
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

        if (progressData.completed_downloads) {
            const progressBarsContainer = document.getElementById("progressBarsContainer");
            if (progressBarsContainer) {
                // Clear and rebuild
                progressBarsContainer.innerHTML = '';

                // Add all completed downloads
                for (const [fileId, fileData] of Object.entries(progressData.completed_downloads)) {
                    completedDownloads.set(fileId, fileData.path);
                    progressBarsContainer.insertAdjacentHTML('beforeend',
                        createProgressBar(fileId, fileData.name, true, 100, fileData.size, fileData.size)
                    );
                }

                const count = Object.keys(progressData.completed_downloads).length;
                showAlert("downloadAlert", `Showing ${count} completed download${count !== 1 ? 's' : ''}`, "success");
            }
        } else {
            showAlert("downloadAlert", "No completed downloads found", "info");
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

function clearProgress() {
    // Clear all completed downloads from UI but keep in state
    const progressBarsContainer = document.getElementById(
        "progressBarsContainer"
    );
    if (progressBarsContainer) {
        const completedBlocks = progressBarsContainer.querySelectorAll(
            ".file-progress-block"
        );
        completedBlocks.forEach((block) => {
            const progressFill = block.querySelector(".progress-fill");
            if (progressFill && progressFill.classList.contains("bg-green-500")) {
                block.remove();
            }
        });
    }

    // Hide progress section if empty
    if (progressBarsContainer && progressBarsContainer.children.length === 0) {
        document.getElementById("downloadProgress").classList.add("hidden");
        document.getElementById("clearProgressBtn").classList.add("hidden");
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
                    progressBarsContainer.insertAdjacentHTML('beforeend',
                        createProgressBar(fileId, fileData.name, true, 100, fileData.size, fileData.size)
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
        const data = await response.json();

        const connectionStatus = document.getElementById("connectionStatus");
        const userInfo = document.getElementById("userInfo");
        const loginSection = document.getElementById("loginSection");
        const downloadSection = document.getElementById("downloadSection");

        if (data.status === "connected") {
            connectionStatus.className =
                "inline-flex items-center px-3 py-1 rounded-full text-xs font-semibold bg-gradient-to-r from-green-400 to-green-500 text-white shadow-md animate-pulse";
            connectionStatus.innerHTML =
                '<i class="fa-solid fa-circle mr-2 text-white animate-pulse"></i><span class="font-bold tracking-wide">Connected</span>';
            userInfo.innerHTML = `<p><strong>User:</strong> ${
        data.user.first_name
      } (@${data.user.username || "N/A"})</p>`;
            loginSection.classList.add("hidden");
            downloadSection.classList.remove("hidden");
        } else if (data.status === "not_authenticated") {
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
                '<i class="fa-solid fa-circle-xmark mr-2 text-white animate-pulse"></i><span class="font-bold tracking-wide">Not Connected</span>';
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

        const data = await response.json();

        if (response.ok) {
            showAlert("loginAlert", data.message, "success");
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

async function listFiles() {
    const channel = document.getElementById("channelUsername").value;
    const limit = document.getElementById("fileLimit").value;
    const fileType = document.getElementById("fileType").value;

    if (!channel) {
        showAlert("downloadAlert", "Please select a channel", "error");
        return;
    }

    try {
        const response = await authFetch("/files/list", {
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
            displayFiles(data.files);
            showAlert("downloadAlert", `Found ${data.count} files`, "success");
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

    if (files.length === 0) {
        filesList.innerHTML = '<p style="margin-top: 20px;">No files found</p>';
        return;
    }

    let html = `
        <div class="flex justify-between items-center mb-4">
            <h3 class="text-lg font-semibold text-indigo-700 flex items-center gap-2">
                <i class="fa-solid fa-folder-open"></i> Files Found
            </h3>
            <div class="flex gap-2">
                <button onclick="selectAllFiles()" class="py-1 px-3 rounded bg-gray-500 text-white font-semibold shadow hover:bg-indigo-600 focus:outline-none focus:ring-2 focus:ring-indigo-400 transition flex items-center gap-1">
                    <i class="fa-solid fa-check-double"></i> Select All
                </button>
                <button onclick="deselectAllFiles()" class="py-1 px-3 rounded bg-gray-500 text-white font-semibold shadow hover:bg-gray-600 focus:outline-none focus:ring-2 focus:ring-gray-400 transition flex items-center gap-1">
                    <i class="fa-solid fa-xmark"></i> Deselect All
                </button>
                <button onclick="downloadSelected('${currentChannel}')" id="downloadSelectedBtn" class="py-1 px-3 rounded bg-gray-500 text-white font-semibold shadow hover:bg-green-600 focus:outline-none focus:ring-2 focus:ring-green-400 transition flex items-center gap-1">
                    <i class="fa-solid fa-download"></i> Download Selected (<span id="selectedCount">0</span>)
                </button>
            </div>
        </div>
        <div class="grid grid-cols-1 sm:grid-cols-1 gap-4">`;

    files.forEach((file) => {
        const size =
            file.file_size > 0 ?
            (file.file_size / 1024 / 1024).toFixed(2) + " MB" :
            "N/A";
        let icon = "fa-file-lines";
        if (file.file_type === "photo") icon = "fa-file-image";
        else if (file.file_type === "video") icon = "fa-file-video";
        else if (file.file_type === "audio") icon = "fa-file-audio";
        else if (file.file_type === "document") icon = "fa-file-lines";

        const isChecked = selectedFiles.has(file.file_id) ? "checked" : "";

        html += `
            <div class="bg-white rounded-lg shadow flex items-center p-4 gap-4 file-item" data-file-id="${
              file.file_id
            }">
                <div class="flex items-center">
                    <input type="checkbox"
                           id="file_${file.file_id}"
                           class="file-checkbox w-5 h-5 text-indigo-600 rounded focus:ring-indigo-500 cursor-pointer"
                           onchange="toggleFileSelection(${file.file_id})"
                           ${isChecked}>
                </div>
                <div>
                    <i class="fa-solid ${icon} text-3xl text-indigo-400"></i>
                </div>
                <div class="flex-1 min-w-0">
                    <div class="font-semibold text-gray-800 truncate" title="${
                      file.file_name
                    }">${file.file_name}</div>
                    <div class="text-xs text-gray-500 mt-1">${
                      file.file_type.charAt(0).toUpperCase() +
                      file.file_type.slice(1)
                    } · ${size} · ${new Date(
      file.date
    ).toLocaleDateString()}</div>
                </div>
                <button onclick="downloadSingle(${
                  file.file_id
                }, '${currentChannel}')" class="py-1 px-3 rounded bg-indigo-500 text-white font-semibold shadow hover:bg-indigo-600 focus:outline-none focus:ring-2 focus:ring-indigo-400 transition flex items-center gap-1">
                    <i class="fa-solid fa-download"></i> Download
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

function clearIndividualProgress(fileId) {
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
    }
}

function formatBytes(bytes) {
    if (!bytes || bytes === 0) return "0 Bytes";
    const k = 1024;
    const sizes = ["Bytes", "KB", "MB", "GB"];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return Math.round((bytes / Math.pow(k, i)) * 100) / 100 + " " + sizes[i];
}

function createProgressBar(
    fileId,
    fileName,
    isComplete = false,
    percentage = 0,
    current = 0,
    total = 0
) {
    const progressId = `progress-${fileId}`;
    let html = `
        <div id="${progressId}" class="file-progress-block" style="margin: 16px 0; padding: 12px; background: white; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.04);">
            <div class="flex justify-between items-center mb-2">
                <div class="file-name" style="font-size: 1em; font-weight: 600;">${fileName}</div>
                ${
                  isComplete
                    ? `
                    <button onclick="clearIndividualProgress('${fileId}')" class="py-1 px-2 rounded bg-gray-500 text-white text-xs font-semibold shadow hover:bg-gray-600 focus:outline-none transition flex items-center gap-1">
                        <i class="fa-solid fa-xmark"></i> Clear
                    </button>
                `
                    : ""
                }
            </div>
            <div class="progress-bar" style="height: 24px; margin: 8px 0;">
                <div class="progress-fill ${
                  isComplete ? "bg-green-500" : ""
                }" style="width: ${percentage}%; min-width: ${
    percentage > 0 ? "30px" : "0"
  };">
                    ${isComplete ? "✓ Complete" : percentage + "%"}
                </div>
            </div>
            <div class="flex justify-between text-xs text-gray-500">
                <span>${formatBytes(current)} / ${formatBytes(total)}</span>
                ${
                  isComplete
                    ? '<span class="text-green-600 font-semibold">Download Complete</span>'
                    : ""
                }
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
  let progressInterval;
  const fileId = `single_${messageId}`;

  try {
    showAlert("downloadAlert", "Starting download...", "info");
    document.getElementById("downloadProgress").classList.remove("hidden");
    document.getElementById("cancelBtn").classList.remove("hidden");
    progressInterval = setInterval(async () => {
      try {
        const response = await authFetch("/download-progress");
        const data = await response.json();
        const progressBarsContainer = document.getElementById(
          "progressBarsContainer"
        );
        if (!progressBarsContainer) return;
        if (data.concurrent_downloads && data.concurrent_downloads[fileId]) {
          const fileData = data.concurrent_downloads[fileId];
          const percentage = fileData.percentage || 0;
          const existingProgress = document.getElementById(
            `progress-${fileId}`
          );

          if (existingProgress) {
            existingProgress.outerHTML = createProgressBar(
              fileId,
              fileData.name,
              false,
              percentage,
              fileData.progress,
              fileData.total
            );
          } else {
            progressBarsContainer.insertAdjacentHTML(
              "beforeend",
              createProgressBar(
                fileId,
                fileData.name,
                false,
                percentage,
                fileData.progress,
                fileData.total
              )
            );
          }
        }
      } catch (error) {
        console.error("Progress update error:", error);
      }
    }, 500);

    const response = await authFetch(
      `/files/download/${messageId}?channel_username=${channel}`,
      {
        method: "POST",
      }
    );

    const data = await response.json();

    if (progressInterval) {
      clearInterval(progressInterval);
    }

    if (response.ok) {
      // Mark as complete
      completedDownloads.set(fileId, data.file_path);

      // Get the last progress data to show final size
      const progressResponse = await authFetch("/download-progress");
      const progressData = await progressResponse.json();
      let finalSize = 0;

      if (
        progressData.concurrent_downloads &&
        progressData.concurrent_downloads[fileId]
      ) {
        finalSize = progressData.concurrent_downloads[fileId].total;
      }

      const existingProgress = document.getElementById(`progress-${fileId}`);
      if (existingProgress) {
        existingProgress.outerHTML = createProgressBar(
          fileId,
          data.file_path.split("/").pop(),
          true,
          100,
          finalSize,
          finalSize
        );
      }

      document.getElementById("cancelBtn").classList.add("hidden");

      showAlert("downloadAlert", "File downloaded successfully!", "success");
    } else {
      showAlert("downloadAlert", data.detail, "error");
      document.getElementById("cancelBtn").classList.add("hidden");
      clearIndividualProgress(fileId);
    }
  } catch (error) {
    if (progressInterval) {
      clearInterval(progressInterval);
    }
    showAlert("downloadAlert", "Error: " + error.message, "error");
    document.getElementById("cancelBtn").classList.add("hidden");
    clearIndividualProgress(fileId);
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
    } else { showAlert("downloadAlert", data.detail || data.message, "error");
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
    } else { showAlert("downloadAlert", data.detail || data.message, "error");
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
        clearInterval(progressMonitoringInterval);
    }

    let hasStarted = false;

    progressMonitoringInterval = setInterval(async () => {
        try {
            const response = await authFetch('/download-progress');
            const data = await response.json();

            if (data.active) {
                hasStarted = true;
            }

            // Handle completed downloads from state (only add once)
            if (data.completed_downloads) {
                for (const [fileId, fileData] of Object.entries(data.completed_downloads)) {
                    if (!completedDownloads.has(fileId)) {
                        completedDownloads.set(fileId, fileData.path);

                        // Add completed progress bar if not exists
                        const existingProgress = document.getElementById(`progress-${fileId}`);
                        if (!existingProgress) {
                            const progressBarsContainer = document.getElementById('progressBarsContainer');
                            if (progressBarsContainer) {
                                progressBarsContainer.insertAdjacentHTML('beforeend',
                                    createProgressBar(fileId, fileData.name, true, 100, fileData.size, fileData.size)
                                );
                            }
                        }
                    }
                }

                // Show clear button if we have completed downloads
                if (Object.keys(data.completed_downloads).length > 0) {
                    document.getElementById('clearProgressBtn')?.classList.remove('hidden');
                }
            }

            // Check if download is complete
            if (!data.active && hasStarted) {
                clearInterval(progressMonitoringInterval);
                progressMonitoringInterval = null;
                document.getElementById('cancelBtn').classList.add('hidden');

                const completedCount = Object.keys(data.completed_downloads || {}).length;
                if (completedCount > 0) {
                    showAlert("downloadAlert", `Download session complete! ${completedCount} files downloaded.`, "success");
                }
                return;
            }

            // Update overall progress text
            const overallText = document.getElementById('overallText');
            if (overallText && data.total > 0) {
                overallText.textContent = `${data.progress || 0}/${data.total} files`;
            }

            // Handle active downloads
            const progressBarsContainer = document.getElementById('progressBarsContainer');
            if (!progressBarsContainer) return;

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
                            fileData.total
                        );
                    } else {
                        // Add new progress bar
                        progressBarsContainer.insertAdjacentHTML('beforeend', createProgressBar(
                            fileId,
                            fileData.name,
                            false,
                            percentage,
                            fileData.progress,
                            fileData.total
                        ));
                    }
                }
            }
        } catch (error) {
            console.error('Progress check error:', error);
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
  toast.className = `flex items-center gap-3 px-4 py-3 rounded shadow-lg text-white text-sm font-medium ${color} animate-fade-in-up`;
  toast.style.minWidth = "220px";
  toast.innerHTML = `<i class="fa-solid ${icon} text-lg"></i><span>${message}</span>`;
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

// On page load, check status and load channels if connected
(async function() {
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