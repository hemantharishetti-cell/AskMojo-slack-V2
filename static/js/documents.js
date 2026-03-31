// Documents management JavaScript
// Version: 2.5

// Make functions globally accessible
window.loadDocuments = loadDocuments;
window.openUploadDocumentModal = openUploadDocumentModal;
// deleteDocument is defined later and assigned directly to window

// Load documents on page load
document.addEventListener('DOMContentLoaded', function () {
    window.allLoadedDocuments = {}; // Global store for document data to avoid attribute size/escaping limits
    setupDocumentsEventListeners();

    // Domain Management Modal Logic
    const manageDomainsBtn = document.getElementById('manageDomainsBtn');
    const domainsModal = document.getElementById('domainsModal');
    const closeDomainsModal = document.getElementById('closeDomainsModal');
    const addDomainModalBtn = document.getElementById('addDomainModalBtn');
    const editDomainModal = document.getElementById('editDomainModal');
    const closeEditDomainModal = document.getElementById('closeEditDomainModal');
    const cancelEditDomainBtn = document.getElementById('cancelEditDomainBtn');
    const editDomainForm = document.getElementById('editDomainForm');
    const editDomainModalTitle = document.getElementById('editDomainModalTitle');
    const editDomainSubmitLabel = document.getElementById('editDomainSubmitLabel');
    const domainNameInput = document.getElementById('domainNameInput');
    const domainDescriptionInput = document.getElementById('domainDescriptionInput');
    const domainActiveInput = document.getElementById('domainActiveInput');
    const editDomainErrorMessage = document.getElementById('editDomainErrorMessage');
    let editingDomainId = null;

    // Open/close modal helpers
    function showModal(modal) {
        modal.classList.remove('hidden');
        modal.style.display = 'flex';
        modal.style.visibility = 'visible';
        modal.style.opacity = '1';
        modal.style.zIndex = '9999';
        modal.style.pointerEvents = 'auto';
    }
    function hideModal(modal) {
        modal.classList.add('hidden');
        modal.style.display = 'none';
        modal.style.visibility = 'hidden';
        modal.style.opacity = '0';
        modal.style.pointerEvents = 'none';
    }

    // Open domains modal
    if (manageDomainsBtn) {
        manageDomainsBtn.addEventListener('click', async () => {
            await loadDomainsTable();
            showModal(domainsModal);
        });
    }
    if (closeDomainsModal) closeDomainsModal.onclick = () => hideModal(domainsModal);

    // Add Domain button in modal
    if (addDomainModalBtn) {
        addDomainModalBtn.addEventListener('click', () => {
            editingDomainId = null;
            editDomainModalTitle.textContent = 'Add Domain';
            editDomainSubmitLabel.textContent = 'Add Domain';
            domainNameInput.value = '';
            domainDescriptionInput.value = '';
            domainActiveInput.checked = true;
            editDomainErrorMessage.classList.add('hidden');
            showModal(editDomainModal);
        });
    }
    if (closeEditDomainModal) closeEditDomainModal.onclick = () => hideModal(editDomainModal);
    if (cancelEditDomainBtn) cancelEditDomainBtn.onclick = () => hideModal(editDomainModal);

    // Load domains into modal table
    async function loadDomainsTable() {
        const tbody = document.getElementById('domainsTableBody');
        if (!tbody) return;
        tbody.innerHTML = '<tr><td colspan="4" class="text-center py-8 text-gray-500 text-sm">Loading domains...</td></tr>';
        try {
            const domains = await apiClient.getDomains(0, 100);
            if (!domains || domains.length === 0) {
                tbody.innerHTML = '<tr><td colspan="4" class="text-center py-8 text-gray-500 text-sm">No domains found.</td></tr>';
                return;
            }
            // Build a map of document counts per domain
            let docCounts = {};
            try {
                const allDocs = await apiClient.getDocuments(0, 10000);
                if (Array.isArray(allDocs)) {
                    allDocs.forEach(d => {
                        const did = d.domain_id || null;
                        docCounts[did] = (docCounts[did] || 0) + 1;
                    });
                }
            } catch (e) {
                // If fetching documents fails, fall back to zero counts silently
                docCounts = {};
            }

            tbody.innerHTML = domains.map(domain => `
                <tr>
                    <td>${escapeHtml(domain.name)}</td>
                    <td>${escapeHtml(String(docCounts[domain.id] || 0))}</td>
                    <td>
                        <span class="px-2 py-1 text-xs font-bold rounded-full ${domain.is_active ? 'bg-green-100 text-green-800' : 'bg-gray-100 text-gray-800'}">
                            ${domain.is_active ? 'Active' : 'Inactive'}
                        </span>
                    </td>
                    <td>
                        <button class="btn btn-ghost btn-xs text-blue-500 hover:bg-blue-50 edit-domain-btn" data-domain-id="${domain.id}"><i class="fas fa-edit"></i></button>
                        <button class="btn btn-ghost btn-xs text-red-500 hover:bg-red-50 delete-domain-btn" data-domain-id="${domain.id}" data-domain-name="${escapeHtml(domain.name)}"><i class="fas fa-trash"></i></button>
                    </td>
                </tr>
            `).join('');
            // Attach edit/delete listeners
            tbody.querySelectorAll('.edit-domain-btn').forEach(btn => {
                btn.addEventListener('click', async function () {
                    const domainId = this.getAttribute('data-domain-id');
                    const domain = domains.find(d => String(d.id) === String(domainId));
                    if (!domain) return;
                    editingDomainId = domain.id;
                    editDomainModalTitle.textContent = 'Edit Domain';
                    editDomainSubmitLabel.textContent = 'Save Changes';
                    domainNameInput.value = domain.name;
                    domainDescriptionInput.value = domain.description || '';
                    domainActiveInput.checked = !!domain.is_active;
                    editDomainErrorMessage.classList.add('hidden');
                    showModal(editDomainModal);
                });
            });
            tbody.querySelectorAll('.delete-domain-btn').forEach(btn => {
                btn.addEventListener('click', async function () {
                    const domainId = this.getAttribute('data-domain-id');
                    const domainName = this.getAttribute('data-domain-name');
                    if (!confirm(`Are you sure you want to delete the domain "${domainName}"?\n\nThis will delete all associated documents and cannot be undone.`)) return;
                    try {
                        await apiClient.deleteDomain(domainId);
                        // Show detailed popup message per design
                        showNotification('Domain deleted.', 'success');
                        // Show note after a short delay to ensure visibility
                        setTimeout(() => {
                            showNotification('Domain deleted. Ensure all documents are assigned to a domain to maintain answer accuracy.', 'info');
                        }, 300);
                        await loadDomainsTable();
                        await loadDomainsAndCategories(); // update dropdowns
                    } catch (e) {
                        showNotification(e.message || 'Failed to delete domain', 'error');
                    }
                });
            });
        } catch (e) {
            tbody.innerHTML = `<tr><td colspan="4" class="text-center py-8 text-red-500 text-sm">${escapeHtml(e.message || 'Failed to load domains')}</td></tr>`;
        }
    }

    // Handle add/edit domain form submit
    if (editDomainForm) {
        editDomainForm.onsubmit = async function (e) {
            e.preventDefault();
            const name = domainNameInput.value.trim();
            const description = domainDescriptionInput.value.trim();
            const is_active = domainActiveInput.checked;
            if (!name) {
                editDomainErrorMessage.textContent = 'Domain name is required.';
                editDomainErrorMessage.classList.remove('hidden');
                return;
            }
            try {
                if (editingDomainId) {
                    // Update
                    await apiClient.updateDomain(editingDomainId, { name, description, is_active });
                    showNotification('Domain updated.', 'success');
                } else {
                    // Add
                    await apiClient.createDomain({ name, description, is_active });
                    // Show required popup message after creation
                    showNotification('Domain added. Enable it in Edit Category to make it visible during document upload..', 'success');
                }
                hideModal(editDomainModal);
                await loadDomainsTable();
                await loadDomainsAndCategories(); // update dropdowns
            } catch (e) {
                editDomainErrorMessage.textContent = e.message || 'Failed to save domain.';
                editDomainErrorMessage.classList.remove('hidden');
            }
        };
    }

    // Description Modal Listeners
    const descriptionModal = document.getElementById('descriptionModal');
    const closeDescriptionModal = document.getElementById('closeDescriptionModal');
    const closeDescModalBtn = document.getElementById('closeDescModalBtn');

    if (closeDescriptionModal) closeDescriptionModal.onclick = () => hideModal(descriptionModal);
    if (closeDescModalBtn) closeDescModalBtn.onclick = () => hideModal(descriptionModal);

    window.showDescriptionModal = function (title, description) {
        const titleEl = document.getElementById('descModalDocTitle');
        const contentEl = document.getElementById('fullDescriptionContent');

        if (titleEl) titleEl.textContent = title;

        if (contentEl) {
            try {
                // Try to pretty-print if it's JSON
                if (description.trim().startsWith('{') || description.trim().startsWith('[')) {
                    const parsed = JSON.parse(description);
                    contentEl.textContent = JSON.stringify(parsed, null, 2);
                } else {
                    contentEl.textContent = description;
                }
            } catch (e) {
                contentEl.textContent = description;
            }
        }

        showModal(descriptionModal);
    };
});

