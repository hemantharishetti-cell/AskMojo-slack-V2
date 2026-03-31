// Instant Multi-Category Search Logic
window.adminSearchQuery = "";

document.addEventListener('DOMContentLoaded', () => {
    const searchInput = document.getElementById('globalSearchInput');
    const resultsDropdown = document.getElementById('globalSearchResults');
    const resultsList = document.getElementById('searchResultsList');

    if (!searchInput || !resultsDropdown || !resultsList) return;

    let debounceTimer;

    searchInput.addEventListener('input', (e) => {
        const query = e.target.value.trim().toLowerCase();
        window.adminSearchQuery = query;

        clearTimeout(debounceTimer);

        // Hide dropdown if query is too short
        if (query.length < 2) {
            hideResults();
        }

        // Always dispatch search to the active page to keep the local table in sync
        // whether the query is short or long (clearing resets the table)
        debounceTimer = setTimeout(() => {
            if (query.length >= 2) {
                performGlobalSearch(query);
            }
            dispatchSearch();
        }, 300);
    });

    // Close dropdown on click outside
    document.addEventListener('click', (e) => {
        if (!searchInput.contains(e.target) && !resultsDropdown.contains(e.target)) {
            hideResults();
        }
    });

    // Keyboard navigation
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
            hideResults();
        }
    });

    function hideResults() {
        resultsDropdown.classList.add('hidden');
    }

    function showResults() {
        resultsDropdown.classList.remove('hidden');
    }

    async function performGlobalSearch(query) {
        const client = window.apiClient;
        if (!client) return;

        try {
            // Fetch relevant data in parallel
            const [users, documents, categories, slackUsers] = await Promise.all([
                client.getUsers().catch(() => []),
                client.getDocuments(0, 100).catch(() => []),
                client.getCategories().catch(() => []),
                client.getSlackUsers().catch(() => [])
            ]);

            const results = [];

            // Filter Users
            users.filter(u =>
                (u.name && u.name.toLowerCase().includes(query)) ||
                (u.email && u.email.toLowerCase().includes(query))
            ).slice(0, 3).forEach(u => results.push({ type: 'user', id: u.id, title: u.name || u.email, subtitle: 'System User', icon: 'fa-user' }));

            // Filter Documents
            documents.filter(d =>
                (d.title && d.title.toLowerCase().includes(query)) ||
                (d.file_name && d.file_name.toLowerCase().includes(query))
            ).slice(0, 3).forEach(d => results.push({ type: 'document', id: d.id, title: d.title || d.file_name, subtitle: 'Document', icon: 'fa-file-alt' }));

            // Filter Categories
            categories.filter(c =>
                (c.name && c.name.toLowerCase().includes(query)) ||
                (c.description && c.description.toLowerCase().includes(query))
            ).slice(0, 3).forEach(c => results.push({ type: 'category', id: c.id, title: c.name, subtitle: 'Collection', icon: 'fa-folder' }));

            // Filter Slack Users
            slackUsers.filter(s =>
                (s.name && s.name.toLowerCase().includes(query)) ||
                (s.email && s.email.toLowerCase().includes(query))
            ).slice(0, 3).forEach(s => results.push({ type: 'slack-user', id: s.id, title: s.name || s.email, subtitle: 'Slack User', icon: 'fab fa-slack' }));

            renderResults(results);
        } catch (err) {
            console.error('Global search error:', err);
        }
    }

    function renderResults(results) {
        if (results.length === 0) {
            resultsList.innerHTML = `
                <div class="p-8 text-center bg-white">
                    <div class="w-12 h-12 bg-gray-50 rounded-full flex items-center justify-center mx-auto mb-3">
                        <i class="fas fa-search text-gray-300"></i>
                    </div>
                    <p class="text-sm font-medium text-gray-900">No results found</p>
                    <p class="text-xs text-gray-500 mt-1">Try a different keyword</p>
                </div>
            `;
        } else {
            resultsList.innerHTML = results.map(res => `
                <div class="search-result-item flex items-center gap-3 p-3 hover:bg-green-50 cursor-pointer transition-colors border-b border-gray-50 last:border-0" 
                     onclick="navigateToResource('${res.type}', '${res.id}')">
                    <div class="w-10 h-10 rounded-lg flex items-center justify-center ${getColorForType(res.type)}">
                        <i class="fas ${res.icon} text-sm"></i>
                    </div>
                    <div class="flex-1 min-w-0">
                        <div class="text-sm font-semibold text-gray-900 truncate">${res.title}</div>
                        <div class="text-[10px] font-bold text-gray-400 uppercase tracking-tighter">${res.subtitle}</div>
                    </div>
                    <i class="fas fa-chevron-right text-gray-300 text-[10px]"></i>
                </div>
            `).join('');
        }
        showResults();
    }

    function getColorForType(type) {
        switch (type) {
            case 'user': return 'bg-blue-100 text-blue-600';
            case 'document': return 'bg-green-100 text-green-600';
            case 'category': return 'bg-purple-100 text-purple-600';
            case 'slack-user': return 'bg-orange-100 text-orange-600';
            default: return 'bg-gray-100 text-gray-600';
        }
    }
});

function dispatchSearch() {
    // Find the currently active page and call its refresh/load function
    const activePage = document.querySelector('.page.active');
    if (!activePage) return;

    const pageId = activePage.id;
    console.log('Dispatching search for page:', pageId);

    if (pageId === 'usersPage' && typeof loadUsers === 'function') {
        loadUsers();
    } else if (pageId === 'documentsPage' && typeof loadDocuments === 'function') {
        loadDocuments();
    } else if (pageId === 'logsPage') {
        if (typeof loadQueryLogs === 'function') loadQueryLogs();
        if (typeof loadUploadLogs === 'function') loadUploadLogs();
    } else if (pageId === 'categoriesPage' && typeof loadCategories === 'function') {
        loadCategories();
    } else if (pageId === 'slackUsersPage' && typeof loadSlackUsers === 'function') {
        loadSlackUsers();
    }
}

function navigateToResource(type, id) {
    const pageMap = {
        'user': 'users',
        'document': 'documents',
        'category': 'categories',
        'slack-user': 'slack-users'
    };

    const page = pageMap[type];
    if (page && typeof switchPage === 'function') {
        switchPage(page);

        // Hide dropdown and clear input
        const searchInput = document.getElementById('globalSearchInput');
        const resultsDropdown = document.getElementById('globalSearchResults');
        if (searchInput) searchInput.value = "";
        window.adminSearchQuery = ""; // Reset query
        if (resultsDropdown) resultsDropdown.classList.add('hidden');

        // Refresh the page to ensure it shows the correct state
        dispatchSearch();

        console.log(`Navigated to ${page} for ID: ${id}`);
    }
}

window.navigateToResource = navigateToResource;
window.dispatchSearch = dispatchSearch;
