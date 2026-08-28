"""API routes for knowledge base operations."""

import logging
import os
from pathlib import Path
from starlette.responses import JSONResponse, Response
from starlette.requests import Request

from ..auth import WebSessionManager
from .documents import require_auth_api, document_to_dict

logger = logging.getLogger(__name__)

_RANGE_OPERATIONS = {"gte", "gt", "lte", "lt"}


def parse_metadata_query_params(metadata_params):
    """Convert "key:operation=value" query params into Elasticsearch-style filter clauses.

    Supports the operations exposed by the web UI's metadata filter dropdown:
    term (equals), match (contains), and gte/gt/lte/lt (range). Also accepts the
    legacy "key=value" form (defaults to "term") for backward compatibility.
    """
    clauses = []
    for meta_pair in metadata_params:
        if "=" not in meta_pair:
            continue
        key_op, value = meta_pair.split("=", 1)
        if ":" in key_op:
            key, operation = key_op.split(":", 1)
        else:
            key, operation = key_op, "term"

        if operation in _RANGE_OPERATIONS:
            clauses.append({"range": {key: {operation: value}}})
        elif operation == "match":
            clauses.append({"match": {key: value}})
        else:
            clauses.append({"term": {key: value}})
    return clauses


def setup_api_routes(app, session_manager: WebSessionManager):
    """Setup API routes for knowledge base operations."""
    from ....config import get_data_dir

    # Get upload directory (needed for serving uploaded files)
    data_dir = get_data_dir()
    upload_dir = Path(data_dir) / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)

    async def api_get(request: Request):
        """JSON API endpoint for getting documents with filters."""
        # Check authentication first
        session_data, error_response = await require_auth_api(request, session_manager, json_response=True)
        if error_response:
            return error_response

        # Get query parameters for filtering
        source_id = request.query_params.get("source_id", "")
        doc_type = request.query_params.get("doc_type", "")
        search = request.query_params.get("search", "")
        limit = int(request.query_params.get("limit", "10"))
        offset = int(request.query_params.get("offset", "0"))
        include_text = request.query_params.get("include_text", "false").lower() == "true"
        
        # Parse metadata filters (key:operation=value pairs) into ES-style "must" clauses
        # so the operation (term, match, gte, lte, gt, lt) is preserved.
        metadata_clauses = parse_metadata_query_params(request.query_params.getlist("metadata"))

        # Parse Elasticsearch-style filter if provided
        filter_dict = {}
        filter_json = request.query_params.get("filter", None)
        es_filter = None
        if filter_json:
            try:
                import json
                es_filter = json.loads(filter_json)
            except json.JSONDecodeError:
                return JSONResponse(
                    {"error": "Invalid JSON in filter parameter"},
                    status_code=400
                )

        if metadata_clauses:
            must_clauses = list(metadata_clauses)
            if es_filter:
                must_clauses.append(es_filter)
            es_filter = {"bool": {"must": must_clauses}} if len(must_clauses) > 1 else must_clauses[0]

        if es_filter:
            filter_dict["filter"] = es_filter

        # Build filter_dict for get() function
        if source_id:
            filter_dict["source_id"] = source_id
        if doc_type:
            filter_dict["doc_type"] = doc_type
        if search:
            filter_dict["text_contains"] = search

        try:
            # Query documents from knowledge base
            from ....kb import get, get_count, get_options
            from ....kb.database import get_db_session

            # Get documents with filters and pagination within a session
            # This ensures all attributes are loaded before session closes
            with get_db_session() as session:
                # Get documents with filters and pagination
                documents_result = get(filter_dict=filter_dict if filter_dict else None, limit=limit, offset=offset, session=session)
                if documents_result is None:
                    documents = []
                elif isinstance(documents_result, list):
                    documents = documents_result
                else:
                    documents = [documents_result]

                # Convert documents to dictionaries while still in session
                # This ensures all attributes are accessible
                documents_data = [document_to_dict(doc, include_text=include_text) for doc in documents]

            # Get total count (outside session, doesn't need objects)
            total_count = get_count(filter_dict=filter_dict if filter_dict else None)

            # Get base filter options (outside session)
            base_options = get_options()

            # Calculate filtered counts for options based on current filters
            # For source options: if doc_type or search is selected, show counts filtered by those
            # For doc_type options: if source_id or search is selected, show counts filtered by those
            filtered_source_options = []
            for source_option in base_options["source_options"]:
                source_id_val = source_option["id"]
                # Build filter dict for this source option
                source_filter = {"source_id": source_id_val}
                if doc_type:
                    source_filter["doc_type"] = doc_type
                if search:
                    source_filter["text_contains"] = search
                filtered_count = get_count(filter_dict=source_filter)
                filtered_source_options.append({
                    "id": source_id_val,
                    "name": source_option["name"],
                    "count": filtered_count
                })

            filtered_doc_type_options = []
            for doc_type_option in base_options["doc_type_options"]:
                doc_type_val = doc_type_option["doc_type"]
                # Build filter dict for this doc_type option
                type_filter = {"doc_type": doc_type_val}
                if source_id:
                    type_filter["source_id"] = source_id
                if search:
                    type_filter["text_contains"] = search
                filtered_count = get_count(filter_dict=type_filter)
                filtered_doc_type_options.append({
                    "doc_type": doc_type_val,
                    "count": filtered_count
                })

            filtered_options = {
                "source_options": filtered_source_options,
                "doc_type_options": filtered_doc_type_options,
            }

            return JSONResponse({
                "documents": documents_data,
                "total_count": total_count,
                "limit": limit,
                "offset": offset,
                "filters": {
                    "source_id": source_id,
                    "doc_type": doc_type,
                    "search": search,
                },
                "options": filtered_options,
            })

        except Exception as e:
            logger.error(f"Error in api_get: {e}", exc_info=True)
            return JSONResponse(
                {"error": str(e)},
                status_code=500
            )
    app.add_route("/api/get", api_get)

    async def api_document(request: Request):
        """JSON API endpoint for getting a single document."""
        # Check authentication first
        session_data, error_response = await require_auth_api(request, session_manager, json_response=True)
        if error_response:
            return error_response

        doc_id = request.path_params["doc_id"]
        include_text = request.query_params.get("include_text", "true").lower() == "true"
        include_binary = request.query_params.get("include_binary", "false").lower() == "true"

        try:
            from ....kb import get

            doc = get(uid=doc_id)
            if not doc:
                return JSONResponse(
                    {"error": "Document not found"},
                    status_code=404
                )

            return JSONResponse({
                "document": document_to_dict(doc, include_text=include_text, include_binary=include_binary)
            })

        except Exception as e:
            logger.error(f"Error in api_document: {e}", exc_info=True)
            return JSONResponse(
                {"error": str(e)},
                status_code=500
            )
    app.add_route("/api/document/{doc_id}", api_document)

    async def api_statistics(request: Request):
        """JSON API endpoint for getting statistics grid."""
        # Backs the admin-only /web/statistics page, so it is gated too.
        session_data, error_response = await require_auth_api(
            request, session_manager, json_response=True, admin_only=True
        )
        if error_response:
            return error_response

        # Get query parameters for filtering
        source_id = request.query_params.get("source_id", "")
        doc_type = request.query_params.get("doc_type", "")

        try:
            from ....kb import get_statistics

            # Get statistics with filters
            stats = get_statistics(
                source_id=source_id if source_id else None,
                doc_type=doc_type if doc_type else None
            )
            
            return JSONResponse(stats)

        except Exception as e:
            logger.error(f"Error in api_statistics: {e}", exc_info=True)
            return JSONResponse(
                {"error": str(e)},
                status_code=500
            )
    app.add_route("/api/statistics", api_statistics)

    async def api_get_chunks(request: Request):
        """JSON API endpoint for getting chunks for a document."""
        # Check authentication first
        session_data, error_response = await require_auth_api(request, session_manager, json_response=True)
        if error_response:
            return error_response

        doc_id = request.path_params["doc_id"]
        strategy = request.query_params.get("strategy", None)

        try:
            from ....kb.embedding import get_chunk_strategies, get_chunks

            # Get chunk strategies for this document
            strategies = get_chunk_strategies(document_id=doc_id)
            
            # Get chunks if strategy is specified
            chunks = None
            if strategy:
                chunks = get_chunks(document_id=doc_id, chunk_strategy=strategy)
                # get_chunks without session returns dicts, so we can use them directly
                # But ensure all required fields are present
                if chunks:
                    chunks = [
                        {
                            "id": chunk.get("id", ""),
                            "chunk_index": chunk.get("chunk_index", 0),
                            "chunk_strategy": chunk.get("chunk_strategy", strategy),
                            "text": chunk.get("text", ""),
                            "char_start_index": chunk.get("char_start_index", 0),
                            "char_end_index": chunk.get("char_end_index", 0),
                            "token_length": chunk.get("token_length", 0),
                        }
                        for chunk in chunks
                    ]

            return JSONResponse({
                "strategies": strategies,
                "chunks": chunks,
            })

        except Exception as e:
            logger.error(f"Error fetching chunks for document {doc_id}: {e}", exc_info=True)
            return JSONResponse(
                {"error": str(e)},
                status_code=500
            )
    app.add_route("/api/document/{doc_id}/chunks", api_get_chunks)

    async def api_options(request: Request):
        """JSON API endpoint for getting filter options with filtered counts."""
        # Check authentication first
        session_data, error_response = await require_auth_api(request, session_manager, json_response=True)
        if error_response:
            return error_response

        # Get query parameters for filtering
        source_id = request.query_params.get("source_id", "")
        doc_type = request.query_params.get("doc_type", "")
        search = request.query_params.get("search", "")

        try:
            from ....kb import get_count, get_options

            # Get base filter options
            base_options = get_options()

            # Build current filter dict
            current_filter = {}
            if source_id:
                current_filter["source_id"] = source_id
            if doc_type:
                current_filter["doc_type"] = doc_type
            if search:
                current_filter["text_contains"] = search

            # Calculate filtered counts for source options
            filtered_source_options = []
            for source_option in base_options["source_options"]:
                source_id_val = source_option["id"]
                # Build filter dict for this source option
                source_filter = {"source_id": source_id_val}
                if doc_type:
                    source_filter["doc_type"] = doc_type
                if search:
                    source_filter["text_contains"] = search
                filtered_count = get_count(filter_dict=source_filter)
                filtered_source_options.append({
                    "id": source_id_val,
                    "name": source_option["name"],
                    "count": filtered_count
                })

            # Calculate filtered counts for doc_type options
            filtered_doc_type_options = []
            for doc_type_option in base_options["doc_type_options"]:
                doc_type_val = doc_type_option["doc_type"]
                # Build filter dict for this doc_type option
                type_filter = {"doc_type": doc_type_val}
                if source_id:
                    type_filter["source_id"] = source_id
                if search:
                    type_filter["text_contains"] = search
                filtered_count = get_count(filter_dict=type_filter)
                filtered_doc_type_options.append({
                    "doc_type": doc_type_val,
                    "count": filtered_count
                })

            return JSONResponse({
                "source_options": filtered_source_options,
                "doc_type_options": filtered_doc_type_options,
            })

        except Exception as e:
            logger.error(f"Error in api_options: {e}", exc_info=True)
            return JSONResponse(
                {"error": str(e)},
                status_code=500
            )
    app.add_route("/api/options", api_options)

    async def api_image(request: Request):
        """Serve image binary data."""
        # Check authentication first
        session_data, error_response = await require_auth_api(request, session_manager)
        if error_response:
            return error_response

        doc_id = request.path_params["doc_id"]

        try:
            from ....kb import get

            doc = get(uid=doc_id)
            if not doc:
                return Response(
                    content=b"Image not found",
                    status_code=404,
                    media_type="text/plain"
                )

            # Check if document has binary data
            if not doc.binary:
                return Response(
                    content=b"No image data available",
                    status_code=404,
                    media_type="text/plain"
                )

            # Determine content type from source_type or default to image
            content_type = doc.source_type
            if not content_type or not content_type.startswith("image/"):
                # Try to infer from common image types
                if doc.doc_type == "image":
                    content_type = "image/png"  # Default fallback
                else:
                    content_type = "application/octet-stream"

            return Response(
                content=doc.binary,
                media_type=content_type,
            )

        except Exception as e:
            logger.error(f"Error serving image {doc_id}: {e}", exc_info=True)
            return Response(
                content=f"Error: {str(e)}".encode(),
                status_code=500,
                media_type="text/plain"
            )
    app.add_route("/files/image/{doc_id}", api_image)

    async def serve_uploaded_file(request: Request):
        """Serve uploaded files from data/uploads directory."""
        # Check authentication first
        session_data, error_response = await require_auth_api(request, session_manager)
        if error_response:
            return error_response

        filename = request.path_params["filename"]
        
        # Security: prevent directory traversal
        if ".." in filename or "/" in filename or "\\" in filename:
            return Response(
                content=b"Invalid filename",
                status_code=400,
                media_type="text/plain"
            )

        try:
            file_path = upload_dir / filename
            
            if not file_path.exists():
                return Response(
                    content=b"File not found",
                    status_code=404,
                    media_type="text/plain"
                )
            
            # Determine content type from file extension
            import mimetypes
            content_type, _ = mimetypes.guess_type(str(file_path))
            if not content_type:
                content_type = "application/octet-stream"
            
            # Read and serve file
            file_content = file_path.read_bytes()
            
            return Response(
                content=file_content,
                media_type=content_type,
                headers={
                    "Content-Disposition": f'inline; filename="{filename}"',
                }
            )

        except Exception as e:
            logger.error(f"Error serving uploaded file {filename}: {e}", exc_info=True)
            return Response(
                content=f"Error: {str(e)}".encode(),
                status_code=500,
                media_type="text/plain"
            )
    app.add_route("/files/uploaded/{filename}", serve_uploaded_file)

    async def serve_local_file(request: Request):
        """Serve local files from data/sources directory."""
        # Check authentication first
        session_data, error_response = await require_auth_api(request, session_manager)
        if error_response:
            return error_response

        filename = request.path_params["filename"]

        try:
            # Get sources directory from DATA_DIR
            from ....config import get_data_dir
            data_dir = get_data_dir()
            sources_dir = Path(data_dir) / "sources"
            file_path = (sources_dir / filename).resolve()

            # Security: ensure resolved path is still inside sources_dir
            if not str(file_path).startswith(str(sources_dir.resolve())):
                return Response(
                    content=b"Invalid filename",
                    status_code=400,
                    media_type="text/plain"
                )

            if not file_path.exists():
                return Response(
                    content=b"File not found",
                    status_code=404,
                    media_type="text/plain"
                )

            # Determine content type from file extension
            import mimetypes
            content_type, _ = mimetypes.guess_type(str(file_path))
            if not content_type:
                content_type = "application/octet-stream"

            # Read and serve file
            file_content = file_path.read_bytes()

            return Response(
                content=file_content,
                media_type=content_type,
                headers={
                    "Content-Disposition": f'inline; filename="{file_path.name}"',
                }
            )

        except Exception as e:
            logger.error(f"Error serving local file {filename}: {e}", exc_info=True)
            return Response(
                content=f"Error: {str(e)}".encode(),
                status_code=500,
                media_type="text/plain"
            )
    app.add_route("/files/local/{filename}", serve_local_file)

    async def api_search(request: Request):
        """JSON API endpoint for search (hybrid, semantic, or fulltext)."""
        # Check authentication first
        session_data, error_response = await require_auth_api(request, session_manager, json_response=True)
        if error_response:
            return error_response

        # Get query parameters
        query = request.query_params.get("query", "")
        if not query:
            return JSONResponse(
                {"error": "Query parameter is required"},
                status_code=400
            )

        search_type = request.query_params.get("search_type", "hybrid")  # hybrid, semantic, or fulltext
        embedding_name = request.query_params.get("embedding_name", None)
        max_results = int(request.query_params.get("max_results", "10"))
        source_id = request.query_params.get("source_id", None)
        doc_type = request.query_params.get("doc_type", None)
        chunking_strategy = request.query_params.get("chunking_strategy", None)

        # Parse metadata filters (key:operation=value pairs) into ES-style "must" clauses
        # so the operation (term, match, gte, lte, gt, lt) is preserved.
        metadata_clauses = parse_metadata_query_params(request.query_params.getlist("metadata"))

        # Parse Elasticsearch-style filter if provided
        filter_dict = None
        filter_json = request.query_params.get("filter", None)
        if filter_json:
            try:
                import json
                filter_dict = json.loads(filter_json)
            except json.JSONDecodeError:
                return JSONResponse(
                    {"error": "Invalid JSON in filter parameter"},
                    status_code=400
                )

        if metadata_clauses:
            must_clauses = list(metadata_clauses)
            if filter_dict:
                must_clauses.append(filter_dict)
            filter_dict = {"bool": {"must": must_clauses}} if len(must_clauses) > 1 else must_clauses[0]

        try:
            # Import appropriate search function based on type
            if search_type == "semantic":
                from ....kb.search import search_semantic
                search_fn = search_semantic
            elif search_type == "fulltext":
                from ....kb.search import search_fulltext
                search_fn = search_fulltext
            else:  # hybrid (default)
                from ....kb.search import search
                search_fn = search

            # Perform search
            result = search_fn(
                query=query,
                embedding_name=embedding_name,
                max_results=max_results,
                source_id=source_id,
                doc_type=doc_type,
                chunking_strategy=chunking_strategy,
                filter=filter_dict,
            )

            # Convert results to document dictionaries
            documents_data = []
            for doc_result in result.get('results', []):
                doc = doc_result.get('document')
                if doc:
                    doc_dict = document_to_dict(doc, include_text=False)
                    # Add search-specific information
                    doc_dict['chunks'] = doc_result.get('chunks', [])

                    # Extract best similarity and rank scores
                    best_similarity = None
                    best_rank = None
                    if doc_result.get('chunks'):
                        first_chunk = doc_result['chunks'][0]
                        best_similarity = first_chunk.get('similarity')
                        best_rank = first_chunk.get('rank')

                    doc_dict['best_similarity'] = best_similarity
                    doc_dict['best_rank'] = best_rank

                    # For hybrid search, include RRF score and ranks
                    if search_type == "hybrid":
                        doc_dict['rrf_score'] = doc_result.get('rrf_score')
                        doc_dict['semantic_rank'] = doc_result.get('semantic_rank')
                        doc_dict['fulltext_rank'] = doc_result.get('fulltext_rank')

                    documents_data.append(doc_dict)

            return JSONResponse({
                "documents": documents_data,
                "total_results": result.get('metadata', {}).get('total_results', 0),
                "query": query,
                "search_type": search_type,
                "embedding_name": result.get('metadata', {}).get('embedding_name'),
                "timing": {
                    "total": result.get('metadata', {}).get('time_search_total'),
                    "embedding": result.get('metadata', {}).get('time_embedding'),
                    "semantic": result.get('metadata', {}).get('time_semantic'),
                    "fulltext": result.get('metadata', {}).get('time_fulltext'),
                    "fusion": result.get('metadata', {}).get('time_fusion'),
                }
            })

        except Exception as e:
            logger.error(f"Error in api_search: {e}", exc_info=True)
            return JSONResponse(
                {"error": str(e)},
                status_code=500
            )
    app.add_route("/api/search", api_search)

    async def api_metadata_keys(request: Request):
        """JSON API endpoint for getting all available metadata keys."""
        # Check authentication first
        session_data, error_response = await require_auth_api(request, session_manager, json_response=True)
        if error_response:
            return error_response

        try:
            from ....kb.utils import get_metadata_keys
            
            # Get all unique metadata keys
            keys = get_metadata_keys()
            
            return JSONResponse({
                "keys": keys
            })

        except Exception as e:
            logger.error(f"Error in api_metadata_keys: {e}", exc_info=True)
            return JSONResponse(
                {"error": str(e)},
                status_code=500
            )
    app.add_route("/api/metadata-keys", api_metadata_keys)

    async def api_logs_document(request: Request):
        """JSON API endpoint for getting all logs for a document."""
        # Check authentication first
        session_data, error_response = await require_auth_api(request, session_manager, json_response=True)
        if error_response:
            return error_response

        doc_id = request.path_params["doc_id"]
        
        try:
            from ....kb.logs import get_all_logs_for_document
            
            logs = get_all_logs_for_document(doc_id)
            return JSONResponse(logs)
        except Exception as e:
            logger.error(f"Error fetching logs for document {doc_id}: {e}", exc_info=True)
            return JSONResponse(
                {"error": str(e)},
                status_code=500
            )
    app.add_route("/api/logs/{doc_id}", api_logs_document)

    async def api_logs(request: Request):
        """JSON API endpoint for getting search logs with filters."""
        # Check authentication first
        session_data, error_response = await require_auth_api(request, session_manager, json_response=True)
        if error_response:
            return error_response

        try:
            from ....kb.logs import get_search_logs
            
            # Get query parameters
            limit = int(request.query_params.get("limit", "20"))
            offset = int(request.query_params.get("offset", "0"))
            query = request.query_params.get("query", None)
            embedding_name = request.query_params.get("embedding_name", None)
            date_from = request.query_params.get("date_from", None)
            date_to = request.query_params.get("date_to", None)
            min_time = request.query_params.get("min_time_search_total", None)
            min_time_search_total = float(min_time) if min_time else None
            
            # Get logs
            logs = get_search_logs(
                limit=limit,
                offset=offset,
                query=query,
                embedding_name=embedding_name,
                date_from=date_from,
                date_to=date_to,
                min_time_search_total=min_time_search_total,
            )
            
            return JSONResponse({
                "logs": logs,
                "limit": limit,
                "offset": offset,
            })

        except Exception as e:
            logger.error(f"Error in api_logs: {e}", exc_info=True)
            return JSONResponse(
                {"error": str(e)},
                status_code=500
            )
    app.add_route("/api/logs", api_logs)

    async def api_similar(request: Request):
        """JSON API endpoint for finding similar documents to a chunk or document."""
        # Check authentication first
        session_data, error_response = await require_auth_api(request, session_manager, json_response=True)
        if error_response:
            return error_response

        # Get query parameters
        chunk_id = request.query_params.get("chunk_id", None)
        document_id = request.query_params.get("document_id", None)
        
        if not chunk_id and not document_id:
            return JSONResponse(
                {"error": "Either chunk_id or document_id parameter is required"},
                status_code=400
            )
        
        if chunk_id and document_id:
            return JSONResponse(
                {"error": "Cannot provide both chunk_id and document_id"},
                status_code=400
            )
        
        embedding_name = request.query_params.get("embedding_name", None)
        max_results = int(request.query_params.get("max_results", "5"))
        source_id = request.query_params.get("source_id", None)
        doc_type = request.query_params.get("doc_type", None)
        chunking_strategy = request.query_params.get("chunking_strategy", None)
        
        # Parse metadata filters (key=value pairs)
        metadata_filters = {}
        metadata_params = request.query_params.getlist("metadata")
        for meta_pair in metadata_params:
            if "=" in meta_pair:
                key, value = meta_pair.split("=", 1)
                metadata_filters[key] = value
        
        # Parse Elasticsearch-style filter if provided
        filter_dict = None
        filter_json = request.query_params.get("filter", None)
        if filter_json:
            try:
                import json
                filter_dict = json.loads(filter_json)
            except json.JSONDecodeError:
                return JSONResponse(
                    {"error": "Invalid JSON in filter parameter"},
                    status_code=400
                )

        try:
            # Import get_similar function
            from ....kb.search import get_similar

            # Perform similarity search
            result = get_similar(
                chunk_id=chunk_id,
                document_id=document_id,
                embedding_name=embedding_name,
                max_results=max_results,
                source_id=source_id,
                doc_type=doc_type,
                chunking_strategy=chunking_strategy,
                filter=filter_dict,
                **metadata_filters
            )
            
            # Convert results to document dictionaries
            documents_data = []
            for doc_result in result.get('results', []):
                doc = doc_result.get('document')
                if doc:
                    doc_dict = document_to_dict(doc, include_text=False)
                    # Add search-specific information
                    doc_dict['chunks'] = doc_result.get('chunks', [])
                    doc_dict['best_similarity'] = doc_result['chunks'][0]['similarity'] if doc_result.get('chunks') else None
                    documents_data.append(doc_dict)
            
            return JSONResponse({
                "documents": documents_data,
                "total_results": result.get('metadata', {}).get('total_results', 0),
                "embedding_name": result.get('metadata', {}).get('embedding_name'),
                "timing": {
                    "total": result.get('metadata', {}).get('time_search_total'),
                }
            })

        except Exception as e:
            logger.error(f"Error in api_similar: {e}", exc_info=True)
            return JSONResponse(
                {"error": str(e)},
                status_code=500
            )
    app.add_route("/api/similar", api_similar)

    async def api_graph_node(request: Request):
        """JSON API endpoint for getting node information."""
        # Check authentication first
        session_data, error_response = await require_auth_api(request, session_manager, json_response=True)
        if error_response:
            return error_response

        # Get query parameters
        node_id = request.query_params.get("id", None)
        node_name = request.query_params.get("name", None)
        node_type = request.query_params.get("type", None)

        if not node_id and not node_name:
            return JSONResponse(
                {"error": "Either 'id' or 'name' parameter is required"},
                status_code=400
            )

        try:
            from ....kb.graph.queries import get_node

            node_data = get_node(
                id=node_id,
                name=node_name,
                type=node_type
            )

            if not node_data:
                return JSONResponse(
                    {"error": "Node not found"},
                    status_code=404
                )

            if node_data["node"].get("created_time"):
                node_data["node"]["created_time"] = str(node_data["node"]["created_time"])

            for rel in node_data.get("outgoing_relations", []):
                if rel.get("created_time"):
                    rel["created_time"] = str(rel["created_time"])

            for rel in node_data.get("incoming_relations", []):
                if rel.get("created_time"):
                    rel["created_time"] = str(rel["created_time"])

            return JSONResponse(node_data)

        except Exception as e:
            logger.error(f"Error in api_graph_node: {e}", exc_info=True)
            return JSONResponse(
                {"error": str(e)},
                status_code=500
            )
    app.add_route("/api/graph/node", api_graph_node)

    async def api_graph_paths(request: Request):
        """JSON API endpoint for finding paths between two nodes."""
        session_data, error_response = await require_auth_api(request, session_manager, json_response=True)
        if error_response:
            return error_response

        start_node_id = request.query_params.get("start_node_id", None)
        end_node_id = request.query_params.get("end_node_id", None)

        if not start_node_id or not end_node_id:
            return JSONResponse(
                {"error": "Both 'start_node_id' and 'end_node_id' parameters are required"},
                status_code=400
            )

        max_depth = int(request.query_params.get("max_depth", "4"))
        limit = int(request.query_params.get("limit", "5"))

        try:
            from ....kb.graph.queries import find_paths

            paths = find_paths(
                start_node_id=start_node_id,
                end_node_id=end_node_id,
                max_depth=max_depth,
                limit=limit
            )

            return JSONResponse({
                "paths": paths,
                "start_node_id": start_node_id,
                "end_node_id": end_node_id,
                "max_depth": max_depth,
                "total_paths": len(paths)
            })

        except ValueError as e:
            return JSONResponse(
                {"error": str(e)},
                status_code=400
            )
        except Exception as e:
            logger.error(f"Error in api_graph_paths: {e}", exc_info=True)
            return JSONResponse(
                {"error": str(e)},
                status_code=500
            )

    app.add_route("/api/graph/paths", api_graph_paths)
