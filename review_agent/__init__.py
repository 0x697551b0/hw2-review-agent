"""
ReviewAgent —— 审查 Agent。

定位：Homework 2「代码审查流程」工作流的**第二个节点**，复用 Homework 1 的 codeguard。

    from review_agent import ReviewAgent, ReviewConfig

    agent = ReviewAgent(ReviewConfig(mode="static"))
    outcome = agent.review("./worktree")
    print(outcome.summary(), outcome.passed)
"""

from review_agent.agent import AGENT_NAME, ReviewAgent, ReviewOutcome
from review_agent.config import ReviewConfig
from review_agent.errors import (
    CodeGuardNotFound,
    NoTargetsError,
    ReviewAgentError,
    ReviewError,
)
from review_agent.fixability import FIXABLE_STRATEGIES, NOT_AUTOFIXABLE, classify
from review_agent.protocol import AgentRequest, AgentResponse, Artifact, FindingContract

__version__ = "1.0.0"
__agent_name__ = AGENT_NAME

__all__ = [
    "AGENT_NAME",
    "AgentRequest",
    "AgentResponse",
    "Artifact",
    "CodeGuardNotFound",
    "FIXABLE_STRATEGIES",
    "FindingContract",
    "NOT_AUTOFIXABLE",
    "NoTargetsError",
    "ReviewAgent",
    "ReviewAgentError",
    "ReviewConfig",
    "ReviewError",
    "ReviewOutcome",
    "__agent_name__",
    "__version__",
    "classify",
]
