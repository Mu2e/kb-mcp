"""Mu2e domain-aware knowledge graph extraction.

Wraps the generic extraction pipeline with Mu2e-specific domain context
and post-processing for entity name normalization.

Usage:
    from kb_mcp.kb.graph.mu2e_extraction import extract_and_process_document_mu2e

    # Extract with Mu2e domain context
    result = extract_and_process_document_mu2e(document_id)

    # Or get just the domain context string for manual use
    from kb_mcp.kb.graph.mu2e_extraction import MU2E_DOMAIN_CONTEXT
"""

import logging
import re
from typing import Dict, Any, List, Optional

from .extraction import extract_relations, process_relations, extract_and_process_document

logger = logging.getLogger(__name__)


# --- Mu2e Domain Context ---
# This is injected into the LLM extraction prompt to guide entity and
# relation extraction with Mu2e-specific knowledge.

MU2E_DOMAIN_CONTEXT = """This document is from the Mu2e experiment at Fermilab.
Mu2e searches for charged lepton flavor violation (CLFV) via neutrinoless
muon-to-electron conversion (mu- N -> e- N) in the field of an aluminum nucleus.

**Key subsystems and abbreviations:**
- Tracker (straw tube tracker for electron momentum measurement)
- Calorimeter (CsI or BaF2 crystal calorimeter, two annular disks)
- CRV / Cosmic Ray Veto (scintillator panels with SiPMs)
- STM / Stopping Target Monitor (monitors muon stops on aluminum target)
- ExtMon / Extinction Monitor (monitors out-of-time beam)
- DS / Detector Solenoid (uniform 1T field housing tracker + calorimeter)
- TS / Transport Solenoid (S-shaped, selects low-momentum negative muons)
- PS / Production Solenoid (5T field around production target)
- IFB / Inner and Outer Proton Absorber
- Stopping Target (aluminum foils where muons stop)

**Common abbreviations to normalize:**
- CRV = Cosmic Ray Veto
- STM = Stopping Target Monitor
- ExtMon = Extinction Monitor
- DS = Detector Solenoid
- TS = Transport Solenoid
- PS = Production Solenoid
- IPA = Inner Proton Absorber
- OPA = Outer Proton Absorber
- DIO = Decay In Orbit (background process)
- RPC = Radiative Pion Capture (background process)
- CE = Conversion Electron (signal)
- TrkAna = Track Analysis (software)
- MDC = Mock Data Challenge (simulation campaign)
- FHiCL = Fermilab Hierarchical Configuration Language

**Physics processes:**
- mu-to-e conversion (signal process)
- Decay In Orbit / DIO (main background)
- Radiative Pion Capture / RPC (background)
- Cosmic ray background
- Beam flash / beam electrons

**Software ecosystem:**
- Offline (main C++ framework, built on art)
- art (event processing framework)
- Geant4 (detector simulation)
- MARS (radiation transport)
- JobSub (Fermilab grid job submission)
- FHiCL (configuration language)
- EventNtuple / TrkAna (analysis tools)

**When extracting relations, prefer:**
- Using the Subsystem type for major detector components (Tracker, Calorimeter, CRV, STM, ExtMon)
- Using BeamElement for solenoids and beamline components (PS, TS, DS, collimators, absorbers)
- Using PhysicsProcess for signal and background processes
- Using Software for code packages and frameworks
- Using FHiCLConfig for .fcl configuration files
- Using the full name with abbreviation, e.g., "Cosmic Ray Veto (CRV)" for canonical names
"""


# --- Abbreviation Normalization Map ---
# Maps common abbreviations to their canonical forms.
# Used in post-processing to standardize entity names.

MU2E_ABBREVIATIONS: Dict[str, str] = {
    # Subsystems
    "CRV": "Cosmic Ray Veto (CRV)",
    "STM": "Stopping Target Monitor (STM)",
    "ExtMon": "Extinction Monitor (ExtMon)",
    # Beam elements
    "DS": "Detector Solenoid (DS)",
    "TS": "Transport Solenoid (TS)",
    "PS": "Production Solenoid (PS)",
    "IPA": "Inner Proton Absorber (IPA)",
    "OPA": "Outer Proton Absorber (OPA)",
    # Physics
    "DIO": "Decay In Orbit (DIO)",
    "RPC": "Radiative Pion Capture (RPC)",
    "CE": "Conversion Electron (CE)",
    "CLFV": "Charged Lepton Flavor Violation (CLFV)",
    # Software
    "TrkAna": "TrkAna",
    "MDC": "Mock Data Challenge (MDC)",
    "FHiCL": "FHiCL",
}


def normalize_mu2e_name(name: str) -> str:
    """Normalize a Mu2e entity name using abbreviation expansion.

    If the name is a bare abbreviation (exact match), expands it to the
    canonical form. Otherwise returns the name unchanged.

    Args:
        name: Entity name from extraction.

    Returns:
        Normalized entity name.

    Examples:
        >>> normalize_mu2e_name("CRV")
        'Cosmic Ray Veto (CRV)'
        >>> normalize_mu2e_name("Tracker")
        'Tracker'
        >>> normalize_mu2e_name("the DS")
        'Detector Solenoid (DS)'
    """
    stripped = name.strip()

    # Exact match on abbreviation
    if stripped in MU2E_ABBREVIATIONS:
        return MU2E_ABBREVIATIONS[stripped]

    # Check for "the X" pattern
    match = re.match(r'^(?:the|The|THE)\s+(.+)$', stripped)
    if match:
        inner = match.group(1).strip()
        if inner in MU2E_ABBREVIATIONS:
            return MU2E_ABBREVIATIONS[inner]

    return stripped


def normalize_mu2e_relations(
    relations: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Post-process extracted relations with Mu2e-specific normalization.

    Applies abbreviation expansion to source and target names.

    Args:
        relations: List of relation dicts from extract_relations().

    Returns:
        Same list with normalized names.
    """
    for relation in relations:
        if "source_name" in relation:
            relation["source_name"] = normalize_mu2e_name(relation["source_name"])
        if "target_name" in relation:
            relation["target_name"] = normalize_mu2e_name(relation["target_name"])
    return relations


def extract_and_process_document_mu2e(
    document_id: str,
    session=None,
) -> Dict[str, Any]:
    """Extract and process relations from a Mu2e document.

    This is a convenience wrapper around extract_and_process_document()
    that automatically injects the Mu2e domain context.

    Args:
        document_id: Document ID to process.
        session: Optional database session.

    Returns:
        Same result dict as extract_and_process_document().
    """
    return extract_and_process_document(
        document_id=document_id,
        domain_context=MU2E_DOMAIN_CONTEXT,
        session=session,
    )
