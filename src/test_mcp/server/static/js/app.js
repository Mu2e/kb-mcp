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
