// Admin dashboard functionality
let currentEditingUserId = null;

document.addEventListener('DOMContentLoaded', async () => {
    // Check if required objects are available
    if (typeof authManager === 'undefined') {
        console.error('authManager is not defined. Make sure auth.js is loaded before admin.js');
        return;
    }

    if (typeof apiClient === 'undefined' && typeof window.apiClient === 'undefined') {
        console.error('apiClient is not defined. Make sure api.js is loaded before admin.js');
        return;
    }

    // Use window.apiClient if apiClient is not in scope
    if (typeof apiClient === 'undefined') {
        window.apiClient = window.apiClient || apiClient;
    }

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

    // Set user info
    const userNameEl = document.getElementById('userName');
    const userEmailEl = document.getElementById('userEmail');
    const userInitialEl = document.getElementById('userInitial');
    
    if (userNameEl) userNameEl.textContent = user.name || user.email;
    if (userEmailEl) userEmailEl.textContent = user.email || 'admin@askmojo.com';
    if (userInitialEl) userInitialEl.textContent = (user.name || user.email).charAt(0).toUpperCase();

    // Setup event listeners
    setupEventListeners();

    // Load initial data
    await loadDashboard();
    await loadUsers();

    // Setup navigation
    setupNavigation();
});

function setupEventListeners() {
    // Logout button
    const logoutBtn = document.getElementById('logoutBtn');
    if (logoutBtn) {
        logoutBtn.addEventListener('click', () => {
            authManager.logout();
        });
    }

    // Create user button
    const createUserBtn = document.getElementById('createUserBtn');
    if (createUserBtn) {
        console.log('Create user button found, attaching click handler');
        createUserBtn.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            console.log('Create user button clicked');
            openCreateModal();
        });
    } else {
        console.error('createUserBtn not found in DOM');
    }

    // Quick action create button (dashboard)
    const quickCreateBtn = document.getElementById('quickCreateUserBtn');
    if (quickCreateBtn) {
        console.log('Quick create user button found, attaching click handler');
        quickCreateBtn.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            console.log('Quick create user button clicked');
            openCreateModal();
        });
    } else {
        console.warn('quickCreateUserBtn not found in DOM (may not be on current page)');
    }

    // Refresh users button
    const refreshBtn = document.getElementById('refreshUsersBtn');
    if (refreshBtn) {
        refreshBtn.addEventListener('click', () => {
            loadUsers();
        });
    }

    // Create modal close
    const closeCreateModalBtn = document.getElementById('closeCreateModal');
    const cancelCreateBtn = document.getElementById('cancelCreate');
    if (closeCreateModalBtn) {
        closeCreateModalBtn.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            console.log('Close create modal button clicked');
            closeCreateModal();
        });
    } else {
        console.warn('closeCreateModalBtn not found');
    }
    if (cancelCreateBtn) {
        cancelCreateBtn.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            console.log('Cancel create button clicked');
            closeCreateModal();
        });
    } else {
        console.warn('cancelCreateBtn not found');
    }

    // Create user form - attach handler when modal opens to ensure form exists
    const createForm = document.getElementById('createUserForm');
    if (createForm) {
        console.log('Attaching submit handler to createUserForm');
        createForm.addEventListener('submit', (e) => {
            console.log('Create form submit event triggered');
            handleCreateUser(e);
        });
        // Also attach to submit button directly as backup
        const createSubmitBtn = createForm.querySelector('button[type="submit"]');
        if (createSubmitBtn) {
            createSubmitBtn.addEventListener('click', (e) => {
                e.preventDefault();
                e.stopPropagation();
                console.log('Create submit button clicked directly');
                handleCreateUser(e);
            });
        }
    } else {
        console.error('createUserForm not found during setup');
    }

    // Edit modal close
    const closeModalBtn = document.getElementById('closeModal');
    const cancelBtn = document.getElementById('cancelEdit');
    if (closeModalBtn) {
        closeModalBtn.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            console.log('Close edit modal button clicked');
            closeModal();
        });
    } else {
        console.warn('closeModalBtn not found');
    }
    if (cancelBtn) {
        cancelBtn.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            console.log('Cancel edit button clicked');
            closeModal();
        });
    } else {
        console.warn('cancelBtn not found');
    }

    // Edit user form
    const editForm = document.getElementById('editUserForm');
    if (editForm) {
        console.log('Attaching submit handler to editUserForm');
        editForm.addEventListener('submit', (e) => {
            console.log('Edit form submit event triggered');
            handleEditUser(e);
        });
        // Also attach to submit button directly as backup
        const editSubmitBtn = editForm.querySelector('button[type="submit"]');
        if (editSubmitBtn) {
            editSubmitBtn.addEventListener('click', (e) => {
                e.preventDefault();
                e.stopPropagation();
                console.log('Edit submit button clicked directly');
                handleEditUser(e);
            });
        }
    } else {
        console.error('editUserForm not found during setup');
    }

    // Close modals on outside click
    const editModal = document.getElementById('editModal');
    if (editModal) {
        editModal.addEventListener('click', (e) => {
            if (e.target.id === 'editModal') {
                closeModal();
            }
        });
    }

    const createModal = document.getElementById('createModal');
    if (createModal) {
        createModal.addEventListener('click', (e) => {
            if (e.target.id === 'createModal') {
                closeCreateModal();
            }
        });
    }
}

