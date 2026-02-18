// Categories Management
document.addEventListener('DOMContentLoaded', () => {
    // ...existing code...
    const categoriesPage = document.getElementById('categoriesPage');
    if (!categoriesPage) {
        console.warn('categoriesPage element not found, Categories script not initializing.');
        return;
    }

    // Check authentication
    if (typeof authManager === 'undefined' || !authManager.requireAuth()) {
        console.error('authManager is not defined or user not authenticated.');
        return;
    }

    // Verify user is admin
    const user = authManager.getUser();
    if (!user || user.role !== 'admin') {
        console.error('User is not an admin, logging out.');
        authManager.logout();
        return;
    }

    // Setup event listeners
    setupCategoriesEventListeners();

    // Load categories when page is shown
    const observer = new MutationObserver((mutations) => {
        mutations.forEach((mutation) => {
            if (mutation.type === 'attributes' && mutation.attributeName === 'class') {
                if (!categoriesPage.classList.contains('hidden')) {
                    console.log('Categories page shown, loading categories...');
                    loadCategories();
                }
            }
        });
    });
    observer.observe(categoriesPage, { attributes: true });

    // Also try to load if page is already visible
    if (!categoriesPage.classList.contains('hidden')) {
        setTimeout(() => loadCategories(), 100);
    }
});

