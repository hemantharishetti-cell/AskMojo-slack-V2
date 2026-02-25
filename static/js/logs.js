// Logs Management JavaScript

// Get API client (resolve lazily in case script load order/caching differs)
function getApiClient() {
    return window.apiClient || (typeof apiClient !== 'undefined' ? apiClient : null);
}

if (!getApiClient()) {
    console.error('API client not available. Make sure api.js is loaded before logs.js');
}

// Helper function to escape HTML
function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// Load query logs
async function loadQueryLogs() {
    const tbody = document.getElementById('queryLogsTableBody');
    if (!tbody) return;

    const client = getApiClient();

    if (!client || !client.getQueryLogs) {
        console.error('API client or getQueryLogs method not available');
        tbody.innerHTML = '<tr><td colspan="8" class="text-center py-8 text-red-500 text-sm">API client not initialized. Please refresh the page.</td></tr>';
        return;
    }

    try {
        tbody.innerHTML = '<tr><td colspan="8" class="text-center py-8 text-gray-500 text-sm">Loading query logs...</td></tr>';
        const response = await client.getQueryLogs(0, 100);
        console.log('Query logs response:', response);
        
        // Handle both direct array and wrapped response
        const logs = Array.isArray(response) ? response : (response.data || response.logs || []);
        console.log('Parsed query logs:', logs, 'Count:', logs.length);
        
        if (!logs || logs.length === 0) {
            tbody.innerHTML = `
                <tr>
                    <td colspan="8" class="text-center py-8">
                        <div class="text-gray-500 text-sm">
                            <i class="fas fa-info-circle mr-2"></i>
                            No query logs found
                        </div>
                        <div class="text-gray-400 text-xs mt-2">
                            Query logs will appear here after users ask questions
                        </div>
                    </td>
                </tr>
            `;
            return;
        }

        tbody.innerHTML = logs.map(log => {
            const date = new Date(log.created_at).toLocaleString();
            const queryPreview = log.query.length > 50 ? log.query.substring(0, 50) + '...' : log.query;
            const processingTime = log.processing_time_seconds ? `${log.processing_time_seconds.toFixed(2)}s` : 'N/A';
            const tokensUsed = log.total_tokens_used ? log.total_tokens_used.toLocaleString() : 'N/A';
            const tokenSavings = log.token_savings_percent ? `${log.token_savings_percent.toFixed(1)}%` : '';
            
            return `
                <tr class="hover:bg-gray-50" data-log-id="${log.id}">
                    <td class="text-sm text-gray-700">${log.id}</td>
                    <td class="text-sm text-gray-700">
                        ${escapeHtml(log.user_name || 'System')}${log.user_email ? `<br><span class="text-xs text-gray-500">${escapeHtml(log.user_email)}</span>` : ''}
                        ${log.slack_user_email ? `<br><span class="text-xs text-purple-600 font-medium"><i class="fab fa-slack mr-1"></i>Slack: ${escapeHtml(log.slack_user_email)}</span>` : ''}
                    </td>
                    <td class="text-sm text-gray-700" title="${escapeHtml(log.query)}">${escapeHtml(queryPreview)}</td>
                    <td class="text-sm text-gray-700">
                        <span class="font-medium">${processingTime}</span>
                    </td>
                    <td class="text-sm text-gray-700">
                        <div class="font-medium">${tokensUsed}</div>
                        ${tokenSavings ? `<div class="text-xs text-green-600">Saved ${tokenSavings}</div>` : ''}
                    </td>
                    <td class="text-sm text-gray-700">${log.source_count || 0}</td>
                    <td class="text-sm text-gray-500">${date}</td>
                    <td class="text-sm">
                        <button onclick="viewQueryLogDetails(${log.id})" class="btn btn-sm btn-ghost text-purple-600 hover:text-purple-700" title="View Details">
                            <i class="fas fa-eye"></i>
                        </button>
                    </td>
                </tr>
            `;
        }).join('');
    } catch (error) {
        console.error('Failed to load query logs:', error);
        console.error('Error details:', error.message, error.stack);
        tbody.innerHTML = `<tr><td colspan="8" class="text-center py-8 text-red-500 text-sm">Failed to load query logs: ${error.message}</td></tr>`;
    }
}

