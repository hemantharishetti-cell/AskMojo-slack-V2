// API client
// API_BASE_URL is already defined in auth.js via window.API_BASE_URL
// Don't redeclare it, just use window.API_BASE_URL directly

class ApiClient {
        async updateDomain(domainId, data) {
            return this.request(`/api/v1/admin/domains/${domainId}`, {
                method: 'PUT',
                body: JSON.stringify(data)
            });
        }
    constructor() {
        this.baseURL = window.API_BASE_URL || window.location.origin;
    }

    async request(endpoint, options = {}) {
        // Get token fresh each time to ensure we have the latest
        let token = null;
        if (typeof authManager !== 'undefined') {
            token = authManager.getToken();
        }
        
        const headers = {
            'Content-Type': 'application/json',
            ...options.headers
        };

        // Check if this is a protected endpoint (not login/register)
        const isProtectedEndpoint = !endpoint.includes('/auth/login') && 
                                   !endpoint.includes('/auth/register') &&
                                   !endpoint.includes('/auth/register-json');
        
        if (token) {
            headers['Authorization'] = `Bearer ${token}`;
        } else if (isProtectedEndpoint) {
            // For protected endpoints, warn and potentially redirect
            console.warn('No token available for protected endpoint:', endpoint);
            // Don't throw here - let the server return 401 so we can handle it properly
        }

        const url = `${this.baseURL}${endpoint}`;
        console.log(`API Request: ${options.method || 'GET'} ${url}`);

        try {
            const response = await fetch(url, {
                ...options,
                headers
            });

            console.log(`API Response: ${response.status} ${response.statusText} for ${url}`);

            if (response.status === 401) {
                console.warn('Received 401 Unauthorized for:', endpoint);
                // Only auto-logout if we're not already on the login page
                if (typeof authManager !== 'undefined') {
                    // Check if we're on a protected page before logging out
                    const currentPath = window.location.pathname;
                    if (!currentPath.includes('index.html') && !currentPath.includes('login')) {
                        console.log('Auto-logging out due to 401 error');
                        authManager.logout();
                    }
                }
                throw new Error('Unauthorized - Please login again');
            }

            if (!response.ok) {
                let errorDetail = `HTTP error! status: ${response.status}`;
                try {
                    const errorData = await response.clone().json();
                    errorDetail = errorData.detail || errorData.message || errorDetail;
                } catch (e) {
                    try {
                        const txt = await response.text();
                        errorDetail = txt || errorDetail;
                    } catch (_) {
                        // ignore
                    }
                }
                console.error(`API Error: ${errorDetail} for ${url}`);
                throw new Error(errorDetail);
            }

            // Handle 204 No Content responses (like DELETE)
            if (response.status === 204) {
                return null;
            }

            // Check if response has content
            const contentType = response.headers.get('content-type');
            if (contentType && contentType.includes('application/json')) {
                const data = await response.json();
                return data;
            }
            
            return null;
        } catch (error) {
            console.error('API request failed:', error);
            // Re-throw with more context if it's not already an Error object
            if (error instanceof Error) {
                throw error;
            }
            throw new Error(error.message || 'Network error occurred');
        }
    }

    // Auth endpoints
    async login(email, password) {
        return this.request('/api/v1/auth/login-json', {
            method: 'POST',
            body: JSON.stringify({ email, password })
        });
    }

    async getCurrentUser() {
        return this.request('/api/v1/auth/me');
    }

    // Admin endpoints
    async getStats() {
        return this.request('/api/v1/admin/stats');
    }

    async getUsers(skip = 0, limit = 100) {
        return this.request(`/api/v1/admin/users?skip=${skip}&limit=${limit}`);
    }

    async getUser(userId) {
        return this.request(`/api/v1/admin/users/${userId}`);
    }

    async updateUser(userId, data) {
        return this.request(`/api/v1/admin/users/${userId}`, {
            method: 'PUT',
            body: JSON.stringify(data)
        });
    }

    async deleteUser(userId) {
        return this.request(`/api/v1/admin/users/${userId}`, {
            method: 'DELETE'
        });
    }

    async toggleUserActive(userId) {
        return this.request(`/api/v1/admin/users/${userId}/toggle-active`, {
            method: 'POST'
        });
    }

    async createUser(data) {
        return this.request('/api/v1/admin/users', {
            method: 'POST',
            body: JSON.stringify(data)
        });
    }

    // Slack integration endpoints
    async getSlackConfig() {
        return this.request('/api/v1/slack/config');
    }

    async createSlackConfig(data) {
        return this.request('/api/v1/slack/config', {
            method: 'POST',
            body: JSON.stringify(data)
        });
    }

    async updateSlackConfig(data) {
        return this.request('/api/v1/slack/config', {
            method: 'PUT',
            body: JSON.stringify(data)
        });
    }

    async deleteSlackConfig() {
        return this.request('/api/v1/slack/config', {
            method: 'DELETE'
        });
    }

    async testSlackConnection(message = "Test message from ASKMOJO admin panel") {
        return this.request('/api/v1/slack/test', {
            method: 'POST',
            body: JSON.stringify({ message })
        });
    }