function setupNavigation() {
    const menuItems = document.querySelectorAll('.menu-item[data-page]');
    menuItems.forEach(item => {
        item.addEventListener('click', (e) => {
            e.preventDefault();
            const page = item.getAttribute('data-page');
            switchPage(page);
            
            // Update active state
            menuItems.forEach(mi => {
                mi.classList.remove('active', 'bg-green-50', 'text-green-700');
                mi.classList.add('text-gray-700');
            });
            item.classList.add('active', 'bg-green-50', 'text-green-700');
            item.classList.remove('text-gray-700');
        });
    });
}

function switchPage(pageName) {
    console.log('Switching to page:', pageName);
    // Hide all pages
    document.querySelectorAll('.page').forEach(page => {
        page.classList.add('hidden');
        page.classList.remove('active');
        page.style.setProperty('display', 'none', 'important');
    });

    // Map page names to element IDs (handle kebab-case to camelCase)
    const pageIdMap = {
        'dashboard': 'dashboardPage',
        'users': 'usersPage',
        'slack': 'slackPage',
        'slack-users': 'slackUsersPage',
        'categories': 'categoriesPage',
        'documents': 'documentsPage',
        'logs': 'logsPage'
    };
    
    const pageId = pageIdMap[pageName] || `${pageName}Page`;
    
    // Show selected page
    const targetPage = document.getElementById(pageId);
    if (targetPage) {
        console.log('Found target page:', targetPage.id);
        targetPage.classList.remove('hidden');
        targetPage.classList.add('active');
        // Force display to override Tailwind's hidden class
        targetPage.style.removeProperty('display');
        targetPage.style.setProperty('display', 'block', 'important');
        // Ensure visibility
        targetPage.style.setProperty('visibility', 'visible', 'important');
        targetPage.style.setProperty('opacity', '1', 'important');
    } else {
        console.error('Target page not found:', pageId);
        // List all available pages for debugging
        const allPages = document.querySelectorAll('.page');
        console.log('Available pages:', Array.from(allPages).map(p => p.id));
    }

    // Load data if needed
    if (pageName === 'logs' && typeof loadLogs === 'function') {
        loadLogs();
    }
    if (pageName === 'dashboard') {
        loadDashboard();
    } else if (pageName === 'users') {
        loadUsers();
    } else if (pageName === 'slack') {
        // Slack page will handle its own loading via slack.js
    } else if (pageName === 'slack-users') {
        // Slack users page will handle its own loading via slack-users.js
        // Use setTimeout to ensure slack-users.js has loaded
        setTimeout(() => {
            console.log('Attempting to load Slack users...');
            if (typeof window.loadSlackUsers === 'function') {
                console.log('Calling window.loadSlackUsers');
                window.loadSlackUsers();
            } else if (typeof loadSlackUsers === 'function') {
                console.log('Calling loadSlackUsers');
                loadSlackUsers();
            } else {
                console.error('loadSlackUsers function not found');
            }
        }, 300);
    } else if (pageName === 'categories') {
        // Categories page will handle its own loading via categories.js
        setTimeout(() => {
            console.log('Attempting to load categories...');
            if (typeof window.loadCategories === 'function') {
                console.log('Calling window.loadCategories');
                window.loadCategories();
            } else if (typeof loadCategories === 'function') {
                console.log('Calling loadCategories');
                loadCategories();
            } else {
                console.error('loadCategories function not found');
            }
        }, 300);
    } else if (pageName === 'documents') {
        // Documents page will handle its own loading via documents.js
        setTimeout(() => {
            console.log('Attempting to load documents...');
            if (typeof window.loadDocuments === 'function') {
                console.log('Calling window.loadDocuments');
                window.loadDocuments();
            } else if (typeof loadDocuments === 'function') {
                console.log('Calling loadDocuments');
                loadDocuments();
            } else {
                console.error('loadDocuments function not found');
            }
        }, 300);
    }
}