// Load upload logs
async function loadUploadLogs() {
    const tbody = document.getElementById('uploadLogsTableBody');
    if (!tbody) return;

    const client = getApiClient();

    if (!client || !client.getUploadLogs) {
        console.error('API client or getUploadLogs method not available');
        tbody.innerHTML = '<tr><td colspan="8" class="text-center py-8 text-red-500 text-sm">API client not initialized. Please refresh the page.</td></tr>';
        return;
    }

    try {
        tbody.innerHTML = '<tr><td colspan="8" class="text-center py-8 text-gray-500 text-sm">Loading upload logs...</td></tr>';
        const response = await client.getUploadLogs(0, 100);
        console.log('Upload logs response:', response);
        console.log('Response type:', typeof response, 'Is array:', Array.isArray(response));
        
        // Handle both direct array and wrapped response
        const logs = Array.isArray(response) ? response : (response.data || response.logs || []);
        console.log('Parsed upload logs:', logs, 'Count:', logs.length);
        console.log('First log:', logs[0]);
        
        if (!logs || logs.length === 0) {
            tbody.innerHTML = `
                <tr>
                    <td colspan="8" class="text-center py-8">
                        <div class="text-gray-500 text-sm">
                            <i class="fas fa-info-circle mr-2"></i>
                            No upload logs found
                        </div>
                        <div class="text-gray-400 text-xs mt-2">
                            Upload logs will appear here after documents are uploaded
                        </div>
                    </td>
                </tr>
            `;
            return;
        }

        const html = logs.map(log => {
            console.log('Rendering log:', log);
            try {
                const date = log.created_at ? new Date(log.created_at).toLocaleString() : 'N/A';
                let statusBadge = '';
                if (log.processing_error) {
                    statusBadge = '<span class="badge badge-error">Error</span>';
                } else if (log.processing_completed) {
                    statusBadge = '<span class="badge badge-success">Completed</span>';
                } else if (log.processing_started) {
                    statusBadge = '<span class="badge badge-warning">Processing</span>';
                } else {
                    statusBadge = '<span class="badge badge-info">Pending</span>';
                }

                const title = log.title || log.document_title || 'Untitled';
                const fileName = log.file_name || '';
                const uploaderName = log.uploader_name || 'Unknown';
                const uploaderEmail = log.uploader_email || '';
                const categoryName = log.category_name || log.category || 'N/A';
                
                // Time display
                const uploadTime = log.upload_time_seconds ? `${log.upload_time_seconds.toFixed(2)}s` : 'N/A';
                const descTime = log.description_generation_time_seconds ? `${log.description_generation_time_seconds.toFixed(2)}s` : '';
                
                // Token display
                const tokensUsed = log.description_tokens_used ? log.description_tokens_used.toLocaleString() : 'N/A';
                const tokensBreakdown = log.description_tokens_prompt && log.description_tokens_completion 
                    ? `<div class="text-xs text-gray-500">P: ${log.description_tokens_prompt.toLocaleString()}, C: ${log.description_tokens_completion.toLocaleString()}</div>`
                    : '';

                return `
                    <tr class="hover:bg-gray-50">
                        <td class="text-sm text-gray-700">${log.id || 'N/A'}</td>
                        <td class="text-sm text-gray-700">
                            <div class="font-medium">${escapeHtml(title)}</div>
                            ${fileName ? `<div class="text-xs text-gray-500">${escapeHtml(fileName)}</div>` : ''}
                        </td>
                        <td class="text-sm text-gray-700">
                            ${escapeHtml(uploaderName)}${uploaderEmail ? `<br><span class="text-xs text-gray-500">${escapeHtml(uploaderEmail)}</span>` : ''}
                        </td>
                        <td class="text-sm text-gray-700">${escapeHtml(categoryName)}</td>
                        <td class="text-sm text-gray-700">
                            <div class="font-medium">${uploadTime}</div>
                            ${descTime ? `<div class="text-xs text-gray-500">Desc: ${descTime}</div>` : ''}
                        </td>
                        <td class="text-sm text-gray-700">
                            <div class="font-medium">${tokensUsed}</div>
                            ${tokensBreakdown}
                        </td>
                        <td class="text-sm">${statusBadge}</td>
                        <td class="text-sm text-gray-500">${date}</td>
                    </tr>
                `;
            } catch (err) {
                console.error('Error rendering log:', log, err);
                return `<tr><td colspan="8" class="text-red-500">Error rendering log: ${err.message}</td></tr>`;
            }
        }).join('');
        
        if (html && html.length > 0) {
            tbody.innerHTML = html;
            console.log('Successfully rendered', logs.length, 'upload log(s)');
            console.log('Table body element:', tbody);
            console.log('Table body innerHTML length:', tbody.innerHTML.length);
        } else {
            console.error('Generated HTML is empty!');
            tbody.innerHTML = '<tr><td colspan="8" class="text-center py-8 text-red-500 text-sm">Error: Failed to generate table rows</td></tr>';
        }
    } catch (error) {
        console.error('Failed to load upload logs:', error);
        console.error('Error details:', error.message, error.stack);
        tbody.innerHTML = `<tr><td colspan="8" class="text-center py-8 text-red-500 text-sm">Failed to load upload logs: ${error.message}</td></tr>`;
    }
}

