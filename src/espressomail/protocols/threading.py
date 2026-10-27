"""RFC 5256 / JWZ-inspired email conversation threading engine."""

from __future__ import annotations

import re
from typing import Sequence

from espressomail.models.entities import EmailMessage, EmailThread, ThreadNode

_SUBJECT_PREFIX_RE = re.compile(r"^(\s*(re|fwd|fw|aw|sv)(\[\d+\])?:\s*)+", re.IGNORECASE)


def normalize_subject(subject: str) -> str:
    """Strip Re:/Fwd: prefixes and normalize whitespace for subject-based thread grouping."""
    if not subject:
        return "(no subject)"
    cleaned = _SUBJECT_PREFIX_RE.sub("", subject.strip())
    return " ".join(cleaned.split()) or "(no subject)"


def build_threads(messages: Sequence[EmailMessage]) -> list[EmailThread]:
    """Group a flat sequence of messages into hierarchical EmailThread conversations.

    Links messages by explicit thread_id, Message-ID / In-Reply-To / References headers,
    and falls back to normalized subject matching within the same account.
    """
    if not messages:
        return []

    by_msgid: dict[str, EmailMessage] = {}
    for m in messages:
        if m.message_id:
            by_msgid[m.message_id] = m

    # Map each message ID to a canonical group key
    parent_map: dict[str, str] = {}

    def find_root(k: str) -> str:
        while parent_map.get(k, k) != k:
            parent_map[k] = parent_map.get(parent_map[k], parent_map[k])
            k = parent_map[k]
        return k

    def union(a: str, b: str) -> None:
        ra, rb = find_root(a), find_root(b)
        if ra != rb:
            parent_map[rb] = ra

    subject_to_key: dict[tuple[str, str], str] = {}

    for m in messages:
        m_key = m.message_id or m.id
        parent_map.setdefault(m_key, m_key)

        if m.thread_id:
            parent_map.setdefault(m.thread_id, m.thread_id)
            union(m.thread_id, m_key)

        if m.in_reply_to:
            parent_map.setdefault(m.in_reply_to, m.in_reply_to)
            union(m.in_reply_to, m_key)

        for ref in m.references:
            parent_map.setdefault(ref, ref)
            union(ref, m_key)

        norm_subj = normalize_subject(m.subject).lower()
        subj_key = (m.account_id, norm_subj)
        if norm_subj != "(no subject)":
            if subj_key in subject_to_key:
                union(subject_to_key[subj_key], m_key)
            else:
                subject_to_key[subj_key] = m_key

    grouped: dict[str, list[EmailMessage]] = {}
    for m in messages:
        m_key = m.message_id or m.id
        root_key = find_root(m_key)
        grouped.setdefault(root_key, []).append(m)

    threads: list[EmailThread] = []
    for t_id, group_msgs in grouped.items():
        # Sort messages chronologically inside each thread
        sorted_msgs = sorted(group_msgs, key=lambda msg: msg.timestamp)
        nodes = _build_tree_nodes(sorted_msgs)
        root_subj = normalize_subject(sorted_msgs[0].subject)
        threads.append(
            EmailThread(
                thread_id=t_id,
                subject=root_subj,
                messages=sorted_msgs,
                nodes=nodes,
                expanded=True,
            )
        )

    # Order threads by latest message timestamp descending
    threads.sort(key=lambda t: t.latest_message.timestamp, reverse=True)
    return threads


def _build_tree_nodes(sorted_msgs: list[EmailMessage]) -> list[ThreadNode]:
    """Construct hierarchical ThreadNode tree with depth levels for UI rendering."""
    node_by_msgid: dict[str, ThreadNode] = {}
    roots: list[ThreadNode] = []

    for m in sorted_msgs:
        node = ThreadNode(message=m, depth=0)
        if m.message_id:
            node_by_msgid[m.message_id] = node

        parent_node: ThreadNode | None = None
        if m.in_reply_to and m.in_reply_to in node_by_msgid:
            parent_node = node_by_msgid[m.in_reply_to]
        else:
            for ref in reversed(m.references):
                if ref in node_by_msgid:
                    parent_node = node_by_msgid[ref]
                    break

        if parent_node is not None and parent_node is not node:
            node.depth = min(6, parent_node.depth + 1)
            parent_node.children.append(node)
        else:
            roots.append(node)

    # Flatten into pre-order traversal list while keeping children references intact
    flat_nodes: list[ThreadNode] = []

    def _walk(n: ThreadNode, depth: int) -> None:
        n.depth = depth
        flat_nodes.append(n)
        for child in n.children:
            _walk(child, min(6, depth + 1))

    for r in roots:
        _walk(r, 0)

    return flat_nodes