// Make loadCategories globally accessible for admin.js to call
window.loadCategories = async function () {
    console.log('loadCategories called');
    const client = window.apiClient;
    if (!client) {
        console.error('API client not available in loadCategories.');
        showNotification('API client not available. Please refresh.', 'error');
        return;
    }

    const tbody = document.getElementById('categoriesTableBody');
    if (!tbody) {
        console.error('categoriesTableBody not found.');
        return;
    }

    tbody.innerHTML = '<tr><td colspan="6" class="text-center py-8 text-gray-500 text-sm"><i class="fas fa-spinner fa-spin mr-2"></i>Loading categories...</td></tr>';

    try {
        console.log('Fetching categories...');
        const categories = await client.getCategories();
        console.log('Categories received:', categories);

        if (categories.length === 0) {
            tbody.innerHTML = '<tr><td colspan="6" class="text-center py-8 text-gray-500 text-sm">No categories found. Click "Add Category" to create one.</td></tr>';
            return;
        }

        // Escape HTML to prevent XSS and syntax errors
        const escapeHtml = (text) => {
            if (!text) return '';
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        };

        // Truncate description to 3-4 words
        const truncateDescription = (text, maxWords = 4) => {
            if (!text) return '';
            const words = text.trim().split(/\s+/);
            if (words.length > maxWords) {
                return words.slice(0, maxWords).join(' ') + '...';
            }
            return text;
        };

        tbody.innerHTML = categories.map(category => {
            const categoryNameEscaped = escapeHtml(category.name || '');
            const fullDescription = category.description ? escapeHtml(category.description) : 'No description';
            const truncatedDescription = truncateDescription(category.description || '', 4);
            const truncatedDescriptionEscaped = escapeHtml(truncatedDescription);
            const collectionNameEscaped = escapeHtml(category.collection_name || '');

            // Display domains as comma-separated list
            const domainsText = category.domains && category.domains.length > 0
                ? category.domains.map(d => escapeHtml(d.name || d)).join(', ')
                : '—';

            return `
            <tr class="table-row">
                <td>
                    <div class="flex items-center gap-3">
                        <div class="w-10 h-10 rounded-lg bg-gradient-to-br from-purple-100 to-indigo-100 flex items-center justify-center">
                            <i class="fas fa-folder text-purple-600 text-base"></i>
                        </div>
                        <div>
                            <div class="text-sm font-semibold text-gray-900">${categoryNameEscaped}</div>
                        </div>
                    </div>
                </td>
                <td class="text-sm text-gray-700" style="max-width: 250px;">
                    <span class="cursor-help text-gray-600 block truncate" title="${(category.description || 'No description').replace(/"/g, '&quot;')}">${truncatedDescriptionEscaped}</span>
                </td>
                <td>
                    <div class="flex flex-wrap gap-1">
                        ${category.domains && category.domains.length > 0
                            ? category.domains.map(d => `<span class="px-2 py-1 text-xs font-semibold text-gray-700 bg-blue-50 rounded-lg">${escapeHtml(d.name || d)}</span>`).join('')
                            : '<span class="text-gray-400 text-xs">—</span>'
                        }
                    </div>
                </td>
                <td>
                    <span class="px-2 py-1 text-xs font-semibold text-gray-700">${category.document_count || 0}</span>
                </td>
                <td>
                    <span class="px-2 py-1 text-xs font-bold rounded-full ${category.is_active ? 'bg-green-100 text-green-800' : 'bg-gray-100 text-gray-800'}">
                        ${category.is_active ? 'Active' : 'Inactive'}
                    </span>
                </td>
               <td>
    <div class="flex items-center gap-2">
        <button class="btn btn-ghost btn-xs text-gray-600 hover:bg-gray-50 view-category-btn" data-category-id="${category.id}" data-category-name="${categoryNameEscaped}" data-collection-name="${collectionNameEscaped}" data-full-description="${fullDescription.replace(/"/g, '&quot;')}" title="View Details">
            <i class="fas fa-eye"></i>
        </button>
        <button class="btn btn-ghost btn-xs text-purple-500 hover:bg-purple-50 generate-desc-btn" data-category-id="${category.id}" data-category-name="${categoryNameEscaped}" title="Generate AI Description">
            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z"/></svg>
        </button>
        <button class="btn btn-ghost btn-xs text-blue-500 hover:bg-blue-50 edit-category-btn" data-category-id="${category.id}">
            <i class="fas fa-edit"></i>
        </button>
        <button class="btn btn-ghost btn-xs text-red-500 hover:bg-red-50 delete-category-btn" data-category-id="${category.id}" data-category-name="${categoryNameEscaped}" data-document-count="${category.document_count || 0}">
            <i class="fas fa-trash"></i>
        </button>
    </div>
</td>

            </tr>
        `;
        }).join('');

        // Attach event listeners to delete buttons
        tbody.querySelectorAll('.delete-category-btn').forEach(btn => {
            btn.addEventListener('click', function () {
                const categoryId = parseInt(this.getAttribute('data-category-id'));
                const categoryName = this.getAttribute('data-category-name');
                const documentCount = parseInt(this.getAttribute('data-document-count') || '0');
                if (typeof window.deleteCategory === 'function') {
                    window.deleteCategory(categoryId, categoryName, documentCount);
                } else {
                    console.error('deleteCategory function not found');
                    alert('Delete function not available. Please refresh the page.');
                }
            });
        });

        // Attach event listeners to edit buttons
        tbody.querySelectorAll('.edit-category-btn').forEach(btn => {
            btn.addEventListener('click', function () {
                const categoryId = parseInt(this.getAttribute('data-category-id'));
                if (typeof window.editCategory === 'function') {
                    window.editCategory(categoryId);
                } else {
                    console.error('editCategory function not found');
                    alert('Edit function not available. Please refresh the page.');
                }
            });
        });

        // Attach event listeners to generate description buttons
        tbody.querySelectorAll('.generate-desc-btn').forEach(btn => {
            btn.addEventListener('click', async function () {
                const categoryId = parseInt(this.getAttribute('data-category-id'));
                const categoryName = this.getAttribute('data-category-name');
                await generateCategoryDescription(categoryId, categoryName);
            });
        });

        // Attach event listeners to view overview buttons
        tbody.querySelectorAll('.view-category-btn').forEach(btn => {
            btn.addEventListener('click', async function () {
                const categoryId = parseInt(this.getAttribute('data-category-id'));
                if (typeof window.viewCategoryOverview === 'function') {
                    await window.viewCategoryOverview(categoryId);
                } else {
                    console.error('viewCategoryOverview function not found');
                    showNotification('View function not available. Please refresh the page.', 'error');
                }
            });
        });



    } catch (error) {
        console.error('Failed to load categories:', error);
        tbody.innerHTML = `<tr><td colspan="6" class="text-center py-8 text-red-500 text-sm"><i class="fas fa-exclamation-circle mr-2"></i>Failed to load categories: ${error.message || 'Unknown error'}</td></tr>`;
        showNotification(error.message || 'Failed to load categories', 'error');
    }
}

