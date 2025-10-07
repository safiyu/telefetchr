// telegram_downloader.js
// JavaScript logic for Telegram File Downloader UI

let statusCheckInterval;
let channelList = [];
let savePath = '';
let selectedFiles = new Set();
let progressMonitoringInterval = null;
let completedDownloads = new Map(); // Track completed downloads

async function loadChannels() {
    try {
        const response = await fetch('/config/channels');
        const data = await response.json();

        const channelList = data.channels;
        const savePath = data.save_path;

        document.getElementById('savePathText').textContent = savePath;

        const channelSelect = document.getElementById('channelUsername');
        channelSelect.innerHTML = '<option value="">Select a channel...</option>';

        channelList.forEach(channel => {
            const option = document.createElement('option');
            option.value = channel.username || channel.id; // fallback to ID if username is missing
            const trimmedName = channel.name.length > 40
                ? channel.name.slice(0, 37) + '...'
                : channel.name;

            option.textContent = trimmedName;
            channelSelect.appendChild(option);
        });
    } catch (error) {
        console.error('Error loading channels:', error);
    }
}

async function checkStatus() {
    try {
        const response = await fetch('/status');
        const data = await response.json();

        const connectionStatus = document.getElementById('connectionStatus');
        const userInfo = document.getElementById('userInfo');
        const loginSection = document.getElementById('loginSection');
        const downloadSection = document.getElementById('downloadSection');

        if (data.status === 'connected') {
            connectionStatus.className = 'inline-flex items-center px-3 py-1 rounded-full text-xs font-semibold bg-gradient-to-r from-green-400 to-green-500 text-white shadow-md animate-pulse';
            connectionStatus.innerHTML = '<i class="fa-solid fa-circle mr-2 text-white animate-pulse"></i><span class="font-bold tracking-wide">Connected</span>';
            userInfo.innerHTML = `<p><strong>User:</strong> ${data.user.first_name} (@${data.user.username || 'N/A'})</p>`;
            loginSection.classList.add('hidden');
            downloadSection.classList.remove('hidden');
        } else if (data.status === 'not_authenticated') {
            connectionStatus.className = 'inline-flex items-center px-3 py-1 rounded-full text-xs font-semibold bg-gradient-to-r from-yellow-400 to-orange-400 text-white shadow-md';
            connectionStatus.innerHTML = '<i class="fa-solid fa-circle-exclamation mr-2 text-white animate-pulse"></i><span class="font-bold tracking-wide">Authentication Required</span>';
            userInfo.innerHTML = '';
            loginSection.classList.remove('hidden');
            downloadSection.classList.add('hidden');
        } else {
            connectionStatus.className = 'inline-flex items-center px-3 py-1 rounded-full text-xs font-semibold bg-gradient-to-r from-red-400 to-pink-500 text-white shadow-md';
            connectionStatus.innerHTML = '<i class="fa-solid fa-circle-xmark mr-2 text-white animate-pulse"></i><span class="font-bold tracking-wide">Not Connected</span>';
            userInfo.innerHTML = '';
            loginSection.classList.add('hidden');
            downloadSection.classList.add('hidden');
        }
    } catch (error) {
        console.error('Status check error:', error);
    }
}

async function requestCode() {
    try {
        const response = await fetch('/login/request-code', {
            method: 'POST'
        });

        const data = await response.json();

        if (response.ok) {
            showAlert('loginAlert', data.message, 'success');
            document.getElementById('requestCodeBtn').classList.add('hidden');
            document.getElementById('codeForm').classList.remove('hidden');
        } else {
            showAlert('loginAlert', data.detail, 'error');
        }
    } catch (error) {
        showAlert('loginAlert', 'Error: ' + error.message, 'error');
    }
}

