"""API routes for knowledge base operations."""

import logging
import os
from pathlib import Path
from starlette.responses import JSONResponse, Response
from starlette.requests import Request

from .web_auth import WebSessionManager
from .web import require_auth_api, document_to_dict

logger = logging.getLogger(__name__)


def setup_api_routes(app, session_manager: WebSessionManager):
    """Setup API routes for knowledge base operations."""
    
    # Get upload directory (needed for serving uploaded files)
    data_dir = os.getenv("DATA_DIR", "data")
    upload_dir = Path(data_dir) / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)

    @app.route("/api/get")
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

        # Build filter_dict for get() function
        filter_dict = {}
        if source_id:
            filter_dict["source_id"] = source_id
        if doc_type:
            filter_dict["doc_type"] = doc_type
        if search:
            filter_dict["text_contains"] = search

        try:
            # Query documents from knowledge base
            from ..kb import get, get_count, get_options

            # Get documents with filters and pagination
            documents_result = get(filter_dict=filter_dict if filter_dict else None, limit=limit, offset=offset)
            if documents_result is None:
                documents = []
            elif isinstance(documents_result, list):
                documents = documents_result
            else:
                documents = [documents_result]

            # Get total count
            total_count = get_count(filter_dict=filter_dict if filter_dict else None)

            # Get base filter options
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

            # Convert documents to dictionaries
            documents_data = [document_to_dict(doc, include_text=include_text) for doc in documents]

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

    @app.route("/api/document/{doc_id}")
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
            from ..kb import get

            doc = get(uuid=doc_id)
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

    @app.route("/api/statistics")
    async def api_statistics(request: Request):
        """JSON API endpoint for getting statistics grid."""
        # Check authentication first
        session_data, error_response = await require_auth_api(request, session_manager, json_response=True)
        if error_response:
            return error_response

        # Get query parameters for filtering
        source_id = request.query_params.get("source_id", "")
        doc_type = request.query_params.get("doc_type", "")

        try:
            from ..kb import get_statistics
            
            if get_statistics is None:
                return JSONResponse(
                    {"error": "Statistics module not available"},
                    status_code=503
                )
            
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

    @app.route("/api/document/{doc_id}/chunks")
    async def api_get_chunks(request: Request):
        """JSON API endpoint for getting chunks for a document."""
        # Check authentication first
        session_data, error_response = await require_auth_api(request, session_manager, json_response=True)
        if error_response:
            return error_response

        doc_id = request.path_params["doc_id"]
        strategy = request.query_params.get("strategy", None)

        try:
            # Try to import embedding functions
            try:
                from ..kb.embedding import get_chunk_strategies, get_chunks
            except ImportError:
                return JSONResponse(
                    {"error": "Embedding module not available"},
                    status_code=503
                )

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

    @app.route("/api/options")
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
            from ..kb import get_count, get_options

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

    @app.route("/files/image/{doc_id}")
    async def api_image(request: Request):
        """Serve image binary data."""
        # Check authentication first
        session_data, error_response = await require_auth_api(request, session_manager)
        if error_response:
            return error_response

        doc_id = request.path_params["doc_id"]

        try:
            from ..kb import get

            doc = get(uuid=doc_id)
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

    @app.route("/files/uploaded/{filename}")
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

    @app.route("/files/local/{filename}")
    async def serve_local_file(request: Request):
        """Serve local files from data/local directory."""
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
            # Get local directory from DATA_DIR
            data_dir = os.getenv("DATA_DIR", "data")
            local_dir = Path(data_dir) / "local"
            file_path = local_dir / filename
            
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
            logger.error(f"Error serving local file {filename}: {e}", exc_info=True)
            return Response(
                content=f"Error: {str(e)}".encode(),
                status_code=500,
                media_type="text/plain"
            )

