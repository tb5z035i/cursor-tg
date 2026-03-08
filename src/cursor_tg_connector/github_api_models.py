from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

GitHubMergeMethod = Literal["merge", "squash", "rebase"]


class GitHubBranchRef(BaseModel):
    ref: str


class GitHubPullRequest(BaseModel):
    node_id: str | None = Field(default=None, alias="node_id")
    number: int
    title: str
    state: str
    draft: bool = False
    merged: bool = False
    html_url: str = Field(alias="html_url")
    mergeable: bool | None = None
    mergeable_state: str | None = Field(default=None, alias="mergeable_state")
    head: GitHubBranchRef
    base: GitHubBranchRef


class GitHubMergeResult(BaseModel):
    merged: bool
    message: str
    sha: str | None = None


class GitHubErrorEnvelope(BaseModel):
    message: str | None = None