async function loadDashboard() {
    const client = window.apiClient || apiClient;
    if (!client) {
        console.error('apiClient is not available');
        return;
    }

    try {
        const stats = await client.getStats();
        
        // Update main stats
        updateElement('totalUsers', stats.total_users || 0);
        updateElement('totalDocuments', stats.total_documents || 0);
        updateElement('totalQueries', stats.total_queries || 0);
        updateElement('activeUsers', stats.active_users || 0);
        
        // Update card stats
        updateElement('totalUsersCard', stats.total_users || 0);
        updateElement('totalDocumentsCard', stats.total_documents || 0);
        updateElement('totalQueriesCard', stats.total_queries || 0);
    } catch (error) {
        console.error('Failed to load dashboard stats:', error);
        if (error.message && error.message.includes('Unauthorized')) {
            showNotification('Session expired. Please login again.', 'error');
            setTimeout(() => authManager.logout(), 2000);
        } else {
            showNotification('Failed to load dashboard statistics', 'error');
        }
    }
}

function updateElement(id, value) {
    const el = document.getElementById(id);
    if (el) el.textContent = value;
}

async function loadUsers() {
    const tbody = document.getElementById('usersTableBody');
    if (!tbody) return;
    
    tbody.innerHTML = '<tr class="table-row"><td colspan="7" class="px-8 py-16 text-center text-gray-500"><i class="fas fa-spinner fa-spin text-3xl mb-4 block text-green-600"></i><p class="font-medium">Loading users...</p></td></tr>';

    const client = window.apiClient || apiClient;
    if (!client) {
        console.error('apiClient is not available');
        tbody.innerHTML = '<tr class="table-row"><td colspan="7" class="px-8 py-16 text-center text-red-500"><i class="fas fa-exclamation-circle text-3xl mb-4 block"></i><p class="font-medium">API client not available</p></td></tr>';
        return;
    }

    try {
        const users = await client.getUsers();
        
        if (users.length === 0) {
            tbody.innerHTML = '<tr class="table-row"><td colspan="7" class="px-8 py-16 text-center text-gray-500"><i class="fas fa-users text-3xl mb-4 block text-gray-400"></i><p class="font-medium">No users found</p></td></tr>';
            return;
        }

            tbody.innerHTML = users.map(user => {
            const createdDate = new Date(user.created_at).toLocaleDateString('en-US', { 
                year: 'numeric', 
                month: 'short', 
                day: 'numeric' 
            });
            
            const statusBadge = user.is_active 
                ? '<span class="px-3 py-1.5 text-xs font-bold rounded-full bg-green-100 text-green-800 border border-green-200">Active</span>'
                : '<span class="px-3 py-1.5 text-xs font-bold rounded-full bg-red-100 text-red-800 border border-red-200">Inactive</span>';
            
            const roleBadge = user.role === 'admin'
                ? '<span class="px-3 py-1.5 text-xs font-bold rounded-full bg-gradient-to-r from-green-100 to-emerald-100 text-green-800 border border-green-200 shadow-sm">Admin</span>'
                : '<span class="px-3 py-1.5 text-xs font-bold rounded-full bg-gray-100 text-gray-800 border border-gray-200">User</span>';

            // Safely escape user name for use in onclick
            const userName = (user.name || 'User').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
            const userId = user.id;
            
            return `
                <tr class="table-row">
                    <td class="px-8 py-5 whitespace-nowrap text-sm font-bold text-gray-900">${user.id}</td>
                    <td class="px-8 py-5 whitespace-nowrap">
                        <div class="flex items-center gap-3">
                            <div class="w-10 h-10 bg-gradient-to-br from-green-100 to-emerald-100 rounded-lg flex items-center justify-center">
                                <span class="text-green-700 font-bold text-sm">${(user.name || 'U').charAt(0).toUpperCase()}</span>
                            </div>
                            <span class="text-sm font-semibold text-gray-900">${user.name || 'Unknown'}</span>
                        </div>
                    </td>
                    <td class="px-8 py-5 whitespace-nowrap text-sm text-gray-700 font-medium">${user.email}</td>
                    <td class="px-8 py-5 whitespace-nowrap">${roleBadge}</td>
                    <td class="px-8 py-5 whitespace-nowrap">${statusBadge}</td>
                    <td class="px-8 py-5 whitespace-nowrap text-sm text-gray-600 font-medium">${createdDate}</td>
                    <td class="px-8 py-5 whitespace-nowrap">
                        <div class="flex items-center gap-2">
                            <button onclick="window.editUser(${userId})" class="px-4 py-2 bg-green-50 text-green-700 rounded-xl hover:bg-green-100 transition-all text-xs font-semibold border border-green-200 hover:border-green-300 hover:shadow-md flex items-center gap-1.5">
                                <i class="fas fa-edit"></i>
                                <span>Edit</span>
                            </button>
                            <button onclick="window.deleteUser(${userId}, '${userName}')" class="px-4 py-2 bg-red-50 text-red-700 rounded-xl hover:bg-red-100 transition-all text-xs font-semibold border border-red-200 hover:border-red-300 hover:shadow-md flex items-center gap-1.5">
                                <i class="fas fa-trash"></i>
                                <span>Delete</span>
                            </button>
                        </div>
                    </td>
                </tr>
            `;
        }).join('');
    } catch (error) {
        console.error('Failed to load users:', error);
        if (error.message && error.message.includes('Unauthorized')) {
            tbody.innerHTML = '<tr class="table-row"><td colspan="7" class="px-8 py-16 text-center text-red-500"><i class="fas fa-exclamation-triangle text-3xl mb-4 block"></i><p class="font-medium">Session expired. Redirecting to login...</p></td></tr>';
            setTimeout(() => authManager.logout(), 2000);
        } else {
            tbody.innerHTML = '<tr class="table-row"><td colspan="7" class="px-8 py-16 text-center text-red-500"><i class="fas fa-exclamation-circle text-3xl mb-4 block"></i><p class="font-medium">Error loading users</p></td></tr>';
        }
    }
}

