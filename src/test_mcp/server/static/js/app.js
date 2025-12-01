// JavaScript for MCP Server web interface - Dynamic document loading with infinite scroll

let currentOffset = 0;
let currentLimit = 10;
let isLoading = false;
let hasMore = true;
let currentFilters = {
    source_id: '',
    doc_type: '',
    search: ''
};

// Initialize on page load
document.addEventListener('DOMContentLoaded', function() {
    initFilters();
    updateFilterCounts(); // Load initial filter counts
    loadDocuments(true); // Initial load
    
    // Set up infinite scroll
    window.addEventListener('scroll', handleScroll);
});

// Initialize filter event listeners
function initFilters() {
    const searchInput = document.getElementById('search-input');
    const sourceFilter = document.getElementById('source-filter');
    const typeFilter = document.getElementById('type-filter');
    const applyButton = document.getElementById('apply-filters');
    
    if (searchInput) {
        // Update filters when focus leaves the search field
        searchInput.addEventListener('blur', function() {
            applyFilters();
        });
    }
    
    if (sourceFilter) {
        sourceFilter.addEventListener('change', function() {
            updateFilterCounts();
            applyFilters();
        });
    }
    
    if (typeFilter) {
        typeFilter.addEventListener('change', function() {
            updateFilterCounts();
            applyFilters();
        });
    }
    
    if (applyButton) {
        applyButton.addEventListener('click', applyFilters);
    }
    
    // Initialize filters from URL params
    const urlParams = new URLSearchParams(window.location.search);
    currentFilters.source_id = urlParams.get('source_id') || '';
    currentFilters.doc_type = urlParams.get('doc_type') || '';
    currentFilters.search = urlParams.get('search') || '';
    
    if (sourceFilter && currentFilters.source_id) {
        sourceFilter.value = currentFilters.source_id;
    }
    if (typeFilter && currentFilters.doc_type) {
        typeFilter.value = currentFilters.doc_type;
    }
    if (searchInput && currentFilters.search) {
        searchInput.value = currentFilters.search;
    }
}

// Apply filters and reload documents
function applyFilters() {
    const searchInput = document.getElementById('search-input');
    const sourceFilter = document.getElementById('source-filter');
    const typeFilter = document.getElementById('type-filter');
    
    currentFilters.search = searchInput?.value || '';
    currentFilters.source_id = sourceFilter?.value || '';
    currentFilters.doc_type = typeFilter?.value || '';
    
    // Reset pagination
    currentOffset = 0;
    hasMore = true;
    
    // Clear document list
    const documentList = document.getElementById('document-list');
    if (documentList) {
        documentList.innerHTML = '<div class="info-box">Loading documents...</div>';
    }
    
    // Update URL without reload
    const params = new URLSearchParams();
    if (currentFilters.source_id) params.set('source_id', currentFilters.source_id);
    if (currentFilters.doc_type) params.set('doc_type', currentFilters.doc_type);
    if (currentFilters.search) params.set('search', currentFilters.search);
    window.history.pushState({}, '', '/web' + (params.toString() ? '?' + params.toString() : ''));
    
    // Load documents (which will also update filter counts from the response)
    loadDocuments(true);
}

// Update filter dropdown counts dynamically from API
async function updateFilterCounts() {
    try {
        const params = new URLSearchParams();
        const searchInput = document.getElementById('search-input');
        const sourceFilter = document.getElementById('source-filter');
        const typeFilter = document.getElementById('type-filter');
        
        const search = searchInput?.value || '';
        const source_id = sourceFilter?.value || '';
        const doc_type = typeFilter?.value || '';
        
        if (source_id) params.set('source_id', source_id);
        if (doc_type) params.set('doc_type', doc_type);
        if (search) params.set('search', search);
        
        const response = await fetch('/api/options?' + params.toString());
        if (!response.ok) return;
        
        const data = await response.json();
        updateFilterCountsFromData(data);
    } catch (error) {
        console.error('Error updating filter counts:', error);
    }
}

// Update filter dropdown counts from data (no API call needed)
function updateFilterCountsFromData(options) {
    // Update source filter counts
    const sourceFilter = document.getElementById('source-filter');
    if (sourceFilter && options.source_options) {
        Array.from(sourceFilter.options).forEach(option => {
            if (option.value) {
                const sourceOption = options.source_options.find(
                    opt => opt.id === option.value
                );
                if (sourceOption) {
                    const display = option.text.split(' (')[0];
                    option.text = `${display} (${sourceOption.count})`;
                    option.setAttribute('data-count', sourceOption.count);
                }
            }
        });
    }
    
    // Update doc_type filter counts
    const typeFilter = document.getElementById('type-filter');
    if (typeFilter && options.doc_type_options) {
        Array.from(typeFilter.options).forEach(option => {
            if (option.value) {
                const typeOption = options.doc_type_options.find(
                    opt => opt.doc_type === option.value
                );
                if (typeOption) {
                    const display = option.text.split(' (')[0];
                    option.text = `${display} (${typeOption.count})`;
                    option.setAttribute('data-count', typeOption.count);
                }
            }
        });
    }
}

