// Toggle Socket Mode fields visibility (make it globally accessible immediately)
window.toggleSocketModeFields = function(enabled) {
    console.log('toggleSocketModeFields called with:', enabled);
    const socketModeFields = document.getElementById('socketModeFields');
    const webhookOption = document.getElementById('webhookOption');
    
    if (socketModeFields) {
        if (enabled) {
            // Remove hidden class and ensure it's visible
            socketModeFields.classList.remove('hidden');
            socketModeFields.style.display = 'block';
            console.log('Socket Mode fields shown');
        } else {
            // Add hidden class and hide with display
            socketModeFields.classList.add('hidden');
            socketModeFields.style.display = 'none';
            console.log('Socket Mode fields hidden');
        }
    } else {
        console.error('socketModeFields element not found');
    }
    
    if (webhookOption) {
        if (enabled) {
            webhookOption.classList.add('opacity-50');
            webhookOption.style.pointerEvents = 'none';
        } else {
            webhookOption.classList.remove('opacity-50');
            webhookOption.style.pointerEvents = 'auto';
        }
    } else {
        console.error('webhookOption element not found');
    }
};

// Slack integration management
document.addEventListener('DOMContentLoaded', async () => {
    // Check if we're on the slack page
    const slackPage = document.getElementById('slackPage');
    if (!slackPage) return;

    // Check authentication
    if (!authManager.requireAuth()) {
        return;
    }

    // Verify user is admin
    const user = authManager.getUser();
    if (!user || user.role !== 'admin') {
        authManager.logout();
        return;
    }

    // Setup event listeners
    setupSlackEventListeners();

    // Load current configuration
    await loadSlackConfig();

    // Update webhook URL display
    updateWebhookUrlDisplay();
    
    // Ensure Socket Mode toggle state is set after loading config
    setTimeout(() => {
        const socketModeToggle = document.getElementById('slackSocketModeEnabled');
        if (socketModeToggle) {
            toggleSocketModeFields(socketModeToggle.checked);
        }
    }, 100);
});

function setupSlackEventListeners() {
    const form = document.getElementById('slackConfigForm');
    if (form) {
        form.addEventListener('submit', handleSlackConfigSubmit);
    }

    const testBtn = document.getElementById('testSlackBtn');
    if (testBtn) {
        testBtn.addEventListener('click', handleTestSlack);
    }

    const deleteBtn = document.getElementById('deleteSlackBtn');
    if (deleteBtn) {
        deleteBtn.addEventListener('click', handleDeleteSlack);
    }
    
    // Socket Mode toggle handler - use immediate execution
    const socketModeToggle = document.getElementById('slackSocketModeEnabled');
    if (socketModeToggle) {
        // Set initial state
        toggleSocketModeFields(socketModeToggle.checked);
        
        // Add change listener
        socketModeToggle.addEventListener('change', function(e) {
            console.log('Socket Mode toggle changed:', e.target.checked);
            toggleSocketModeFields(e.target.checked);
        });
    }
}

async function loadSlackConfig() {
    try {
        // Ensure we have a valid token before making the request
        if (!authManager.getToken()) {
            console.warn('No authentication token available. Redirecting to login...');
            authManager.logout();
            return;
        }
        
        const config = await apiClient.getSlackConfig();
        if (config) {
            document.getElementById('slackWorkspaceName').value = config.workspace_name || '';
            document.getElementById('slackWorkspaceId').value = config.workspace_id || '';
            document.getElementById('slackSocketModeEnabled').checked = config.socket_mode_enabled || false;
            document.getElementById('slackAppToken').value = config.app_token ? '••••••••' : '';
            document.getElementById('slackBotToken').value = config.bot_token ? '••••••••' : '';
            document.getElementById('slackWebhookUrl').value = config.webhook_url || '';
            document.getElementById('slackSigningSecret').value = config.signing_secret ? '••••••••' : '';
            document.getElementById('slackChannelId').value = config.channel_id || '';
            document.getElementById('slackIsActive').checked = config.is_active !== false;
            
            // Show/hide Socket Mode fields
            toggleSocketModeFields(config.socket_mode_enabled || false);
            
            // Update status display
            updateSlackStatus(config);
        } else {
            // No config exists
            updateSlackStatus(null);
        }
    } catch (error) {
        console.error('Error loading Slack config:', error);
        updateSlackStatus(null);
    }
}

