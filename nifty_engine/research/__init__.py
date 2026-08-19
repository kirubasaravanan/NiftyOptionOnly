"""Research package — analysis that observes the live engine but never steers it.

Nothing in here may be imported by, or influence, any trading decision path.
Modules here exist to accumulate the evidence needed to decide whether a
proposed change is worth making, before it is made.
"""
from .shadow_ev import ShadowEVLogger, compute_shadow_ev

__all__ = ["ShadowEVLogger", "compute_shadow_ev"]
