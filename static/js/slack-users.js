// Slack Users Management
let fetchedUserData = null;

document.addEventListener('DOMContentLoaded', () => {
    console.log('slack-users.js loaded');
    const slackUsersPage = document.getElementById('slackUsersPage');
    if (!slackUsersPage) {
        console.error('slackUsersPage element not found');
        return;
    }
    console.log('slackUsersPage element found');

    // Check authentication
    if (typeof authManager === 'undefined') {
        console.error('authManager not available');
        return;
    }

    if (!authManager.requireAuth()) {
        console.log('Auth required, redirecting...');
        return;
    }

    // Verify user is admin
    const user = authManager.getUser();
    if (!user || user.role !== 'admin') {
        console.log('User is not admin, logging out...');
        authManager.logout();
        return;
    }

    // Setup event listeners
    setupSlackUsersEventListeners();

    // Load Slack users when page is shown
    const observer = new MutationObserver((mutations) => {
        mutations.forEach((mutation) => {
            if (mutation.type === 'attributes' && mutation.attributeName === 'class') {
                if (!slackUsersPage.classList.contains('hidden')) {
                    console.log('Slack users page shown, loading users...');
                    loadSlackUsers();
                }
            }
        });
    });
    observer.observe(slackUsersPage, { attributes: true });
    
    // Also try to load if page is already visible
    if (!slackUsersPage.classList.contains('hidden')) {
        console.log('Page already visible, loading users...');
        setTimeout(() => loadSlackUsers(), 100);
    }
});

function setupSlackUsersEventListeners() {
    const fetchForm = document.getElementById('fetchSlackUserForm');
    if (fetchForm) {
        fetchForm.addEventListener('submit', handleFetchSlackUser);
    }

    // Handle search type toggle (email vs user_id)
    const fetchUserType = document.getElementById('fetchUserType');
    const fetchUserInput = document.getElementById('fetchUserInput');
    const fetchUserLabel = document.getElementById('fetchUserLabel');
    const fetchUserIcon = document.getElementById('fetchUserIcon');
    
    if (fetchUserType && fetchUserInput && fetchUserLabel && fetchUserIcon) {
        fetchUserType.addEventListener('change', (e) => {
            const isUserId = e.target.value === 'user_id';
            fetchUserLabel.textContent = isUserId ? 'Slack User ID' : 'Email Address';
            fetchUserInput.type = isUserId ? 'text' : 'email';
            fetchUserInput.placeholder = isUserId ? 'U0A7PHVTB37' : 'user@example.com';
            fetchUserIcon.className = isUserId 
                ? 'fas fa-id-card absolute left-3 top-1/2 transform -translate-y-1/2 text-gray-400 text-sm'
                : 'fas fa-envelope absolute left-3 top-1/2 transform -translate-y-1/2 text-gray-400 text-sm';
            fetchUserInput.value = ''; // Clear input when switching
        });
    }

    const registerBtn = document.getElementById('registerSlackUserBtn');
    if (registerBtn) {
        registerBtn.addEventListener('click', handleRegisterSlackUser);
    }

    const clearFetchedInfo = document.getElementById('clearFetchedInfo');
    if (clearFetchedInfo) {
        clearFetchedInfo.addEventListener('click', () => {
            const infoDiv = document.getElementById('fetchedUserInfo');
            const form = document.getElementById('fetchSlackUserForm');
            if (infoDiv) infoDiv.classList.add('hidden');
            if (form) form.reset();
            fetchedUserData = null;
        });
    }

    const cancelRegister = document.getElementById('cancelRegister');
    if (cancelRegister) {
        cancelRegister.addEventListener('click', () => {
            const infoDiv = document.getElementById('fetchedUserInfo');
            if (infoDiv) infoDiv.classList.add('hidden');
            fetchedUserData = null;
        });
    }
}