async function editUser(userId) {
    console.log('editUser called with userId:', userId);
    
    // Prevent multiple simultaneous calls
    if (currentEditingUserId === userId) {
        const modal = document.getElementById('editModal');
        if (modal && !modal.classList.contains('hidden')) {
            console.log('Edit modal already open for this user');
            return;
        }
    }
    
    const client = window.apiClient || apiClient;
    if (!client) {
        console.error('apiClient is not available');
        showNotification('API client not available', 'error');
        return;
    }

    try {
        console.log('Fetching user data for ID:', userId);
        const user = await client.getUser(userId);
        console.log('User data received:', user);
        currentEditingUserId = userId;

        const editUserIdEl = document.getElementById('editUserId');
        const editNameEl = document.getElementById('editName');
        const editEmailEl = document.getElementById('editEmail');
        const editRoleEl = document.getElementById('editRole');
        const editIsActiveEl = document.getElementById('editIsActive');
        const editPasswordEl = document.getElementById('editPassword');

        if (!editUserIdEl || !editNameEl || !editEmailEl || !editRoleEl || !editIsActiveEl || !editPasswordEl) {
            console.error('Edit form elements not found');
            showNotification('Edit form not properly initialized', 'error');
            return;
        }

        editUserIdEl.value = user.id;
        editNameEl.value = user.name;
        editEmailEl.value = user.email;
        editRoleEl.value = user.role;
        editIsActiveEl.value = user.is_active.toString();
        editPasswordEl.value = '';
        
        const errorDiv = document.getElementById('editErrorMessage');
        if (errorDiv) {
            errorDiv.classList.add('hidden');
            errorDiv.innerHTML = '';
        }

        const modal = document.getElementById('editModal');
        if (modal) {
            console.log('Opening edit modal');
            // Remove hidden class and ensure flex display with !important to override Tailwind
            modal.classList.remove('hidden');
            modal.classList.add('flex');
            modal.style.setProperty('display', 'flex', 'important');
            modal.style.setProperty('z-index', '9999', 'important');
            modal.style.setProperty('opacity', '1', 'important');
            modal.style.setProperty('pointer-events', 'auto', 'important');
            
            // Ensure modal-box is interactive
            const modalBox = modal.querySelector('.modal-box');
            if (modalBox) {
                modalBox.style.setProperty('pointer-events', 'auto', 'important');
                modalBox.style.setProperty('position', 'relative', 'important');
                modalBox.style.setProperty('z-index', '10000', 'important');
            }
            console.log('Edit modal opened successfully', modal.style.display, modal.classList);
        } else {
            console.error('editModal element not found');
            showNotification('Edit modal not found. Please refresh the page.', 'error');
        }
    } catch (error) {
        console.error('Error in editUser:', error);
        showNotification('Failed to load user data: ' + error.message, 'error');
    }
}

