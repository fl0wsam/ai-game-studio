"""
studio/verification - Automated Verification & Simulation Pipeline for Autonomous AI Game Studio.
Exports syntax validators, TSCN schema checker, asset verifier, engine simulator, and pipeline.
"""
from studio.verification.asset_verifier import (
    AssetIntegrityVerifier,
    AssetVerificationResult,
    AssetVerifier,
    DiscoveredAssetRef,
    validate_magic_bytes,
)
from studio.verification.engine_simulator import (
    DualModeEngineSimulator,
    EngineSimulator,
    MockEngineSimulator,
    SimulatedNode,
    SimulationResult,
)
from studio.verification.pipeline import (
    VerificationPipeline,
    route_errors_for_review,
)
from studio.verification.syntax_validators import (
    BaseSyntaxValidator,
    CSharpSyntaxValidator,
    GDScriptSyntaxValidator,
    HTMLSyntaxValidator,
    JavaScriptSyntaxValidator,
    MultiEngineSyntaxValidator,
    PythonSyntaxValidator,
)
from studio.verification.tscn_validator import (
    GODOT4_NODE_CLASSES,
    GODOT4_RESOURCE_TYPES,
    TSCNValidator,
)

__all__ = [
    # Syntax Validators
    "BaseSyntaxValidator",
    "PythonSyntaxValidator",
    "GDScriptSyntaxValidator",
    "JavaScriptSyntaxValidator",
    "HTMLSyntaxValidator",
    "CSharpSyntaxValidator",
    "MultiEngineSyntaxValidator",
    # TSCN Validator
    "TSCNValidator",
    "GODOT4_NODE_CLASSES",
    "GODOT4_RESOURCE_TYPES",
    # Asset Verifier
    "AssetVerifier",
    "AssetIntegrityVerifier",
    "DiscoveredAssetRef",
    "AssetVerificationResult",
    "validate_magic_bytes",
    # Engine Simulator
    "SimulatedNode",
    "SimulationResult",
    "MockEngineSimulator",
    "DualModeEngineSimulator",
    "EngineSimulator",
    # Pipeline
    "VerificationPipeline",
    "route_errors_for_review",
]
