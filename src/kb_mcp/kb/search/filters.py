"""Shared filter utilities for Elasticsearch-style filtering."""

from typing import Any, Dict, List, Optional, Callable, Tuple
from sqlalchemy import func, and_, or_
from sqlalchemy.dialects import postgresql


def _parse_elasticsearch_filter(
    doc_alias: Any,
    filter_dict: Dict[str, Any],
    dialect_name: Optional[str] = None,
) -> Any:
    """
    Parse an Elasticsearch-style filter query and convert it to SQLAlchemy filter conditions.
    
    Supports Elasticsearch query DSL syntax:
    - term: {"term": {"field": "value"}} - exact match
    - terms: {"terms": {"field": ["value1", "value2"]}} - match any value (OR)
    - range: {"range": {"field": {"gte": "min", "lte": "max"}}} - range query
    - match: {"match": {"field": "value"}} - contains/substring match (LIKE '%value%')
      Note: This is a convenience for substring matching. In Elasticsearch, `match` is for
      full-text search, but here we use it for simple substring matching on metadata.
    - wildcard: {"wildcard": {"field": "pattern"}} or {"wildcard": {"field": {"value": "pattern"}}}
      - Pattern match with * (zero or more chars) and ? (single char) wildcards
      - Supports both shorthand and full Elasticsearch structure
    - bool: {"bool": {"must": [...], "should": [...], "must_not": [...]}} - boolean logic
    
    Args:
        doc_alias: SQLAlchemy aliased Document model (needed to reference the meta column)
        filter_dict: Elasticsearch-style filter dictionary
        dialect_name: Database dialect name ("postgresql" or "sqlite") - determines which
                     JSON operator to use (JSONB for PostgreSQL, json_extract for SQLite)
    
    Returns:
        SQLAlchemy filter condition(s)
    
    Note:
        `doc_alias` is needed to build SQLAlchemy column expressions (e.g., `doc_alias.meta[field]`).
        It's passed through recursive calls for nested bool queries, even though it's only
        actually used at leaf nodes (term/terms/range/match/wildcard queries).
    """
    # Internal helper to build metadata filters (inlined for simplicity)
    def build_meta_filter(field: str, value: Any, operator: str = "==") -> Any:
        return _build_metadata_filter(doc_alias, field, value, dialect_name, operator)
    
    # Internal recursive helper that uses the builder
    def _parse_recursive(filter_dict: Dict[str, Any]) -> Any:
        if not isinstance(filter_dict, dict):
            raise ValueError(f"Filter must be a dictionary, got {type(filter_dict)}")
        
        # Handle bool query
        if "bool" in filter_dict:
            bool_clauses = filter_dict["bool"]
            conditions = []
            
            # must: AND conditions (all must match)
            if "must" in bool_clauses:
                must_conditions = [_parse_recursive(clause) for clause in bool_clauses["must"]]
                if must_conditions:
                    conditions.append(and_(*must_conditions))
            
            # should: OR conditions (at least one must match)
            if "should" in bool_clauses:
                should_conditions = [_parse_recursive(clause) for clause in bool_clauses["should"]]
                if should_conditions:
                    minimum_should_match = bool_clauses.get("minimum_should_match", 1)
                    if minimum_should_match == len(should_conditions):
                        # All should match = AND
                        conditions.append(and_(*should_conditions))
                    elif minimum_should_match == 1:
                        # At least one matches = OR
                        conditions.append(or_(*should_conditions))
                    else:
                        # Complex case: need at least N matches
                        # For simplicity, we'll use OR (at least one) for now
                        # TODO: Implement proper minimum_should_match logic
                        conditions.append(or_(*should_conditions))
            
            # must_not: NOT conditions
            if "must_not" in bool_clauses:
                must_not_conditions = [_parse_recursive(clause) for clause in bool_clauses["must_not"]]
                if must_not_conditions:
                    # NOT (A OR B) = (NOT A) AND (NOT B)
                    not_conditions = [~cond for cond in must_not_conditions]
                    conditions.append(and_(*not_conditions))
            
            if not conditions:
                return None
            
            # Combine all bool conditions with AND
            return and_(*conditions) if len(conditions) > 1 else conditions[0]
        
        # Handle term query: exact match
        if "term" in filter_dict:
            term_query = filter_dict["term"]
            if not isinstance(term_query, dict) or len(term_query) != 1:
                raise ValueError("term query must have exactly one field")
            
            field, value = next(iter(term_query.items()))
            return build_meta_filter(field, value, operator="==")
        
        # Handle terms query: match any value (OR)
        if "terms" in filter_dict:
            terms_query = filter_dict["terms"]
            if not isinstance(terms_query, dict) or len(terms_query) != 1:
                raise ValueError("terms query must have exactly one field")
            
            field, values = next(iter(terms_query.items()))
            if not isinstance(values, list):
                raise ValueError("terms query value must be a list")
            
            # Create OR condition for multiple values
            conditions = [build_meta_filter(field, value, operator="==") for value in values]
            return or_(*conditions) if len(conditions) > 1 else conditions[0]
        
        # Handle range query
        if "range" in filter_dict:
            range_query = filter_dict["range"]
            if not isinstance(range_query, dict) or len(range_query) != 1:
                raise ValueError("range query must have exactly one field")
            
            field, range_params = next(iter(range_query.items()))
            if not isinstance(range_params, dict):
                raise ValueError("range parameters must be a dictionary")
            
            # Check if this is a direct Document column (insert_time, creating_time, update_time)
            # vs a metadata field
            direct_columns = {"insert_time", "creating_time", "update_time"}
            is_direct_column = field in direct_columns
            
            conditions = []
            if "gte" in range_params:
                if is_direct_column:
                    conditions.append(getattr(doc_alias, field) >= range_params["gte"])
                else:
                    conditions.append(build_meta_filter(field, range_params["gte"], operator=">="))
            if "gt" in range_params:
                if is_direct_column:
                    conditions.append(getattr(doc_alias, field) > range_params["gt"])
                else:
                    conditions.append(build_meta_filter(field, range_params["gt"], operator=">"))
            if "lte" in range_params:
                if is_direct_column:
                    conditions.append(getattr(doc_alias, field) <= range_params["lte"])
                else:
                    conditions.append(build_meta_filter(field, range_params["lte"], operator="<="))
            if "lt" in range_params:
                if is_direct_column:
                    conditions.append(getattr(doc_alias, field) < range_params["lt"])
                else:
                    conditions.append(build_meta_filter(field, range_params["lt"], operator="<"))
            
            if not conditions:
                raise ValueError("range query must have at least one range parameter (gte, gt, lte, lt)")
            
            return and_(*conditions) if len(conditions) > 1 else conditions[0]
        
        # Handle match query: contains/substring match (LIKE '%value%')
        if "match" in filter_dict:
            match_query = filter_dict["match"]
            if not isinstance(match_query, dict) or len(match_query) != 1:
                raise ValueError("match query must have exactly one field")
            
            field, value = next(iter(match_query.items()))
            return _build_metadata_filter(doc_alias, field, f"%{value}%", dialect_name, operator="LIKE")
        
        # Handle wildcard query: pattern match with * and ? wildcards
        if "wildcard" in filter_dict:
            wildcard_query = filter_dict["wildcard"]
            if not isinstance(wildcard_query, dict) or len(wildcard_query) != 1:
                raise ValueError("wildcard query must have exactly one field")
            
            field, pattern_or_dict = next(iter(wildcard_query.items()))
            
            # Support both shorthand {"wildcard": {"field": "pattern"}} 
            # and full form {"wildcard": {"field": {"value": "pattern"}}}
            if isinstance(pattern_or_dict, dict):
                # Full Elasticsearch form: {"wildcard": {"field": {"value": "pattern"}}}
                if "value" not in pattern_or_dict:
                    raise ValueError("wildcard query with dict must have 'value' key")
                pattern = pattern_or_dict["value"]
            else:
                # Shorthand form: {"wildcard": {"field": "pattern"}}
                pattern = pattern_or_dict
            
            # Convert Elasticsearch wildcards (*, ?) to SQL wildcards (%, _)
            sql_pattern = pattern.replace("*", "%").replace("?", "_")
            return _build_metadata_filter(doc_alias, field, sql_pattern, dialect_name, operator="LIKE")
        
        raise ValueError(f"Unknown filter type: {filter_dict.keys()}. Supported: term, terms, range, match, wildcard, bool")
    
    return _parse_recursive(filter_dict)


