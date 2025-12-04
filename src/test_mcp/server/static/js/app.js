// JavaScript for MCP Server web interface - Dynamic document loading with infinite scroll

let currentOffset = 0;
let currentLimit = 10;
let isLoading = false;
let hasMore = true;
let currentFilters = {
    source_id: '',
    doc_type: '',
    search: '',
    metadata: [],
    date_type: 'insert_time',  // insert_time, creating_time, or update_time
    date_from: '',
    date_to: ''
};

// Cache for metadata keys
let metadataKeysCache = null;

// Initialize on page load
document.addEventListener('DOMContentLoaded', async function() {
    // Check if we're on the logs page
    if (window.location.pathname === '/web/logs') {
        initLogsPage();
    } else {
        initFilters();
        await loadMetadataKeys(); // Load available metadata keys
        updateFilterCounts(); // Load initial filter counts
        
        // Check for uploaded_docs query parameter and highlight those documents
        const urlParams = new URLSearchParams(window.location.search);
        const uploadedDocs = urlParams.get('uploaded_docs');
        if (uploadedDocs) {
            // Store uploaded doc IDs to highlight them after loading
            window.uploadedDocIds = uploadedDocs.split(',').filter(id => id);
        }
        
        loadDocuments(true); // Initial load
        
        // Set up infinite scroll
        window.addEventListener('scroll', handleScroll);
    }
});

// Load available metadata keys from API
async function loadMetadataKeys(retries = 3) {
    if (metadataKeysCache) {
        updateMetadataKeysDropdown(metadataKeysCache);
        return;
    }
    
    // Wait for the select element to exist
    // This element only exists on the main /web page, not on other pages
    let keyInput = document.getElementById('metadata-key-input');
    if (!keyInput) {
        // Retry after a short delay if element doesn't exist yet
        if (retries > 0) {
            setTimeout(() => loadMetadataKeys(retries - 1), 100);
            return;
        }
        // Silently return if element doesn't exist (we're probably on a different page)
        return;
    }
    
    try {
        const response = await fetch('/api/metadata-keys');
        if (!response.ok) {
            console.error('Failed to load metadata keys:', response.status, response.statusText);
            return;
        }
        
        const data = await response.json();
        if (!data || !Array.isArray(data.keys)) {
            console.error('Invalid response from metadata-keys API:', data);
            return;
        }
        
        metadataKeysCache = data.keys || [];
        updateMetadataKeysDropdown(metadataKeysCache);
    } catch (error) {
        console.error('Error loading metadata keys:', error);
    }
}

// Update metadata keys dropdown
function updateMetadataKeysDropdown(keys) {
    const keyInput = document.getElementById('metadata-key-input');
    if (!keyInput) {
        console.warn('metadata-key-input element not found');
        return;
    }
    
    // Store current value
    const currentValue = keyInput.value;
    
    // Clear and repopulate
    keyInput.innerHTML = '<option value="">Select key...</option>';
    
    if (!keys || keys.length === 0) {
        // Add a placeholder option if no keys available
        const option = document.createElement('option');
        option.value = '';
        option.textContent = 'No metadata keys available';
        option.disabled = true;
        keyInput.appendChild(option);
        return;
    }
    
    keys.forEach(key => {
        const option = document.createElement('option');
        option.value = key;
        option.textContent = key;
        if (key === currentValue) {
            option.selected = true;
        }
        keyInput.appendChild(option);
    });
}

