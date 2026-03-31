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
        let logs = Array.isArray(response) ? response : (response.data || response.logs || []);

        // Apply global search filter
        if (window.adminSearchQuery) {
            const query = window.adminSearchQuery.toLowerCase();
            logs = logs.filter(log =>
                (log.query && log.query.toLowerCase().includes(query)) ||
                (log.user_name && log.user_name.toLowerCase().includes(query)) ||
                (log.user_email && log.user_email.toLowerCase().includes(query)) ||
                (log.slack_user_email && log.slack_user_email.toLowerCase().includes(query)) ||
                (log.answer && log.answer.toLowerCase().includes(query))
            );
        }

        console.log('Parsed query logs:', logs, 'Count:', logs.length);

        if (!logs || logs.length === 0) {
            tbody.innerHTML = `
                <tr>
                    <td colspan="8" class="text-center py-8">
                        <div class="text-gray-500 text-sm">
                            <i class="fas fa-info-circle mr-2"></i>
                            ${window.adminSearchQuery ? 'No query logs match "' + window.adminSearchQuery + '"' : 'No query logs found'}
                        </div>
                        ${!window.adminSearchQuery ? `
                        <div class="text-gray-400 text-xs mt-2">
                            Query logs will appear here after users ask questions
                        </div>` : ''}
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
        tbody.innerHTML = '<tr><td colspan="9" class="text-center py-8 text-gray-500 text-sm">Loading upload logs...</td></tr>';
        const response = await client.getUploadLogs(0, 100);
        console.log('Upload logs response:', response);
        console.log('Response type:', typeof response, 'Is array:', Array.isArray(response));

        // Handle both direct array and wrapped response
        let logs = Array.isArray(response) ? response : (response.data || response.logs || []);

        // Apply global search filter
        if (window.adminSearchQuery) {
            const query = window.adminSearchQuery.toLowerCase();
            logs = logs.filter(log =>
                (log.title && log.title.toLowerCase().includes(query)) ||
                (log.document_title && log.document_title.toLowerCase().includes(query)) ||
                (log.file_name && log.file_name.toLowerCase().includes(query)) ||
                (log.uploader_name && log.uploader_name.toLowerCase().includes(query)) ||
                (log.uploader_email && log.uploader_email.toLowerCase().includes(query)) ||
                (log.category_name && log.category_name.toLowerCase().includes(query)) ||
                (log.category && log.category.toLowerCase().includes(query))
            );
        }

        console.log('Parsed upload logs:', logs, 'Count:', logs.length);
        console.log('First log:', logs[0]);

        if (!logs || logs.length === 0) {
            tbody.innerHTML = `
                <tr>
                    <td colspan="9" class="text-center py-8">
                        <div class="text-gray-500 text-sm">
                            <i class="fas fa-info-circle mr-2"></i>
                            ${window.adminSearchQuery ? 'No upload logs match "' + window.adminSearchQuery + '"' : 'No upload logs found'}
                        </div>
                        ${!window.adminSearchQuery ? `
                        <div class="text-gray-400 text-xs mt-2">
                            Upload logs will appear here after documents are uploaded
                        </div>` : ''}
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
                        <td class="text-sm">
                            <button onclick="viewUploadLogDetails(${log.id})" class="btn btn-sm btn-ghost text-green-600 hover:text-green-700" title="View Details">
                                <i class="fas fa-eye"></i>
                            </button>
                        </td>
                    </tr>
                `;
            } catch (err) {
                console.error('Error rendering log:', log, err);
                return `<tr><td colspan="9" class="text-red-500">Error rendering log: ${err.message}</td></tr>`;
            }
        }).join('');

        if (html && html.length > 0) {
            tbody.innerHTML = html;
            console.log('Successfully rendered', logs.length, 'upload log(s)');
            console.log('Table body element:', tbody);
            console.log('Table body innerHTML length:', tbody.innerHTML.length);
        } else {
            console.error('Generated HTML is empty!');
            tbody.innerHTML = '<tr><td colspan="9" class="text-center py-8 text-red-500 text-sm">Error: Failed to generate table rows</td></tr>';
        }
    } catch (error) {
        console.error('Failed to load upload logs:', error);
        console.error('Error details:', error.message, error.stack);
        tbody.innerHTML = `<tr><td colspan="9" class="text-center py-8 text-red-500 text-sm">Failed to load upload logs: ${error.message}</td></tr>`;
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
            if (!tokenUsage) {
                tokenUsage = {
                    total_tokens_used: log.total_tokens_used,
                    total_tokens_without_toon: log.total_tokens_without_toon,
                    total_savings: log.token_savings,
                    total_savings_percent: log.token_savings_percent
                };
            }
            if (!apiCalls && log.api_calls_json) {
                apiCalls = [{
                    call_name: 'Stored Query Log (raw)',
                    model_used: 'N/A',
                    tokens_used: log.total_tokens_used || 0,
                    time_taken_seconds: log.processing_time_seconds || 0,
                    response_content: log.api_calls_json
                }];
            }
            if (!toonSavings && log.toon_savings_json) {
                toonSavings = {
                    total_savings: log.token_savings,
                    total_savings_percent: log.token_savings_percent,
                    raw_payload: log.toon_savings_json
                };
            }
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
                    ${renderSystemWarnings(tokenUsage)}
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
                                    <div>Time Taken: ${call.time_taken_seconds !== undefined ? call.time_taken_seconds + 's' : 'N/A'}</div>
                                    ${call.savings !== undefined ? `<div>Savings: ${call.savings?.toLocaleString() || 0} (${call.savings_percent?.toFixed(2) || 0}%)</div>` : ''}
                                </div>
                                ${call.request_prompt ? `
                                <details class="mt-2">
                                    <summary class="cursor-pointer text-xs text-purple-600 hover:text-purple-700">View Request Prompt</summary>
                                    <pre class="mt-2 p-2 bg-gray-50 rounded text-xs overflow-x-auto max-h-40 overflow-y-auto">${renderToolInput(call.call_name, call.request_prompt)}</pre>
                                </details>
                                ` : ''}
                                ${call.response_content ? `
                                <details class="mt-2">
                                    <summary class="cursor-pointer text-xs text-purple-600 hover:text-purple-700">View Response</summary>
                                    <div class="mt-2 text-xs">
                                        ${renderToolOutput(call.call_name, call.response_content)}
                                    </div>
                                </details>
                                ` : ''}
                            </div>
                        `).join('')}
                    </div>
                </div>
                ` : ''}
                
                <!-- Sources Used -->
                ${log.sources && log.sources.length > 0 ? `
                <div class="bg-orange-50 rounded-xl p-4">
                    <h4 class="font-bold text-gray-900 mb-3 flex items-center gap-2">
                        <i class="fas fa-file-alt text-orange-600"></i>
                        Sources Used (${log.sources.length})
                    </h4>
                    <div class="space-y-2">
                        ${log.sources.map((src, idx) => `
                            <div class="bg-white p-3 rounded border flex items-start gap-3">
                                <div class="w-7 h-7 rounded-full bg-orange-100 flex items-center justify-center flex-shrink-0 mt-0.5">
                                    <span class="text-xs font-bold text-orange-700">${idx + 1}</span>
                                </div>
                                <div class="flex-1 min-w-0">
                                    <div class="text-sm font-semibold text-gray-900 truncate">${escapeHtml(src.document_title || 'Untitled')}</div>
                                    <div class="text-xs text-gray-500 font-mono mt-0.5 truncate">
                                        <i class="fas fa-paperclip mr-1"></i>${escapeHtml(src.file_name || 'Unknown file')}
                                    </div>
                                </div>
                                ${src.relevance_score !== null && src.relevance_score !== undefined ? `
                                <div class="flex-shrink-0 text-right">
                                    <span class="px-2 py-0.5 text-xs font-bold rounded-full bg-orange-100 text-orange-700">${(src.relevance_score * 100).toFixed(0)}%</span>
                                </div>` : ''}
                            </div>
                        `).join('')}
                    </div>
                </div>
                ` : (log.source_count > 0 ? `
                <div class="bg-orange-50 rounded-xl p-4">
                    <h4 class="font-bold text-gray-900 mb-2 flex items-center gap-2">
                        <i class="fas fa-file-alt text-orange-600"></i>
                        Sources Used
                    </h4>
                    <p class="text-sm text-gray-600">${log.source_count} source(s) referenced (details not available for older logs)</p>
                </div>` : '')}

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

// View upload log details
async function viewUploadLogDetails(logId) {
    console.log('Viewing details for upload log:', logId);
    const modal = document.getElementById('uploadLogDetailsModal');
    const content = document.getElementById('uploadLogDetailsContent');

    const client = getApiClient();

    if (!modal || !content) {
        console.error('Upload Modal elements not found');
        return;
    }

    try {
        const response = await client.getUploadLogs(0, 1000);
        const logs = Array.isArray(response) ? response : (response.data || response.logs || []);
        const log = (logs || []).find(l => l.id === logId);

        if (!log) {
            content.innerHTML = '<div class="text-red-500">Upload Log not found</div>';
            return;
        }

        const date = new Date(log.created_at).toLocaleString();
        const documentTitle = escapeHtml(log.title || log.document_title || 'Untitled');
        const chunkCount = typeof log.chunk_count !== 'undefined' ? log.chunk_count : 'N/A';
        const descriptionHTML = renderDocDescription(log.document_description);

        content.innerHTML = `
            <div class="space-y-6">
                <!-- Basic Info -->
                <div class="bg-gray-50 rounded-xl p-4">
                    <h4 class="font-bold text-gray-900 mb-3 flex items-center gap-2">
                        <i class="fas fa-file-alt text-green-600"></i>
                        Document Information
                    </h4>
                    <div class="grid grid-cols-2 gap-4 text-sm">
                        <div>
                            <span class="text-gray-600 font-medium">Log ID:</span>
                            <span class="text-gray-900 ml-2">${log.id}</span>
                        </div>
                        <div>
                            <span class="text-gray-600 font-medium">Document Title:</span>
                            <span class="text-gray-900 ml-2 font-semibold">${documentTitle}</span>
                        </div>
                        <div>
                            <span class="text-gray-600 font-medium">Original File:</span>
                            <span class="text-gray-900 ml-2 font-mono text-xs">${escapeHtml(log.file_name || 'N/A')}</span>
                        </div>
                        <div>
                            <span class="text-gray-600 font-medium">Date Uploaded:</span>
                            <span class="text-gray-900 ml-2">${date}</span>
                        </div>
                        <div class="flex items-center justify-between">
                            <div class="flex items-center">
                                <span class="text-gray-600 font-medium">Chunks Generated:</span>
                                <span class="text-green-600 ml-2 font-bold px-2 py-1 bg-green-100 rounded-md shadow-sm border border-green-200">${chunkCount}</span>
                            </div>
                            <button id="viewChunksBtn" onclick="viewDocumentChunks(${log.document_id})"
                                class="flex items-center gap-2 px-3 py-1.5 text-xs font-semibold text-white bg-indigo-600 hover:bg-indigo-700 active:scale-95 rounded-lg transition-all shadow-sm">
                                <i class="fas fa-layer-group" id="viewChunksBtnIcon"></i>
                                <span>View Chunks</span>
                            </button>
                        </div>
                        <div>
                            <span class="text-gray-600 font-medium">Status:</span>
                            <span class="ml-2">
                                ${log.processing_error ? '<span class="badge badge-error">Error</span>' :
                (log.processing_completed ? '<span class="badge badge-success">Completed</span>' :
                    (log.processing_started ? '<span class="badge badge-warning">Processing</span>' :
                        '<span class="badge badge-info">Pending</span>'))}
                            </span>
                        </div>
                    </div>
                </div>

                <!-- Document Description -->
                <div class="bg-blue-50 rounded-xl p-4">
                    <div class="flex items-center justify-between mb-3">
                        <h4 class="font-bold text-gray-900 flex items-center gap-2">
                            <i class="fas fa-align-left text-blue-600"></i>
                            Generated Description
                        </h4>
                        <button id="regenerateDescBtn" onclick="triggerRegenerateDescription(${log.id})"
                            class="flex items-center gap-2 px-3 py-1.5 text-xs font-semibold text-white bg-blue-600 hover:bg-blue-700 active:scale-95 rounded-lg transition-all shadow-sm">
                            <i class="fas fa-sync-alt" id="regenerateBtnIcon"></i>
                            <span id="regenerateBtnLabel">Regenerate Description</span>
                        </button>
                    </div>
                    <div id="uploadLogDescriptionBox" class="max-h-[60vh] overflow-y-auto">${descriptionHTML}</div>
               </div>

                <!-- Chunks Section (Hidden by default) -->
                <div id="chunksViewSection" class="hidden bg-indigo-50 rounded-xl p-4 mt-4">
                    <div class="flex items-center justify-between mb-3">
                        <h4 class="font-bold text-gray-900 flex items-center gap-2">
                            <i class="fas fa-layer-group text-indigo-600"></i>
                            Document Chunks (Vector DB)
                        </h4>
                        <span id="chunksCountBadge" class="badge badge-info text-xs"></span>
                    </div>
                    <div id="chunksContentBox" class="max-h-[50vh] overflow-y-auto space-y-3">
                        <!-- Chunks will go here -->
                    </div>
                </div>

                <!-- Chunks Section (Hidden by default) -->
                <div id="chunksViewSection" class="hidden bg-indigo-50 rounded-xl p-4 mt-4">
                    <div class="flex items-center justify-between mb-3">
                        <h4 class="font-bold text-gray-900 flex items-center gap-2">
                            <i class="fas fa-layer-group text-indigo-600"></i>
                            Document Chunks (Vector DB)
                        </h4>
                        <span id="chunksCountBadge" class="badge badge-info text-xs"></span>
                    </div>
                    <div id="chunksContentBox" class="max-h-[50vh] overflow-y-auto space-y-3">
                        <!-- Chunks will go here -->
                    </div>
                </div>
            </div>
        `;

        // Show modal
        modal.classList.remove('hidden');
        modal.style.display = 'flex';
        modal.style.visibility = 'visible';
        modal.style.opacity = '1';

        // Close button handlers
        const closeBtn = document.getElementById('closeUploadLogDetailsModal');
        const closeBtn2 = document.getElementById('closeUploadLogDetailsModalBtn');

        const closeModal = () => {
            modal.classList.add('hidden');
            modal.style.display = 'none';
        };

        if (closeBtn) closeBtn.onclick = closeModal;
        if (closeBtn2) closeBtn2.onclick = closeModal;

    } catch (error) {
        console.error('Error loading config details:', error);
        content.innerHTML = `<div class="text-red-500">Error loading upload details: ${error.message}</div>`;
    }
}

// ── Render document description (pretty-printed JSON or plain text fallback) ──
function renderDocDescription(raw) {
    if (!raw) {
        return `<div class="text-gray-400 italic text-sm p-4">No description available for this document.</div>`;
    }

    // Try to parse and pretty-print JSON
    let pretty = raw;
    try {
        const parsed = JSON.parse(raw);
        pretty = JSON.stringify(parsed, null, 2);
    } catch (_) { /* not JSON — display as-is */ }

    return `<pre class="text-gray-800 text-xs font-mono bg-white p-4 rounded border leading-relaxed shadow-inner overflow-x-auto whitespace-pre-wrap break-words">${escapeHtml(pretty)}</pre>`;
}

// ── Render Tool-specific outputs ──
function renderToolOutput(toolName, output) {
    if (!output) return '<div class="text-gray-400 italic">No output</div>';

    // Tool 1: get_all_collections
    const lowerName = toolName.toLowerCase();
    if (lowerName.includes('get_all_collections')) {
        let data = output;
        if (typeof output === 'string') {
            try { data = JSON.parse(output); } catch (e) { return renderDocDescription(output); }
        }

        const collections = data.collections || [];
        if (collections.length === 0) return '<div class="text-gray-500 italic">No collections found.</div>';

        return `
            <div class="overflow-x-auto mt-2">
                <table class="table table-xs w-full border border-gray-200 bg-white">
                    <thead class="bg-gray-50">
                        <tr>
                            <th class="border-b">ID</th>
                            <th class="border-b">Name</th>
                            <th class="border-b">Description</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${collections.map(c => `
                            <tr>
                                <td class="font-mono text-gray-500 border-b">${c.collection_id}</td>
                                <td class="font-bold text-gray-800 border-b">${escapeHtml(c.collection_name)}</td>
                                <td class="text-gray-600 border-b">${c.metadata ? escapeHtml(c.metadata) : '<span class="text-gray-400 italic">Empty (Generate in Categories section)</span>'}</td>
                            </tr>
                        `).join('')}
                    </tbody>
                </table>
            </div>
        `;
    }

    // Default: Pretty JSON
    return renderDocDescription(typeof output === 'string' ? output : JSON.stringify(output, null, 2));
}

function renderSystemWarnings(tokenUsageJson) {
    if (!tokenUsageJson) return '';

    let parsed = tokenUsageJson;
    if (typeof tokenUsageJson === 'string') {
        try {
            parsed = JSON.parse(tokenUsageJson);
        } catch (_) {
            return '';
        }
    }

    const warnings = parsed?.system_warnings;
    if (!Array.isArray(warnings) || warnings.length === 0) return '';

    return warnings.map(w => {
        const title = w?.type === 'embedding_dim_mismatch'
            ? 'Critical: Embedding mismatch detected'
            : 'System warning';
        const collection = w?.collection_name ? `<div class="text-xs mt-1"><strong>Collection:</strong> ${escapeHtml(w.collection_name)}</div>` : '';
        const message = w?.message ? escapeHtml(w.message) : 'No details available.';
        return `
            <div class="mb-3 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-900">
                <div class="font-semibold">${title}</div>
                ${collection}
                <div class="mt-2 whitespace-pre-wrap">${message}</div>
            </div>
        `;
    }).join('');
}

function renderToolInput(toolName, input) {
    if (!input || (typeof input === 'object' && Object.keys(input).length === 0)) {
        const lowerName = toolName.toLowerCase();
        if (lowerName.includes('get_all_collections')) {
            return '<div class="text-xs text-gray-500 italic">None (Fetches all available categories)</div>';
        }
        return '<div class="text-xs text-gray-400 italic">Empty</div>';
    }
    return renderDocDescription(typeof input === 'string' ? input : JSON.stringify(input, null, 2));
}


// Regenerate description handler (called from modal button)
async function triggerRegenerateDescription(logId) {
    const btn = document.getElementById('regenerateDescBtn');
    const icon = document.getElementById('regenerateBtnIcon');
    const label = document.getElementById('regenerateBtnLabel');
    const descBox = document.getElementById('uploadLogDescriptionBox');

    if (!btn || !descBox) return;

    // Show loading state
    btn.disabled = true;
    btn.classList.add('opacity-70', 'cursor-not-allowed');
    icon.classList.add('fa-spin');
    label.textContent = 'Regenerating…';

    try {
        const client = window.apiClient;
        if (!client) throw new Error('API client not available');

        const result = await client.regenerateDescription(logId);

        if (result && result.success) {
            // Live-update the description box with rendered JSON
            descBox.innerHTML = renderDocDescription(result.new_description || '');

            const timeStr = result.generation_time_seconds ? `${result.generation_time_seconds}s` : '';
            const tokenStr = result.tokens_used ? ` · ${result.tokens_used.toLocaleString()} tokens` : '';
            showNotification(`Description regenerated successfully! ${timeStr}${tokenStr}`, 'success');

            // Refresh the upload logs table in the background
            if (typeof loadUploadLogs === 'function') loadUploadLogs();
        } else {
            throw new Error('Unexpected response from server');
        }
    } catch (err) {
        console.error('Regenerate description failed:', err);
        showNotification(err.message || 'Failed to regenerate description', 'error');
    } finally {
        // Restore button state
        btn.disabled = false;
        btn.classList.remove('opacity-70', 'cursor-not-allowed');
        icon.classList.remove('fa-spin');
        label.textContent = 'Regenerate Description';
    }
}

window.triggerRegenerateDescription = triggerRegenerateDescription;
window.renderDocDescription = renderDocDescription;



// Make functions globally available
window.loadQueryLogs = loadQueryLogs;
window.loadUploadLogs = loadUploadLogs;
window.loadLogs = loadLogs;
window.viewQueryLogDetails = viewQueryLogDetails;
window.viewUploadLogDetails = viewUploadLogDetails;

// Initialize tabs when DOM is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initTabs);
} else {
    initTabs();
}


window.viewDocumentChunks = async function(documentId) {
    if (!documentId) {
        alert("No document associated with this log.");
        return;
    }
    const section = document.getElementById('chunksViewSection');
    const content = document.getElementById('chunksContentBox');
    const countBadge = document.getElementById('chunksCountBadge');
    
    // Toggle visibility
    if (section.classList.contains('hidden')) {
        section.classList.remove('hidden');
        content.innerHTML = '<div class="text-center py-4 text-gray-500"><i class="fas fa-spinner fa-spin mr-2"></i>Loading chunks...</div>';
        try {
            const client = window.apiClient;
            if (!client) throw new Error('API client not available');
            const data = await client.getDocumentChunks(documentId);
            const chunks = data.chunks || [];
            
            countBadge.textContent = `${chunks.length} chunks`;
            
            if (chunks.length === 0) {
                content.innerHTML = '<div class="text-gray-500 text-center italic py-4">No chunks found in Vector DB for this document.</div>';
                return;
            }
            
            content.innerHTML = chunks.map((chunk, idx) => `
                <div class="bg-white border rounded-lg p-3 shadow-sm">
                    <div class="text-xs font-bold text-gray-400 mb-2 border-b pb-1">Chunk #${idx+1}</div>
                    <div class="text-xs font-mono text-gray-800 whitespace-pre-wrap break-words">${escapeHtml(chunk)}</div>
                </div>
            `).join('');
            
        } catch(e) {
            console.error('Error fetching chunks:', e);
            content.innerHTML = `<div class="text-red-500 py-4">Error loading chunks: ${e.message || 'Unknown error'}</div>`;
            countBadge.textContent = 'Error';
        }
    } else {
        section.classList.add('hidden');
    }
};
