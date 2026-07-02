# Knowledge Graph

Functions for knowledge graph extraction and querying.

## Mu2e Ontology

The Mu2e-specific ontology (`kb/graph/mu2e_ontology.py`) defines 20 node types and 26 relation verbs:

**Node types:** Subsystem, Component, BeamElement, PhysicsProcess, Particle, Software, Dataset, FHiCLConfig, Simulation, Algorithm, ComputingResource, ArtModule, CodePackage, DataProduct, Event, Topic, Keyword, DocType, Group, Institution

**Relation verbs:** contains, connects_to, background_to, produces, decays_to, detected_by, simulated_by, configured_by, depends_on, processes, calibrated_with, monitors, described_in, specifies, implements, produces_data, consumes_data, included_by, has_topic, has_keyword, has_type, presented_at, affiliated_with, parent_topic, belongs_to, cross_references

Seed the ontology (idempotent):
```python
from kb_mcp.kb.graph.mu2e_ontology import seed_mu2e_ontology
seed_mu2e_ontology()  # Safe to call multiple times
```

## Queries

::: kb_mcp.kb.graph.get_node

::: kb_mcp.kb.graph.get_nodes_for_document

::: kb_mcp.kb.graph.get_document_for_node

::: kb_mcp.kb.graph.find_paths

## Batch Operations

::: kb_mcp.kb.graph.extract_all

## Extraction

::: kb_mcp.kb.graph.extract_relations

::: kb_mcp.kb.graph.process_relations

::: kb_mcp.kb.graph.extract_and_process_document

## Schema Access

::: kb_mcp.kb.graph.get_verbs

::: kb_mcp.kb.graph.get_node_types