function clearFetchedUserInfo() {
    const info = document.getElementById('fetchedUserInfo');
    const form = document.getElementById('fetchSlackUserForm');
    if (info) info.classList.add('hidden');
    if (form) form.reset();
    fetchedUserData = null;
}

async function handleFetchSlackUser(e) {
    e.preventDefault();
    const client = window.apiClient;
    if (!client) {
        showNotification('API client not available', 'error');
        return;
    }

    const fetchUserType = document.getElementById('fetchUserType');
    const fetchUserInput = document.getElementById('fetchUserInput');
    const inputValue = fetchUserInput.value.trim();
    const isUserId = fetchUserType && fetchUserType.value === 'user_id';
    
    const errorDiv = document.getElementById('fetchErrorMessage');
    const errorText = document.getElementById('fetchErrorText');
    const infoDiv = document.getElementById('fetchedUserInfo');
    const submitBtn = e.target.querySelector('button[type="submit"]') || e.target;

    if (!inputValue) {
        if (errorDiv && errorText) {
            errorText.textContent = `Please enter ${isUserId ? 'a Slack User ID' : 'an email address'}`;
            errorDiv.classList.remove('hidden');
        }
        return;
    }

    if (errorDiv) {
        errorDiv.classList.add('hidden');
    }

    // Show loading state
    const originalBtnText = submitBtn.innerHTML;
    if (submitBtn) {
        submitBtn.disabled = true;
        submitBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> <span>Fetching...</span>';
    }

    try {
        console.log(`Fetching Slack user by ${isUserId ? 'User ID' : 'email'}:`, inputValue);
        const userInfo = await client.fetchSlackUser(inputValue, isUserId);
        fetchedUserData = userInfo;
        console.log('User info received:', userInfo);

        // Display user info
        if (infoDiv) {
            const name = userInfo.name || userInfo.real_name || 'Unknown';
            const displayName = userInfo.display_name || '';
            const email = userInfo.email || '-';
            const slackId = userInfo.id || '-';
            const status = userInfo.is_active ? 'Active' : 'Inactive';
            const statusColor = userInfo.is_active ? 'text-green-600' : 'text-gray-600';

            document.getElementById('fetchedName').textContent = name;
            const displayNameEl = document.getElementById('fetchedDisplayName');
            if (displayNameEl) {
                displayNameEl.textContent = displayName || '';
                displayNameEl.style.display = displayName ? 'block' : 'none';
            }
            document.getElementById('fetchedEmail').textContent = email;
            document.getElementById('fetchedSlackId').textContent = slackId;
            const statusEl = document.getElementById('fetchedStatus');
            statusEl.textContent = status;
            statusEl.className = `text-xs font-semibold ml-1 ${statusColor}`;

            // Set user image if available
            const userImage = document.getElementById('fetchedUserImage');
            const userIcon = document.getElementById('fetchedUserIcon');
            if (userInfo.image_48 || userInfo.image_72) {
                const imageUrl = userInfo.image_72 || userInfo.image_48;
                if (userImage) {
                    userImage.src = imageUrl;
                    userImage.classList.remove('hidden');
                }
                if (userIcon) {
                    userIcon.style.display = 'none';
                }
            } else {
                if (userImage) {
                    userImage.classList.add('hidden');
                }
                if (userIcon) {
                    userIcon.style.display = 'block';
                }
            }

            infoDiv.classList.remove('hidden');
            showNotification('User information fetched successfully', 'success');
        }
    } catch (error) {
        console.error('Failed to fetch Slack user:', error);
        if (errorDiv && errorText) {
            errorText.textContent = error.message || 'Failed to fetch user from Slack. Make sure the email exists in your Slack workspace.';
            errorDiv.classList.remove('hidden');
        }
        if (infoDiv) {
            infoDiv.classList.add('hidden');
        }
        showNotification(error.message || 'Failed to fetch user from Slack', 'error');
    } finally {
        if (submitBtn) {
            submitBtn.disabled = false;
            submitBtn.innerHTML = originalBtnText;
        }
    }
}