def _build_metadata_filter(
    doc_alias: Any,
    field: str,
    value: Any,
    dialect_name: Optional[str] = None,
    operator: str = "==",
) -> Any:
    """
    Build a SQLAlchemy filter for a metadata field.
    
    Args:
        doc_alias: SQLAlchemy aliased Document model
        field: Metadata field name
        value: Value to compare against
        dialect_name: Database dialect name ("postgresql" or "sqlite")
        operator: Comparison operator ("==", ">=", ">", "<=", "<", "LIKE")
    
    Returns:
        SQLAlchemy filter condition
    """
    if dialect_name == "postgresql":
        # PostgreSQL JSONB operators
        field_expr = doc_alias.meta[field].astext
    else:
        # SQLite json_extract
        field_expr = func.json_extract(doc_alias.meta, f"$.{field}")
    
    # Convert value to appropriate type for comparison
    if operator == "==":
        return field_expr == str(value)
    elif operator == ">=":
        return field_expr >= str(value)
    elif operator == ">":
        return field_expr > str(value)
    elif operator == "<=":
        return field_expr <= str(value)
    elif operator == "<":
        return field_expr < str(value)
    elif operator == "LIKE":
        # LIKE operator for pattern matching (contains, wildcard)
        return field_expr.like(str(value))
    else:
        raise ValueError(f"Unsupported operator: {operator}")


