from __future__ import annotations

from atlas_core.capabilities.execution import CapabilityExecutionProfile

from .inventory import DeploymentInventory

ExecutionProfileIndex = DeploymentInventory

__all__ = ["CapabilityExecutionProfile", "DeploymentInventory", "ExecutionProfileIndex"]