// Tab switching
function initTabs() {
    const tabs = document.querySelectorAll('[data-tab]');
    tabs.forEach(tab => {
        tab.addEventListener('click', (e) => {
            e.preventDefault();
            const tabName = tab.getAttribute('data-tab');
            
            // Update tab active state
            tabs.forEach(t => t.classList.remove('tab-active'));
            tab.classList.add('tab-active');
            
            // Show/hide content
            const queryTab = document.getElementById('queryLogsTab');
            const uploadTab = document.getElementById('uploadLogsTab');
            
            if (tabName === 'queries') {
                if (queryTab) {
                    queryTab.classList.remove('hidden');
                    queryTab.style.display = 'block';
                }
                if (uploadTab) {
                    uploadTab.classList.add('hidden');
                    uploadTab.style.display = 'none';
                }
                loadQueryLogs();
            } else if (tabName === 'uploads') {
                if (queryTab) {
                    queryTab.classList.add('hidden');
                    queryTab.style.display = 'none';
                }
                if (uploadTab) {
                    uploadTab.classList.remove('hidden');
                    uploadTab.style.display = 'block';
                }
                loadUploadLogs();
            }
        });
    });
}

// Load logs when page is shown
function loadLogs() {
    console.log('loadLogs called');
    const activeTab = document.querySelector('.tab-active');
    console.log('Active tab:', activeTab, activeTab ? activeTab.getAttribute('data-tab') : 'none');
    
    // Ensure query logs tab is visible by default
    const queryTab = document.getElementById('queryLogsTab');
    const uploadTab = document.getElementById('uploadLogsTab');
    
    if (activeTab && activeTab.getAttribute('data-tab') === 'queries') {
        if (queryTab) {
            queryTab.classList.remove('hidden');
            queryTab.style.display = 'block';
        }
        if (uploadTab) {
            uploadTab.classList.add('hidden');
            uploadTab.style.display = 'none';
        }
        loadQueryLogs();
    } else {
        // Default to upload logs if no active tab or if uploads tab is active
        if (queryTab) {
            queryTab.classList.add('hidden');
            queryTab.style.display = 'none';
        }
        if (uploadTab) {
            uploadTab.classList.remove('hidden');
            uploadTab.style.display = 'block';
        }
        loadUploadLogs();
    }
}

