"""Mu2e-specific ontology for the knowledge graph.

Defines domain-specific node types and relation verbs tailored to the Mu2e
experiment. These extend the generic defaults (Concept, Hardware, Person, etc.)
with Mu2e-specific categories.

Usage:
    from kb_mcp.kb.graph.mu2e_ontology import seed_mu2e_ontology
    seed_mu2e_ontology()  # Idempotent — safe to call multiple times
"""

import logging
from typing import List, Tuple

from ..database import get_db_session
from .db_models import GraphNodeType, GraphVerb

logger = logging.getLogger(__name__)


# --- Mu2e Node Types ---
# These complement the generic defaults (Concept, Document, Hardware, etc.)
# and add domain-specific categories for the Mu2e experiment.

MU2E_NODE_TYPES: List[Tuple[str, str]] = [
    # Experiment structure
    ("Subsystem", "Major detector subsystems (e.g., Tracker, Calorimeter, CRV, STM, ExtMon)"),
    ("Component", "Specific hardware components within a subsystem (e.g., straw tube, SiPM, crystal, panel)"),
    ("BeamElement", "Beamline elements (e.g., Production Solenoid, Transport Solenoid, Detector Solenoid, collimators, absorbers)"),

    # Physics
    ("PhysicsProcess", "Physics processes and signals (e.g., mu-to-e conversion, DIO, RPC, cosmic rays, beam flash)"),
    ("Particle", "Particles relevant to Mu2e (e.g., muon, electron, pion, proton, neutron)"),

    # Software & computing
    ("Software", "Software packages, frameworks, and tools (e.g., Offline, art, Geant4, MARS, JobSub)"),
    ("Dataset", "Data samples, MC datasets, and ntuples (e.g., MDC2020, ensembleMDS1g, EventNtuple)"),
    ("FHiCLConfig", "FHiCL configuration files and parameter sets (e.g., Production.fcl, TrkAna.fcl)"),

    # Simulation & reconstruction
    ("Simulation", "Simulation campaigns and configurations (e.g., MDC2020, Stage1, Mixing)"),
    ("Algorithm", "Reconstruction or analysis algorithms (e.g., track fitting, calorimeter clustering, PID)"),

    # Infrastructure
    ("ComputingResource", "Computing infrastructure (e.g., mu2egpvm, dcache, BlueArc, CVMFS, FermiGrid)"),

    # Code-specific
    ("ArtModule", "art framework module class (EDProducer, EDAnalyzer, EDFilter) in the Offline repository"),
    ("CodePackage", "Source code package/directory in a Mu2e repository (e.g., TrkHitReco, CalPatRec, CRVReco)"),
    ("DataProduct", "art data product type produced or consumed by modules (e.g., ComboHitCollection, KalSeed)"),

    # DocDB entity types
    ("Event", "Meetings, conferences, reviews, and workshops (from DocDB events/meetings)"),
    ("Topic", "DocDB topic categories used to classify documents (hierarchical)"),
    ("Keyword", "Free-form keywords assigned to DocDB documents"),
    ("DocType", "Document type classifications in DocDB (e.g., Technical Note, Talk, Poster)"),
    ("Group", "Organizational or security groups in DocDB (e.g., working groups, access groups)"),
    ("Institution", "Research institutions affiliated with Mu2e collaborators"),
]


# --- Mu2e Relation Verbs ---
# These complement the generic defaults (references, cites, part_of, etc.)
# and add Mu2e-specific relationships.

MU2E_VERBS: List[Tuple[str, str]] = [
    # Structural
    ("contains", "Containment (Subsystem contains Component, BeamElement contains Component)"),
    ("connects_to", "Physical or logical connection between elements"),

    # Physics relationships
    ("background_to", "Background process relationship (PhysicsProcess is background to signal)"),
    ("produces", "Production relationship (BeamElement produces Particle, process produces particle)"),
    ("decays_to", "Decay relationship (Particle decays to other particles)"),
    ("detected_by", "Detection relationship (Particle/PhysicsProcess detected by Subsystem/Component)"),

    # Software relationships
    ("simulated_by", "Simulation tool (PhysicsProcess simulated by Software/Simulation)"),
    ("configured_by", "Configuration (Software/Algorithm configured by FHiCLConfig)"),
    ("depends_on", "Software dependency (Software depends on Software)"),
    ("processes", "Data processing (Algorithm processes Dataset)"),

    # Calibration & measurement
    ("calibrated_with", "Calibration relationship (Subsystem/Component calibrated with Algorithm/Dataset)"),
    ("monitors", "Monitoring relationship (Software/Algorithm monitors Subsystem/Component)"),

    # Documentation
    ("described_in", "Documentation link (any entity described in Document)"),
    ("specifies", "Specification (Document specifies Component/Subsystem requirements)"),

    # Code-specific
    ("implements", "Code implements an algorithm or functionality"),
    ("produces_data", "Module produces a data product type"),
    ("consumes_data", "Module consumes a data product type"),
    ("included_by", "Header file is included by source file"),

    # DocDB entity relationships
    ("has_topic", "Document is classified under a topic category"),
    ("has_keyword", "Document is tagged with a keyword"),
    ("has_type", "Document is classified as a document type"),
    ("presented_at", "Document was presented at an event/meeting"),
    ("affiliated_with", "Person is affiliated with an institution"),
    ("parent_topic", "Topic is a sub-topic of a parent topic (hierarchy)"),
    ("belongs_to", "Person or entity belongs to an organizational group"),
    ("cross_references", "Document cross-references another document"),
]


def seed_mu2e_ontology(session=None) -> dict:
    """Seed Mu2e-specific node types and verbs into the knowledge graph.

    This is idempotent — existing types/verbs are skipped. Safe to call
    multiple times. Only adds entries that don't already exist (matched
    by label/name).

    Args:
        session: Optional database session.

    Returns:
        Dict with counts: {"types_added", "types_skipped", "verbs_added", "verbs_skipped"}
    """
    stats = {"types_added": 0, "types_skipped": 0, "verbs_added": 0, "verbs_skipped": 0}

    with get_db_session(session) as session:
        # Seed node types
        existing_types = {
            t.label for t in session.query(GraphNodeType.label).all()
        }

        for label, description in MU2E_NODE_TYPES:
            if label in existing_types:
                stats["types_skipped"] += 1
                continue
            node_type = GraphNodeType(label=label, description=description)
            session.add(node_type)
            stats["types_added"] += 1
            logger.info(f"Added Mu2e node type: {label}")

        # Seed verbs
        existing_verbs = {
            v.name for v in session.query(GraphVerb.name).all()
        }

        for name, description in MU2E_VERBS:
            if name in existing_verbs:
                stats["verbs_skipped"] += 1
                continue
            verb = GraphVerb(name=name, description=description)
            session.add(verb)
            stats["verbs_added"] += 1
            logger.info(f"Added Mu2e verb: {name}")

        session.commit()

    total_types = stats["types_added"] + stats["types_skipped"]
    total_verbs = stats["verbs_added"] + stats["verbs_skipped"]
    logger.info(
        f"Mu2e ontology seeded: {stats['types_added']}/{total_types} types added, "
        f"{stats['verbs_added']}/{total_verbs} verbs added"
    )

    return stats
