import pytest
from app.docs import DocEntry, DocsProvider


def test_docs_provider_default():
    provider = DocsProvider()
    safety_docs = provider.search_docs("safety")
    assert len(safety_docs) > 0
    assert "Safety and Risk Classification Standards" in safety_docs[0].title


def test_docs_provider_add():
    provider = DocsProvider()
    provider.add_doc(DocEntry(title="Custom Guide", category="custom", content="Custom content"))
    results = provider.search_docs("custom")
    assert len(results) == 1
    assert results[0].title == "Custom Guide"