// Initialize filter event listeners
function initFilters() {
    const searchInput = document.getElementById('search-input');
    const sourceFilter = document.getElementById('source-filter');
    const typeFilter = document.getElementById('type-filter');
    const applyButton = document.getElementById('apply-filters');
    const addMetadataButton = document.getElementById('add-metadata-filter');
    const metadataKeyInput = document.getElementById('metadata-key-input');
    const metadataValueInput = document.getElementById('metadata-value-input');
    const metadataFiltersRow = document.getElementById('metadata-filters-row');
    
    // Update filters when search input changes
    if (searchInput) {
        // Update filters when focus leaves the search field
        searchInput.addEventListener('blur', function() {
            applyFilters();
        });
        
        // Also update on Enter key
        searchInput.addEventListener('keypress', function(e) {
            if (e.key === 'Enter') {
                applyFilters();
            }
        });
    }
    
    // Date filter inputs
    const dateType = document.getElementById('date-type');
    const dateFrom = document.getElementById('date-from');
    const dateTo = document.getElementById('date-to');
    const metadataOperation = document.getElementById('metadata-operation');
    
    if (dateType) {
        dateType.addEventListener('change', function() {
            currentFilters.date_type = this.value;
            applyFilters();
        });
    }
    
    if (dateFrom) {
        dateFrom.addEventListener('change', function() {
            currentFilters.date_from = this.value;
            applyFilters();
        });
    }
    
    if (dateTo) {
        dateTo.addEventListener('change', function() {
            currentFilters.date_to = this.value;
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
    
    // Metadata filter management
    if (addMetadataButton && metadataKeyInput && metadataValueInput && metadataOperation) {
        addMetadataButton.addEventListener('click', function() {
            const key = metadataKeyInput.value.trim();
            const value = metadataValueInput.value.trim();
            const operation = metadataOperation.value;
            if (key && value) {
                // Check if already exists (same key and operation)
                const exists = currentFilters.metadata.some(m => m.key === key && m.operation === operation);
                if (!exists) {
                    currentFilters.metadata.push({ key, value, operation });
                    updateMetadataFiltersDisplay();
                    applyFilters();
                    // Keep the values in the dropdowns so user can easily add another filter
                } else {
                    // If filter already exists, clear only the value field
                    metadataValueInput.value = '';
                }
            }
        });
        
        // Allow Enter key to add filter
        metadataKeyInput.addEventListener('keypress', function(e) {
            if (e.key === 'Enter') {
                metadataValueInput.focus();
            }
        });
        metadataValueInput.addEventListener('keypress', function(e) {
            if (e.key === 'Enter') {
                addMetadataButton.click();
            }
        });
    }
    
    // Initialize filters from URL params
    const urlParams = new URLSearchParams(window.location.search);
    currentFilters.source_id = urlParams.get('source_id') || '';
    currentFilters.doc_type = urlParams.get('doc_type') || '';
    currentFilters.search = urlParams.get('search') || '';
    currentFilters.date_type = urlParams.get('date_type') || 'insert_time';
    currentFilters.date_from = urlParams.get('date_from') || '';
    currentFilters.date_to = urlParams.get('date_to') || '';
    
    // Parse metadata filters from URL (format: key:operation=value)
    const metadataParams = urlParams.getAll('metadata');
    currentFilters.metadata = metadataParams.map(param => {
        if (param.includes('=')) {
            const [keyOp, value] = param.split('=', 2);
            if (keyOp.includes(':')) {
                const [key, operation] = keyOp.split(':', 2);
                return { key, value, operation: operation || 'term' };
            } else {
                // Backward compatibility: no operation means 'term'
                return { key: keyOp, value, operation: 'term' };
            }
        }
        return null;
    }).filter(m => m !== null);
    
    // Get dateType element again (it was defined earlier in the function)
    const dateTypeElement = document.getElementById('date-type');
    
    if (sourceFilter && currentFilters.source_id) {
        sourceFilter.value = currentFilters.source_id;
    }
    if (typeFilter && currentFilters.doc_type) {
        typeFilter.value = currentFilters.doc_type;
    }
    if (searchInput && currentFilters.search) {
        searchInput.value = currentFilters.search;
    }
    if (dateTypeElement && currentFilters.date_type) {
        dateTypeElement.value = currentFilters.date_type;
    }
    if (dateFrom && currentFilters.date_from) {
        dateFrom.value = currentFilters.date_from;
    }
    if (dateTo && currentFilters.date_to) {
        dateTo.value = currentFilters.date_to;
    }
    
    updateMetadataFiltersDisplay();
}

// Update metadata filters display
function updateMetadataFiltersDisplay() {
    const container = document.getElementById('metadata-filters-list');
    if (!container) return;
    
    container.innerHTML = '';
    
    // Show date range filter if active
    if (currentFilters.date_from || currentFilters.date_to) {
        const dateTypeLabels = {
            'insert_time': 'insertion',
            'creating_time': 'creation',
            'update_time': 'update'
        };
        const dateTypeLabel = dateTypeLabels[currentFilters.date_type] || currentFilters.date_type;
        const dateRange = [];
        if (currentFilters.date_from) dateRange.push(`from ${currentFilters.date_from}`);
        if (currentFilters.date_to) dateRange.push(`to ${currentFilters.date_to}`);
        const badge = document.createElement('span');
        badge.className = 'metadata-filter-badge';
        badge.style.cssText = 'display: inline-flex; align-items: center; gap: 5px; padding: 3px 8px; background: #e3f2fd; border-radius: 3px; font-size: 11px;';
        badge.innerHTML = `
            <span>${dateTypeLabel} date: ${dateRange.join(' ')}</span>
            <button type="button" onclick="clearDateFilters()" style="background: none; border: none; cursor: pointer; color: #1976d2; font-weight: bold; padding: 0 3px; font-size: 14px;">×</button>
        `;
        container.appendChild(badge);
    }
    
    // Show generic metadata filters
    currentFilters.metadata.forEach((filter, index) => {
        const badge = document.createElement('span');
        badge.className = 'metadata-filter-badge';
        const opSymbol = {
            'term': '=',
            'match': '~',
            'gte': '≥',
            'lte': '≤',
            'gt': '>',
            'lt': '<'
        }[filter.operation] || '=';
        badge.style.cssText = 'display: inline-flex; align-items: center; gap: 5px; padding: 3px 8px; background: #e3f2fd; border-radius: 3px; font-size: 11px;';
        badge.innerHTML = `
            <span>${escapeHtml(filter.key)} ${opSymbol} ${escapeHtml(filter.value)}</span>
            <button type="button" onclick="removeMetadataFilter(${index})" style="background: none; border: none; cursor: pointer; color: #1976d2; font-weight: bold; padding: 0 3px; font-size: 14px;">×</button>
        `;
        container.appendChild(badge);
    });
}

// Clear date filters
window.clearDateFilters = function() {
    currentFilters.date_from = '';
    currentFilters.date_to = '';
    const dateFrom = document.getElementById('date-from');
    const dateTo = document.getElementById('date-to');
    if (dateFrom) dateFrom.value = '';
    if (dateTo) dateTo.value = '';
    updateMetadataFiltersDisplay();
    applyFilters();
};

// Remove metadata filter (exposed globally for onclick handlers)
window.removeMetadataFilter = function(index) {
    currentFilters.metadata.splice(index, 1);
    updateMetadataFiltersDisplay();
    applyFilters();
};

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
    if (currentFilters.date_type && currentFilters.date_type !== 'insert_time') {
        params.set('date_type', currentFilters.date_type);
    }
    if (currentFilters.date_from) params.set('date_from', currentFilters.date_from);
    if (currentFilters.date_to) params.set('date_to', currentFilters.date_to);
    currentFilters.metadata.forEach(filter => {
        // Store as key:operation=value for URL
        params.append('metadata', `${filter.key}:${filter.operation}=${filter.value}`);
    });
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
        // Use search API only if there's an actual search query
        // For metadata-only filters, use the regular get API (which now supports filters)
        const hasSearch = currentFilters.search && currentFilters.search.trim();
        const hasMetadataFilters = currentFilters.date_from || currentFilters.date_to || currentFilters.metadata.length > 0;
        const useSearchAPI = hasSearch; // Only use search API when there's an actual search query
        const showSimilarity = hasSearch; // Only show similarity when there's an actual search query
        const apiEndpoint = useSearchAPI ? '/api/search' : '/api/get';
        
        const params = new URLSearchParams();
        if (useSearchAPI) {
            // For search API, we need a query
            // If only metadata filters are set (no search query), use a very broad query
            // We'll use a single space to match all documents, then filter by metadata
            const searchQuery = hasSearch ? currentFilters.search : ' ';
            params.set('query', searchQuery);
            params.set('max_results', hasMetadataFilters && !hasSearch ? '100' : '20'); // Higher limit when filtering by metadata only
            if (currentFilters.source_id) params.set('source_id', currentFilters.source_id);
            if (currentFilters.doc_type) params.set('doc_type', currentFilters.doc_type);
            
            // Build Elasticsearch-style filter for complex queries
            const filterParts = [];
            
            // Date range filter (using selected date type: insert_time, creating_time, or update_time)
            if (currentFilters.date_from || currentFilters.date_to) {
                const rangeFilter = {};
                if (currentFilters.date_from) rangeFilter.gte = currentFilters.date_from;
                if (currentFilters.date_to) rangeFilter.lte = currentFilters.date_to;
                const dateField = currentFilters.date_type || 'insert_time';
                filterParts.push({"range": {[dateField]: rangeFilter}});
            }
            
            // Add generic metadata filters with their operations
            currentFilters.metadata.forEach(filter => {
                if (filter.operation === 'term') {
                    filterParts.push({"term": {[filter.key]: filter.value}});
                } else if (filter.operation === 'match') {
                    filterParts.push({"match": {[filter.key]: filter.value}});
                } else if (['gte', 'lte', 'gt', 'lt'].includes(filter.operation)) {
                    // Range query for single operator
                    const rangeFilter = {};
                    rangeFilter[filter.operation] = filter.value;
                    filterParts.push({"range": {[filter.key]: rangeFilter}});
                }
            });
            
            // Combine all filters with bool.must (AND only)
            if (filterParts.length > 0) {
                const filterJson = JSON.stringify({
                    "bool": {
                        "must": filterParts
                    }
                });
                params.set('filter', filterJson);
            }
        } else {
            // Regular document listing (no search, but may have metadata filters)
            if (currentFilters.source_id) params.set('source_id', currentFilters.source_id);
            if (currentFilters.doc_type) params.set('doc_type', currentFilters.doc_type);
            
            // Build Elasticsearch-style filter for metadata filters (same as search API)
            const filterParts = [];
            
            // Date range filter (using selected date type: insert_time, creating_time, or update_time)
            if (currentFilters.date_from || currentFilters.date_to) {
                const rangeFilter = {};
                if (currentFilters.date_from) rangeFilter.gte = currentFilters.date_from;
                if (currentFilters.date_to) rangeFilter.lte = currentFilters.date_to;
                const dateField = currentFilters.date_type || 'insert_time';
                filterParts.push({"range": {[dateField]: rangeFilter}});
            }
            
            // Add generic metadata filters with their operations
            currentFilters.metadata.forEach(filter => {
                if (filter.operation === 'term') {
                    filterParts.push({"term": {[filter.key]: filter.value}});
                } else if (filter.operation === 'match') {
                    filterParts.push({"match": {[filter.key]: filter.value}});
                } else if (['gte', 'lte', 'gt', 'lt'].includes(filter.operation)) {
                    // Range query for single operator
                    const rangeFilter = {};
                    rangeFilter[filter.operation] = filter.value;
                    filterParts.push({"range": {[filter.key]: rangeFilter}});
                }
            });
            
            // Combine all filters with bool.must (AND only)
            if (filterParts.length > 0) {
                const filterJson = JSON.stringify({
                    "bool": {
                        "must": filterParts
                    }
                });
                params.set('filter', filterJson);
            }
            
            params.set('limit', currentLimit);
            params.set('offset', currentOffset);
        }
        
        const response = await fetch(apiEndpoint + '?' + params.toString());
        if (!response.ok) {
            throw new Error('Failed to load documents');
        }
        
        const data = await response.json();
        
        // Update total count
        const totalCountEl = document.getElementById('total-count');
        if (totalCountEl) {
            if (useSearchAPI) {
                totalCountEl.textContent = data.total_results || 0;
            } else {
                totalCountEl.textContent = data.total_count || 0;
            }
        }
        
        // Update filter counts from API response (only for non-search)
        if (!useSearchAPI && data.options) {
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
                    documentList.appendChild(createDocumentElement(doc, useSearchAPI, showSimilarity));
                });
                
                // Check if there are more documents
                if (useSearchAPI) {
                    // For search, we don't support pagination yet
                    hasMore = false;
                } else {
                    hasMore = (currentOffset + data.documents.length) < data.total_count;
                    currentOffset += data.documents.length;
                }
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
function createDocumentElement(doc, isSearchResult = false, showSimilarity = true) {
    const div = document.createElement('div');
    div.className = 'document-item';
    
    // Highlight newly uploaded documents
    if (window.uploadedDocIds && window.uploadedDocIds.includes(doc.id)) {
        div.style.border = '2px solid #4CAF50';
        div.style.backgroundColor = '#f0f8f0';
    }
    
    div.setAttribute('data-source-id', doc.source_id);
    div.setAttribute('data-doc-type', doc.doc_type);
    div.setAttribute('data-doc-id', doc.id);
    
    // Make entire box clickable
    div.addEventListener('click', function(e) {
        // Don't navigate if clicking on action buttons
        if (!e.target.closest('.document-actions')) {
            window.location.href = `/web/document/${doc.id}`;
        }
    });
    
    const insertTime = doc.insert_time ? new Date(doc.insert_time).toLocaleString() : 'N/A';
    const textPreview = doc.text_preview ? `<div class="document-preview">${escapeHtml(doc.text_preview)}...</div>` : '';
    
    // Add search result info if this is from a search AND we should show similarity
    // Only show similarity scores when there's an actual search query, not just metadata filters
    let searchInfo = '';
    if (isSearchResult && showSimilarity) {
        if (doc.best_similarity !== null && doc.best_similarity !== undefined) {
            searchInfo = `<div class="search-info" style="background: #e8f5e9; padding: 8px; border-radius: 4px; margin: 10px 0; font-size: 14px;">
                <strong>Similarity:</strong> ${(doc.best_similarity * 100).toFixed(2)}% | 
                <strong>Matching Chunks:</strong> ${doc.chunks ? doc.chunks.length : 0}
            </div>`;
        }
    }
    
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
        (() => {
            if (!doc.uri) return '';
            // Handle local URIs the same way as the document detail page
            let uriHref = doc.uri;
            if (doc.uri.startsWith('local://uploads/')) {
                const filename = doc.uri.replace('local://uploads/', '');
                uriHref = `/files/uploaded/${encodeURIComponent(filename)}`;
            } else if (doc.uri.startsWith('local://local/')) {
                const filename = doc.uri.replace('local://local/', '');
                uriHref = `/files/local/${encodeURIComponent(filename)}`;
            }
            return `<a href="${escapeHtml(uriHref)}" target="_blank" rel="noopener noreferrer" class="btn">Open URI</a>`;
        })() : '';
    
    // Build display title: include metadata title if available
    let displayTitle = doc.doc_id || doc.id;
    if (doc.meta && doc.meta.title && doc.meta.title.trim()) {
        displayTitle = `${doc.meta.title} (${doc.doc_id || doc.id})`;
    }
    
    div.innerHTML = `
        <div class="document-header">
            <div>
                <h3 style="margin: 0;">
                    <a href="/web/document/${doc.id}">${escapeHtml(displayTitle)}</a>
                </h3>
                <div class="document-meta">
                    <strong>Source:</strong> ${escapeHtml(doc.source_id)} | 
                    <strong>Type:</strong> ${escapeHtml(doc.doc_type)} | 
                    <strong>Inserted:</strong> ${insertTime}
                </div>
            </div>
        </div>
        ${searchInfo}
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

// Reusable form loading indicator
// Sets up a loading indicator for form submissions
// Args:
//   formId: ID of the form element
//   submitButtonId: ID of the submit button
//   statusContainerId: ID of the container to show loading message
//   loadingMessage: Optional custom loading message (default: "Processing, please wait...")
function setupFormLoadingIndicator(formId, submitButtonId, statusContainerId, loadingMessage) {
    const form = document.getElementById(formId);
    const submitBtn = document.getElementById(submitButtonId);
    const statusContainer = document.getElementById(statusContainerId);
    
    if (!form || !submitBtn || !statusContainer) {
        console.warn('Form loading indicator: Missing required elements', {formId, submitButtonId, statusContainerId});
        return;
    }
    
    form.addEventListener('submit', function(e) {
        // Disable submit button
        submitBtn.disabled = true;
        submitBtn.style.opacity = '0.6';
        submitBtn.style.cursor = 'not-allowed';
        const originalText = submitBtn.textContent;
        submitBtn.textContent = 'Processing...';
        
        // Show loading indicator
        const message = loadingMessage || 'Processing, please wait...';
        statusContainer.innerHTML = `
            <div class="info-box" style="display: flex; align-items: center; gap: 10px; margin-top: 15px;">
                <div style="border: 3px solid #f3f3f3; border-top: 3px solid #4CAF50; border-radius: 50%; width: 20px; height: 20px; animation: spin 1s linear infinite; flex-shrink: 0;"></div>
                <div>${escapeHtml(message)}</div>
            </div>
            <style>
            @keyframes spin {
                0% { transform: rotate(0deg); }
                100% { transform: rotate(360deg); }
            }
            </style>
        `;
        
        // Store original button text for potential reset (though redirect should happen)
        submitBtn.dataset.originalText = originalText;
    });
}

// Chunk highlighting functionality for document detail page
function initChunkHighlighting(docId) {
    const strategyRadios = document.querySelectorAll('input[name="chunk-strategy"]');
    const chunkInfo = document.getElementById('chunk-info');
    const chunkDetailsPanel = document.getElementById('chunk-details-panel');
    const chunkDetailsContent = document.getElementById('chunk-details-content');
    const textElement = document.getElementById('document-text');
    let originalText = null;
    let currentChunks = null;
    let currentStrategy = null;
    
    if (!textElement) {
        console.log('Text element not found');
        return;
    }
    
    if (!strategyRadios || strategyRadios.length === 0) {
        console.log('Strategy radios not found - chunking may not be available');
        return;
    }
    
    // Store original text
    originalText = textElement.textContent;
    console.log('Chunk highlighting initialized for document', docId);
    
    // Initially hide chunk details panel - will be shown when chunks are loaded
    if (chunkDetailsPanel) {
        chunkDetailsPanel.style.display = 'none';
    }
    
    // Load chunks when strategy is selected
    strategyRadios.forEach(radio => {
        radio.addEventListener('change', async function() {
            if (!this.checked) return;
            const strategy = this.value;
            currentStrategy = strategy;
            
            // Update visual state of radio buttons
            strategyRadios.forEach(r => {
                const label = r.closest('label');
                if (label) {
                    label.style.background = r.checked ? '#f0f8ff' : '#fff';
                }
            });
            
            await loadChunksForStrategy(strategy);
        });
    });
    
    // Load chunks for selected strategy
    async function loadChunksForStrategy(strategy) {
        if (!strategy) {
            clearChunkHighlights();
            if (chunkInfo) chunkInfo.textContent = '';
            // Hide chunk details panel when no strategy selected
            if (chunkDetailsPanel) {
                chunkDetailsPanel.style.display = 'none';
            }
            return;
        }
        
        try {
            console.log('Loading chunks for strategy:', strategy);
            const response = await fetch(`/api/document/${docId}/chunks?strategy=${encodeURIComponent(strategy)}`);
            if (!response.ok) {
                const errorText = await response.text();
                throw new Error(`Failed to load chunks: ${response.status} ${errorText}`);
            }
            
            const data = await response.json();
            console.log('Chunks response:', data);
            currentChunks = data.chunks || [];
            
            if (chunkInfo) {
                chunkInfo.textContent = `Loaded ${currentChunks.length} chunk(s)`;
            }
            
            if (currentChunks.length > 0) {
                console.log('Setting up highlights for', currentChunks.length, 'chunks');
                // Show chunk details panel when chunks are available
                if (chunkDetailsPanel) {
                    chunkDetailsPanel.style.display = 'block';
                    chunkDetailsContent.innerHTML = '<p>Hover over a highlighted chunk to see details.</p>';
                }
                setupChunkHighlights();
            } else {
                console.log('No chunks found for strategy:', strategy);
                if (chunkInfo) chunkInfo.textContent = 'No chunks found for this strategy';
                // Hide chunk details panel when no chunks
                if (chunkDetailsPanel) {
                    chunkDetailsPanel.style.display = 'none';
                }
            }
        } catch (error) {
            console.error('Error loading chunks:', error);
            if (chunkInfo) chunkInfo.textContent = `Error: ${error.message}`;
            // Hide panel on error
            if (chunkDetailsPanel) {
                chunkDetailsPanel.style.display = 'none';
            }
        }
    }
    
    // Load chunks for default (first) strategy
    const defaultRadio = document.querySelector('input[name="chunk-strategy"]:checked');
    if (defaultRadio) {
        loadChunksForStrategy(defaultRadio.value);
    }
    
    function clearChunkHighlights() {
        // Restore original text
        if (originalText) {
            textElement.textContent = originalText;
        }
    }
    
    function setupChunkHighlights() {
        if (!currentChunks || currentChunks.length === 0 || !originalText) return;
        
        // Sort chunks by start index (ascending), then by end index (descending) for overlaps
        const sortedChunks = [...currentChunks].sort((a, b) => {
            const startDiff = (a.char_start_index || 0) - (b.char_start_index || 0);
            if (startDiff !== 0) return startDiff;
            return (b.char_end_index || 0) - (a.char_end_index || 0);
        });
        
        // Build a structure to handle overlaps
        // We'll create segments and track which chunks cover each segment
        const segments = [];
        const positions = new Set();
        
        // Collect all start and end positions
        sortedChunks.forEach(chunk => {
            positions.add(chunk.char_start_index || 0);
            positions.add(chunk.char_end_index || originalText.length);
        });
        
        const sortedPositions = Array.from(positions).sort((a, b) => a - b);
        
        // Create segments between positions
        for (let i = 0; i < sortedPositions.length - 1; i++) {
            const start = sortedPositions[i];
            const end = sortedPositions[i + 1];
            const coveringChunks = sortedChunks.filter(chunk => {
                const chunkStart = chunk.char_start_index || 0;
                const chunkEnd = chunk.char_end_index || originalText.length;
                return chunkStart <= start && chunkEnd >= end;
            });
            segments.push({ start, end, chunks: coveringChunks });
        }
        
        // Build HTML with proper overlap handling
        let html = '';
        for (const segment of segments) {
            const segmentText = originalText.substring(segment.start, segment.end);
            const numChunks = segment.chunks.length;
            
            if (numChunks === 0) {
                // No chunks cover this segment
                html += escapeHtml(segmentText);
            } else if (numChunks === 1) {
                // Single chunk - simple highlight
                const chunk = segment.chunks[0];
                const chunkIndex = (chunk.chunk_index !== null && chunk.chunk_index !== undefined) ? chunk.chunk_index : '';
                html += `<span class="chunk-highlight chunk-single" data-chunk-index="${chunkIndex}" data-chunk-id="${chunk.id || ''}" title="Chunk #${chunkIndex} (tokens: ${chunk.token_length || 'N/A'})">${escapeHtml(segmentText)}</span>`;
            } else {
                // Multiple chunks overlap - use nested spans with different colors
                const chunkIds = segment.chunks.map(c => {
                    const idx = c.chunk_index;
                    return (idx !== null && idx !== undefined) ? idx : '';
                }).join(',');
                const chunkTokens = segment.chunks.map(c => c.token_length || 'N/A').join(', ');
                html += `<span class="chunk-highlight chunk-overlap" data-chunk-indices="${chunkIds}" data-overlap-count="${numChunks}" title="Overlapping chunks: #${chunkIds} (tokens: ${chunkTokens})">${escapeHtml(segmentText)}</span>`;
            }
        }
        
        // Update content
        textElement.innerHTML = html;
        
        // Add CSS for overlap highlighting
        if (!document.getElementById('chunk-highlight-styles')) {
            const style = document.createElement('style');
            style.id = 'chunk-highlight-styles';
            style.textContent = `
                .chunk-highlight {
                    cursor: pointer;
                    transition: background-color 0.2s;
                }
                .chunk-single {
                    /* No default background - only show on hover */
                }
                .chunk-overlap {
                    /* No default background - only show on hover */
                }
                .chunk-highlight:hover {
                    background-color: rgba(255, 235, 59, 0.3) !important;
                }
                .chunk-overlap:hover {
                    background-color: rgba(255, 152, 0, 0.4) !important;
                    border-bottom: 2px solid rgba(255, 152, 0, 0.6) !important;
                }
            `;
            document.head.appendChild(style);
        }
        
        // Add hover event listeners
        const highlights = textElement.querySelectorAll('.chunk-highlight');
        highlights.forEach(span => {
            span.addEventListener('mouseenter', function() {
                const chunkIndex = this.getAttribute('data-chunk-index');
                const chunkIndices = this.getAttribute('data-chunk-indices');
                const overlapCount = parseInt(this.getAttribute('data-overlap-count') || '1');
                
                // Handle single or overlapping chunks
                if (chunkIndex !== null && chunkIndex !== '') {
                    // Single chunk
                    const chunkIndexNum = chunkIndex === '' ? null : parseInt(chunkIndex);
                    const chunk = currentChunks.find(c => {
                        if (chunkIndexNum !== null) {
                            return c.chunk_index === chunkIndexNum;
                        }
                        return false;
                    });
                    if (chunkDetailsContent && chunk) {
                        const charStart = chunk.char_start_index || 0;
                        const charEnd = chunk.char_end_index || 0;
                        const charLength = charEnd - charStart;
                        const tokenLength = chunk.token_length || 'N/A';
                        const strategy = chunk.chunk_strategy || currentStrategy || 'N/A';
                        const displayIndex = (chunk.chunk_index !== null && chunk.chunk_index !== undefined) ? chunk.chunk_index : chunkIndex;
                        
                        chunkDetailsContent.innerHTML = `
                            <div style="line-height: 1.8;">
                                <div><strong>Chunk Index:</strong> #${displayIndex}</div>
                                <div><strong>Tokens:</strong> ${tokenLength}</div>
                                <div><strong>Characters:</strong> ${charLength}</div>
                                <div><strong>Strategy:</strong> ${escapeHtml(strategy)}</div>
                            </div>
                        `;
                    }
                    
                    // Highlight all segments with this chunk
                    document.querySelectorAll(`.chunk-highlight[data-chunk-index="${chunkIndex}"], .chunk-highlight[data-chunk-indices*="${chunkIndex}"]`).forEach(el => {
                        el.classList.add('chunk-hover');
                        el.style.backgroundColor = '#ffeb3b';
                        el.style.padding = '2px 0';
                        el.style.borderRadius = '3px';
                    });
                } else if (chunkIndices) {
                    // Overlapping chunks - just show which chunks overlap
                    const indices = chunkIndices.split(',').map(i => {
                        const parsed = parseInt(i.trim());
                        return isNaN(parsed) ? null : parsed;
                    }).filter(i => i !== null);
                    
                    if (chunkDetailsContent && indices.length > 0) {
                        const chunkNumbers = indices.map(idx => `#${idx}`).join(', ');
                        chunkDetailsContent.innerHTML = `
                            <div style="line-height: 1.8;">
                                <div><strong>Overlapping Chunks:</strong> ${chunkNumbers}</div>
                            </div>
                        `;
                    }
                    
                    // Highlight all overlapping segments
                    document.querySelectorAll(`.chunk-highlight[data-chunk-indices*="${chunkIndices}"]`).forEach(el => {
                        el.classList.add('chunk-hover');
                        el.style.backgroundColor = '#ff9800';
                        el.style.padding = '2px 0';
                        el.style.borderRadius = '3px';
                    });
                }
            });
            
            span.addEventListener('mouseleave', function() {
                // Remove highlight from all chunks
                document.querySelectorAll('.chunk-highlight').forEach(el => {
                    el.classList.remove('chunk-hover');
                    el.style.backgroundColor = '';
                    el.style.padding = '';
                    el.style.borderRadius = '';
                });
                
                // Reset details panel
                if (chunkDetailsContent) {
                    chunkDetailsContent.innerHTML = '<p>Hover over a highlighted chunk to see details.</p>';
                }
            });
        });
    }
}

