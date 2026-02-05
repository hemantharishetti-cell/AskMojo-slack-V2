// Login page functionality
document.addEventListener('DOMContentLoaded', () => {
    // Check if apiClient is defined (check both local and window scope)
    const client = typeof apiClient !== 'undefined' ? apiClient : (typeof window !== 'undefined' && window.apiClient);
    if (!client) {
        console.error('apiClient is not defined. Check script loading order.');
        const errorDiv = document.getElementById('errorMessage');
        if (errorDiv) {
            errorDiv.innerHTML = '<i class="fas fa-exclamation-circle"></i><span>Error loading scripts. Please refresh the page (Ctrl+F5 or Cmd+Shift+R to hard refresh).</span>';
            errorDiv.classList.remove('hidden');
        }
        return;
    }

    // Check if authManager is defined
    if (typeof authManager === 'undefined') {
        console.error('authManager is not defined. Check script loading order.');
        const errorDiv = document.getElementById('errorMessage');
        if (errorDiv) {
            errorDiv.innerHTML = '<i class="fas fa-exclamation-circle"></i><span>Error loading scripts. Please refresh the page.</span>';
            errorDiv.classList.remove('hidden');
        }
        return;
    }

    const loginForm = document.getElementById('loginForm');
    const errorMessage = document.getElementById('errorMessage');
    const loginBtnText = document.getElementById('loginBtnText');

    // Check if already logged in
    if (authManager.isAuthenticated()) {
        window.location.href = '/static/admin.html';
        return;
    }

    loginForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        if (errorMessage) {
            errorMessage.classList.add('hidden');
            errorMessage.innerHTML = '';
        }

        const email = document.getElementById('email').value;
        const password = document.getElementById('password').value;

        const submitBtn = loginForm.querySelector('button[type="submit"]');
        if (submitBtn) {
            submitBtn.disabled = true;
            if (loginBtnText) loginBtnText.textContent = 'Logging in...';
        }

        try {
            // Use the client we already verified exists
            const response = await client.login(email, password);
            console.log('Login successful, received token');
            
            // Store token immediately
            authManager.setToken(response.access_token);
            
            // Verify token was stored
            const storedToken = authManager.getToken();
            if (!storedToken) {
                throw new Error('Failed to store authentication token');
            }
            console.log('Token stored successfully');
            
            // Get user info with the stored token
            const user = await client.getCurrentUser();
            console.log('User info retrieved:', user);
            authManager.setUser(user);

            // Check if user is admin
            if (user.role !== 'admin') {
                authManager.logout();
                throw new Error('Access denied. Admin role required.');
            }

            window.location.href = '/static/admin.html';
        } catch (error) {
            if (errorMessage) {
                errorMessage.innerHTML = `<i class="fas fa-exclamation-circle"></i><span>${error.message || 'Login failed. Please check your credentials.'}</span>`;
                errorMessage.classList.remove('hidden');
            }
        } finally {
            if (submitBtn) {
                submitBtn.disabled = false;
                if (loginBtnText) loginBtnText.textContent = 'Login';
            }
        }
    });
});
