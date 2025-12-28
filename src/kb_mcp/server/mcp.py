"""MCP tool definitions for the knowledge base server."""

import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def kb_search(
    query: str,
    max_results: int = 5,
    search_filter: str | None = None,
) -> str:
    """Search the knowledge base using hybrid search (combines semantic and full-text).

    Returns the most relevant documents matching the query using Reciprocal Rank Fusion
    to combine semantic (vector) and full-text search results.

    Args:
        query: Natural language search query
        max_results: Maximum number of documents to return (default: 5)
        search_filter: Optional JSON filter string. Supports Elasticsearch-style queries:

            - term: `{"term": {"field": "value"}}` - exact match
            - terms: `{"terms": {"field": ["val1", "val2"]}}` - match any
            - match: `{"match": {"field": "value"}}` - substring match
            - range: `{"range": {"field": {"gte": "min", "lte": "max"}}}` - range query
            - bool: `{"bool": {"must": [...], "should": [...]}}` - boolean logic

            Common filters:

            - By source: `{"term": {"source_id": "inspire-sld"}}`
            - By type: `{"term": {"doc_type": "pdf"}}`
            - By author: `{"match": {"author": "Smith"}}`
            - By insertion time: `{"range": {"insert_time": {"gte": "2024-01-01"}}}`

    Returns:
        JSON string with matching documents including id, source_id, doc_id, title, uri, and text.
    """
    from ..kb import search
    
    try:
        # Parse filter JSON if provided
        filter_dict = None
        if search_filter:
            try:
                filter_dict = json.loads(search_filter)
            except json.JSONDecodeError as e:
                return json.dumps({"error": f"Invalid filter JSON: {e}"}, indent=2)
        
        response = search(
            query=query,
            max_results=max_results,
            filter=filter_dict,
        )
        
        results = response.get("results", [])
        
        if not results:
            return json.dumps({"message": "No results found", "results": []}, indent=2)
        
        # Format results for MCP response
        formatted_results = []
        for result in results:
            doc = result.get("document")
            chunks = result.get("chunks", [])
            
            if doc:
                formatted_results.append({
                    "id": doc.id,
                    "source_id": doc.source_id,
                    "doc_id": doc.doc_id,
                    "title": doc.title or doc.title_gen,
                    "uri": doc.uri,
                    "text": doc.text,
                })
        
        return json.dumps({
            "count": len(formatted_results),
            "results": formatted_results,
        }, indent=2)
        
    except Exception as e:
        logger.error(f"Error in kb_search: {e}", exc_info=True)
        return json.dumps({"error": str(e)}, indent=2)


def kb_search_semantic(
    query: str,
    max_results: int = 5,
    search_filter: str | None = None,
) -> str:
    """Search the knowledge base using semantic search only.

    Returns the most relevant documents matching the query based on semantic similarity
    using vector embeddings. Good for conceptual matches.

    Args:
        query: Natural language search query
        max_results: Maximum number of documents to return (default: 5)
        search_filter: Optional JSON filter string. Supports Elasticsearch-style queries:

            - term: `{"term": {"field": "value"}}` - exact match
            - terms: `{"terms": {"field": ["val1", "val2"]}}` - match any
            - match: `{"match": {"field": "value"}}` - substring match
            - range: `{"range": {"field": {"gte": "min", "lte": "max"}}}` - range query
            - bool: `{"bool": {"must": [...], "should": [...]}}` - boolean logic

            Common filters:

            - By source: `{"term": {"source_id": "inspire-sld"}}`
            - By type: `{"term": {"doc_type": "pdf"}}`
            - By author: `{"match": {"author": "Smith"}}`
            - By insertion time: `{"range": {"insert_time": {"gte": "2024-01-01"}}}`

    Returns:
        JSON string with matching documents including id, source_id, doc_id, title, uri, and text.
    """
    from ..kb.search import search_semantic

    try:
        # Parse filter JSON if provided
        filter_dict = None
        if search_filter:
            try:
                filter_dict = json.loads(search_filter)
            except json.JSONDecodeError as e:
                return json.dumps({"error": f"Invalid filter JSON: {e}"}, indent=2)

        response = search_semantic(
            query=query,
            max_results=max_results,
            filter=filter_dict,
        )

        results = response.get("results", [])

        if not results:
            return json.dumps({"message": "No results found", "results": []}, indent=2)

        # Format results for MCP response
        formatted_results = []
        for result in results:
            doc = result.get("document")
            chunks = result.get("chunks", [])

            if doc:
                formatted_results.append({
                    "id": doc.id,
                    "source_id": doc.source_id,
                    "doc_id": doc.doc_id,
                    "title": doc.title or doc.title_gen,
                    "uri": doc.uri,
                    "text": doc.text,
                })

        return json.dumps({
            "count": len(formatted_results),
            "results": formatted_results,
        }, indent=2)

    except Exception as e:
        logger.error(f"Error in kb_search_semantic: {e}", exc_info=True)
        return json.dumps({"error": str(e)}, indent=2)