function setupCategoriesEventListeners() {
    // Add Domain Button & Modal
    const addDomainBtnCategory = document.getElementById('addDomainBtnCategory');
    const addDomainModal = document.getElementById('addDomainModal');
    const closeAddDomainModal = document.getElementById('closeAddDomainModal');
    const cancelAddDomain = document.getElementById('cancelAddDomain');
    const addDomainForm = document.getElementById('addDomainForm');
    const addDomainInput = document.getElementById('addDomainInput');
    const addDomainErrorMessage = document.getElementById('addDomainErrorMessage');

    function showAddDomainModal() {
        if (addDomainModal && addDomainInput && addDomainErrorMessage) {
            addDomainInput.value = '';
            addDomainErrorMessage.classList.add('hidden');
            addDomainModal.classList.remove('hidden');
            addDomainModal.style.setProperty('display', 'flex', 'important');
            addDomainInput.focus();
        }
    }
    function hideAddDomainModal() {
        if (addDomainModal && addDomainInput && addDomainErrorMessage) {
            addDomainModal.classList.add('hidden');
            addDomainModal.style.setProperty('display', 'none', 'important');
            addDomainInput.value = '';
            addDomainErrorMessage.classList.add('hidden');
        }
    }
    if (addDomainBtnCategory) addDomainBtnCategory.addEventListener('click', showAddDomainModal);
    if (closeAddDomainModal) closeAddDomainModal.addEventListener('click', hideAddDomainModal);
    if (cancelAddDomain) cancelAddDomain.addEventListener('click', hideAddDomainModal);
    if (addDomainForm) {
        addDomainForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            const domainName = addDomainInput.value.trim();
            if (!domainName) {
                addDomainErrorMessage.textContent = 'Domain name is required.';
                addDomainErrorMessage.classList.remove('hidden');
                addDomainInput.focus();
                return;
            }
            try {
                const client = window.apiClient;
                const created = await client.createDomain({ name: domainName, description: null, is_active: true });
                showNotification(`Domain "${created.name}" created successfully.`, 'success');
                hideAddDomainModal();
                await loadCategories();
            } catch (error) {
                addDomainErrorMessage.textContent = error.message || 'Failed to create domain.';
                addDomainErrorMessage.classList.remove('hidden');
                addDomainInput.focus();
            }
        });
    }

    // Delete Domain Button & Modal
    const deleteDomainBtnCategory = document.getElementById('deleteDomainBtnCategory');
    const deleteDomainModal = document.getElementById('deleteDomainModal');
    const closeDeleteDomainModal = document.getElementById('closeDeleteDomainModal');
    const cancelDeleteDomain = document.getElementById('cancelDeleteDomain');
    const deleteDomainForm = document.getElementById('deleteDomainForm');
    const deleteDomainSelect = document.getElementById('deleteDomainSelect');
    const deleteDomainErrorMessage = document.getElementById('deleteDomainErrorMessage');

    function showDeleteDomainModal() {
        if (!deleteDomainModal) return;
        // Load domains
        const client = window.apiClient;
        if (!client) {
            showNotification('API client not available. Please refresh.', 'error');
            return;
        }
        client.getDomains(0, 100).then(domains => {
            if (deleteDomainSelect) {
                deleteDomainSelect.innerHTML = '<option value="">Select Domain</option>';
                if (domains && domains.length > 0) {
                    domains.forEach(domain => {
                        const option = document.createElement('option');
                        option.value = domain.id;
                        option.textContent = domain.name;
                        deleteDomainSelect.appendChild(option);
                    });
                } else {
                    deleteDomainSelect.innerHTML = '<option value="">No domains available</option>';
                }
            }
            if (deleteDomainErrorMessage) deleteDomainErrorMessage.classList.add('hidden');
            deleteDomainModal.classList.remove('hidden');
            deleteDomainModal.style.setProperty('display', 'flex', 'important');
            if (deleteDomainSelect) deleteDomainSelect.focus();
        }).catch(error => {
            showNotification(error.message || 'Failed to load domains', 'error');
        });
    }
    function hideDeleteDomainModal() {
        deleteDomainModal.classList.add('hidden');
        deleteDomainModal.style.setProperty('display', 'none', 'important');
        if (deleteDomainSelect) deleteDomainSelect.value = '';
        if (deleteDomainErrorMessage) deleteDomainErrorMessage.classList.add('hidden');
    }
    if (deleteDomainBtnCategory) deleteDomainBtnCategory.addEventListener('click', showDeleteDomainModal);
    if (closeDeleteDomainModal) closeDeleteDomainModal.addEventListener('click', hideDeleteDomainModal);
    if (cancelDeleteDomain) cancelDeleteDomain.addEventListener('click', hideDeleteDomainModal);
    if (deleteDomainForm) {
        deleteDomainForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            const domainId = deleteDomainSelect?.value;
            if (!domainId) {
                if (deleteDomainErrorMessage) {
                    deleteDomainErrorMessage.textContent = 'Please select a domain to delete.';
                    deleteDomainErrorMessage.classList.remove('hidden');
                }
                return;
            }
            const client = window.apiClient;
            if (!client) {
                showNotification('API client not available. Please refresh.', 'error');
                return;
            }
            try {
                await client.deleteDomain(parseInt(domainId));
                showNotification('Domain deleted successfully.', 'success');
                hideDeleteDomainModal();
                await loadCategories();
            } catch (error) {
                if (deleteDomainErrorMessage) {
                    deleteDomainErrorMessage.textContent = error.message || 'Failed to delete domain.';
                    deleteDomainErrorMessage.classList.remove('hidden');
                } else {
                    showNotification(error.message || 'Failed to delete domain', 'error');
                }
            }
        });
    }

    // Add Category Button
    const addCategoryBtn = document.getElementById('addCategoryBtn');
    if (addCategoryBtn) {
        addCategoryBtn.addEventListener('click', openCreateCategoryModal);
    }

    // Create Category Modal
    const createCategoryModal = document.getElementById('createCategoryModal');
    const closeCreateCategoryModal = document.getElementById('closeCreateCategoryModal');
    const cancelCreateCategory = document.getElementById('cancelCreateCategory');
    const createCategoryForm = document.getElementById('createCategoryForm');

    if (closeCreateCategoryModal) {
        closeCreateCategoryModal.addEventListener('click', () => {
            createCategoryModal.classList.add('hidden');
            createCategoryModal.style.setProperty('display', 'none', 'important');
        });
    }

    if (cancelCreateCategory) {
        cancelCreateCategory.addEventListener('click', () => {
            createCategoryModal.classList.add('hidden');
            createCategoryModal.style.setProperty('display', 'none', 'important');
        });
    }

    if (createCategoryForm) {
        createCategoryForm.addEventListener('submit', handleCreateCategory);
    }

    // Edit Category Modal
    const editCategoryModal = document.getElementById('editCategoryModal');
    const closeEditCategoryModal = document.getElementById('closeEditCategoryModal');
    const cancelEditCategory = document.getElementById('cancelEditCategory');
    const editCategoryForm = document.getElementById('editCategoryForm');

    if (closeEditCategoryModal) {
        closeEditCategoryModal.addEventListener('click', () => {
            editCategoryModal.classList.add('hidden');
            editCategoryModal.style.setProperty('display', 'none', 'important');
        });
    }

    if (cancelEditCategory) {
        cancelEditCategory.addEventListener('click', () => {
            editCategoryModal.classList.add('hidden');
            editCategoryModal.style.setProperty('display', 'none', 'important');
        });
    }

    if (editCategoryForm) {
        editCategoryForm.addEventListener('submit', handleEditCategory);
    }

    // Category Overview modal close buttons
    const closeOverview = document.getElementById('closeCategoryOverviewModal');
    const dismissOverview = document.getElementById('dismissCategoryOverview');
    const overviewModal = document.getElementById('categoryOverviewModal');
    function hideOverview() {
        if (!overviewModal) return;
        overviewModal.classList.add('hidden');
        overviewModal.style.setProperty('display', 'none', 'important');
        overviewModal.style.setProperty('visibility', 'hidden', 'important');
        overviewModal.style.setProperty('opacity', '0', 'important');
    }
    if (closeOverview) closeOverview.addEventListener('click', hideOverview);
    if (dismissOverview) dismissOverview.addEventListener('click', hideOverview);

    // (Delete Domain Modal event listeners already set above)

    // (handlers defined above)
}

