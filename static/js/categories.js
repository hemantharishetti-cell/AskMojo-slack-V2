// Categories Management
document.addEventListener('DOMContentLoaded', () => {
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
window.loadCategories = async function() {
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

        tbody.innerHTML = categories.map(category => {
            const categoryNameEscaped = escapeHtml(category.name || '');
            const categoryDescriptionEscaped = category.description ? escapeHtml(category.description) : '<span class="text-gray-400">No description</span>';
            const collectionNameEscaped = escapeHtml(category.collection_name || '');
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
                <td class="text-sm text-gray-700">${categoryDescriptionEscaped}</td>
                <td>
                    <span class="px-2 py-1 text-xs font-mono bg-gray-100 text-gray-700 rounded-lg">${collectionNameEscaped}</span>
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
            btn.addEventListener('click', function() {
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
            btn.addEventListener('click', function() {
                const categoryId = parseInt(this.getAttribute('data-category-id'));
                if (typeof window.editCategory === 'function') {
                    window.editCategory(categoryId);
                } else {
                    console.error('editCategory function not found');
                    alert('Edit function not available. Please refresh the page.');
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

window.editCategory = async function(categoryId) {
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

window.deleteCategory = async function(categoryId, categoryName, documentCount) {
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

