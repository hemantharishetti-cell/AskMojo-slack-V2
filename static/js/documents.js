// Documents management JavaScript
// Version: 2.5

// Make functions globally accessible
window.loadDocuments = loadDocuments;
window.openUploadDocumentModal = openUploadDocumentModal;
// deleteDocument is defined later and assigned directly to window

// Load documents on page load
document.addEventListener('DOMContentLoaded', function() {
    setupDocumentsEventListeners();
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

        const documents = await client.getDocuments(0, 100);
        
        if (!documents || documents.length === 0) {
            tbody.innerHTML = `<tr><td colspan="8" class="text-center py-8 text-gray-500 text-sm">No documents found. Upload your first document to get started.</td></tr>`;
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
                            ${doc.description ? `<div class="text-xs text-gray-500 mt-0.5 line-clamp-1">${escapeHtml(doc.description.substring(0, 60))}${doc.description.length > 60 ? '...' : ''}</div>` : ''}
                        </div>
                    </div>
                </td>
                <td>
                    <span class="px-2 py-1 text-xs font-semibold text-gray-700 bg-purple-50 rounded-lg">${escapeHtml(categoryName)}</span>
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
                        <button class="btn btn-ghost btn-xs text-red-500 hover:bg-red-50 delete-document-btn" data-document-id="${doc.id}" data-document-title="${escapeHtml(doc.title || 'Untitled')}">
                            <i class="fas fa-trash"></i>
                        </button>
                    </div>
                </td>
            </tr>
        `;
        }).join('');

        // Attach event listeners to delete buttons
        tbody.querySelectorAll('.delete-document-btn').forEach(btn => {
            btn.addEventListener('click', function() {
                const documentId = parseInt(this.getAttribute('data-document-id'));
                const documentTitle = this.getAttribute('data-document-title');
                if (typeof window.deleteDocument === 'function') {
                    window.deleteDocument(documentId, documentTitle);
                } else {
                    console.error('deleteDocument function not found');
                    alert('Delete function not available. Please refresh the page.');
                }
            });
        });

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

    // Load categories into dropdown
    loadCategoriesForDocument();

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

async function loadCategoriesForDocument() {
    const categorySelect = document.getElementById('documentCategory');
    if (!categorySelect) return;

    const client = window.apiClient;
    if (!client) {
        console.error('API client not available');
        return;
    }

    try {
        const categories = await client.getCategories(0, 100);
        
        // Clear existing options except "No Category"
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
window.deleteDocument = async function(documentId, documentTitle) {
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