function updateSlackStatus(config) {
    const statusCard = document.getElementById('slackStatusCard');
    const statusBadge = document.getElementById('slackStatusBadge');
    const activeBadge = document.getElementById('slackActiveBadge');
    const workspaceNameDisplay = document.getElementById('slackWorkspaceNameDisplay');
    const workspaceIdDisplay = document.getElementById('slackWorkspaceIdDisplay');
    
    if (config && config.workspace_name) {
        // Show status card
        if (statusCard) {
            statusCard.classList.remove('hidden');
        }
        
        // Update workspace info
        if (workspaceNameDisplay) {
            workspaceNameDisplay.textContent = config.workspace_name || 'Unnamed Workspace';
        }
        if (workspaceIdDisplay) {
            workspaceIdDisplay.textContent = config.workspace_id || 'No ID';
        }
        
        // Update active badge
        if (activeBadge) {
            if (config.is_active) {
                activeBadge.className = 'px-3 py-1.5 text-xs font-bold rounded-full bg-green-100 text-green-800 border border-green-200';
                activeBadge.textContent = 'Active';
            } else {
                activeBadge.className = 'px-3 py-1.5 text-xs font-bold rounded-full bg-gray-100 text-gray-800 border border-gray-200';
                activeBadge.textContent = 'Inactive';
            }
        }
        
        // Update top status badge
        if (statusBadge) {
            if (config.is_active) {
                statusBadge.className = 'px-4 py-2 rounded-xl bg-green-100 border-2 border-green-300';
                statusBadge.innerHTML = '<div class="flex items-center gap-2"><div class="w-2 h-2 rounded-full bg-green-500 animate-pulse"></div><span class="text-sm font-semibold text-green-700">Connected</span></div>';
            } else {
                statusBadge.className = 'px-4 py-2 rounded-xl bg-yellow-100 border-2 border-yellow-300';
                statusBadge.innerHTML = '<div class="flex items-center gap-2"><div class="w-2 h-2 rounded-full bg-yellow-500"></div><span class="text-sm font-semibold text-yellow-700">Inactive</span></div>';
            }
        }
    } else {
        // Hide status card, show not configured
        if (statusCard) {
            statusCard.classList.add('hidden');
        }
        if (statusBadge) {
            statusBadge.className = 'px-4 py-2 rounded-xl bg-gray-100 border-2 border-gray-200';
            statusBadge.innerHTML = '<div class="flex items-center gap-2"><div class="w-2 h-2 rounded-full bg-gray-400"></div><span class="text-sm font-semibold text-gray-700">Not Configured</span></div>';
        }
    }
}

async function handleSlackConfigSubmit(e) {
    e.preventDefault();
    const errorDiv = document.getElementById('slackErrorMessage');
    const successDiv = document.getElementById('slackSuccessMessage');
    
    if (errorDiv) {
        errorDiv.classList.add('hidden');
        errorDiv.innerHTML = '';
    }
    if (successDiv) {
        successDiv.classList.add('hidden');
        successDiv.innerHTML = '';
    }

    const socketModeEnabled = document.getElementById('slackSocketModeEnabled').checked;
    const appToken = document.getElementById('slackAppToken').value.trim();
    const botToken = document.getElementById('slackBotToken').value.trim();
    
    // Validate Socket Mode requirements
    if (socketModeEnabled) {
        if (!appToken || appToken === '••••••••') {
            if (errorDiv) {
                errorDiv.classList.remove('hidden');
                errorDiv.innerHTML = '<i class="fas fa-exclamation-circle"></i><span class="text-xs">App-Level Token is required when Socket Mode is enabled</span>';
            }
            return;
        }
        if (!botToken || botToken === '••••••••') {
            if (errorDiv) {
                errorDiv.classList.remove('hidden');
                errorDiv.innerHTML = '<i class="fas fa-exclamation-circle"></i><span class="text-xs">Bot Token is required when Socket Mode is enabled</span>';
            }
            return;
        }
    }
    
    const configData = {
        workspace_name: document.getElementById('slackWorkspaceName').value.trim() || null,
        workspace_id: document.getElementById('slackWorkspaceId').value.trim() || null,
        socket_mode_enabled: socketModeEnabled,
        app_token: socketModeEnabled ? (appToken === '••••••••' ? null : appToken) : null,
        bot_token: botToken === '••••••••' ? null : (botToken.trim() || null),
        webhook_url: document.getElementById('slackWebhookUrl').value.trim() || null,
        signing_secret: document.getElementById('slackSigningSecret').value.trim() || null,
        channel_id: document.getElementById('slackChannelId').value.trim() || null,
        is_active: document.getElementById('slackIsActive').checked
    };

    // Don't send masked passwords
    if (configData.app_token === '••••••••') {
        configData.app_token = null;
    }
    if (configData.bot_token === '••••••••') {
        configData.bot_token = null;
    }
    if (configData.signing_secret === '••••••••') {
        delete configData.signing_secret;
    }

    // Validate that at least one connection method is provided (if Socket Mode is disabled)
    if (!socketModeEnabled && !configData.webhook_url && !configData.bot_token) {
        if (errorDiv) {
            errorDiv.innerHTML = '<i class="fas fa-exclamation-circle"></i> Please provide either a Webhook URL or Bot Token';
            errorDiv.classList.remove('hidden');
        }
        return;
    }

    try {
        const existing = await apiClient.getSlackConfig();
        let result;
        if (existing) {
            result = await apiClient.updateSlackConfig(configData);
        } else {
            result = await apiClient.createSlackConfig(configData);
        }
        
        if (successDiv) {
            const successText = document.getElementById('slackSuccessText');
            if (successText) {
                successText.textContent = 'Slack configuration saved successfully!';
            }
            successDiv.classList.remove('hidden');
            setTimeout(() => {
                successDiv.classList.add('hidden');
            }, 5000);
        }
        
        // Reload to show updated values
        await loadSlackConfig();
    } catch (error) {
        console.error('Error saving Slack config:', error);
        if (errorDiv) {
            const errorText = document.getElementById('slackErrorText');
            if (errorText) {
                errorText.textContent = error.message || 'Failed to save configuration';
            }
            errorDiv.classList.remove('hidden');
        }
    }
}