async function handleRegisterSlackUser() {
    if (!fetchedUserData) {
        showNotification('No user data to register', 'error');
        return;
    }

    const client = window.apiClient;
    if (!client) return;

    const registerBtn = document.getElementById('registerSlackUserBtn');
    if (registerBtn) {
        registerBtn.disabled = true;
        registerBtn.innerHTML = '<i class="fas fa-spinner fa-spin text-xs"></i> <span>Registering...</span>';
    }

    try {
        const userData = {
            slack_user_id: fetchedUserData.id,
            email: fetchedUserData.email,
            name: fetchedUserData.name,
            real_name: fetchedUserData.real_name,
            display_name: fetchedUserData.display_name,
            image_24: fetchedUserData.image_24,
            image_32: fetchedUserData.image_32,
            image_48: fetchedUserData.image_48,
            image_72: fetchedUserData.image_72,
            image_192: fetchedUserData.image_192,
            is_admin: fetchedUserData.is_admin || false,
            is_owner: fetchedUserData.is_owner || false,
            is_bot: fetchedUserData.is_bot || false,
            is_active: fetchedUserData.is_active !== false,
            timezone: fetchedUserData.tz,
            tz_label: fetchedUserData.tz_label,
            tz_offset: fetchedUserData.tz_offset,
            is_registered: true
        };

        await client.createSlackUser(userData);
        showNotification('Slack user registered successfully', 'success');
        clearFetchedUserInfo();
        await loadSlackUsers();
    } catch (error) {
        console.error('Failed to register Slack user:', error);
        showNotification(error.message || 'Failed to register user', 'error');
    } finally {
        if (registerBtn) {
            registerBtn.disabled = false;
            registerBtn.innerHTML = '<i class="fas fa-check text-xs"></i> <span>Register User</span>';
        }
    }
}