async function verifyCode() {
    const code = document.getElementById('verificationCode').value;

    if (!code) {
        showAlert('loginAlert', 'Please enter the code', 'error');
        return;
    }

    try {
        const response = await fetch('/login/verify', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({code: code})
        });

        const data = await response.json();

        if (response.ok) {
            showAlert('loginAlert', 'Login successful!', 'success');
            setTimeout(() => {
                checkStatus();
                loadChannels();
            }, 1000);
        } else {
            if (data.detail && (data.detail.includes('2FA') || data.detail.includes('password'))) {
                document.getElementById('codeForm').classList.add('hidden');
                document.getElementById('passwordForm').classList.remove('hidden');
                showAlert('loginAlert', 'Please enter your 2FA password', 'info');
            } else {
                showAlert('loginAlert', data.detail, 'error');
            }
        }
    } catch (error) {
        showAlert('loginAlert', 'Error: ' + error.message, 'error');
    }
}

async function verify2FA() {
    const password = document.getElementById('password2fa').value;

    if (!password) {
        showAlert('loginAlert', 'Please enter your password', 'error');
        return;
    }

    try {
        const response = await fetch('/login/password', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({password: password})
        });

        const data = await response.json();

        if (response.ok) {
            showAlert('loginAlert', 'Login successful!', 'success');
            setTimeout(() => {
                checkStatus();
                loadChannels();
            }, 1000);
        } else {
            showAlert('loginAlert', data.detail, 'error');
        }
    } catch (error) {
        showAlert('loginAlert', 'Error: ' + error.message, 'error');
    }
}

async function listFiles() {
    const channel = document.getElementById('channelUsername').value;
    const limit = document.getElementById('fileLimit').value;
    const fileType = document.getElementById('fileType').value;

    if (!channel) {
        showAlert('downloadAlert', 'Please select a channel', 'error');
        return;
    }

    try {
        const response = await fetch('/files/list', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                channel_username: channel,
                limit: parseInt(limit),
                filter_type: fileType || null
            })
        });

        const data = await response.json();

        if (response.ok) {
            displayFiles(data.files);
            showAlert('downloadAlert', `Found ${data.count} files`, 'success');
        } else {
            showAlert('downloadAlert', data.detail, 'error');
        }
    } catch (error) {
        showAlert('downloadAlert', 'Error: ' + error.message, 'error');
    }
}

function displayFiles(files) {
    const filesList = document.getElementById('filesList');
    const currentChannel = document.getElementById('channelUsername').value;

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

    files.forEach(file => {
        const size = file.file_size > 0 ? (file.file_size / 1024 / 1024).toFixed(2) + ' MB' : 'N/A';
        let icon = 'fa-file-lines';
        if (file.file_type === 'photo') icon = 'fa-file-image';
        else if (file.file_type === 'video') icon = 'fa-file-video';
        else if (file.file_type === 'audio') icon = 'fa-file-audio';
        else if (file.file_type === 'document') icon = 'fa-file-lines';
        
        const isChecked = selectedFiles.has(file.file_id) ? 'checked' : '';
        
        html += `
            <div class="bg-white rounded-lg shadow flex items-center p-4 gap-4 file-item" data-file-id="${file.file_id}">
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
                    <div class="font-semibold text-gray-800 truncate" title="${file.file_name}">${file.file_name}</div>
                    <div class="text-xs text-gray-500 mt-1">${file.file_type.charAt(0).toUpperCase() + file.file_type.slice(1)} · ${size} · ${new Date(file.date).toLocaleDateString()}</div>
                </div>
                <button onclick="downloadSingle(${file.file_id}, '${currentChannel}')" class="py-1 px-3 rounded bg-indigo-500 text-white font-semibold shadow hover:bg-indigo-600 focus:outline-none focus:ring-2 focus:ring-indigo-400 transition flex items-center gap-1">
                    <i class="fa-solid fa-download"></i> Download
                </button>
            </div>
        `;
    });

    html += '</div>';
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
    const checkboxes = document.querySelectorAll('.file-checkbox');
    checkboxes.forEach(checkbox => {
        checkbox.checked = true;
        const fileId = parseInt(checkbox.id.replace('file_', ''));
        selectedFiles.add(fileId);
    });
    updateSelectedCount();
}