    // Slack user management endpoints
    async fetchSlackUser(emailOrUserId, isUserId = false) {
        const payload = isUserId 
            ? { slack_user_id: emailOrUserId }
            : { email: emailOrUserId };
        return this.request('/api/v1/slack/users/fetch', {
            method: 'POST',
            body: JSON.stringify(payload)
        });
    }

    async createSlackUser(data) {
        return this.request('/api/v1/slack/users', {
            method: 'POST',
            body: JSON.stringify(data)
        });
    }

    async getSlackUsers(skip = 0, limit = 100) {
        return this.request(`/api/v1/slack/users?skip=${skip}&limit=${limit}`);
    }

    async getSlackUser(userId) {
        return this.request(`/api/v1/slack/users/${userId}`);
    }

    async updateSlackUser(userId, data) {
        return this.request(`/api/v1/slack/users/${userId}`, {
            method: 'PUT',
            body: JSON.stringify(data)
        });
    }

    async deleteSlackUser(userId) {
        return this.request(`/api/v1/slack/users/${userId}`, {
            method: 'DELETE'
        });
    }

    // Category management endpoints
    async getCategories(skip = 0, limit = 100, domain_id = null) {
        const params = new URLSearchParams({ skip: String(skip), limit: String(limit) });
        if (domain_id) params.append('domain_id', String(domain_id));
        return this.request(`/api/v1/admin/categories?${params.toString()}`);
    }

    async getCategory(categoryId) {
        return this.request(`/api/v1/admin/categories/${categoryId}`);
    }

    async createCategory(data) {
        return this.request('/api/v1/admin/categories', {
            method: 'POST',
            body: JSON.stringify(data)
        });
    }

    async updateCategory(categoryId, data) {
        return this.request(`/api/v1/admin/categories/${categoryId}`, {
            method: 'PUT',
            body: JSON.stringify(data)
        });
    }

    // Domain management endpoints
    async getDomains(skip = 0, limit = 100) {
        return this.request(`/api/v1/admin/domains?skip=${skip}&limit=${limit}`);
    }

    async createDomain(data) {
        return this.request('/api/v1/admin/domains', {
            method: 'POST',
            body: JSON.stringify(data)
        });
    }

    async deleteDomain(domainId) {
        return this.request(`/api/v1/admin/domains/${domainId}`, {
            method: 'DELETE'
        });
    }

    async getCategoryDomains() {
        return this.request('/api/v1/admin/category-domains');
    }

    async backfillCategoryDomains(payload) {
        return this.request('/api/v1/admin/category-domains/backfill', {
            method: 'POST',
            body: JSON.stringify(payload || {})
        });
    }

    async deleteCategory(categoryId) {
        return this.request(`/api/v1/admin/categories/${categoryId}`, {
            method: 'DELETE'
        });
    }

    // Document management endpoints
    async uploadDocument(formData) {
        // For file uploads, we need to use FormData and not set Content-Type header
        // The browser will set it automatically with the boundary
        let token = null;
        if (typeof authManager !== 'undefined') {
            token = authManager.getToken();
        }

        const headers = {};
        if (token) {
            headers['Authorization'] = `Bearer ${token}`;
        }

        const url = `${this.baseURL}/api/v1/upload`;
        console.log(`API Request: POST ${url} (multipart/form-data)`);

        const response = await fetch(url, {
            method: 'POST',
            headers,
            body: formData
        });

        console.log(`API Response: ${response.status} ${response.statusText} for ${url}`);

        if (response.status === 401) {
            if (typeof authManager !== 'undefined') {
                authManager.logout();
            }
            throw new Error('Unauthorized - Please login again');
        }

        if (!response.ok) {
            let errorDetail = 'An error occurred';
            try {
                const errorData = await response.json();
                errorDetail = errorData.detail || errorData.message || errorDetail;
            } catch (e) {
                // If JSON parsing fails, try to get text
                try {
                    const txt = await response.text();
                    errorDetail = txt || errorDetail;
                } catch (_) {
                    // If text parsing also fails, use status code description
                    errorDetail = `HTTP ${response.status}: ${response.statusText}`;
                }
            }
            throw new Error(errorDetail);
        }

        return await response.json();
    }

    async getDocuments(skip = 0, limit = 100) {
        return this.request(`/api/v1/documents?skip=${skip}&limit=${limit}`);
    }

    async getDocument(documentId) {
        return this.request(`/api/v1/documents/${documentId}`);
    }

    async deleteDocument(documentId) {
        return this.request(`/api/v1/documents/${documentId}`, {
            method: 'DELETE'
        });
    }

    // Logs API methods
    async getQueryLogs(skip = 0, limit = 100) {
        return this.request(`/api/v1/admin/logs/queries?skip=${skip}&limit=${limit}`);
    }

    async getUploadLogs(skip = 0, limit = 100) {
        return this.request(`/api/v1/admin/logs/uploads?skip=${skip}&limit=${limit}`);
    }
}

// Initialize apiClient and make it globally available
const apiClient = new ApiClient();
window.apiClient = apiClient; // Make it globally available