// Add Domain handler
function openAddDomainModal() {
    const newDomain = prompt('Enter new domain name (e.g., DevOps, Policies, Security):', '');
    if (!newDomain || !newDomain.trim()) return;

    handleAddDomain(newDomain.trim());
}

async function handleAddDomain(domainName) {
    const client = window.apiClient;
    if (!client) {
        showNotification('API client not available. Please refresh.', 'error');
        return;
    }

    try {
        const created = await client.createDomain({ name: domainName, description: null, is_active: true });
        showNotification(`Domain "${created.name}" created successfully.`, 'success');
        // Reload categories to reflect any category-domain associations
        await loadCategories();
    } catch (error) {
        showNotification(error.message || 'Failed to create domain', 'error');
    }
}

// Delete Domain handler
async function openDeleteDomainModal() {
    const client = window.apiClient;
    if (!client) {
        showNotification('API client not available. Please refresh.', 'error');
        return;
    }

    try {
        // Load domains
        const domains = await client.getDomains(0, 100);
        const select = document.getElementById('deleteDomainSelect');
        if (!select) {
            showNotification('Delete domain modal not found', 'error');
            return;
        }
        // Populate select with available domains
        select.innerHTML = '<option value="">Select Domain</option>';
        if (domains && domains.length > 0) {
            domains.forEach(domain => {
                const option = document.createElement('option');
                option.value = domain.id;
                option.textContent = domain.name;
                select.appendChild(option);
            });
        } else {
            select.innerHTML = '<option value="">No domains available</option>';
        }
        // Always show disclaimer (already in modal HTML)
        // Show modal
        const modal = document.getElementById('deleteDomainModal');
        if (modal) {
            const errorDiv = document.getElementById('deleteDomainErrorMessage');
            if (errorDiv) {
                errorDiv.classList.add('hidden');
            }
            modal.classList.remove('hidden');
            modal.style.setProperty('display', 'flex', 'important');
            select.focus();
        }
    } catch (error) {
        showNotification(error.message || 'Failed to load domains', 'error');
    }
}