function setupDocumentsEventListeners() {
    // Upload Document Button
    const uploadDocumentBtn = document.getElementById('uploadDocumentBtn');
    if (uploadDocumentBtn) {
        uploadDocumentBtn.addEventListener('click', openUploadDocumentModal);
    }

    // Close Upload Modal
    const closeUploadDocumentModalBtn = document.getElementById('closeUploadDocumentModal');
    if (closeUploadDocumentModalBtn) {
        closeUploadDocumentModalBtn.addEventListener('click', closeUploadDocumentModal);
    }

    // Cancel Upload Button
    const cancelUploadDocumentBtn = document.getElementById('cancelUploadDocument');
    if (cancelUploadDocumentBtn) {
        cancelUploadDocumentBtn.addEventListener('click', closeUploadDocumentModal);
    }

    // Upload Document Form Submit
    const uploadDocumentForm = document.getElementById('uploadDocumentForm');
    if (uploadDocumentForm) {
        uploadDocumentForm.addEventListener('submit', handleUploadDocument);
    }

    // Note: Domain logic has been removed as it is now optional and unused in pipeline.

    // Note: Domain creation from the upload modal has been disabled — users must select existing domains.
}

async function loadDocuments() {
    const tbody = document.getElementById('documentsTableBody');
    if (!tbody) {
        console.error('Documents table body not found');
        return;
    }

    const client = window.apiClient;
    if (!client) {
        tbody.innerHTML = `<tr><td colspan="8" class="text-center py-8 text-red-500 text-sm"><i class="fas fa-exclamation-circle mr-2"></i>API client not available. Please refresh.</td></tr>`;
        return;
    }

    try {
        tbody.innerHTML = `<tr><td colspan="8" class="text-center py-8 text-gray-500 text-sm"><i class="fas fa-spinner fa-spin mr-2"></i>Loading documents...</td></tr>`;

        let documents = await client.getDocuments(0, 100);

        // Store documents in global map for easy access without attribute limits
        window.allLoadedDocuments = {};
        if (Array.isArray(documents)) {
            documents.forEach(doc => {
                window.allLoadedDocuments[doc.id] = doc;
            });
        }

        // Apply global search filter
        if (window.adminSearchQuery) {
            const query = window.adminSearchQuery.toLowerCase();
            documents = documents.filter(doc =>
                (doc.title && doc.title.toLowerCase().includes(query)) ||
                (doc.description && doc.description.toLowerCase().includes(query)) ||
                (doc.file_name && doc.file_name.toLowerCase().includes(query))
            );
        }

        // Load domains for mapping
        const domains = await client.getDomains(0, 100);
        const domainMap = {};
        if (domains && Array.isArray(domains)) {
            domains.forEach(d => domainMap[d.id] = d.name);
        }

        if (!documents || documents.length === 0) {
            tbody.innerHTML = `<tr><td colspan="8" class="text-center py-8 text-gray-500 text-sm">${window.adminSearchQuery ? 'No documents match "' + window.adminSearchQuery + '"' : 'No documents found. Upload your first document to get started.'}</td></tr>`;
            return;
        }

        // Also load categories to get category names
        const categories = await client.getCategories(0, 100);
        const categoryMap = {};
        if (categories && Array.isArray(categories)) {
            categories.forEach(cat => {
                categoryMap[cat.id] = cat.name;
            });
        }

        // Format documents
        tbody.innerHTML = documents.map(doc => {
            const categoryName = doc.category_id && categoryMap[doc.category_id]
                ? categoryMap[doc.category_id]
                : (doc.category || 'No Category');
            const domainName = doc.domain_id ? (domainMap[doc.domain_id] || '—') : '—';

            const uploadedDate = doc.created_at
                ? new Date(doc.created_at).toLocaleDateString('en-US', {
                    year: 'numeric',
                    month: 'short',
                    day: 'numeric'
                })
                : 'N/A';

            const processedStatus = doc.processed
                ? '<span class="px-2 py-1 text-xs font-bold rounded-full bg-green-100 text-green-800">Processed</span>'
                : '<span class="px-2 py-1 text-xs font-bold rounded-full bg-yellow-100 text-yellow-800">Processing</span>';

            const internalBadge = doc.internal_only
                ? '<span class="px-2 py-1 text-xs font-bold rounded-full bg-red-100 text-red-800">Yes</span>'
                : '<span class="px-2 py-1 text-xs font-bold rounded-full bg-gray-100 text-gray-800">No</span>';

            return `
            <tr class="table-row hover:bg-gray-50">
                <td>
                    <div class="flex items-center gap-3">
                        <div class="w-10 h-10 rounded-lg bg-gradient-to-br from-green-100 to-emerald-100 flex items-center justify-center">
                            <i class="fas fa-file-alt text-green-600 text-base"></i>
                        </div>
                        <div>
                            <div class="text-sm font-semibold text-gray-900">${escapeHtml(doc.title || 'Untitled')}</div>
                            ${doc.description ? `<div class="text-xs text-gray-500 mt-0.5 line-clamp-1 cursor-pointer hover:text-green-600 transition-colors view-description-btn" data-document-id="${doc.id}">${escapeHtml(doc.description.substring(0, 80))}${doc.description.length > 80 ? '...' : ''}</div>` : ''}
                        </div>
                    </div>
                </td>
                <td>
                    <span class="px-2 py-1 text-xs font-semibold text-gray-700 bg-purple-50 rounded-lg">${escapeHtml(categoryName)}</span>
                </td>
                <td>
                    <span class="px-2 py-1 text-xs font-semibold text-gray-700 bg-blue-50 rounded-lg">${escapeHtml(domainName)}</span>
                </td>
                <td class="text-sm text-gray-700">
                    <span class="font-mono text-xs">${escapeHtml(doc.file_name || 'N/A')}</span>
                </td>
                <td class="text-sm text-gray-700">
                    ${doc.uploader ? escapeHtml(doc.uploader.name || doc.uploader.email || 'Unknown') : 'Unknown'}
                </td>
                <td>${processedStatus}</td>
                <td>${internalBadge}</td>
                <td class="text-xs text-gray-500">${uploadedDate}</td>
                <td>
                    <div class="flex items-center gap-2">
                        <button class="btn btn-ghost btn-xs text-blue-500 hover:bg-blue-50 regenerate-doc-btn" data-document-id="${doc.id}" title="Regenerate Description">
                            <i class="fas fa-sync-alt"></i>
                        </button>
                        <button class="btn btn-ghost btn-xs text-red-500 hover:bg-red-50 delete-document-btn" data-document-id="${doc.id}" data-document-title="${escapeHtml(doc.title || 'Untitled')}">
                            <i class="fas fa-trash"></i>
                        </button>
                    </div>
                </td>
            </tr>
        `;
        }).join('');

        // Attach event listeners via delegation or direct attachment
        // Using delegation for description clicks and delete buttons
        tbody.onclick = function (e) {
            const descBtn = e.target.closest('.view-description-btn');
            if (descBtn) {
                const docId = descBtn.getAttribute('data-document-id');
                const doc = window.allLoadedDocuments[docId];
                if (doc && window.showDescriptionModal) {
                    window.showDescriptionModal(doc.title || 'Untitled', doc.description);
                }
                return;
            }

            const deleteBtn = e.target.closest('.delete-document-btn');
            if (deleteBtn) {
                const documentId = parseInt(deleteBtn.getAttribute('data-document-id'));
                const documentTitle = deleteBtn.getAttribute('data-document-title');
                if (typeof window.deleteDocument === 'function') {
                    window.deleteDocument(documentId, documentTitle);
                }
                return;
            }

            const regenerateBtn = e.target.closest('.regenerate-doc-btn');
            if (regenerateBtn) {
                const documentId = regenerateBtn.getAttribute('data-document-id');
                triggerRegenerateDocumentDescription(documentId, regenerateBtn);
                return;
            }
        };

    } catch (error) {
        console.error('Failed to load documents:', error);
        tbody.innerHTML = `<tr><td colspan="8" class="text-center py-8 text-red-500 text-sm"><i class="fas fa-exclamation-circle mr-2"></i>Failed to load documents: ${error.message || 'Unknown error'}</td></tr>`;
        showNotification(error.message || 'Failed to load documents', 'error');
    }
}