function deselectAllFiles() {
    const checkboxes = document.querySelectorAll('.file-checkbox');
    checkboxes.forEach(checkbox => {
        checkbox.checked = false;
    });
    selectedFiles.clear();
    updateSelectedCount();
}

function updateSelectedCount() {
    const countElement = document.getElementById('selectedCount');
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
    const progressBarsContainer = document.getElementById('progressBarsContainer');
    if (progressBarsContainer && progressBarsContainer.children.length === 0) {
        document.getElementById('downloadProgress').classList.add('hidden');
    }
}

function formatBytes(bytes) {
    if (!bytes || bytes === 0) return '0 Bytes';
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return Math.round((bytes / Math.pow(k, i)) * 100) / 100 + ' ' + sizes[i];
}

function createProgressBar(fileId, fileName, isComplete = false, percentage = 0, current = 0, total = 0) {
    const progressId = `progress-${fileId}`;
    let html = `
        <div id="${progressId}" class="file-progress-block" style="margin: 16px 0; padding: 12px; background: white; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.04);">
            <div class="flex justify-between items-center mb-2">
                <div class="file-name" style="font-size: 1em; font-weight: 600;">${fileName}</div>
                ${isComplete ? `
                    <button onclick="clearIndividualProgress('${fileId}')" class="py-1 px-2 rounded bg-gray-500 text-white text-xs font-semibold shadow hover:bg-gray-600 focus:outline-none transition flex items-center gap-1">
                        <i class="fa-solid fa-xmark"></i> Clear
                    </button>
                ` : ''}
            </div>
            <div class="progress-bar" style="height: 24px; margin: 8px 0;">
                <div class="progress-fill ${isComplete ? 'bg-green-500' : ''}" style="width: ${percentage}%; min-width: ${percentage > 0 ? '30px' : '0'};">
                    ${isComplete ? '✓ Complete' : percentage + '%'}
                </div>
            </div>
            <div class="flex justify-between text-xs text-gray-500">
                <span>${formatBytes(current)} / ${formatBytes(total)}</span>
                ${isComplete ? '<span class="text-green-600 font-semibold">Download Complete</span>' : ''}
            </div>
        </div>
    `;
    return html;
}

async function downloadSelected(channel) {
    if (selectedFiles.size === 0) {
        showAlert('downloadAlert', 'Please select at least one file to download', 'warning');
        return;
    }

    try {
        const response = await fetch('/files/download-selected', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                channel_username: channel,
                message_ids: Array.from(selectedFiles)
            })
        });

        const data = await response.json();

        if (response.ok) {
            showAlert('downloadAlert', data.message, 'success');
            
												  
																						   
										
													 
			 
			
            document.getElementById('downloadProgress').classList.remove('hidden');
            document.getElementById('cancelBtn').classList.remove('hidden');
																				
            startProgressMonitoring();
        } else {
            showAlert('downloadAlert', data.detail, 'error');
        }
    } catch (error) {
        showAlert('downloadAlert', 'Error: ' + error.message, 'error');
    }
}