async function handleDeleteDomain(e) {
    e.preventDefault();
    
    const domainId = document.getElementById('deleteDomainSelect')?.value;
    if (!domainId) {
        showNotification('Please select a domain to delete', 'error');
        return;
    }

    const client = window.apiClient;
    if (!client) {
        showNotification('API client not available. Please refresh.', 'error');
        return;
    }

    try {
        await client.deleteDomain(parseInt(domainId));
        showNotification('Domain deleted successfully.', 'success');
        
        // Close modal
        const modal = document.getElementById('deleteDomainModal');
        if (modal) {
            modal.classList.add('hidden');
            modal.style.setProperty('display', 'none', 'important');
        }

        // Reload categories
        await loadCategories();
    } catch (error) {
        const errorDiv = document.getElementById('deleteDomainErrorMessage');
        if (errorDiv) {
            errorDiv.classList.remove('hidden');
            errorDiv.textContent = error.message || 'Failed to delete domain';
        } else {
            showNotification(error.message || 'Failed to delete domain', 'error');
        }
    }
}

function openCreateCategoryModal() {
    const modal = document.getElementById('createCategoryModal');
    if (!modal) {
        console.error('createCategoryModal not found');
        return;
    }

    // Reset form
    const form = document.getElementById('createCategoryForm');
    if (form) {
        form.reset();
    }
    const errorDiv = document.getElementById('createCategoryErrorMessage');
    if (errorDiv) {
        errorDiv.classList.add('hidden');
    }
    const isActiveSelect = document.getElementById('createCategoryIsActive');
    if (isActiveSelect) {
        isActiveSelect.value = 'true';
    }

    // No domain selection on creation

    // Show modal - ensure it's visible
    modal.classList.remove('hidden');
    modal.style.removeProperty('display');
    modal.style.setProperty('display', 'flex', 'important');
    modal.style.setProperty('visibility', 'visible', 'important');
    modal.style.setProperty('opacity', '1', 'important');
    modal.style.setProperty('z-index', '9999', 'important');
    modal.style.setProperty('pointer-events', 'auto', 'important');

    // Ensure modal-box is also visible
    const modalBox = modal.querySelector('.modal-box');
    if (modalBox) {
        modalBox.style.setProperty('pointer-events', 'auto', 'important');
        modalBox.style.setProperty('position', 'relative', 'important');
        modalBox.style.setProperty('z-index', '10000', 'important');
    }

    console.log('Create category modal opened');
}

window.editCategory = async function (categoryId) {
    console.log('editCategory called with categoryId:', categoryId);
    const client = window.apiClient;
    if (!client) {
        showNotification('API client not available. Please refresh.', 'error');
        return;
    }

    const modal = document.getElementById('editCategoryModal');
    if (!modal) {
        console.error('editCategoryModal not found');
        return;
    }

    try {
        // Fetch category data
        const category = await client.getCategory(categoryId);
        console.log('Category data received:', category);

        // Populate form
        document.getElementById('editCategoryId').value = category.id;
        document.getElementById('editCategoryName').value = category.name;
        document.getElementById('editCategoryDescription').value = category.description || '';
        document.getElementById('editCategoryCollectionName').value = category.collection_name;
        document.getElementById('editCategoryIsActive').value = category.is_active ? 'true' : 'false';
        document.getElementById('editCategoryErrorMessage').classList.add('hidden');

        // Load domains into checkboxes first
        await loadDomainsIntoCheckboxes('editCategoryDomains');

        // Set selected domains
        if (category.domains && category.domains.length > 0) {
            const selectedDomainIds = category.domains.map(d => d.id);
            setSelectedDomainIds('editCategoryDomains', selectedDomainIds);
        }

        // Show modal - ensure it's visible
        modal.classList.remove('hidden');
        modal.style.removeProperty('display');
        modal.style.setProperty('display', 'flex', 'important');
        modal.style.setProperty('visibility', 'visible', 'important');
        modal.style.setProperty('opacity', '1', 'important');
        modal.style.setProperty('z-index', '9999', 'important');
        modal.style.setProperty('pointer-events', 'auto', 'important');

        // Ensure modal-box is also visible
        const modalBox = modal.querySelector('.modal-box');
        if (modalBox) {
            modalBox.style.setProperty('pointer-events', 'auto', 'important');
            modalBox.style.setProperty('position', 'relative', 'important');
            modalBox.style.setProperty('z-index', '10000', 'important');
        }

        console.log('Edit category modal opened');
    } catch (error) {
        console.error('Failed to fetch category:', error);
        showNotification(error.message || 'Failed to load category data', 'error');
    }
}