// ============================================================================
// Logs Page Functions
// ============================================================================

let logsOffset = 0;
let logsLoading = false;
let logsHasMore = true;

// Initialize logs page
function initLogsPage() {
    const applyButton = document.getElementById('apply-log-filters');
    const clearButton = document.getElementById('clear-log-filters');
    
    // Load initial filters from URL
    const urlParams = new URLSearchParams(window.location.search);
    const dateFrom = document.getElementById('log-date-from');
    const dateTo = document.getElementById('log-date-to');
    const minTime = document.getElementById('log-min-time');
    const queryInput = document.getElementById('log-query');
    
    if (dateFrom) dateFrom.value = urlParams.get('date_from') || '';
    if (dateTo) dateTo.value = urlParams.get('date_to') || '';
    if (minTime) minTime.value = urlParams.get('min_time_search_total') || '';
    if (queryInput) queryInput.value = urlParams.get('query') || '';
    
    if (applyButton) {
        applyButton.addEventListener('click', applyLogFilters);
    }
    
    if (clearButton) {
        clearButton.addEventListener('click', clearLogFilters);
    }
    
    // Load initial logs
    loadLogs(true);
    
    // Set up infinite scroll
    window.addEventListener('scroll', handleLogsScroll);
}

// Apply log filters
function applyLogFilters() {
    const dateFrom = document.getElementById('log-date-from')?.value || '';
    const dateTo = document.getElementById('log-date-to')?.value || '';
    const minTime = document.getElementById('log-min-time')?.value || '';
    const query = document.getElementById('log-query')?.value || '';
    
    const params = new URLSearchParams();
    if (dateFrom) params.set('date_from', dateFrom);
    if (dateTo) params.set('date_to', dateTo);
    if (minTime) params.set('min_time_search_total', minTime);
    if (query) params.set('query', query);
    
    window.history.pushState({}, '', '/web/logs' + (params.toString() ? '?' + params.toString() : ''));
    
    loadLogs(true);
}