def kb_search_fulltext(
    query: str,
    max_results: int = 5,
    search_filter: str | None = None,
) -> str:
    """Search the knowledge base using full-text search only.

    Returns the most relevant documents matching the query based on PostgreSQL
    full-text search with tsvector. Good for exact keyword matches.

    Args:
        query: Natural language search query
        max_results: Maximum number of documents to return (default: 5)
        search_filter: Optional JSON filter string. Supports Elasticsearch-style queries:

            - term: `{"term": {"field": "value"}}` - exact match
            - terms: `{"terms": {"field": ["val1", "val2"]}}` - match any
            - match: `{"match": {"field": "value"}}` - substring match
            - range: `{"range": {"field": {"gte": "min", "lte": "max"}}}` - range query
            - bool: `{"bool": {"must": [...], "should": [...]}}` - boolean logic

            Common filters:

            - By source: `{"term": {"source_id": "inspire-sld"}}`
            - By type: `{"term": {"doc_type": "pdf"}}`
            - By author: `{"match": {"author": "Smith"}}`
            - By insertion time: `{"range": {"insert_time": {"gte": "2024-01-01"}}}`

    Returns:
        JSON string with matching documents including id, source_id, doc_id, title, uri, and text.
    """
    from ..kb.search import search_fulltext

    try:
        # Parse filter JSON if provided
        filter_dict = None
        if search_filter:
            try:
                filter_dict = json.loads(search_filter)
            except json.JSONDecodeError as e:
                return json.dumps({"error": f"Invalid filter JSON: {e}"}, indent=2)

        response = search_fulltext(
            query=query,
            max_results=max_results,
            filter=filter_dict,
        )

        results = response.get("results", [])

        if not results:
            return json.dumps({"message": "No results found", "results": []}, indent=2)

        # Format results for MCP response
        formatted_results = []
        for result in results:
            doc = result.get("document")
            chunks = result.get("chunks", [])

            if doc:
                formatted_results.append({
                    "id": doc.id,
                    "source_id": doc.source_id,
                    "doc_id": doc.doc_id,
                    "title": doc.title or doc.title_gen,
                    "uri": doc.uri,
                    "text": doc.text,
                })

        return json.dumps({
            "count": len(formatted_results),
            "results": formatted_results,
        }, indent=2)

    except Exception as e:
        logger.error(f"Error in kb_search_fulltext: {e}", exc_info=True)
        return json.dumps({"error": str(e)}, indent=2)


def kb_get(identifier: str) -> str:
    """Get a specific document from the knowledge base.
    
    Args:
        identifier: Document identifier. Can be:
        
            - UUID: `"550e8400-e29b-41d4-a716-446655440000"`
            - source_id_doc_id: `"inspire-sld_12345"`
            - doc_id only: `"12345"`
    
    Returns:
        JSON string with document details including id, source_id, doc_id, title, uri, doc_type, text, and meta.
    """
    from ..kb import get
    
    try:
        result = get(identifier=identifier)
        
        if result is None:
            return json.dumps({"error": "Document not found"}, indent=2)
        
        # Handle single document
        if not isinstance(result, list):
            doc = result
            return json.dumps({
                "id": doc.id,
                "source_id": doc.source_id,
                "doc_id": doc.doc_id,
                "title": doc.title or doc.title_gen,
                "uri": doc.uri,
                "doc_type": doc.doc_type,
                "text": doc.text,
                "meta": doc.meta,
            }, indent=2)
        
        # Handle list of documents
        return json.dumps({
            "count": len(result),
            "documents": [
                {
                    "id": doc.id,
                    "source_id": doc.source_id,
                    "doc_id": doc.doc_id,
                    "title": doc.title or doc.title_gen,
                    "uri": doc.uri,
                    "summary": doc.summary,
                }
                for doc in result
            ],
        }, indent=2)
        
    except Exception as e:
        logger.error(f"Error in kb_get: {e}", exc_info=True)
        return json.dumps({"error": str(e)}, indent=2)


def register_tools(mcp):
    """Register MCP tools with the FastMCP instance.

    Args:
        mcp: FastMCP instance to register tools with
    """
    mcp.tool()(kb_search)
    mcp.tool()(kb_search_semantic)
    mcp.tool()(kb_search_fulltext)
    mcp.tool()(kb_get)


def register_resources(mcp, oauth_provider, base_url):
    """Register MCP resources with the FastMCP instance.
    
    Args:
        mcp: FastMCP instance to register resources with
        oauth_provider: OAuth provider instance
        base_url: Base URL for the server
    """
    
    @mcp.resource("kb://sources")
    def list_kb_sources() -> str:
        """List all available sources in the knowledge base."""
        from ..kb import list_sources
        
        try:
            sources = list_sources()
            return json.dumps({
                "sources": [
                    {
                        "source_id": s.get("source_id"),
                        "name": s.get("name"),
                        "document_count": s.get("document_count"),
                    }
                    for s in sources
                ],
            }, indent=2)
        except Exception as e:
            return json.dumps({"error": str(e)}, indent=2)

    @mcp.resource("kb://document/{doc_id}")
    def get_kb_document(doc_id: str) -> str:
        """Get a document by its UUID."""
        from ..kb import get
        
        try:
            doc = get(uuid=doc_id)
            if doc is None:
                return json.dumps({"error": "Document not found"}, indent=2)
            
            return json.dumps({
                "source_id": doc.source_id,
                "doc_id": doc.doc_id,
                "title": doc.title or doc.title_gen,
                "summary": doc.summary,
                "text": doc.text,
            }, indent=2)
        except Exception as e:
            return json.dumps({"error": str(e)}, indent=2)


def register_prompts(mcp):
    """Register MCP prompts with the FastMCP instance.
    
    Args:
        mcp: FastMCP instance to register prompts with
    """
    
    @mcp.prompt()
    def search_kb(topic: str) -> str:
        """Search the knowledge base for information about a topic."""
        return f"""Search the knowledge base for information about: {topic}

Use the kb_search tool with the query "{topic}" to find relevant documents.
Then summarize the key findings from the search results."""

    @mcp.prompt()
    def research_question(question: str) -> str:
        """Research a question using the knowledge base."""
        return f"""Research the following question using the knowledge base:

Question: {question}

Steps:
1. Use kb_search to find relevant documents
2. Review the search results and identify the most relevant documents
3. Use kb_get to retrieve full content of key documents if needed
4. Synthesize the information to answer the question
5. Cite the source documents in your response"""