// View query log details
async function viewQueryLogDetails(logId) {
    console.log('Viewing details for log:', logId);
    const modal = document.getElementById('queryLogDetailsModal');
    const content = document.getElementById('queryLogDetailsContent');

    const client = getApiClient();
    
    if (!modal || !content) {
        console.error('Modal elements not found');
        return;
    }
    
    try {
        // Fetch the specific log (we'll need to get it from the already loaded logs or fetch it)
        const response = await client.getQueryLogs(0, 1000); // Get more logs to find the one we need
        const logs = Array.isArray(response) ? response : (response.data || response.logs || []);
        const log = (logs || []).find(l => l.id === logId);
        
        if (!log) {
            content.innerHTML = '<div class="text-red-500">Log not found</div>';
            return;
        }
        
        // Parse JSON strings
        let tokenUsage = null;
        let apiCalls = null;
        let toonSavings = null;
        
        try {
            if (log.token_usage_json) tokenUsage = JSON.parse(log.token_usage_json);
            if (log.api_calls_json) apiCalls = JSON.parse(log.api_calls_json);
            if (log.toon_savings_json) toonSavings = JSON.parse(log.toon_savings_json);
        } catch (e) {
            console.error('Error parsing JSON:', e);
        }
        
        const date = new Date(log.created_at).toLocaleString();
        const processingTime = log.processing_time_seconds ? `${log.processing_time_seconds.toFixed(2)} seconds` : 'N/A';
        
        content.innerHTML = `
            <div class="space-y-6">
                <!-- Basic Info -->
                <div class="bg-gray-50 rounded-xl p-4">
                    <h4 class="font-bold text-gray-900 mb-3 flex items-center gap-2">
                        <i class="fas fa-info-circle text-purple-600"></i>
                        Basic Information
                    </h4>
                    <div class="grid grid-cols-2 gap-4 text-sm">
                        <div>
                            <span class="text-gray-600 font-medium">Query ID:</span>
                            <span class="text-gray-900 ml-2">${log.id}</span>
                        </div>
                        <div>
                            <span class="text-gray-600 font-medium">User:</span>
                            <span class="text-gray-900 ml-2">${escapeHtml(log.user_name || 'System')}</span>
                            ${log.user_email ? `<br><span class="text-xs text-gray-500">${escapeHtml(log.user_email)}</span>` : ''}
                            ${log.slack_user_email ? `<br><span class="text-xs text-purple-600 font-medium"><i class="fab fa-slack mr-1"></i>Slack: ${escapeHtml(log.slack_user_email)}</span>` : ''}
                        </div>
                        <div>
                            <span class="text-gray-600 font-medium">Date:</span>
                            <span class="text-gray-900 ml-2">${date}</span>
                        </div>
                        <div>
                            <span class="text-gray-600 font-medium">Processing Time:</span>
                            <span class="text-gray-900 ml-2 font-semibold">${processingTime}</span>
                        </div>
                        <div class="col-span-2">
                            <span class="text-gray-600 font-medium">Query:</span>
                            <div class="text-gray-900 mt-1 p-2 bg-white rounded border">${escapeHtml(log.query)}</div>
                        </div>
                    </div>
                </div>
                
                <!-- Answer -->
                ${log.answer ? `
                <div class="bg-blue-50 rounded-xl p-4">
                    <h4 class="font-bold text-gray-900 mb-3 flex items-center gap-2">
                        <i class="fas fa-comment-alt text-blue-600"></i>
                        Generated Answer
                    </h4>
                    <div class="text-gray-800 whitespace-pre-wrap bg-white p-4 rounded border max-h-96 overflow-y-auto">${escapeHtml(log.answer)}</div>
                </div>
                ` : ''}
                
                <!-- Token Usage -->
                ${tokenUsage ? `
                <div class="bg-green-50 rounded-xl p-4">
                    <h4 class="font-bold text-gray-900 mb-3 flex items-center gap-2">
                        <i class="fas fa-coins text-green-600"></i>
                        Token Usage
                    </h4>
                    <div class="grid grid-cols-2 gap-4 text-sm">
                        <div>
                            <span class="text-gray-600 font-medium">Total Tokens Used:</span>
                            <span class="text-gray-900 ml-2 font-semibold">${tokenUsage.total_tokens_used?.toLocaleString() || 'N/A'}</span>
                        </div>
                        <div>
                            <span class="text-gray-600 font-medium">Without TOON:</span>
                            <span class="text-gray-900 ml-2">${tokenUsage.total_tokens_without_toon?.toLocaleString() || 'N/A'}</span>
                        </div>
                        <div>
                            <span class="text-gray-600 font-medium">Savings:</span>
                            <span class="text-green-600 ml-2 font-semibold">${tokenUsage.total_savings?.toLocaleString() || 0} tokens</span>
                        </div>
                        <div>
                            <span class="text-gray-600 font-medium">Savings %:</span>
                            <span class="text-green-600 ml-2 font-semibold">${tokenUsage.total_savings_percent?.toFixed(2) || 0}%</span>
                        </div>
                    </div>
                    ${tokenUsage.breakdown_by_call && tokenUsage.breakdown_by_call.length > 0 ? `
                    <div class="mt-4">
                        <h5 class="font-semibold text-gray-700 mb-2">Breakdown by Call:</h5>
                        <div class="space-y-2">
                            ${tokenUsage.breakdown_by_call.map(call => `
                                <div class="bg-white p-3 rounded border text-xs">
                                    <div class="font-medium text-gray-900">${escapeHtml(call.call)}</div>
                                    <div class="grid grid-cols-3 gap-2 mt-2 text-gray-600">
                                        <div>Used: ${call.tokens_used?.toLocaleString() || 0}</div>
                                        <div>Without TOON: ${call.tokens_without_toon?.toLocaleString() || 0}</div>
                                        <div class="text-green-600">Saved: ${call.savings?.toLocaleString() || 0} (${call.savings_percent?.toFixed(2) || 0}%)</div>
                                    </div>
                                </div>
                            `).join('')}
                        </div>
                    </div>
                    ` : ''}
                </div>
                ` : ''}
                
                <!-- API Calls -->
                ${apiCalls && apiCalls.length > 0 ? `
                <div class="bg-purple-50 rounded-xl p-4">
                    <h4 class="font-bold text-gray-900 mb-3 flex items-center gap-2">
                        <i class="fas fa-code text-purple-600"></i>
                        API Calls (${apiCalls.length})
                    </h4>
                    <div class="space-y-3">
                        ${apiCalls.map((call, idx) => `
                            <div class="bg-white p-4 rounded border">
                                <div class="flex items-center justify-between mb-2">
                                    <h5 class="font-semibold text-gray-900">${escapeHtml(call.call_name || `Call ${idx + 1}`)}</h5>
                                    <span class="badge badge-info">${call.model_used || 'N/A'}</span>
                                </div>
                                <div class="grid grid-cols-2 gap-2 text-xs text-gray-600 mb-2">
                                    <div>Tokens: ${call.tokens_used?.toLocaleString() || 0}</div>
                                    <div>Savings: ${call.savings?.toLocaleString() || 0} (${call.savings_percent?.toFixed(2) || 0}%)</div>
                                </div>
                                ${call.request_prompt ? `
                                <details class="mt-2">
                                    <summary class="cursor-pointer text-xs text-purple-600 hover:text-purple-700">View Request Prompt</summary>
                                    <pre class="mt-2 p-2 bg-gray-50 rounded text-xs overflow-x-auto max-h-40 overflow-y-auto">${escapeHtml(call.request_prompt)}</pre>
                                </details>
                                ` : ''}
                                ${call.response_content ? `
                                <details class="mt-2">
                                    <summary class="cursor-pointer text-xs text-purple-600 hover:text-purple-700">View Response</summary>
                                    <pre class="mt-2 p-2 bg-gray-50 rounded text-xs overflow-x-auto max-h-40 overflow-y-auto">${typeof call.response_content === 'string' ? escapeHtml(call.response_content) : escapeHtml(JSON.stringify(call.response_content, null, 2))}</pre>
                                </details>
                                ` : ''}
                            </div>
                        `).join('')}
                    </div>
                </div>
                ` : ''}
                
                <!-- TOON Savings -->
                ${toonSavings ? `
                <div class="bg-yellow-50 rounded-xl p-4">
                    <h4 class="font-bold text-gray-900 mb-3 flex items-center gap-2">
                        <i class="fas fa-chart-line text-yellow-600"></i>
                        TOON Savings Breakdown
                    </h4>
                    <div class="text-sm">
                        <div class="grid grid-cols-2 gap-4 mb-3">
                            <div>
                                <span class="text-gray-600 font-medium">Total Savings:</span>
                                <span class="text-yellow-600 ml-2 font-semibold">${toonSavings.total_savings?.toLocaleString() || 0} tokens</span>
                            </div>
                            <div>
                                <span class="text-gray-600 font-medium">Savings %:</span>
                                <span class="text-yellow-600 ml-2 font-semibold">${toonSavings.total_savings_percent?.toFixed(2) || 0}%</span>
                            </div>
                        </div>
                        ${toonSavings.by_call && toonSavings.by_call.length > 0 ? `
                        <div class="space-y-2">
                            ${toonSavings.by_call.map(call => `
                                <div class="bg-white p-2 rounded border text-xs">
                                    <span class="font-medium">${escapeHtml(call.call_name)}:</span>
                                    <span class="text-yellow-600 ml-2">${call.savings?.toLocaleString() || 0} tokens (${call.savings_percent?.toFixed(2) || 0}%)</span>
                                </div>
                            `).join('')}
                        </div>
                        ` : ''}
                    </div>
                </div>
                ` : ''}
            </div>
        `;
        
        // Show modal
        modal.classList.remove('hidden');
        modal.style.display = 'flex';
        modal.style.visibility = 'visible';
        modal.style.opacity = '1';
        
        // Close button handlers
        const closeBtn = document.getElementById('closeQueryLogDetailsModal');
        const closeBtn2 = document.getElementById('closeQueryLogDetailsModalBtn');
        
        const closeModal = () => {
            modal.classList.add('hidden');
            modal.style.display = 'none';
        };
        
        if (closeBtn) closeBtn.onclick = closeModal;
        if (closeBtn2) closeBtn2.onclick = closeModal;
        
    } catch (error) {
        console.error('Error loading log details:', error);
        content.innerHTML = `<div class="text-red-500">Error loading log details: ${error.message}</div>`;
    }
}

// Make functions globally available
window.loadQueryLogs = loadQueryLogs;
window.loadUploadLogs = loadUploadLogs;
window.loadLogs = loadLogs;
window.viewQueryLogDetails = viewQueryLogDetails;

// Initialize tabs when DOM is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initTabs);
} else {
    initTabs();
}

