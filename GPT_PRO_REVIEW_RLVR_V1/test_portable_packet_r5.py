"""Root-level entry point for clean-extraction R5 test discovery."""

from mvp_same_source_v1.test_mvp_static_contract import StaticContractTests
from mvp_same_source_v1.test_validate_eight_stack_completion import (
    EightStackCompletionTests,
)
from mvp_same_source_v1.test_validate_mvp_replicates_r3 import (
    R4ValidatorMutationTests,
)
from real_assets.tests.test_native_controlled_assets import (
    NativeControlledAssetValidationTests,
)

__all__ = [
    "StaticContractTests",
    "EightStackCompletionTests",
    "R4ValidatorMutationTests",
    "NativeControlledAssetValidationTests",
]
