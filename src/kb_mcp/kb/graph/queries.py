"""Query functions for knowledge graph."""

import logging
from typing import List, Dict, Any, Optional

from sqlalchemy import and_, func
from sqlalchemy.orm import aliased

from ..database import get_db_session
from .db_models import GraphNode, GraphNodeType, GraphRelation, GraphRelationEvidence, GraphNodeMap, GraphVerb

logger = logging.getLogger(__name__)


def get_nodes_for_document(
    document_id: str,
    session=None,
) -> List[Dict[str, Any]]:
    """
    Get all nodes mentioned in a document with mention counts.

    Args:
        document_id: Document ID to query.
        session: Optional database session.

    Returns:
        List of node info dicts sorted by mention count descending:
        [
          {
            "id": str,
            "name": str,
            "type": str,
            "aliases": List[str],
            "mention_count": int,
            "node": GraphNode  # Full ORM object
          },
          ...
        ]
    """
    with get_db_session(session) as session:
        # Query with joins
        results = session.query(
            GraphNodeMap,
            GraphNode,
            GraphNodeType.label
        ).join(
            GraphNode, GraphNodeMap.node_id == GraphNode.id
        ).join(
            GraphNodeType, GraphNode.type_id == GraphNodeType.id
        ).filter(
            GraphNodeMap.document_id == document_id
        ).order_by(
            GraphNodeMap.count.desc()
        ).all()

        # Build result dicts
        node_list = []
        for node_map, node, type_label in results:
            node_list.append({
                "id": node.id,
                "name": node.name,
                "type": type_label,
                "aliases": node.aliases if node.aliases else [],
                "mention_count": node_map.count,
                "node": node  # Full ORM object for further use
            })

        logger.debug(f"Found {len(node_list)} nodes for document {document_id}")
        return node_list

def get_document_for_node(
    node_id: str,  
    session=None,
) -> List[Dict[str, Any]]:
    """
    Get all documents mentioning a specific node with mention counts.

    Args:
        node_id: Node UUID to query.
        session: Optional database session.

    Returns:
        List of document info dicts sorted by mention count descending:
        [
          {
            "document_id": str,
            "mention_count": int
          },
          ...
        ]
    """
    with get_db_session(session) as session:
        # Query with joins
        results = session.query(
            GraphNodeMap,
            GraphNode,
            GraphNodeType.label
        ).join(
            GraphNode, GraphNodeMap.node_id == GraphNode.id
        ).join(
            GraphNodeType, GraphNode.type_id == GraphNodeType.id
        ).filter(
            GraphNodeMap.node_id == node_id
        ).order_by(
            GraphNodeMap.count.desc()
        ).all()

        # Build result dicts
        document_list = []
        for node_map, node, type_label in results:
            document_list.append({
                "document_id": node_map.document_id,
                "mention_count": node_map.count
            })

        logger.debug(f"Found {len(document_list)} documents for node {node_id}")
        return document_list