async function downloadSingle(messageId, channel) {
    let progressInterval;
    const fileId = `single_${messageId}`;
    
    try {
        showAlert('downloadAlert', 'Starting download...', 'info');
        
								  
																					   
									
												 
		 
		
        document.getElementById('downloadProgress').classList.remove('hidden');
        document.getElementById('cancelBtn').classList.remove('hidden');
																			
        
        progressInterval = setInterval(async () => {
            try {
                const response = await fetch('/download-progress');
                const data = await response.json();
							  
                
                const progressBarsContainer = document.getElementById('progressBarsContainer');
                if (!progressBarsContainer) return;
				 
                
                if (data.concurrent_downloads && data.concurrent_downloads[fileId]) {
                    const fileData = data.concurrent_downloads[fileId];
                    const percentage = fileData.percentage || 0;
                    const existingProgress = document.getElementById(`progress-${fileId}`);
                    
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
            } catch (error) {
                console.error('Progress update error:', error);
            }
        }, 500);

        const response = await fetch(`/files/download/${messageId}?channel_username=${channel}`, {
            method: 'POST'
        });

        const data = await response.json();

        if (progressInterval) {
            clearInterval(progressInterval);
        }

        if (response.ok) {
            // Mark as complete
            completedDownloads.set(fileId, data.file_path);
            
            // Get the last progress data to show final size
            const progressResponse = await fetch('/download-progress');
            const progressData = await progressResponse.json();
            let finalSize = 0;
            
            if (progressData.concurrent_downloads && progressData.concurrent_downloads[fileId]) {
                finalSize = progressData.concurrent_downloads[fileId].total;
            }
            
            const existingProgress = document.getElementById(`progress-${fileId}`);
            if (existingProgress) {
                existingProgress.outerHTML = createProgressBar(
                    fileId,
                    data.file_path.split('/').pop(),
                    true,
                    100,
                    finalSize,
                    finalSize
                );
            }
            
            document.getElementById('cancelBtn').classList.add('hidden');
						
            showAlert('downloadAlert', 'File downloaded successfully!', 'success');
        } else {
            showAlert('downloadAlert', data.detail, 'error');
            document.getElementById('cancelBtn').classList.add('hidden');
            clearIndividualProgress(fileId);
        }
    } catch (error) {
        if (progressInterval) {
            clearInterval(progressInterval);
        }
        showAlert('downloadAlert', 'Error: ' + error.message, 'error');
        document.getElementById('cancelBtn').classList.add('hidden');
        clearIndividualProgress(fileId);
    }
}

async function downloadAll() {
    const channel = document.getElementById('channelUsername').value;
    const limit = document.getElementById('fileLimit').value;
    const fileType = document.getElementById('fileType').value;

    if (!channel) {
        showAlert('downloadAlert', 'Please select a channel', 'error');
        return;
    }

    try {
        const response = await fetch('/files/download-all', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                channel_username: channel,
                limit: parseInt(limit),
                filter_type: fileType || null
            })
        });

        const data = await response.json();

        if (response.ok) {
            showAlert('downloadAlert', data.message, 'success');
            
									  
																						   
										
													 
			 
			
            document.getElementById('downloadProgress').classList.remove('hidden');
            document.getElementById('cancelBtn').classList.remove('hidden');
																				
            startProgressMonitoring();
        } else {
            showAlert('downloadAlert', data.detail, 'error');
        }
    } catch (error) {
        showAlert('downloadAlert', 'Error: ' + error.message, 'error');
    }
}

async function cancelDownload() {
    try {
        const response = await fetch('/download/cancel', {
            method: 'POST'
        });

        const data = await response.json();

        if (response.ok) {
            showAlert('downloadAlert', data.message, 'info');
            
												  
            if (progressMonitoringInterval) {
                clearInterval(progressMonitoringInterval);
                progressMonitoringInterval = null;
            }
            
            // Keep completed downloads, only remove active ones
            const progressBarsContainer = document.getElementById('progressBarsContainer');
            if (progressBarsContainer) {
                const activeDownloads = progressBarsContainer.querySelectorAll('.file-progress-block');
                activeDownloads.forEach(block => {
                    const fileId = block.id.replace('progress-', '');
                    if (!completedDownloads.has(fileId)) {
                        block.remove();
                    }
                });
            }
            
								 
            const overallText = document.getElementById('overallText');
            if (overallText) {
                overallText.textContent = '';
            }
            
												 
            selectedFiles.clear();
            updateSelectedCount();
            deselectAllFiles();
            
													
																				
            document.getElementById('cancelBtn').classList.add('hidden');
																				
        } else {
            showAlert('downloadAlert', data.detail || data.message, 'error');
        }
    } catch (error) {
        showAlert('downloadAlert', 'Error: ' + error.message, 'error');
    }
}

