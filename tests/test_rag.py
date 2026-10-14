import pytest

from app.graph.graph import build_graph
from app.graph.rag_nodes import (
    generate_answer,
    grade_documents,
    rerank_documents,
    retrieve_parent_chunks,
)
from app.graph.state import Intent
from app.rag.store import ParentChildVectorStore, get_default_vector_store


class TestParentChildVectorStore:
    def test_chunking_and_retrieval(self):
        store = ParentChildVectorStore()
        store.add_document(
            doc_id="test_doc",
            title="Test Policy",
            text=(
                "Paragraph 1 contains rule Alpha regarding credit cards. "
                "Credit card fee is ten dollars.\n\n"
                "Paragraph 2 contains rule Beta regarding wire transfers. "
                "Wire transfer fee is twenty dollars."
            ),
        )

        assert len(store.parents) >= 2
        assert len(store.children) >= 2

        results = store.retrieve("credit card fee", top_k=1)
        assert len(results) == 1
        assert "rule Alpha" in results[0]["text"]
        assert results[0]["title"] == "Test Policy"

    def test_deduplication_of_parents(self):
        store = ParentChildVectorStore()
        store.add_document(
            doc_id="long_doc",
            title="Long Policy",
            text=(
                "Sentence one mentions wire transfer fee. "
                "Sentence two also discusses wire transfer cutoff times. "
                "Sentence three covers wire transfer maximum limits."
            ),
        )

        results = store.retrieve("wire transfer", top_k=5)
        # Even though multiple children match "wire transfer", only one parent should be returned
        assert len(results) == 1
        assert results[0]["doc_id"] == "long_doc"


class TestRAGNodes:
    def test_retrieve_parent_chunks_node(self):
        state = {"user_input": "What is the wire transfer fee?"}
        output = retrieve_parent_chunks(state)
        assert "retrieved_docs" in output
        assert len(output["retrieved_docs"]) > 0
        assert any("wire" in doc.lower() for doc in output["retrieved_docs"])

    def test_grade_documents_relevant(self):
        state = {
            "user_input": "international wire transfer fees",
            "retrieved_docs": [
                "International wire transfers incur a $45 fee and require up to 48 hours to settle."
            ],
        }
        output = grade_documents(state)
        assert output["is_relevant"] is True
        assert output["relevance_score"] > 0.15

    def test_grade_documents_irrelevant(self):
        state = {
            "user_input": "what is the recipe for chocolate cake",
            "retrieved_docs": [
                "International wire transfers incur a $45 fee."
            ],
        }
        output = grade_documents(state)
        assert output["is_relevant"] is False

    def test_rerank_documents_ordering(self):
        state = {
            "user_input": "inactivity fee",
            "retrieved_docs": [
                "Domestic wire transfers incur a $25 fee.",
                "Inactive accounts are subject to an inactivity fee of $5 per month.",
            ],
        }
        output = rerank_documents(state)
        assert "reranked_docs" in output
        assert len(output["reranked_docs"]) == 2
        assert "inactivity fee" in output["reranked_docs"][0].lower()

    def test_generate_answer_when_relevant(self):
        state = {
            "is_relevant": True,
            "reranked_docs": ["Domestic wire transfers incur a $25 fee."],
        }
        output = generate_answer(state)
        assert output["route"] == "policy_question"
        assert "$25 fee" in output["answer"]

    def test_generate_answer_when_not_relevant(self):
        state = {
            "is_relevant": False,
            "reranked_docs": [],
        }
        output = generate_answer(state)
        assert output["route"] == "policy_question"
        assert "No relevant policy documents found" in output["answer"]


class TestEndToEndPolicyRAGGraph:
    def test_policy_graph_execution(self):
        graph = build_graph()
        result = graph.invoke({"user_input": "What is the fee for an international wire transfer policy?"})

        assert result["intent"] == Intent.POLICY_QUESTION
        assert result["route"] == "policy_question"
        assert result["is_relevant"] is True
        assert len(result["retrieved_docs"]) > 0
        assert len(result["reranked_docs"]) > 0
        assert "$45" in result["answer"]

    def test_natural_policy_query_without_policy_keyword(self):
        graph = build_graph()
        result = graph.invoke({"user_input": "What is the fee for an international wire transfer?"})

        assert result["intent"] == Intent.POLICY_QUESTION
        assert result["route"] == "policy_question"
        assert result["is_relevant"] is True
        assert "$45" in result["answer"]

    def test_pdf_ingested_policy_query(self):
        graph = build_graph()
        result = graph.invoke({"user_input": "What are the rules for opening minor accounts?"})

        assert result["intent"] == Intent.POLICY_QUESTION
        assert result["route"] == "policy_question"
        assert result["is_relevant"] is True
        assert any("minor" in doc.lower() for doc in result["retrieved_docs"])

