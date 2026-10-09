"""
Graph Analytics AI - Common Library for ArangoDB Graph Analytics Engine Orchestration

This library provides a unified interface for orchestrating ArangoDB Graph Analytics Engine (GAE)
operations across both Arango Managed Platform (AMP) and self-managed deployments.

Key Features:
- Unified GAE connection management (AMP and self-managed)
- Complete workflow orchestration (deploy, load, analyze, store, cleanup)
- Support for multiple graph algorithms (PageRank, WCC, SCC, Label Propagation, Betweenness)
- Cost tracking and engine lifecycle management
- Comprehensive error handling and retry logic
"""

__version__ = "3.1.0"

from .config import (
    get_arango_config,
    get_gae_config,
    GAEConfig,
    ArangoConfig,
    DeploymentMode,
)
from .db_connection import get_db_connection, get_connection_info
from .gae_connection import GAEManager, GenAIGAEConnection, GAEConnectionBase
from .gae_orchestrator import (
    GAEOrchestrator,
    AnalysisConfig,
    AnalysisResult,
    AnalysisStatus,
)
from .utils import (
    validate_endpoint_format,
    check_password_format,
    validate_credentials,
    get_credential_validation_report,
)
from . import results, queries, export

__all__ = [
    # Configuration
    "get_arango_config",
    "get_gae_config",
    "GAEConfig",
    "ArangoConfig",
    "DeploymentMode",
    # Database
    "get_db_connection",
    "get_connection_info",
    # GAE Connections
    "GAEManager",
    "GenAIGAEConnection",
    "GAEConnectionBase",
    # Orchestration
    "GAEOrchestrator",
    "AnalysisConfig",
    "AnalysisResult",
    "AnalysisStatus",
    # Utilities
    "validate_endpoint_format",
    "check_password_format",
    "validate_credentials",
    "get_credential_validation_report",
    # Result Management Modules
    "results",
    "queries",
    "export",
]