async function handleEditUser(e) {
    if (e) {
        e.preventDefault();
        e.stopPropagation();
    }
    console.log('handleEditUser called', e);
    
    // Get form - could be from event target or directly
    const form = (e && e.target && e.target.tagName === 'FORM') ? e.target : document.getElementById('editUserForm');
    if (!form) {
        console.error('editUserForm not found');
        showNotification('Form not found. Please refresh the page.', 'error');
        return;
    }
    
    // Prevent multiple submissions
    const submitBtn = form.querySelector('button[type="submit"]');
    if (submitBtn && submitBtn.disabled) {
        console.log('Form already submitting, ignoring...');
        return;
    }
    if (submitBtn) {
        submitBtn.disabled = true;
        const originalHTML = submitBtn.innerHTML;
        submitBtn.innerHTML = '<i class="fas fa-spinner fa-spin mr-2"></i>Saving...';
        submitBtn.dataset.originalHTML = originalHTML;
    }
    
    const errorDiv = document.getElementById('editErrorMessage');
    
    const userId = currentEditingUserId;
    if (!userId) {
        console.error('No user ID set for editing');
        if (submitBtn) {
            submitBtn.disabled = false;
            submitBtn.innerHTML = submitBtn.dataset.originalHTML || 'Save Changes';
        }
        if (errorDiv) {
            errorDiv.innerHTML = '<i class="fas fa-exclamation-circle"></i> No user selected for editing';
            errorDiv.classList.remove('hidden');
        }
        return;
    }

    const editNameEl = document.getElementById('editName');
    const editEmailEl = document.getElementById('editEmail');
    const editRoleEl = document.getElementById('editRole');
    const editIsActiveEl = document.getElementById('editIsActive');
    const editPasswordEl = document.getElementById('editPassword');

    if (!editNameEl || !editEmailEl || !editRoleEl || !editIsActiveEl || !editPasswordEl) {
        console.error('Edit form elements not found');
        if (submitBtn) {
            submitBtn.disabled = false;
            submitBtn.innerHTML = submitBtn.dataset.originalHTML || 'Save Changes';
        }
        if (errorDiv) {
            errorDiv.innerHTML = '<i class="fas fa-exclamation-circle"></i> Form elements not found';
            errorDiv.classList.remove('hidden');
        }
        return;
    }

    const updateData = {
        name: editNameEl.value.trim(),
        email: editEmailEl.value.trim(),
        role: editRoleEl.value,
        is_active: editIsActiveEl.value === 'true'
    };

    const password = editPasswordEl.value;
    if (password) {
        updateData.password = password;
    }

    console.log('Update data:', { ...updateData, password: password ? '***' : 'not set' });

    // Validate required fields
    if (!updateData.name || !updateData.name.trim()) {
        if (errorDiv) {
            errorDiv.innerHTML = '<i class="fas fa-exclamation-circle"></i> Name is required';
            errorDiv.classList.remove('hidden');
        }
        if (submitBtn) {
            submitBtn.disabled = false;
            submitBtn.innerHTML = submitBtn.dataset.originalHTML || 'Save Changes';
        }
        return;
    }

    if (!updateData.email || !updateData.email.trim()) {
        if (errorDiv) {
            errorDiv.innerHTML = '<i class="fas fa-exclamation-circle"></i> Email is required';
            errorDiv.classList.remove('hidden');
        }
        if (submitBtn) {
            submitBtn.disabled = false;
            submitBtn.innerHTML = submitBtn.dataset.originalHTML || 'Save Changes';
        }
        return;
    }

    // Basic email validation
    const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    if (!emailRegex.test(updateData.email)) {
        if (errorDiv) {
            errorDiv.innerHTML = '<i class="fas fa-exclamation-circle"></i> Please enter a valid email address';
            errorDiv.classList.remove('hidden');
        }
        if (submitBtn) {
            submitBtn.disabled = false;
            submitBtn.innerHTML = submitBtn.dataset.originalHTML || 'Save Changes';
        }
        return;
    }

    // Validate password length if provided
    if (updateData.password && updateData.password.length < 6) {
        if (errorDiv) {
            errorDiv.innerHTML = '<i class="fas fa-exclamation-circle"></i> Password must be at least 6 characters';
            errorDiv.classList.remove('hidden');
        }
        if (submitBtn) {
            submitBtn.disabled = false;
            submitBtn.innerHTML = submitBtn.dataset.originalHTML || 'Save Changes';
        }
        return;
    }

    const client = window.apiClient || apiClient;
    if (!client) {
        console.error('apiClient is not available');
        if (errorDiv) {
            errorDiv.innerHTML = '<i class="fas fa-exclamation-circle"></i> API client not available';
            errorDiv.classList.remove('hidden');
        }
        if (submitBtn) {
            submitBtn.disabled = false;
            submitBtn.innerHTML = submitBtn.dataset.originalHTML || 'Save Changes';
        }
        return;
    }

    try {
        console.log('Updating user with data:', { ...updateData, password: updateData.password ? '***' : 'not set' });
        await client.updateUser(userId, updateData);
        console.log('User updated successfully');
        closeModal();
        await loadUsers();
        await loadDashboard(); // Refresh stats
        showNotification('User updated successfully', 'success');
    } catch (error) {
        console.error('Error updating user:', error);
        if (errorDiv) {
            errorDiv.innerHTML = `<i class="fas fa-exclamation-circle"></i> ${error.message || 'Failed to update user'}`;
            errorDiv.classList.remove('hidden');
        }
    } finally {
        if (submitBtn) {
            submitBtn.disabled = false;
            submitBtn.innerHTML = submitBtn.dataset.originalHTML || 'Save Changes';
        }
    }
}