def get_filters_fallback(
    doc_alias: Any,
    source_id: Optional[str] = None,
    doc_type: Optional[str] = None,
    filter: Optional[Dict[str, Any]] = None,
    dialect_name: Optional[str] = None,
    **kwargs
) -> List[Any]:
    """
    Build SQLAlchemy filter conditions for document queries (SQLite fallback).
    
    Supports both simple kwargs (for backward compatibility) and Elasticsearch-style
    filter queries for complex filtering.
    
    Args:
        doc_alias: SQLAlchemy aliased Document model (or Document class)
        source_id: Optional filter by source ID
        doc_type: Optional filter by document type
        filter: Optional Elasticsearch-style filter query (dict)
        dialect_name: Database dialect name ("postgresql" or "sqlite")
        **kwargs: Simple metadata filters (for backward compatibility)
                  - Direct field names are treated as metadata filters
                  - Example: author="John" filters meta.author == "John"
    
    Returns:
        List of SQLAlchemy filter conditions (can be combined with and_() or or_())
    
    Examples:
        ```python
        # Simple filters (backward compatible)
        filters = get_filters_fallback(
            doc_alias, source_id="atlas", author="John"
        )

        # Elasticsearch-style filter with contains
        filters = get_filters_fallback(
            doc_alias,
            filter={
                "bool": {
                    "must": [
                        {"match": {"author": "Simon"}},  # Contains "Simon"
                        {"range": {"date": {"gte": "2020-01-01"}}}
                    ],
                    "should": [
                        {"term": {"category": "A"}},
                        {"wildcard": {"title": "*test*"}}  # Pattern match
                    ],
                    "minimum_should_match": 1
                }
            }
        )
        ```
    """
    filters = []
    
    if source_id:
        filters.append(doc_alias.source_id == source_id)
    
    if doc_type:
        filters.append(doc_alias.doc_type == doc_type)
    
    # Parse Elasticsearch-style filter if provided
    if filter:
        es_filter = _parse_elasticsearch_filter(doc_alias, filter, dialect_name)
        if es_filter is not None:
            filters.append(es_filter)
    
    # Handle simple kwargs (backward compatibility)
    # Direct field names are treated as metadata filters
    for key, value in kwargs.items():
        # Skip reserved parameters
        if key in ("session", "explain_analyse", "embedding_name", "max_results"):
            continue
        
        # Treat as metadata filter
        filter_cond = _build_metadata_filter(doc_alias, key, value, dialect_name, operator="==")
        filters.append(filter_cond)
    
    return filters


def get_filters_pgvector(
    doc_alias: Any,
    filter_dict: Dict[str, Any],
    param_counter: int = 0,
) -> Tuple[str, Dict[str, Any]]:
    """
    Build PostgreSQL SQL filter from Elasticsearch-style filter using unified parser.
    
    This uses _parse_elasticsearch_filter to build SQLAlchemy expressions,
    then compiles them to PostgreSQL SQL strings with named parameters.
    
    Args:
        doc_alias: SQLAlchemy aliased Document model
        filter_dict: Elasticsearch-style filter dictionary
        param_counter: Starting counter for parameter names
    
    Returns:
        Tuple of (SQL WHERE clause fragment, parameter dictionary)
    """
    # Parse to SQLAlchemy expression using unified parser
    filter_expr = _parse_elasticsearch_filter(doc_alias, filter_dict, dialect_name="postgresql")
    
    if filter_expr is None:
        return "", {}
    
    # Compile to SQL string with named parameters
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.sql import compiler
    
    # Compile the expression
    compiled = filter_expr.compile(
        dialect=postgresql.dialect(),
        compile_kwargs={"literal_binds": False}
    )
    
    # Get SQL string (SQLAlchemy uses %s for positional parameters)
    sql = str(compiled)
    
    # Extract parameters and convert to named parameters
    params = {}
    counter = param_counter
    
    # SQLAlchemy stores parameters in compiled.params
    # The order matches the %s placeholders in the SQL
    if hasattr(compiled, 'params'):
        param_dict = compiled.params if isinstance(compiled.params, dict) else {}
        # Get parameter values - SQLAlchemy may use different structures
        # Try to get them in order
        if isinstance(compiled.params, dict):
            # For dict, we need to match keys to positions
            # This is tricky - let's use a simpler approach
            # Count %s and replace them
            param_count = sql.count('%s')
            param_values = list(compiled.params.values())[:param_count] if param_count > 0 else []
        else:
            param_values = list(compiled.params) if compiled.params else []
        
        # Replace each %s with a named parameter
        for value in param_values:
            param_name = f"filter_{counter}"
            params[param_name] = str(value) if value is not None else None
            # Replace first occurrence of %s
            sql = sql.replace('%s', f':{param_name}', 1)
            counter += 1
    
    return sql, params