async function loadSlackUsers() {
    console.log('loadSlackUsers called');
    const client = window.apiClient;
    if (!client) {
        console.error('apiClient not available');
        return;
    }

    // Check if method exists
    if (typeof client.getSlackUsers !== 'function') {
        console.error('getSlackUsers method not found on apiClient');
        console.log('Available methods:', Object.getOwnPropertyNames(Object.getPrototypeOf(client)));
        const tbody = document.getElementById('slackUsersTableBody');
        if (tbody) {
            tbody.innerHTML = '<tr><td colspan="6" class="text-center py-8 text-red-500 text-sm">Error: API method not available. Please refresh the page.</td></tr>';
        }
        return;
    }

    const tbody = document.getElementById('slackUsersTableBody');
    if (!tbody) {
        console.error('slackUsersTableBody not found');
        return;
    }

    try {
        console.log('Fetching Slack users...');
        const users = await client.getSlackUsers();
        console.log('Slack users received:', users);
        
        if (!users || users.length === 0) {
            tbody.innerHTML = '<tr><td colspan="6" class="text-center py-8 text-gray-500 text-sm">No registered Slack users</td></tr>';
            return;
        }

        // Store user data in a Map for easy access
        const userDataMap = new Map();
        users.forEach(user => {
            userDataMap.set(user.id, {
                id: user.id,
                name: user.name || user.email || 'Unknown',
                isRegistered: user.is_registered
            });
        });
        
        const tableRows = users.map(user => {
            const userId = user.id;
            const isRegistered = user.is_registered;
            
            return `
            <tr class="table-row">
                <td>
                    <div class="flex items-center gap-3">
                        ${user.image_48 ? `<img src="${user.image_48}" alt="${(user.name || '').replace(/"/g, '&quot;')}" class="w-8 h-8 rounded-full">` : '<div class="w-8 h-8 rounded-full bg-blue-100 flex items-center justify-center"><i class="fas fa-user text-blue-600 text-xs"></i></div>'}
                        <div>
                            <div class="text-sm font-semibold text-gray-900">${(user.name || user.real_name || 'Unknown').replace(/</g, '&lt;').replace(/>/g, '&gt;')}</div>
                            ${user.display_name ? `<div class="text-xs text-gray-500">${user.display_name.replace(/</g, '&lt;').replace(/>/g, '&gt;')}</div>` : ''}
                        </div>
                    </div>
                </td>
                <td class="text-sm text-gray-700">${(user.email || '-').replace(/</g, '&lt;').replace(/>/g, '&gt;')}</td>
                <td class="text-xs text-gray-500 font-mono">${user.slack_user_id}</td>
                <td>
                    <span class="px-2 py-1 text-xs font-bold rounded-full ${user.is_active ? 'bg-green-100 text-green-800' : 'bg-gray-100 text-gray-800'}">
                        ${user.is_active ? 'Active' : 'Inactive'}
                    </span>
                </td>
                <td>
                    <span class="px-2 py-1 text-xs font-bold rounded-full ${user.is_registered ? 'bg-blue-100 text-blue-800' : 'bg-red-100 text-red-800'}">
                        ${user.is_registered ? 'Registered' : 'Not Registered'}
                    </span>
                </td>
                <td>
                    <div class="flex items-center gap-2">
                        <button data-action="toggle" data-user-id="${userId}" data-is-registered="${isRegistered}" class="btn btn-xs ${isRegistered ? 'btn-warning' : 'btn-success'} rounded-lg">
                            <i class="fas ${isRegistered ? 'fa-ban' : 'fa-check'} text-xs"></i>
                            ${isRegistered ? 'Disable' : 'Enable'}
                        </button>
                        <button data-action="delete" data-user-id="${userId}" class="btn btn-xs btn-error rounded-lg">
                            <i class="fas fa-trash text-xs"></i>
                        </button>
                    </div>
                </td>
            </tr>
            `;
        }).join('');
        
        tbody.innerHTML = tableRows;
        
        // Attach event listeners to action buttons
        tbody.querySelectorAll('button[data-action="toggle"]').forEach(btn => {
            btn.addEventListener('click', async () => {
                const userId = parseInt(btn.dataset.userId);
                const isRegistered = btn.dataset.isRegistered === 'true';
                await toggleSlackUserRegistration(userId, !isRegistered);
            });
        });
        
        tbody.querySelectorAll('button[data-action="delete"]').forEach(btn => {
            btn.addEventListener('click', async () => {
                const userId = parseInt(btn.dataset.userId);
                const userData = userDataMap.get(userId);
                const userName = userData ? userData.name : 'Unknown';
                await deleteSlackUser(userId, userName);
            });
        });
    } catch (error) {
        console.error('Failed to load Slack users:', error);
        tbody.innerHTML = '<tr><td colspan="6" class="text-center py-8 text-red-500 text-sm">Failed to load users</td></tr>';
    }
}

async function toggleSlackUserRegistration(userId, isRegistered) {
    const client = window.apiClient;
    if (!client) return;

    try {
        await client.updateSlackUser(userId, { is_registered: isRegistered });
        showNotification(`User ${isRegistered ? 'registered' : 'unregistered'} successfully`, 'success');
        await loadSlackUsers();
    } catch (error) {
        console.error('Failed to update Slack user:', error);
        showNotification(error.message || 'Failed to update user', 'error');
    }
}

async function deleteSlackUser(userId, userName) {
    if (!confirm(`Are you sure you want to delete ${userName}?`)) {
        return;
    }

    const client = window.apiClient;
    if (!client) return;

    try {
        await client.deleteSlackUser(userId);
        showNotification('Slack user deleted successfully', 'success');
        await loadSlackUsers();
    } catch (error) {
        console.error('Failed to delete Slack user:', error);
        showNotification(error.message || 'Failed to delete user', 'error');
    }
}

// Make functions globally available
window.toggleSlackUserRegistration = toggleSlackUserRegistration;
window.deleteSlackUser = deleteSlackUser;
window.loadSlackUsers = loadSlackUsers;