async function deleteUser(userId, userName) {
    console.log('deleteUser called with userId:', userId, 'userName:', userName);
    
    // Validate inputs
    if (!userId) {
        console.error('No user ID provided for deletion');
        showNotification('Invalid user ID', 'error');
        return;
    }
    
    if (!userName) {
        userName = 'this user';
    }
    
    if (!confirm(`Are you sure you want to delete user "${userName}"? This action cannot be undone.`)) {
        console.log('Delete cancelled by user');
        return;
    }

    const client = window.apiClient || apiClient;
    if (!client) {
        console.error('apiClient is not available');
        showNotification('API client not available', 'error');
        return;
    }

    try {
        console.log('Deleting user with ID:', userId);
        const result = await client.deleteUser(userId);
        console.log('User deleted successfully:', result);
        await loadUsers();
        await loadDashboard(); // Refresh stats
        showNotification('User deleted successfully', 'success');
    } catch (error) {
        console.error('Error deleting user:', error);
        const errorMessage = error.message || 'Failed to delete user';
        showNotification('Failed to delete user: ' + errorMessage, 'error');
    }
}

function openCreateModal() {
    console.log('openCreateModal called');
    try {
        const modal = document.getElementById('createModal');
        if (!modal) {
            console.error('createModal element not found');
            showNotification('Create modal not found. Please refresh the page.', 'error');
            return;
        }
        console.log('Opening create modal');
        // Remove hidden class and ensure flex display with !important to override Tailwind
        modal.classList.remove('hidden');
        modal.classList.add('flex');
        modal.style.setProperty('display', 'flex', 'important');
        modal.style.setProperty('z-index', '9999', 'important');
        modal.style.setProperty('opacity', '1', 'important');
        modal.style.setProperty('pointer-events', 'auto', 'important');
        
        // Ensure modal-box is interactive
        const modalBox = modal.querySelector('.modal-box');
        if (modalBox) {
            modalBox.style.setProperty('pointer-events', 'auto', 'important');
            modalBox.style.setProperty('position', 'relative', 'important');
            modalBox.style.setProperty('z-index', '10000', 'important');
        }
        
        const form = document.getElementById('createUserForm');
        if (form) {
            form.reset();
        } else {
            console.warn('createUserForm not found');
        }
        
        const errorDiv = document.getElementById('createErrorMessage');
        if (errorDiv) {
            errorDiv.classList.add('hidden');
            errorDiv.innerHTML = '';
        }
        console.log('Create modal opened successfully', modal.style.display, modal.classList);
    } catch (error) {
        console.error('Error opening create modal:', error);
        showNotification('Error opening create modal: ' + error.message, 'error');
    }
}