async function handleCreateCategory(e) {
    e.preventDefault();
    const client = window.apiClient;
    if (!client) {
        showNotification('API client not available. Please refresh.', 'error');
        return;
    }

    const submitBtn = e.target.querySelector('button[type="submit"]');
    const errorDiv = document.getElementById('createCategoryErrorMessage');

    // Disable submit button
    submitBtn.disabled = true;
    submitBtn.innerHTML = '<i class="fas fa-spinner fa-spin mr-2"></i>Creating...';
    errorDiv.classList.add('hidden');

    try {
        const categoryData = {
            name: document.getElementById('createCategoryName').value.trim(),
            description: document.getElementById('createCategoryDescription').value.trim() || null,
            is_active: document.getElementById('createCategoryIsActive').value === 'true'
        };

        if (!categoryData.name) {
            throw new Error('Category name is required');
        }

        console.log('Creating category:', categoryData);
        await client.createCategory(categoryData);

        showNotification('Category created successfully!', 'success');

        // Close modal
        const modal = document.getElementById('createCategoryModal');
        modal.classList.add('hidden');
        modal.style.setProperty('display', 'none', 'important');

        // Reload categories
        await loadCategories();
    } catch (error) {
        console.error('Failed to create category:', error);
        errorDiv.innerHTML = `<i class="fas fa-exclamation-circle mr-2"></i>${error.message || 'Failed to create category'}`;
        errorDiv.classList.remove('hidden');
    } finally {
        submitBtn.disabled = false;
        submitBtn.innerHTML = 'Create Category';
    }
}

async function handleEditCategory(e) {
    e.preventDefault();
    const client = window.apiClient;
    if (!client) {
        showNotification('API client not available. Please refresh.', 'error');
        return;
    }

    const categoryId = document.getElementById('editCategoryId').value;
    const submitBtn = e.target.querySelector('button[type="submit"]');
    const errorDiv = document.getElementById('editCategoryErrorMessage');

    // Disable submit button
    submitBtn.disabled = true;
    submitBtn.innerHTML = '<i class="fas fa-spinner fa-spin mr-2"></i>Saving...';
    errorDiv.classList.add('hidden');

    try {
        const categoryData = {
            name: document.getElementById('editCategoryName').value.trim(),
            description: document.getElementById('editCategoryDescription').value.trim() || null,
            domain_ids: getSelectedDomainIds('editCategoryDomains'),
            is_active: document.getElementById('editCategoryIsActive').value === 'true'
        };

        if (!categoryData.name) {
            throw new Error('Category name is required');
        }

        console.log('Updating category:', categoryId, categoryData);
        await client.updateCategory(categoryId, categoryData);

        showNotification('Category updated successfully!', 'success');

        // Close modal
        const modal = document.getElementById('editCategoryModal');
        modal.classList.add('hidden');
        modal.style.setProperty('display', 'none', 'important');

        // Reload categories
        await loadCategories();
    } catch (error) {
        console.error('Failed to update category:', error);
        errorDiv.innerHTML = `<i class="fas fa-exclamation-circle mr-2"></i>${error.message || 'Failed to update category'}`;
        errorDiv.classList.remove('hidden');
    } finally {
        submitBtn.disabled = false;
        submitBtn.innerHTML = 'Save Changes';
    }
}

