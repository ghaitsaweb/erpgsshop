"""Bounded local knowledge retrieval for the Hashy Assistant.

The first parity slice is lexical retrieval: documents stay in the company
database and are not silently sent to an embedding service.  The selected
source identifiers are stored with the answer so users can see what evidence
was supplied to the provider.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter

from django.db import transaction
from django.db.models import Q

from .models import AIKnowledgeChunk, AIKnowledgeDocument


CHUNK_CHARACTERS = 1_200
CHUNK_OVERLAP = 160
MAX_QUERY_TERMS = 10
MAX_DATABASE_CANDIDATES = 240
MAX_FALLBACK_DOCUMENTS = 20
MAX_RETRIEVED_CHUNKS = 5
MAX_CHUNKS_PER_DOCUMENT = 2

_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)
_STOP_WORDS = frozenset(
    {
        "a", "an", "and", "are", "atau", "bagaimana", "dari", "dan", "dengan", "di", "for", "from",
        "ini", "is", "itu", "ke", "keputusan", "of", "on", "pada", "please", "saya", "sebuah", "the",
        "to", "tolong", "untuk", "what", "yang",
    }
)


def query_terms(value: str) -> tuple[str, ...]:
    """Return stable query terms without synonym or provider expansion."""

    seen = set()
    terms = []
    for word in _WORD_RE.findall((value or "").lower()):
        if len(word) < 3 or word in _STOP_WORDS or word in seen:
            continue
        seen.add(word)
        terms.append(word)
        if len(terms) == MAX_QUERY_TERMS:
            break
    return tuple(terms)


def split_knowledge_text(value: str) -> list[str]:
    """Split text into overlap-safe chunks while retaining readable boundaries."""

    text = (value or "").strip()
    if not text:
        return []
    chunks = []
    start = 0
    while start < len(text):
        end = min(len(text), start + CHUNK_CHARACTERS)
        if end < len(text):
            boundary_start = start + (CHUNK_CHARACTERS * 3 // 5)
            boundary = max(text.rfind("\n", boundary_start, end), text.rfind(" ", boundary_start, end))
            if boundary > start:
                end = boundary
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        next_start = max(end - CHUNK_OVERLAP, start + 1)
        while next_start < len(text) and not text[next_start].isspace():
            next_start += 1
        start = next_start if next_start < len(text) else end
    return chunks


def rebuild_document_chunks(document: AIKnowledgeDocument) -> int:
    """Atomically replace one document's bounded local retrieval index."""

    chunks = split_knowledge_text(document.extracted_text)
    with transaction.atomic():
        AIKnowledgeChunk.objects.filter(document_id=document.pk).delete()
        AIKnowledgeChunk.objects.bulk_create(
            [
                AIKnowledgeChunk(
                    company_id=document.company_id,
                    branch_id=document.branch_id,
                    document_id=document.pk,
                    ordinal=ordinal,
                    content=content,
                    content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
                )
                for ordinal, content in enumerate(chunks)
            ],
            batch_size=250,
        )
    return len(chunks)


def _score_chunk(content: str, terms: tuple[str, ...]) -> tuple[int, int]:
    words = Counter(_WORD_RE.findall(content.lower()))
    matched = sum(1 for term in terms if words.get(term))
    occurrences = sum(min(words.get(term, 0), 2) for term in terms)
    phrase_bonus = 2 if len(terms) > 1 and " ".join(terms) in content.lower() else 0
    return occurrences + phrase_bonus, matched


def _branch_scope(branch_id):
    return Q(branch_id__isnull=True) | Q(branch_id=branch_id) if branch_id else Q(branch_id__isnull=True)


def _fallback_chunks(company_id: int, terms: tuple[str, ...], branch_id=None):
    """Serve legacy documents that have not yet been indexed by the migration."""

    predicate = Q()
    for term in terms:
        predicate |= Q(extracted_text__icontains=term)
    documents = (
        AIKnowledgeDocument.objects.filter(
            company_id=company_id,
            active=True,
            status=AIKnowledgeDocument.Status.READY,
            chunks__isnull=True,
        )
        .filter(_branch_scope(branch_id))
        .filter(predicate)
        .only("id", "title", "category", "extracted_text")
        .order_by("id")[:MAX_FALLBACK_DOCUMENTS]
    )
    candidates = []
    for document in documents:
        candidates.extend((document, ordinal, content) for ordinal, content in enumerate(split_knowledge_text(document.extracted_text)))
    return candidates[:MAX_DATABASE_CANDIDATES]


def retrieve_knowledge(company_id: int, query: str, *, branch_id=None) -> dict:
    """Return relevant company-scoped excerpts plus provenance for one turn."""

    terms = query_terms(query)
    if not terms:
        return {"context": "", "citations": [], "confidence_percent": 0}

    predicate = Q()
    for term in terms:
        predicate |= Q(content__icontains=term)
    indexed_chunks = list(
        AIKnowledgeChunk.objects.filter(
            company_id=company_id,
            document__active=True,
            document__status=AIKnowledgeDocument.Status.READY,
        )
        .filter(_branch_scope(branch_id))
        .filter(predicate)
        .select_related("document")
        .only("id", "document_id", "ordinal", "content", "document__title", "document__category")
        .order_by("document_id", "ordinal")[:MAX_DATABASE_CANDIDATES]
    )
    candidates = [(chunk.document, chunk.ordinal, chunk.content) for chunk in indexed_chunks]
    candidates.extend(_fallback_chunks(company_id, terms, branch_id))

    ranked = []
    for document, ordinal, content in candidates:
        score, matched = _score_chunk(content, terms)
        if score:
            ranked.append((score, matched, document.pk, ordinal, document, content))
    ranked.sort(key=lambda item: (-item[0], -item[1], item[2], item[3]))

    per_document = Counter()
    selected = []
    for score, matched, _, ordinal, document, content in ranked:
        if per_document[document.pk] >= MAX_CHUNKS_PER_DOCUMENT:
            continue
        per_document[document.pk] += 1
        selected.append((score, matched, ordinal, document, content))
        if len(selected) == MAX_RETRIEVED_CHUNKS:
            break
    if not selected:
        return {"context": "", "citations": [], "confidence_percent": 0}

    citations = []
    context_parts = []
    coverage = []
    for number, (_, matched, ordinal, document, content) in enumerate(selected, start=1):
        label = f"K{number}"
        category = document.category or "General"
        context_parts.append(f"[{label}] {document.title} ({category})\n{content}")
        citations.append({"label": label, "title": document.title, "category": category, "chunk_ordinal": ordinal + 1})
        coverage.append(matched / len(terms))
    return {
        "context": "\n\n".join(context_parts),
        "citations": citations,
        "confidence_percent": round(100 * sum(coverage) / len(coverage)),
    }