// Load documents from API
async function loadDocuments(reset = false) {
    if (isLoading) return;
    
    isLoading = true;
    const loadingMore = document.getElementById('loading-more');
    if (loadingMore && !reset) {
        loadingMore.style.display = 'block';
    }
    
    try {
        const params = new URLSearchParams();
        if (currentFilters.source_id) params.set('source_id', currentFilters.source_id);
        if (currentFilters.doc_type) params.set('doc_type', currentFilters.doc_type);
        if (currentFilters.search) params.set('search', currentFilters.search);
        params.set('limit', currentLimit);
        params.set('offset', currentOffset);
        
        const response = await fetch('/api/get?' + params.toString());
        if (!response.ok) {
            throw new Error('Failed to load documents');
        }
        
        const data = await response.json();
        
        // Update total count
        const totalCountEl = document.getElementById('total-count');
        if (totalCountEl) {
            totalCountEl.textContent = data.total_count || 0;
        }
        
        // Update filter counts from API response (no separate API call needed)
        if (data.options) {
            updateFilterCountsFromData(data.options);
        }
        
        // Render documents
        const documentList = document.getElementById('document-list');
        if (documentList) {
            if (reset) {
                documentList.innerHTML = '';
            }
            
            if (data.documents && data.documents.length > 0) {
                data.documents.forEach(doc => {
                    documentList.appendChild(createDocumentElement(doc));
                });
                
                // Check if there are more documents
                hasMore = (currentOffset + data.documents.length) < data.total_count;
                currentOffset += data.documents.length;
            } else if (reset) {
                documentList.innerHTML = '<div class="info-box">No documents found.</div>';
                hasMore = false;
            }
        }
        
    } catch (error) {
        console.error('Error loading documents:', error);
        const documentList = document.getElementById('document-list');
        if (documentList && reset) {
            documentList.innerHTML = '<div class="error-box">Error loading documents. Please try again.</div>';
        }
    } finally {
        isLoading = false;
        if (loadingMore) {
            loadingMore.style.display = 'none';
        }
    }
}

// Create document element from document data
function createDocumentElement(doc) {
    const div = document.createElement('div');
    div.className = 'document-item';
    div.setAttribute('data-source-id', doc.source_id);
    div.setAttribute('data-doc-type', doc.doc_type);
    
    // Make entire box clickable
    div.addEventListener('click', function(e) {
        // Don't navigate if clicking on action buttons
        if (!e.target.closest('.document-actions')) {
            window.location.href = `/web/document/${doc.id}`;
        }
    });
    
    const insertTime = doc.insert_time ? new Date(doc.insert_time).toLocaleString() : 'N/A';
    const textPreview = doc.text_preview ? `<div class="document-preview">${escapeHtml(doc.text_preview)}...</div>` : '';
    
    let metaInfo = '';
    if (doc.meta && Object.keys(doc.meta).length > 0) {
        const metaItems = [];
        let count = 0;
        for (const [key, value] of Object.entries(doc.meta)) {
            if (count >= 3) break;
            if (typeof value === 'string' && value.length < 50) {
                metaItems.push(`${key}: ${value}`);
                count++;
            }
        }
        if (metaItems.length > 0) {
            metaInfo = `<div class="document-meta">Meta: ${metaItems.join(', ')}</div>`;
        }
    }
    
    const uriButton = doc.uri ? 
        `<a href="${escapeHtml(doc.uri)}" target="_blank" rel="noopener noreferrer" class="btn">Open URI</a>` : '';
    
    div.innerHTML = `
        <div class="document-header">
            <div>
                <h3 style="margin: 0;">
                    <a href="/web/document/${doc.id}">${escapeHtml(doc.doc_id || doc.id)}</a>
                </h3>
                <div class="document-meta">
                    <strong>Source:</strong> ${escapeHtml(doc.source_id)} | 
                    <strong>Type:</strong> ${escapeHtml(doc.doc_type)} | 
                    <strong>Inserted:</strong> ${insertTime}
                </div>
            </div>
        </div>
        ${textPreview}
        ${metaInfo}
        <div class="document-actions">
            <a href="/web/document/${doc.id}" class="btn">View Full Document</a>
            ${uriButton}
        </div>
    `;
    
    return div;
}

// Handle scroll for infinite scroll
function handleScroll() {
    if (isLoading || !hasMore) return;
    
    // Check if user is near bottom of page
    const scrollTop = window.pageYOffset || document.documentElement.scrollTop;
    const windowHeight = window.innerHeight;
    const documentHeight = document.documentElement.scrollHeight;
    
    // Load more when user is 200px from bottom
    if (scrollTop + windowHeight >= documentHeight - 200) {
        loadDocuments(false);
    }
}

// Escape HTML to prevent XSS
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}