def get_node(
    id: Optional[str] = None,
    name: Optional[str] = None,
    type: Optional[str] = None,
    include_incoming: bool = True,
    include_outgoing: bool = True,
    session=None,
) -> Dict[str, Any]:
    """
    Get a node with all its relations (incoming and outgoing).

    Args:
        id: Node UUID (optional). If used , takes precedence over name.
        name: Node name (optional, optional type for filtering).
        type: Node type (optional used together with name for filtering).
        include_incoming: Whether to include incoming relations (default: True).
        include_outgoing: Whether to include outgoing relations (default: True).
        session: Optional database session.

    Returns:
        Node info dict with relations:
        {
          "node": {
            "id": str,
            "name": str,
            "type": str,
            "aliases": List[str],
            "created_time": datetime,
            "meta": dict
            },
            "outgoing_relations": [
                {
                "relation_id": str,
                "verb": str,
                "target_node": {
                    "id": str,
                    "name": str,
                    "type": str
                },
                "evidence_count": int,
                "max_confidence": float,
                "created_time": datetime
                },
                ...
            ],
            "incoming_relations": [
                {
                "relation_id": str,
                "verb": str,
                ...
            ]
            "statistics": {
                "total_outgoing": int,
                "total_incoming": int,
                "total_relations": int,
                "total_documents": int
            },
    """
    if id is None and name is None:
        raise ValueError("Either 'id' or 'name' must be provided to get a node.")

    with get_db_session(session) as session:
        # 1. Fetch Node + Type in one go
        if id:
            node_query = session.query(GraphNode, GraphNodeType.label).join(
                GraphNodeType, GraphNode.type_id == GraphNodeType.id
            ).filter(GraphNode.id == id)
        else:
            # Note: You might want to replicate find_node logic here to keep the join optimization
            from .matching import find_node
            # For simplicity, we assume find_node returns the object, 
            # but ideally we'd do the join here too.
            node_obj = find_node(name, type)
            if not node_obj: return None
            # Re-query to get the type label efficiently if find_node didn't return it
            node_query = session.query(GraphNode, GraphNodeType.label).join(
                GraphNodeType, GraphNode.type_id == GraphNodeType.id
            ).filter(GraphNode.id == node_obj.id)

        result = node_query.first()
        if not result:
            return None
            
        node, type_label = result

        # 2. Build Basic Node Dict
        node_dict = {
            "id": str(node.id),
            "name": node.name,
            "type": type_label,
            "aliases": node.aliases if node.aliases else [],
            "created_time": node.created_time,
            "meta": node.meta if node.meta else {}
        }

        # 3. Fetch Linked Documents (Fulfilling the docstring promise)
        # Assuming you have a GraphNodeMap table
        linked_docs = session.query(GraphNodeMap.document_id).filter(
            GraphNodeMap.node_id == node.id
        ).all()
        linked_doc_ids = [str(d[0]) for d in linked_docs]

        outgoing_relations = []
        incoming_relations = []

        # 4. Optimized Outgoing Query
        if include_outgoing:
            # Aliases for clarity
            TargetNode = aliased(GraphNode)
            TargetType = aliased(GraphNodeType)

            # Query: Relation + Verb + Target + TargetType + Evidence Count + Avg Confidence
            # Grouping by Relation ID to aggregate evidence
            query = (
                session.query(
                    GraphRelation,
                    GraphVerb.name,
                    TargetNode,
                    TargetType.label,
                    func.count(GraphRelationEvidence.id).label("ev_count"),
                    func.max(GraphRelationEvidence.confidence).label("ev_conf")
                )
                .join(GraphVerb, GraphRelation.verb_id == GraphVerb.id)
                .join(TargetNode, GraphRelation.target_id == TargetNode.id)
                .join(TargetType, TargetNode.type_id == TargetType.id)
                .outerjoin(GraphRelationEvidence, GraphRelation.id == GraphRelationEvidence.relation_id)
                .filter(GraphRelation.source_id == node.id)
                .group_by(GraphRelation.id, GraphVerb.name, TargetNode.id, TargetNode.name, TargetType.label)
            )

            for rel, verb_name, target, target_type, ev_count, ev_conf in query.all():
                outgoing_relations.append({
                    "relation_id": str(rel.id),
                    "verb": verb_name,
                    "target_node": {
                        "id": str(target.id),
                        "name": target.name,
                        "type": target_type
                    },
                    "evidence_count": ev_count if ev_count else 0,
                    "max_confidence": float(ev_conf) if ev_conf else 0.0,
                    "created_time": rel.created_time
                })

        # 5. Optimized Incoming Query (Symmetric to above)
        if include_incoming:
            SourceNode = aliased(GraphNode)
            SourceType = aliased(GraphNodeType)

            query = (
                session.query(
                    GraphRelation,
                    GraphVerb.name,
                    SourceNode,
                    SourceType.label,
                    func.count(GraphRelationEvidence.id).label("ev_count"),
                    func.avg(GraphRelationEvidence.confidence).label("ev_conf")
                )
                .join(GraphVerb, GraphRelation.verb_id == GraphVerb.id)
                .join(SourceNode, GraphRelation.source_id == SourceNode.id)
                .join(SourceType, SourceNode.type_id == SourceType.id)
                .outerjoin(GraphRelationEvidence, GraphRelation.id == GraphRelationEvidence.relation_id)
                .filter(GraphRelation.target_id == node.id)
                .group_by(GraphRelation.id, GraphVerb.name, SourceNode.id, SourceNode.name, SourceType.label)
            )

            for rel, verb_name, source, source_type, ev_count, ev_conf in query.all():
                incoming_relations.append({
                    "relation_id": str(rel.id),
                    "verb": verb_name,
                    "source_node": {
                        "id": str(source.id),
                        "name": source.name,
                        "type": source_type
                    },
                    "evidence_count": ev_count if ev_count else 0,
                    "max_confidence": float(ev_conf) if ev_conf else 0.0,
                    "created_time": rel.created_time
                })

        statistics = {
            "total_outgoing": len(outgoing_relations),
            "total_incoming": len(incoming_relations),
            "total_relations": len(outgoing_relations) + len(incoming_relations),
            "total_documents": len(linked_doc_ids)
        }

        return {
            "node": node_dict,
            "outgoing_relations": outgoing_relations,
            "incoming_relations": incoming_relations,
            "statistics": statistics,
            "linked_documents": linked_doc_ids
        }


from sqlalchemy import text