async function handleTestSlack() {
    const errorDiv = document.getElementById('slackErrorMessage');
    const successDiv = document.getElementById('slackSuccessMessage');
    const testBtn = document.getElementById('testSlackBtn');
    
    if (errorDiv) {
        errorDiv.classList.add('hidden');
        errorDiv.innerHTML = '';
    }
    if (successDiv) {
        successDiv.classList.add('hidden');
        successDiv.innerHTML = '';
    }

    if (testBtn) {
        testBtn.disabled = true;
        testBtn.innerHTML = '<i class="fas fa-spinner fa-spin mr-2"></i>Testing...';
    }

    try {
        const result = await apiClient.testSlackConnection();
        if (result.success) {
            if (successDiv) {
                const successText = document.getElementById('slackSuccessText');
                if (successText) {
                    successText.textContent = result.message;
                }
                successDiv.classList.remove('hidden');
                setTimeout(() => {
                    successDiv.classList.add('hidden');
                }, 5000);
            }
        } else {
            if (errorDiv) {
                const errorText = document.getElementById('slackErrorText');
                if (errorText) {
                    errorText.textContent = `${result.message}${result.error ? ': ' + result.error : ''}`;
                }
                errorDiv.classList.remove('hidden');
            }
        }
    } catch (error) {
        console.error('Error testing Slack connection:', error);
        if (errorDiv) {
            const errorText = document.getElementById('slackErrorText');
            if (errorText) {
                errorText.textContent = error.message || 'Failed to test connection';
            }
            errorDiv.classList.remove('hidden');
        }
    } finally {
        if (testBtn) {
            testBtn.disabled = false;
            testBtn.innerHTML = '<i class="fab fa-slack mr-2"></i>Test Connection';
        }
    }
}

async function handleDeleteSlack() {
    if (!confirm('Are you sure you want to delete the Slack configuration? This action cannot be undone.')) {
        return;
    }

    const errorDiv = document.getElementById('slackErrorMessage');
    const successDiv = document.getElementById('slackSuccessMessage');
    
    if (errorDiv) {
        errorDiv.classList.add('hidden');
        errorDiv.innerHTML = '';
    }
    if (successDiv) {
        successDiv.classList.add('hidden');
        successDiv.innerHTML = '';
    }

    try {
        await apiClient.deleteSlackConfig();
        if (successDiv) {
            const successText = document.getElementById('slackSuccessText');
            if (successText) {
                successText.textContent = 'Slack configuration deleted successfully';
            }
            successDiv.classList.remove('hidden');
            setTimeout(() => {
                successDiv.classList.add('hidden');
            }, 5000);
        }
        
        // Clear form and update status
        document.getElementById('slackConfigForm').reset();
        updateSlackStatus(null);
    } catch (error) {
        console.error('Error deleting Slack config:', error);
        if (errorDiv) {
            const errorText = document.getElementById('slackErrorText');
            if (errorText) {
                errorText.textContent = error.message || 'Failed to delete configuration';
            }
            errorDiv.classList.remove('hidden');
        }
    }
}

function updateWebhookUrlDisplay() {
    // Update both webhook URL displays
    const baseUrl = window.location.origin;
    const webhookUrl = `${baseUrl}/api/v1/slack/webhook`;
    
    const display1 = document.getElementById('webhookUrlDisplay');
    const display2 = document.getElementById('webhookUrlDisplayGuide');
    
    if (display1) display1.textContent = webhookUrl;
    if (display2) display2.textContent = webhookUrl;
}

function copyWebhookUrl() {
    const display = document.getElementById('webhookUrlDisplay');
    if (display) {
        const text = display.textContent;
        navigator.clipboard.writeText(text).then(() => {
            // Show success message
            const successDiv = document.getElementById('slackSuccessMessage');
            if (successDiv) {
                const successText = document.getElementById('slackSuccessText');
                if (successText) {
                    successText.textContent = 'Webhook URL copied to clipboard!';
                }
                successDiv.classList.remove('hidden');
                setTimeout(() => {
                    successDiv.classList.add('hidden');
                }, 3000);
            }
        }).catch(err => {
            console.error('Failed to copy:', err);
            const errorDiv = document.getElementById('slackErrorMessage');
            if (errorDiv) {
                const errorText = document.getElementById('slackErrorText');
                if (errorText) {
                    errorText.textContent = 'Failed to copy URL';
                }
                errorDiv.classList.remove('hidden');
            }
        });
    }
}

// Make function available globally
window.copyWebhookUrl = copyWebhookUrl;