function openUploadDocumentModal() {
    const modal = document.getElementById('uploadDocumentModal');
    if (!modal) return;

    // Reset form
    const form = document.getElementById('uploadDocumentForm');
    if (form) {
        form.reset();
    }

    // Hide error message
    const errorDiv = document.getElementById('uploadDocumentErrorMessage');
    if (errorDiv) {
        errorDiv.classList.add('hidden');
    }

    // Load domains and categories into dropdowns
    loadDomainsAndCategories();

    // Show modal
    modal.classList.remove('hidden');
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

    console.log('Upload document modal opened');
}

function closeUploadDocumentModal() {
    const modal = document.getElementById('uploadDocumentModal');
    if (!modal) return;

    modal.classList.add('hidden');
    modal.style.setProperty('display', 'none', 'important');
    modal.style.setProperty('visibility', 'hidden', 'important');
    modal.style.setProperty('opacity', '0', 'important');

    console.log('Upload document modal closed');
}

async function loadDomainsAndCategories() {
    const categorySelect = document.getElementById('documentCategory');
    if (!categorySelect) return;

    const client = window.apiClient;
    if (!client) {
        console.error('API client not available');
        return;
    }

    try {
        // Populate categories
        await loadCategoriesForDocument(null);
    } catch (error) {
        console.error('Failed to load categories:', error);
    }
}