def find_paths(
    start_node_id: str,
    end_node_id: str,
    max_depth: int = 4,
    limit: int = 5,
    session=None
) -> List[Dict[str, Any]]:
    """
    Finds paths between two nodes (Undirected / Bidirectional).
    Returns the top 'limit' shortest paths.

    Args:
        start_node_id: UUID of the starting node.
        end_node_id: UUID of the ending node.
        max_depth: Maximum depth to search (default: 4).
        limit: Maximum number of paths to return (default: 5).
        session: Optional database session.
    
    Returns:
        List of paths, each path is a dict:
        {
            "length": int,
            "cha": [
                {
                    "entity": {
                        "id": str,
                        "name": str,
                        "type": str
                    },
                    "connection": {
                        "verb": str,
                        "direction": "forward" | "backward",
                        "relation_id": str
                    } | None  # None for the starting node
                },
                ...
            ]
        }
    """

    # Logic:
    # We look for any relation where 'current_id' is EITHER source OR target.
    # We grab the 'other' side as the next ID.
    
    query = text(f"""
        WITH RECURSIVE search_graph(
            current_id, 
            depth, 
            path_nodes, 
            path_edges
        ) AS (
            -- ANCHOR: Start Node
            SELECT 
                id, 
                0, 
                ARRAY[id]::text[], 
                ARRAY[]::text[]  -- FIX: Removed trailing comma here
            FROM graph_nodes 
            WHERE id = :start_id

            UNION ALL

            -- RECURSIVE STEP
            SELECT 
                CASE 
                    WHEN r.source_id = p.current_id THEN r.target_id 
                    ELSE r.source_id 
                END,
                p.depth + 1,
                p.path_nodes || (CASE 
                    WHEN r.source_id = p.current_id THEN r.target_id 
                    ELSE r.source_id 
                END),
                p.path_edges || r.id
            FROM graph_relations r, search_graph p
            WHERE 
                (r.source_id = p.current_id OR r.target_id = p.current_id)
                
                AND p.depth < :max_depth
                
                AND NOT (
                    (CASE 
                        WHEN r.source_id = p.current_id THEN r.target_id 
                        ELSE r.source_id 
                    END) = ANY(p.path_nodes)
                )
        )
        SELECT path_nodes, path_edges, depth 
        FROM search_graph 
        WHERE current_id = :end_id
        ORDER BY depth ASC
        LIMIT :limit;
    """)

    paths_data = []
    
    with get_db_session(session) as session:
        # check that start_node_id and end_node_id are valid UUIDs
        res = session.query(GraphNode).filter(GraphNode.id == start_node_id).all()
        if not res:
            raise ValueError("Invalid start_node_id")

        res = session.query(GraphNode).filter(GraphNode.id == end_node_id).all()
        if not res:
            raise ValueError("Invalid end_node_id")

        result = session.execute(query, {
            "start_id": start_node_id, 
            "end_id": end_node_id,
            "max_depth": max_depth,
            "limit": limit
        }).fetchall()

        if not result:
            return []

        for row in result:
            node_ids = row[0]
            edge_ids = row[1]
            
            # Fetch Nodes
            nodes_res = session.query(GraphNode, GraphNodeType.label).join(
                GraphNodeType, GraphNode.type_id == GraphNodeType.id
            ).filter(
                GraphNode.id.in_(node_ids) # Pass the list of strings directly
            ).all()
            
            node_map = {
                str(n.id): {"id": str(n.id), "name": n.name, "type": t_label} 
                for n, t_label in nodes_res
            }

            # Fetch Edges
            edges_res = session.query(GraphRelation, GraphVerb.name).join(
                GraphVerb, GraphRelation.verb_id == GraphVerb.id
            ).filter(GraphRelation.id.in_(edge_ids)).all()
            
            edge_map = {
                str(r.id): {"verb": v_name, "source": str(r.source_id), "target": str(r.target_id)}
                for r, v_name in edges_res
            }

            # Reconstruct the Path as a Linear Chain
            # Format: [Node, Relation, Node, Relation, Node]
            linear_path = []
            
            # A. Add the Start Node
            start_node_id = str(node_ids[0])
            start_node_data = node_map.get(start_node_id)
            linear_path.append({
                "element": "node",
                "id": start_node_data["id"],
                "name": start_node_data["name"],
                "label": start_node_data["type"] # 'type' is a reserved keyword in some systems, label is safer
            })

            # B. Loop through the edges and append (Edge -> Next Node)
            for i, edge_id in enumerate(edge_ids):
                edge_info = edge_map.get(str(edge_id))
                next_node_id = str(node_ids[i+1])
                prev_node_id = str(node_ids[i])
                
                # Determine Direction
                # "forward" = Arrow points from Prev -> Next
                # "backward" = Arrow points from Next -> Prev
                direction = "forward" if edge_info["source"] == prev_node_id else "backward"

                # 1. Append the Relationship (The Arrow)
                linear_path.append({
                    "element": "relationship",
                    "id": str(edge_id),
                    "verb": edge_info["verb"],
                    "direction": direction
                })

                # 2. Append the Next Node (The Box)
                next_node_data = node_map.get(next_node_id)
                linear_path.append({
                    "element": "node",
                    "id": next_node_data["id"],
                    "name": next_node_data["name"],
                    "label": next_node_data["type"]
                })

            paths_data.append({
                "length": row[2],
                "chain": linear_path  # <--- The new flat list
            })

    return paths_data