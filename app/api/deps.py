from functools import lru_cache
from langgraph.graph.state import CompiledStateGraph

from app.graph.graph import build_graph


@lru_cache(maxsize=1)
def get_graph() -> CompiledStateGraph:
    """Return the cached singleton compiled graph instance."""
    return build_graph()