window.deleteCategory = async function (categoryId, categoryName, documentCount) {
    console.log('deleteCategory called with:', { categoryId, categoryName, documentCount });

    const client = window.apiClient;
    if (!client) {
        console.error('API client not available');
        if (typeof showNotification === 'function') {
            showNotification('API client not available. Please refresh.', 'error');
        } else {
            alert('API client not available. Please refresh.');
        }
        return;
    }

    // Check if client has deleteCategory method
    if (typeof client.deleteCategory !== 'function') {
        console.error('client.deleteCategory is not a function. Available methods:', Object.keys(client));
        if (typeof showNotification === 'function') {
            showNotification('Delete function not available. Please refresh the page.', 'error');
        } else {
            alert('Delete function not available. Please refresh the page.');
        }
        return;
    }

    if (documentCount > 0) {
        const message = `Cannot delete category with ${documentCount} document(s). Please reassign or delete documents first.`;
        if (typeof showNotification === 'function') {
            showNotification(message, 'error');
        } else {
            alert(message);
        }
        return;
    }

    if (!confirm(`Are you sure you want to delete the category "${categoryName}"? This action cannot be undone.`)) {
        return;
    }

    try {
        console.log('Deleting category:', categoryId);
        const response = await client.deleteCategory(categoryId);
        console.log('Delete response:', response);

        // Show success message
        if (typeof showNotification === 'function') {
            showNotification('Category deleted successfully!', 'success');
        } else {
            alert('Category deleted successfully!');
        }

        // Reload categories after a short delay to ensure UI updates
        setTimeout(async () => {
            if (typeof window.loadCategories === 'function') {
                await window.loadCategories();
            } else if (typeof loadCategories === 'function') {
                await loadCategories();
            } else {
                console.error('loadCategories function not found, reloading page');
                window.location.reload();
            }
        }, 500);
    } catch (error) {
        console.error('Failed to delete category:', error);
        console.error('Error details:', {
            message: error.message,
            stack: error.stack,
            name: error.name
        });

        const errorMessage = error.message || error.detail || 'Failed to delete category';
        if (typeof showNotification === 'function') {
            showNotification(errorMessage, 'error');
        } else {
            alert(`Error: ${errorMessage}`);
        }
    }
}

// Ensure deleteCategory is globally accessible
if (typeof window !== 'undefined') {
    // Already defined above, but ensure it's accessible
    console.log('deleteCategory function registered globally');
}


// ============================================================
// GENERATE AI DESCRIPTION FOR CATEGORY
// Calls API to analyze all documents and generate description
// ============================================================
window.generateCategoryDescription = async function (categoryId, categoryName) {
    // Check API client availability
    const client = window.apiClient;
    if (!client) {
        showNotification('API client not available. Please refresh.', 'error');
        return;
    }

    // Confirm with user
    if (!confirm(`Generate AI description for "${categoryName}"?\n\nThis will analyze all documents in this category and create a searchable description.`)) {
        return;
    }

    // Show loading notification
    showNotification('Generating AI description... This may take a moment.', 'info');

    try {
        // Use apiClient.request (handles auth automatically like other API calls)
        const result = await client.request(`/api/v1/admin/categories/${categoryId}/generate-description`, {
            method: 'POST'
        });

        // Show success message
        showNotification(`✓ Description generated! Analyzed ${result.documents_analyzed} document(s).`, 'success');

        // Reload categories to show new description
        await loadCategories();

    } catch (error) {
        console.error('[ERROR] Failed to generate description:', error);
        showNotification(error.message || 'Failed to generate description', 'error');
    }
}

// Helper functions for domain selection (multi-select checkboxes)
async function loadDomainsIntoCheckboxes(containerId) {
    const container = document.getElementById(containerId);
    if (!container) return;

    const client = window.apiClient;
    if (!client) return;

    try {
        const domains = await client.getDomains(0, 100);

        // Clear existing checkboxes
        container.innerHTML = '';

        if (!domains || domains.length === 0) {
            container.innerHTML = '<p class="text-gray-500 text-sm">No domains available. Create domains first.</p>';
            return;
        }

        // Create checkboxes for each domain
        domains.forEach(domain => {
            const checkboxDiv = document.createElement('div');
            checkboxDiv.className = 'form-control';
            const label = document.createElement('label');
            label.className = 'label cursor-pointer justify-start gap-3';
            const checkbox = document.createElement('input');
            checkbox.type = 'checkbox';
            checkbox.className = 'checkbox checkbox-primary';
            checkbox.value = domain.id;
            const labelText = document.createElement('span');
            labelText.className = 'label-text text-sm text-gray-700';
            labelText.textContent = domain.name;

            label.appendChild(checkbox);
            label.appendChild(labelText);
            checkboxDiv.appendChild(label);
            container.appendChild(checkboxDiv);
        });
    } catch (error) {
        console.error('Failed to load domains:', error);
        container.innerHTML = '<p class="text-red-500 text-sm">Failed to load domains</p>';
    }
}

function getSelectedDomainIds(containerId) {
    const container = document.getElementById(containerId);
    if (!container) return [];

    const checkboxes = container.querySelectorAll('input[type="checkbox"]:checked');
    return Array.from(checkboxes).map(cb => parseInt(cb.value));
}

function setSelectedDomainIds(containerId, selectedIds) {
    const container = document.getElementById(containerId);
    if (!container) return;

    const checkboxes = container.querySelectorAll('input[type="checkbox"]');
    checkboxes.forEach(checkbox => {
        const id = parseInt(checkbox.value);
        checkbox.checked = selectedIds.includes(id);
    });
}