// Clear log filters
function clearLogFilters() {
    const dateFrom = document.getElementById('log-date-from');
    const dateTo = document.getElementById('log-date-to');
    const minTime = document.getElementById('log-min-time');
    const queryInput = document.getElementById('log-query');
    
    if (dateFrom) dateFrom.value = '';
    if (dateTo) dateTo.value = '';
    if (minTime) minTime.value = '';
    if (queryInput) queryInput.value = '';
    
    window.history.pushState({}, '', '/web/logs');
    loadLogs(true);
}

// Load logs from API
async function loadLogs(reset = false) {
    if (logsLoading) return;
    
    logsLoading = true;
    const loadingEl = document.getElementById('logs-loading');
    if (loadingEl && !reset) {
        loadingEl.style.display = 'block';
    }
    
    try {
        const dateFrom = document.getElementById('log-date-from')?.value || '';
        const dateTo = document.getElementById('log-date-to')?.value || '';
        const minTime = document.getElementById('log-min-time')?.value || '';
        const query = document.getElementById('log-query')?.value || '';
        
        const params = new URLSearchParams();
        params.set('limit', '20');
        params.set('offset', reset ? '0' : logsOffset.toString());
        if (dateFrom) params.set('date_from', dateFrom);
        if (dateTo) params.set('date_to', dateTo);
        if (minTime) params.set('min_time_search_total', minTime);
        if (query) params.set('query', query);
        
        const response = await fetch('/api/logs?' + params.toString());
        if (!response.ok) {
            throw new Error('Failed to load logs');
        }
        
        const data = await response.json();
        const logs = data.logs || [];
        
        const logsList = document.getElementById('logs-list');
        if (logsList) {
            if (reset) {
                logsList.innerHTML = '';
                logsOffset = 0;
                logsHasMore = true;
            }
            
            if (logs.length > 0) {
                logs.forEach(log => {
                    logsList.appendChild(createLogElement(log));
                });
                
                logsOffset += logs.length;
                logsHasMore = logs.length === 20; // If we got a full page, there might be more
            } else if (reset) {
                logsList.innerHTML = '<div class="info-box">No logs found.</div>';
                logsHasMore = false;
            }
        }
    } catch (error) {
        console.error('Error loading logs:', error);
        const logsList = document.getElementById('logs-list');
        if (logsList && reset) {
            logsList.innerHTML = '<div class="error-box">Error loading logs. Please try again.</div>';
        }
    } finally {
        logsLoading = false;
        if (loadingEl) {
            loadingEl.style.display = 'none';
        }
    }
}