async function loadCategoriesForDocument(domain_id = null) {
    const categorySelect = document.getElementById('documentCategory');
    if (!categorySelect) return;

    const client = window.apiClient;
    if (!client) {
        console.error('API client not available');
        return;
    }

    try {
        // Pass domain_id (can be null for all categories)
        const domain_id_param = domain_id ? parseInt(domain_id) : null;
        const categories = await client.getCategories(0, 100, domain_id_param);

        // Clear existing options except default
        categorySelect.innerHTML = '<option value="">No Category</option>';

        if (categories && Array.isArray(categories)) {
            categories
                .filter(cat => cat.is_active) // Only show active categories
                .forEach(category => {
                    const option = document.createElement('option');
                    option.value = category.id;
                    option.textContent = category.name;
                    categorySelect.appendChild(option);
                });
        }
    } catch (error) {
        console.error('Failed to load categories:', error);
    }
}

async function handleUploadDocument(event) {
    event.preventDefault();

    const client = window.apiClient;
    if (!client) {
        showNotification('API client not available. Please refresh.', 'error');
        return;
    }

    const form = event.target;
    const fileInput = document.getElementById('documentFile');
    const titleInput = document.getElementById('documentTitle');
    const categorySelect = document.getElementById('documentCategory');
    const internalOnlyCheckbox = document.getElementById('documentInternalOnly');
    const errorDiv = document.getElementById('uploadDocumentErrorMessage');

    // Validate required fields
    if (!fileInput.files || fileInput.files.length === 0) {
        showError('Please select a file to upload');
        return;
    }

    if (!titleInput.value.trim()) {
        showError('Please enter a document title');
        return;
    }

    // Hide previous errors
    if (errorDiv) {
        errorDiv.classList.add('hidden');
    }

    // Create FormData
    const formData = new FormData();
    formData.append('file', fileInput.files[0]);
    formData.append('title', titleInput.value.trim());

    if (categorySelect.value) {
        formData.append('category_id', categorySelect.value);
    }
    // Domain logic removed

    // Description is now auto-generated from full PDF, no need to send it

    if (internalOnlyCheckbox.checked) {
        formData.append('internal_only', 'true');
    }

    try {
        // Disable submit button
        const submitBtn = form.querySelector('button[type="submit"]');
        if (submitBtn) {
            submitBtn.disabled = true;
            submitBtn.innerHTML = '<i class="fas fa-spinner fa-spin mr-2 text-white"></i><span class="text-white">Uploading & Generating Description...</span>';
            submitBtn.style.opacity = '0.9';
        }

        // Show notification about upload and description generation
        showNotification('Uploading document and generating description from full PDF content... This may take a moment.', 'info');

        const result = await client.uploadDocument(formData);

        showNotification('Document uploaded successfully! Description generated from full PDF. Vector processing will start shortly.', 'success');
        closeUploadDocumentModal();

        // Reload documents list
        await loadDocuments();

    } catch (error) {
        console.error('Failed to upload document:', error);
        showError(error.message || 'Failed to upload document');
    } finally {
        // Re-enable submit button
        const submitBtn = form.querySelector('button[type="submit"]');
        if (submitBtn) {
            submitBtn.disabled = false;
            submitBtn.innerHTML = '<i class="fas fa-upload mr-2"></i>Upload Document';
            submitBtn.style.opacity = '1';
        }
    }
}