// Category Overview Modal (view-only): total docs, associated domains, docs grouped by domain
window.viewCategoryOverview = async function (categoryId) {
    const client = window.apiClient;
    if (!client) {
        showNotification('API client not available. Please refresh.', 'error');
        return;
    }

    // Escape HTML to prevent XSS
    const escapeHtml = (text) => {
        if (!text) return '';
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    };

    try {
        // Load category details, domains, and all documents (client-side grouping)
        const [category, documents, domains] = await Promise.all([
            client.getCategory(categoryId),
            client.getDocuments(0, 1000),
            client.getDomains(0, 1000)
        ]);

        const domainMap = {};
        domains.forEach(d => domainMap[d.id] = d.name);

        const docsInCategory = (documents || []).filter(d => d.category_id === categoryId);
        const totalDocs = docsInCategory.length;

        // Group by domain_id
        const grouped = {};
        docsInCategory.forEach(doc => {
            const did = doc.domain_id || 0; // 0 for no-domain
            if (!grouped[did]) grouped[did] = [];
            grouped[did].push(doc);
        });

        // Build HTML content
        let content = '';
        
        // Category header with description and collection name
        content += `<div class="mb-6 pb-4 border-b border-gray-200">`;
        content += `<div class="text-lg font-bold text-gray-900">${category.name}</div>`;
        
        // Full description
        if (category.description) {
            content += `<div class="mt-3 p-3 bg-blue-50 border border-blue-200 rounded-lg">`;
            content += `<div class="text-xs font-semibold text-blue-700 mb-1">Description</div>`;
            content += `<div class="text-sm text-blue-900">${escapeHtml(category.description)}</div>`;
            content += `</div>`;
        }
        
        // Collection name and other metadata
        content += `<div class="mt-3 grid grid-cols-2 gap-3">`;
        content += `<div><div class="text-xs font-semibold text-gray-600 mb-1">Collection Name</div><div class="text-sm font-mono bg-gray-100 p-2 rounded text-gray-800">${escapeHtml(category.collection_name)}</div></div>`;
        content += `<div><div class="text-xs font-semibold text-gray-600 mb-1">Total Documents</div><div class="text-sm font-semibold text-gray-900">${docsInCategory.length}</div></div>`;
        content += `</div>`;
        content += `</div>`;

        // Associated domains list (from grouping for clarity)
        const associatedDomainIds = Object.keys(grouped).filter(k => k !== '0').map(id => parseInt(id));
        const associatedDomains = associatedDomainIds.length
            ? associatedDomainIds.map(id => domainMap[id] || `ID ${id}`).join(', ')
            : '—';
        content += `<div class="mb-4"><span class="text-sm font-semibold text-gray-700">Domains:</span> <span class="text-sm text-gray-800">${associatedDomains}</span></div>`;

        // Documents grouped by domain
        content += '<div class="space-y-4 max-h-96 overflow-y-auto pr-1">';
        const domainIdsOrdered = Object.keys(grouped).map(id => parseInt(id));
        domainIdsOrdered.sort((a, b) => {
            const an = a === 0 ? '' : (domainMap[a] || '');
            const bn = b === 0 ? '' : (domainMap[b] || '');
            return an.localeCompare(bn);
        });
        domainIdsOrdered.forEach(did => {
            const docs = grouped[did];
            const dn = did === 0 ? '—' : (domainMap[did] || `Domain ${did}`);
            content += `<div class="border border-gray-200 rounded-xl"><div class="px-4 py-2 bg-gray-50 text-sm font-semibold text-gray-800 flex items-center justify-between"><span>Domain: ${dn}</span><span class="text-xs text-gray-600">${docs.length} document(s)</span></div>`;
            content += '<div class="p-3">';
            content += docs.map(doc => `
                <div class="flex items-center justify-between py-1.5">
                    <div class="text-sm text-gray-800">${escapeHtml(doc.title || 'Untitled')}</div>
                    <div class="text-xs text-gray-500 font-mono">${escapeHtml(doc.file_name || '')}</div>
                </div>
            `).join('');
            content += '</div></div>';
        });
        content += '</div>';

        // Inject into modal and show
        const modal = document.getElementById('categoryOverviewModal');
        const container = document.getElementById('categoryOverviewContent');
        if (!modal || !container) return;
        container.innerHTML = content;
        modal.classList.remove('hidden');
        modal.style.setProperty('display', 'flex', 'important');
        modal.style.setProperty('visibility', 'visible', 'important');
        modal.style.setProperty('opacity', '1', 'important');
        modal.style.setProperty('z-index', '9999', 'important');
    } catch (e) {
        console.error('Failed to load category overview:', e);
        showNotification(e.message || 'Failed to load overview', 'error');
    }
}