// Create log element
function createLogElement(log) {
    const div = document.createElement('div');
    div.className = 'document-item';
    div.style.padding = '12px';
    div.style.marginBottom = '12px';
    div.style.borderBottom = '1px solid #eee';
    
    const time = log.created_time ? new Date(log.created_time).toLocaleString() : 'N/A';
    const searchTime = log.time_search_total ? `${log.time_search_total.toFixed(3)}s` : 'N/A';
    const embedTime = log.time_embedding ? `${log.time_embedding.toFixed(3)}s` : 'N/A';
    const similarity = log.best_similarity ? `${(log.best_similarity * 100).toFixed(2)}%` : 'N/A';
    const resultsCount = log.total_results || 0;
    
    // Build parameters list
    const params = [];
    if (log.embedding_name) params.push(`Embedding: ${escapeHtml(log.embedding_name)}`);
    if (log.max_results) params.push(`Max Results: ${log.max_results}`);
    if (log.source_id) params.push(`Source: ${escapeHtml(log.source_id)}`);
    if (log.doc_type) params.push(`Type: ${escapeHtml(log.doc_type)}`);
    if (log.chunking_strategy) params.push(`Strategy: ${escapeHtml(log.chunking_strategy)}`);
    
    // Build search URL with all parameters
    const searchParams = new URLSearchParams();
    if (log.query) searchParams.set('search', log.query);
    if (log.embedding_name) searchParams.set('embedding_name', log.embedding_name);
    if (log.source_id) searchParams.set('source_id', log.source_id);
    if (log.doc_type) searchParams.set('doc_type', log.doc_type);
    if (log.chunking_strategy) searchParams.set('chunking_strategy', log.chunking_strategy);
    
    // Extract date filters from filter_params if they exist
    let dateType = 'insert_time';
    let dateFrom = '';
    let dateTo = '';
    if (log.filter_params && typeof log.filter_params === 'object') {
        // Check if filter_params contains a bool.must with range queries
        if (log.filter_params.bool && log.filter_params.bool.must) {
            log.filter_params.bool.must.forEach(condition => {
                if (condition.range) {
                    const rangeField = Object.keys(condition.range)[0];
                    if (['insert_time', 'creating_time', 'update_time'].includes(rangeField)) {
                        dateType = rangeField;
                        const range = condition.range[rangeField];
                        if (range.gte) dateFrom = range.gte;
                        if (range.lte) dateTo = range.lte;
                    }
                }
            });
        }
    }
    
    if (dateFrom) searchParams.set('date_from', dateFrom);
    if (dateTo) searchParams.set('date_to', dateTo);
    if (dateType !== 'insert_time') searchParams.set('date_type', dateType);
    
    // Add filters to URL (excluding date filters which we've already extracted)
    if (log.filter_params) {
        // Create a copy of filter_params without date range filters
        const filterCopy = JSON.parse(JSON.stringify(log.filter_params));
        if (filterCopy.bool && filterCopy.bool.must) {
            filterCopy.bool.must = filterCopy.bool.must.filter(condition => {
                if (condition.range) {
                    const rangeField = Object.keys(condition.range)[0];
                    return !['insert_time', 'creating_time', 'update_time'].includes(rangeField);
                }
                return true;
            });
            if (filterCopy.bool.must.length === 0) {
                delete filterCopy.bool;
            }
        }
        if (Object.keys(filterCopy).length > 0 && (!filterCopy.bool || Object.keys(filterCopy.bool).length > 0)) {
            searchParams.set('filter', JSON.stringify(filterCopy));
        }
    }
    if (log.metadata_filters) {
        // Add metadata filters as key=value pairs
        Object.entries(log.metadata_filters).forEach(([key, value]) => {
            searchParams.append('metadata', `${key}=${value}`);
        });
    }
    
    const searchUrl = `/web?${searchParams.toString()}`;
    
    // Build filters display
    let filtersHtml = '';
    if (log.filter_params || log.metadata_filters) {
        const filters = log.filter_params || log.metadata_filters || {};
        filtersHtml = `<div style="margin-top: 6px; padding: 4px 8px; background: #f5f5f5; border-radius: 3px; font-size: 11px; color: #555;">
            <strong>Filters:</strong> ${escapeHtml(JSON.stringify(filters))}
        </div>`;
    }
    
    // Build results list
    let resultsHtml = '';
    if (log.results && Array.isArray(log.results) && log.results.length > 0) {
        resultsHtml = '<div style="margin-top: 8px;"><strong style="font-size: 12px;">Results:</strong><ul style="margin: 4px 0 0 0; padding-left: 20px; font-size: 12px;">';
        log.results.forEach(result => {
            const docId = result.document_id || result.id || 'N/A';
            const chunkCount = result.chunk_ids ? result.chunk_ids.length : 0;
            resultsHtml += `<li><a href="/web/document/${docId}" style="color: #1976d2; text-decoration: none;">${escapeHtml(docId)}</a>${chunkCount > 0 ? ` <span style="color: #666;">(${chunkCount})</span>` : ''}</li>`;
        });
        resultsHtml += '</ul></div>';
    } else if (resultsCount > 0) {
        resultsHtml = `<div style="margin-top: 8px; font-size: 12px; color: #666;">${resultsCount} result${resultsCount !== 1 ? 's' : ''}</div>`;
    }
    
    div.innerHTML = `
        <div style="display: grid; grid-template-columns: 1fr auto; gap: 10px;">
            <div>
                <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 4px;">
                    <div style="font-size: 14px; font-weight: bold; flex: 1;">${escapeHtml(log.query || '(empty query)')}</div>
                    <a href="${searchUrl}" style="font-size: 11px; color: #1976d2; text-decoration: none; padding: 4px 8px; border: 1px solid #1976d2; border-radius: 3px; white-space: nowrap;">View Search →</a>
                </div>
                <div style="font-size: 11px; color: #666; line-height: 1.4;">
                    <div>${time}</div>
                    <div>${params.length > 0 ? params.join(' | ') : 'No parameters'}</div>
                    <div>Time: Embed ${embedTime} | Search ${searchTime} | Similarity: ${similarity} | Results: ${resultsCount}</div>
                </div>
                ${filtersHtml}
                ${resultsHtml}
            </div>
        </div>
    `;
    
    return div;
}

// Handle scroll for logs page
function handleLogsScroll() {
    if (window.location.pathname !== '/web/logs') return;
    
    if (logsLoading || !logsHasMore) return;
    
    // Check if user scrolled near bottom
    const scrollTop = window.pageYOffset || document.documentElement.scrollTop;
    const windowHeight = window.innerHeight;
    const documentHeight = document.documentElement.scrollHeight;
    
    if (scrollTop + windowHeight >= documentHeight - 200) {
        loadLogs(false);
    }
}
