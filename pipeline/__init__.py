"""Pipeline package init.

Suppresses a LangChain `PendingDeprecationWarning` that fires at import time
from `langgraph.checkpoint.serde.jsonplus` (LC_REVIVER is instantiated at
module load with no `allowed_objects`, and we cannot pass through to it).
The filter must run BEFORE any `langgraph` import elsewhere in the package.
"""
import warnings

try:
    from langchain_core._api import LangChainPendingDeprecationWarning

    warnings.filterwarnings(
        "ignore",
        message=r"The default value of `allowed_objects` will change",
        category=LangChainPendingDeprecationWarning,
    )
except ImportError:
    warnings.filterwarnings(
        "ignore",
        message=r"The default value of `allowed_objects` will change",
    )