function closeCreateModal() {
    const modal = document.getElementById('createModal');
    if (modal) {
        modal.classList.add('hidden');
        modal.classList.remove('flex');
        modal.style.setProperty('display', 'none', 'important');
    }
    const form = document.getElementById('createUserForm');
    if (form) form.reset();
    const errorDiv = document.getElementById('createErrorMessage');
    if (errorDiv) {
        errorDiv.classList.add('hidden');
        errorDiv.innerHTML = '';
    }
}

async function handleCreateUser(e) {
    if (e) {
        e.preventDefault();
        e.stopPropagation();
    }
    console.log('handleCreateUser called', e);
    
    // Get form - could be from event target or directly
    const form = (e && e.target && e.target.tagName === 'FORM') ? e.target : document.getElementById('createUserForm');
    if (!form) {
        console.error('createUserForm not found');
        showNotification('Form not found. Please refresh the page.', 'error');
        return;
    }
    
    const submitBtn = form.querySelector('button[type="submit"]');
    
    // Prevent multiple submissions
    if (submitBtn && submitBtn.disabled) {
        console.log('Form already submitting, ignoring...');
        return;
    }
    
    // Store original button state
    let originalHTML = '';
    if (submitBtn) {
        originalHTML = submitBtn.innerHTML;
        submitBtn.disabled = true;
        submitBtn.innerHTML = '<i class="fas fa-spinner fa-spin mr-2"></i>Creating...';
    }
    
    const errorDiv = document.getElementById('createErrorMessage');
    if (errorDiv) {
        errorDiv.classList.add('hidden');
        errorDiv.innerHTML = '';
    }

    const nameEl = document.getElementById('createName');
    const emailEl = document.getElementById('createEmail');
    const passwordEl = document.getElementById('createPassword');
    const passwordConfirmEl = document.getElementById('createPasswordConfirm');
    const roleEl = document.getElementById('createRole');
    const isActiveEl = document.getElementById('createIsActive');

    if (!nameEl || !emailEl || !passwordEl || !passwordConfirmEl || !roleEl || !isActiveEl) {
        console.error('Create form elements not found');
        if (errorDiv) {
            errorDiv.innerHTML = '<i class="fas fa-exclamation-circle"></i> Form elements not found';
            errorDiv.classList.remove('hidden');
        }
        if (submitBtn) {
            submitBtn.disabled = false;
            submitBtn.innerHTML = originalHTML;
        }
        return;
    }

    const name = nameEl.value.trim();
    const email = emailEl.value.trim();
    const password = passwordEl.value;
    const passwordConfirm = passwordConfirmEl.value;
    const role = roleEl.value;
    const isActive = isActiveEl.value === 'true';

    console.log('Form data:', { name, email, role, isActive, passwordLength: password.length });

    // Validate required fields
    if (!name) {
        if (errorDiv) {
            errorDiv.innerHTML = '<i class="fas fa-exclamation-circle"></i> Name is required';
            errorDiv.classList.remove('hidden');
        }
        if (submitBtn) {
            submitBtn.disabled = false;
            submitBtn.innerHTML = originalHTML;
        }
        return;
    }

    if (!email) {
        if (errorDiv) {
            errorDiv.innerHTML = '<i class="fas fa-exclamation-circle"></i> Email is required';
            errorDiv.classList.remove('hidden');
        }
        if (submitBtn) {
            submitBtn.disabled = false;
            submitBtn.innerHTML = originalHTML;
        }
        return;
    }

    // Basic email validation
    const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    if (!emailRegex.test(email)) {
        if (errorDiv) {
            errorDiv.innerHTML = '<i class="fas fa-exclamation-circle"></i> Please enter a valid email address';
            errorDiv.classList.remove('hidden');
        }
        if (submitBtn) {
            submitBtn.disabled = false;
            submitBtn.innerHTML = originalHTML;
        }
        return;
    }

    // Validate password match
    if (password !== passwordConfirm) {
        if (errorDiv) {
            errorDiv.innerHTML = '<i class="fas fa-exclamation-circle"></i> Passwords do not match';
            errorDiv.classList.remove('hidden');
        }
        if (submitBtn) {
            submitBtn.disabled = false;
            submitBtn.innerHTML = originalHTML;
        }
        return;
    }

    // Validate password length
    if (password.length < 6) {
        if (errorDiv) {
            errorDiv.innerHTML = '<i class="fas fa-exclamation-circle"></i> Password must be at least 6 characters';
            errorDiv.classList.remove('hidden');
        }
        if (submitBtn) {
            submitBtn.disabled = false;
            submitBtn.innerHTML = originalHTML;
        }
        return;
    }

    const userData = {
        name,
        email,
        password,
        role,
        is_active: isActive
    };

    const client = window.apiClient || apiClient;
    if (!client) {
        console.error('apiClient is not available');
        if (errorDiv) {
            errorDiv.innerHTML = '<i class="fas fa-exclamation-circle"></i> API client not available';
            errorDiv.classList.remove('hidden');
        }
        if (submitBtn) {
            submitBtn.disabled = false;
            submitBtn.innerHTML = originalHTML;
        }
        return;
    }

    try {
        console.log('Creating user with data:', { ...userData, password: '***' });
        const result = await client.createUser(userData);
        console.log('User created successfully:', result);
        closeCreateModal();
        await loadUsers();
        await loadDashboard(); // Refresh stats
        showNotification('User created successfully', 'success');
    } catch (error) {
        console.error('Error creating user:', error);
        if (errorDiv) {
            errorDiv.innerHTML = `<i class="fas fa-exclamation-circle"></i> ${error.message || 'Failed to create user'}`;
            errorDiv.classList.remove('hidden');
        }
    } finally {
        if (submitBtn) {
            submitBtn.disabled = false;
            submitBtn.innerHTML = originalHTML || '<i class="fas fa-user-plus mr-2"></i>Create User';
        }
    }
}