function showError(message) {
    const errorDiv = document.getElementById('uploadDocumentErrorMessage');
    if (errorDiv) {
        const errorText = errorDiv.querySelector('span') || errorDiv;
        if (errorText.tagName === 'SPAN') {
            errorText.textContent = message;
        } else {
            errorDiv.innerHTML = `<i class="fas fa-exclamation-circle"></i><span>${message}</span>`;
        }
        errorDiv.classList.remove('hidden');
    } else {
        showNotification(message, 'error');
    }
}

// Helper function to escape HTML
function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// Delete document function
window.deleteDocument = async function (documentId, documentTitle) {
    const client = window.apiClient;
    if (!client) {
        showNotification('API client not available. Please refresh.', 'error');
        return;
    }

    if (!confirm(`Are you sure you want to delete the document "${documentTitle}"?\n\nThis will permanently delete:\n- The document from the database\n- All associated chunks from ChromaDB\n- The physical file from disk\n\nThis action cannot be undone.`)) {
        return;
    }

    try {
        console.log('Deleting document:', documentId);
        await client.deleteDocument(documentId);
        showNotification('Document deleted successfully!', 'success');
        await loadDocuments();
    } catch (error) {
        console.error('Failed to delete document:', error);
        showNotification(error.message || 'Failed to delete document', 'error');
    }
}
async function triggerRegenerateDocumentDescription(documentId, btnElement) {
    const icon = btnElement.querySelector('i');
    if (!icon) return;

    const originalClasses = icon.className;
    
    try {
        // Disable button and show loading state
        btnElement.disabled = true;
        icon.className = 'fas fa-spinner fa-spin';
        
        showNotification('Regenerating description... This may take a moment.', 'info');
        
        const result = await apiClient.regenerateDocumentDescription(documentId);
        
        if (result && result.success) {
            showNotification('Description regenerated successfully!', 'success');
            // Refresh the documents list to show the new description
            await loadDocuments();
        } else {
            throw new Error('Failed to regenerate description');
        }
    } catch (error) {
        console.error('Description regeneration failed:', error);
        showNotification(error.message || 'Failed to regenerate description', 'error');
    } finally {
        // Restore button state
        btnElement.disabled = false;
        icon.className = originalClasses;
    }
}