function startProgressMonitoring() {
										
    if (progressMonitoringInterval) {
        clearInterval(progressMonitoringInterval);
    }
    
    let hasStarted = false;
    
    progressMonitoringInterval = setInterval(async () => {
        try {
            const response = await fetch('/download-progress');
            const data = await response.json();

														
            if (data.active) {
                hasStarted = true;
            }

																					
            if (!data.active && hasStarted) {
                clearInterval(progressMonitoringInterval);
                progressMonitoringInterval = null;
                document.getElementById('cancelBtn').classList.add('hidden');
																					   
                
                // Mark all active downloads as complete
                if (data.concurrent_downloads && Object.keys(data.concurrent_downloads).length === 0) {
                    const progressBarsContainer = document.getElementById('progressBarsContainer');
                    if (progressBarsContainer) {
                        const activeBlocks = progressBarsContainer.querySelectorAll('.file-progress-block');
                        activeBlocks.forEach(block => {
                            const fileId = block.id.replace('progress-', '');
                            if (!completedDownloads.has(fileId)) {
                                const fileName = block.querySelector('.file-name').textContent;
                                const progressFill = block.querySelector('.progress-fill');
                                const width = progressFill.style.width;
                                
                                // Only mark as complete if it reached 100%
                                if (width === '100%') {
                                    completedDownloads.set(fileId, fileName);
                                    block.outerHTML = createProgressBar(fileId, fileName, true, 100, 1, 1);
                                }
                            }
                        });
                    }
                }
                
                return;
            }

            const overallText = document.getElementById('overallText');
            if (overallText) {
                overallText.textContent = `${data.progress}/${data.total} files`;
            }

            const progressBarsContainer = document.getElementById('progressBarsContainer');
            if (!progressBarsContainer) return;
            
							  
							  
            if (data.active && data.concurrent_downloads) {
                for (const [fileId, fileData] of Object.entries(data.concurrent_downloads)) {
                    const percentage = fileData.percentage || 0;
                    const existingProgress = document.getElementById(`progress-${fileId}`);
                    
                    if (existingProgress && !completedDownloads.has(fileId)) {
                        existingProgress.outerHTML = createProgressBar(
                            fileId,
																									 
						  
									 
																																												   
                            fileData.name,
                            false,
                            percentage,
									  
                            fileData.progress,
                            fileData.total
                        );
                    } else if (!existingProgress && !completedDownloads.has(fileId)) {
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
    const toastContainer = document.getElementById('toastContainer');
    if (!toastContainer) return;
    let color = 'bg-indigo-500';
    let icon = 'fa-info-circle';
    if (type === 'success') { color = 'bg-green-500'; icon = 'fa-circle-check'; }
    else if (type === 'error') { color = 'bg-red-500'; icon = 'fa-circle-xmark'; }
    else if (type === 'warning') { color = 'bg-yellow-400 text-gray-900'; icon = 'fa-triangle-exclamation'; }
    else if (type === 'info') { color = 'bg-blue-500'; icon = 'fa-circle-info'; }
    const toast = document.createElement('div');
    toast.className = `flex items-center gap-3 px-4 py-3 rounded shadow-lg text-white text-sm font-medium ${color} animate-fade-in-up`;
    toast.style.minWidth = '220px';
    toast.innerHTML = `<i class="fa-solid ${icon} text-lg"></i><span>${message}</span>`;
    toastContainer.appendChild(toast);
    setTimeout(() => {
        toast.classList.add('opacity-0');
        toast.style.transition = 'opacity 0.5s';
        setTimeout(() => toast.remove(), 500);
    }, 4000);
}

// Toast fade-in animation
const style = document.createElement('style');
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
    await checkStatus();
    const connectionStatus = document.getElementById('connectionStatus');
    if (connectionStatus && connectionStatus.textContent.includes('Connected')) {
        await loadChannels();
    }
})();
statusCheckInterval = setInterval(checkStatus, 5000);