function closeModal() {
    const modal = document.getElementById('editModal');
    if (modal) {
        modal.classList.add('hidden');
        modal.classList.remove('flex');
        modal.style.setProperty('display', 'none', 'important');
    }
    currentEditingUserId = null;
    const form = document.getElementById('editUserForm');
    if (form) form.reset();
    const errorDiv = document.getElementById('editErrorMessage');
    if (errorDiv) {
        errorDiv.classList.add('hidden');
        errorDiv.innerHTML = '';
    }
}

function showNotification(message, type = 'info') {
    // Create a simple notification
    const notification = document.createElement('div');
    notification.className = `fixed top-4 right-4 px-6 py-4 rounded-lg shadow-lg z-50 ${
        type === 'success' ? 'bg-green-500' : 
        type === 'error' ? 'bg-red-500' : 
        'bg-blue-500'
    } text-white font-medium`;
    notification.innerHTML = `<i class="fas fa-${type === 'success' ? 'check-circle' : type === 'error' ? 'exclamation-circle' : 'info-circle'} mr-2"></i>${message}`;
    
    document.body.appendChild(notification);
    
    setTimeout(() => {
        notification.style.opacity = '0';
        notification.style.transition = 'opacity 0.3s';
        setTimeout(() => notification.remove(), 300);
    }, 3000);
}

// Make functions available globally
window.editUser = editUser;
window.deleteUser = deleteUser;
window.openCreateModal = openCreateModal;
