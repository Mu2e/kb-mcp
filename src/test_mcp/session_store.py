"""
Shared session persistence for both web and OAuth sessions.

Supports both disk-based storage (local development) and Firestore (production).
For disk: Lazy loading on first access, keeps in memory, writes to disk on every set.
For Firestore: Direct reads/writes (no bulk loading, queries on-demand).

Note: Values should be pre-serialized (e.g., via model_dump(mode='json') for Pydantic models)
before being passed to set().
"""

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class SessionStore:
    """Generic session storage with disk and Firestore support."""

    def __init__(self, collection_name: str):
        """Initialize session store.

        Args:
            collection_name: Firestore collection name or base file name
        """
        self.collection_name = collection_name
        self.use_firestore = os.getenv("SESSION_STORE_FIRESTORE", "false").lower() == "true"

        # File storage setup - path determined from collection name
        # Use DATA_DIR env var if set (e.g., /data for Cloud Storage mount), otherwise "data/"
        data_dir = os.getenv("DATA_DIR", "data")
        self.persistence_file = Path(data_dir) / f"{collection_name}.json"
        self.persistence_file.parent.mkdir(parents=True, exist_ok=True)

        # For disk storage: in-memory cache (loaded at startup)
        # For Firestore: not used (direct Firestore queries)
        # None means not loaded yet, {} means loaded but empty
        self._data: Optional[Dict[str, Any]] = None
        
        # For cleanup throttling (only for disk storage)
        self._last_cleanup_time = 0.0
        self._cleanup_throttle_seconds = 300  # Clean up at most once every 5 minutes

        # Firestore setup
        if self.use_firestore:
            try:
                from google.cloud import firestore
                self.db = firestore.AsyncClient()
                logger.info(f"SessionStore[{collection_name}]: Using Google Cloud Firestore (direct queries)")
            except ImportError:
                logger.error(f"SESSION_STORE_FIRESTORE=true but google-cloud-firestore not installed")
                raise
        else:
            logger.info(f"SessionStore[{collection_name}]: Using disk storage at {self.persistence_file} (lazy loading on first access)")

    def _load_from_disk(self) -> Dict[str, Any]:
        """Load data from disk JSON file."""
        if self.persistence_file.exists():
            try:
                with open(self.persistence_file, 'r') as f:
                    data = json.load(f)
                    return data
            except Exception as e:
                logger.error(f"Error loading from {self.persistence_file}: {e}")
        return {}

    def _save_to_disk(self, data: Dict[str, Any]) -> None:
        """Save data to disk as JSON.
        
        Note: Data should already be serialized (e.g., via model_dump(mode='json') for Pydantic models).
        The default=str is kept as a fallback for any edge cases.
        """
        try:
            with open(self.persistence_file, 'w') as f:
                json.dump(data, f, indent=2, default=str)
            logger.debug(f"Saved data to {self.persistence_file}")
        except Exception as e:
            logger.error(f"Error saving to {self.persistence_file}: {e}")

    async def get(self, key: str, subkey: str) -> Any:
        """
        Get a value from storage.
        
        For disk: reads from in-memory cache (loaded at startup).
        For Firestore: queries Firestore directly.
        
        Args:
            key: Top-level key (e.g., 'access_tokens')
            subkey: Subkey (e.g., token value for access_tokens)
        
        Returns:
            The value, or None if not found
        """
        if self.use_firestore:
            # Direct Firestore query
            return await self._get_from_firestore(key, subkey)
        else:
            # Lazy load from disk on first access
            if self._data is None:
                self._data = self._load_from_disk()
            
            # Read from in-memory cache
            return self._data.get(key, {}).get(subkey)

    async def set(self, key: str, subkey: str, value: Any) -> None:
        """
        Set a value in storage.
        
        For disk: updates in-memory cache and saves to disk immediately.
        For Firestore: writes directly to Firestore.
        
        Args:
            key: Top-level key (e.g., 'access_tokens')
            subkey: Subkey (e.g., token value for access_tokens)
            value: Value to set
        """
        if self.use_firestore:
            # Direct Firestore write
            await self._set_to_firestore(key, subkey, value)
        else:
            # Load from disk if not already loaded
            if self._data is None:
                self._data = self._load_from_disk()
            
            # Update in-memory cache
            # Note: Values should already be serialized (e.g., via model_dump(mode='json') for Pydantic models)
            if key not in self._data:
                self._data[key] = {}
            self._data[key][subkey] = value
            
            # Save to disk immediately
            self._save_to_disk(self._data)
            
            # Periodically clean up expired sessions (throttled to avoid overhead)
            await self._cleanup_expired_sessions_throttled()

    async def delete(self, key: str, subkey: str) -> None:
        """
        Delete a value from storage.
        
        For disk: removes from in-memory cache and saves to disk immediately.
        For Firestore: deletes directly from Firestore.
        
        Args:
            key: Top-level key (e.g., 'access_tokens')
            subkey: Subkey (e.g., token value for access_tokens)
        """
        if self.use_firestore:
            # Direct Firestore delete
            await self._delete_from_firestore(key, subkey)
        else:
            # Load from disk if not already loaded
            if self._data is None:
                self._data = self._load_from_disk()
            
            # Delete from in-memory cache
            if key in self._data and subkey in self._data[key]:
                del self._data[key][subkey]
            
            # Save to disk immediately
            self._save_to_disk(self._data)

    # Firestore direct access methods
    async def _get_from_firestore(self, key: str, subkey: str) -> Any:
        """Get value directly from Firestore.
        
        Uses individual documents for better performance:
        Document ID = {key}:{subkey} in collection {collection_name}
        """
        try:
            # Composite document ID: {key}:{subkey}
            doc_id = f"{key}:{subkey}"
            doc_ref = self.db.collection(self.collection_name).document(doc_id)
            doc = await doc_ref.get()
            if doc.exists:
                data = doc.to_dict()
                if data:
                    # If value was wrapped (non-dict stored), unwrap it
                    if len(data) == 1 and 'value' in data:
                        return data['value']
                return data
        except Exception as e:
            logger.error(f"Error getting from Firestore collection {self.collection_name}: {e}")
        return None

    async def _set_to_firestore(self, key: str, subkey: str, value: Any) -> None:
        """Set value directly in Firestore.
        
        Uses individual documents for better performance:
        Document ID = {key}:{subkey} in collection {collection_name}
        """
        try:
            # Composite document ID: {key}:{subkey}
            doc_id = f"{key}:{subkey}"
            doc_ref = self.db.collection(self.collection_name).document(doc_id)
            # Note: Value should already be serialized (e.g., via model_dump(mode='json') for Pydantic models)
            if isinstance(value, dict):
                doc_data = value
            else:
                doc_data = {'value': value}
            await doc_ref.set(doc_data)
            
            logger.debug(f"Set {key}.{subkey} in Firestore collection {self.collection_name}")
        except Exception as e:
            logger.error(f"Error setting in Firestore collection {self.collection_name}: {e}")

    async def _delete_from_firestore(self, key: str, subkey: str) -> None:
        """Delete value directly from Firestore.
        
        Uses individual documents for better performance:
        Deletes document with ID = {key}:{subkey} in collection {collection_name}
        """
        try:
            # Composite document ID: {key}:{subkey}
            doc_id = f"{key}:{subkey}"
            doc_ref = self.db.collection(self.collection_name).document(doc_id)
            await doc_ref.delete()
            
            logger.debug(f"Deleted {key}.{subkey} from Firestore collection {self.collection_name}")
        except Exception as e:
            logger.error(f"Error deleting from Firestore collection {self.collection_name}: {e}")

    async def count_items(self, key: str) -> int:
        """
        Count items in a top-level key (for cases like counting sessions).
        
        For disk: reads from in-memory cache.
        For Firestore: queries all documents under {collection_name}/{key}/ and counts them.
        
        Args:
            key: Top-level key (e.g., 'access_tokens')
        
        Returns:
            Number of items in that key (0 if key doesn't exist)
        """
        if self.use_firestore:
            # Query all documents with document ID starting with "{key}:"
            try:
                from google.cloud.firestore import FieldPath
                collection_ref = self.db.collection(self.collection_name)
                # Query documents where document ID starts with "{key}:"
                # Use range query: >= "{key}:" and < "{key}:~" (tilde is last ASCII char)
                query = collection_ref.where(
                    FieldPath.document_id(), '>=', f"{key}:"
                ).where(
                    FieldPath.document_id(), '<', f"{key}:~"
                )
                docs = query.stream()
                count = sum(1 for _ in docs)
                return count
            except Exception as e:
                logger.error(f"Error counting items in Firestore collection {self.collection_name} for key {key}: {e}")
                return 0
        else:
            # Disk storage: read from in-memory cache
            if self._data is None:
                self._data = self._load_from_disk()
            
            value = self._data.get(key, {})
            if isinstance(value, dict):
                return len(value)
            return 0

    async def cleanup_expired_sessions(self) -> None:
        """
        Clean up expired sessions from storage.
        
        For disk: removes expired items from in-memory cache and saves to disk.
        For Firestore: no-op (TTL policies handle expiration automatically).
        
        Checks these keys for expiration:
        - auth_codes: expires_at field
        - access_tokens: expires_at field
        - sessions: expires_at field (for web sessions)
        
        Also cleans up related entries:
        - github_tokens linked to expired auth_codes/access_tokens
        - token_users linked to expired access_tokens
        """
        # Firestore handles expiration via TTL policies
        if self.use_firestore or self._data is None:
            return
        
        current_time = time.time()
        cleaned_count = 0
        
        # Define keys to clean up and their related keys to also delete
        cleanup_config = [
            ('auth_codes', ['github_tokens']),  # auth_code → also delete github_tokens[code_id]
            ('access_tokens', ['github_tokens', 'token_users']),  # access_token → also delete both
            ('sessions', []),  # sessions → no related cleanup
        ]
        
        for key, related_keys in cleanup_config:
            items = self._data.get(key, {})
            if not isinstance(items, dict):
                continue
            
            # Find expired items (only items with expires_at field)
            expired_ids = [
                item_id for item_id, item_dict in items.items()
                if isinstance(item_dict, dict) 
                and 'expires_at' in item_dict
                and item_dict.get('expires_at')  # Check it's not None
                and item_dict['expires_at'] < current_time
            ]
            
            # Delete expired items and related entries
            for item_id in expired_ids:
                del items[item_id]
                for related_key in related_keys:
                    related_items = self._data.get(related_key, {})
                    if isinstance(related_items, dict) and item_id in related_items:
                        del related_items[item_id]
                cleaned_count += 1
        
        # Clean up token_users entries for revoked API keys (those starting with sk_)
        # Only for oauth_sessions collection
        if self.collection_name == "oauth_sessions":
            token_users = self._data.get('token_users', {})
            if isinstance(token_users, dict):
                # Load current API keys to check which ones are still valid
                try:
                    import json
                    data_dir = os.getenv("DATA_DIR", "data")
                    api_keys_file = Path(data_dir) / "api_keys.json"
                    if api_keys_file.exists():
                        with open(api_keys_file) as f:
                            valid_api_keys = set(json.load(f).keys())
                        
                        # Find API keys in token_users that are no longer valid
                        revoked_api_keys = [
                            token for token in token_users.keys()
                            if token.startswith("sk_") and token not in valid_api_keys
                        ]
                        
                        # Remove revoked API keys from token_users
                        for revoked_key in revoked_api_keys:
                            del token_users[revoked_key]
                            cleaned_count += 1
                except Exception as e:
                    logger.debug(f"Error checking API keys during cleanup: {e}")
        
        # Save if we cleaned up anything
        if cleaned_count > 0:
            self._save_to_disk(self._data)
            logger.info(f"Cleaned up {cleaned_count} expired session(s) from {self.collection_name}")

    async def _cleanup_expired_sessions_throttled(self) -> None:
        """Clean up expired sessions, but only if throttle time has passed."""
        if self.use_firestore:
            return
        
        current_time = time.time()
        if current_time - self._last_cleanup_time >= self._cleanup_throttle_seconds:
            await self.cleanup_expired_sessions()
            self._last_cleanup_time = current_time
