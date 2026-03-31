// Authentication utilities
// Define API_BASE_URL on window to make it available globally
if (typeof window.API_BASE_URL === 'undefined') {
    window.API_BASE_URL = window.location.origin;
}
const API_BASE_URL = window.API_BASE_URL;

class AuthManager {
    constructor() {
        this.tokenKey = 'admin_token';
        this.userKey = 'admin_user';
    }

    getToken() {
        return localStorage.getItem(this.tokenKey);
    }

    setToken(token) {
        localStorage.setItem(this.tokenKey, token);
    }

    removeToken() {
        localStorage.removeItem(this.tokenKey);
        localStorage.removeItem(this.userKey);
    }

    getUser() {
        const userStr = localStorage.getItem(this.userKey);
        return userStr ? JSON.parse(userStr) : null;
    }

    setUser(user) {
        localStorage.setItem(this.userKey, JSON.stringify(user));
    }

    isAuthenticated() {
        return !!this.getToken();
    }

    async checkAuth() {
        if (!this.isAuthenticated()) {
            return false;
        }

        try {
            const response = await fetch(`${API_BASE_URL}/api/v1/auth/me`, {
                headers: {
                    'Authorization': `Bearer ${this.getToken()}`
                }
            });

            if (response.ok) {
                const user = await response.json();
                this.setUser(user);
                return true;
            } else {
                this.removeToken();
                return false;
            }
        } catch (error) {
            console.error('Auth check failed:', error);
            this.removeToken();
            return false;
        }
    }

    logout() {
        this.removeToken();
        window.location.href = '/static/index.html';
    }

    requireAuth() {
        if (!this.isAuthenticated()) {
            window.location.href = '/static/index.html';
            return false;
        }
        return true;
    }
}

const authManager = new AuthManager();

