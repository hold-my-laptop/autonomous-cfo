"""Loading the demo state, also after a failed start (e.g. the database was not up yet)."""

import logging
import threading
import time

from api.agent import AgentOrchestrator
from api.domain.importer import StatementImportError
from api.domain.proof.dataset import ProofDatasetError
from api.state import StateNotLoadedError, StateRepository

logger = logging.getLogger(__name__)


class StateLoader:
    """Starts the repository and recovers interrupted agent runs once it is loaded.

    A failed start is retried by the next request, at most every `retry_s` seconds, so the
    API becomes ready on its own when the database comes up after it."""

    def __init__(
        self, repository: StateRepository, agent: AgentOrchestrator, retry_s: float
    ) -> None:
        self._repository = repository
        self._agent = agent
        self._retry_s = retry_s
        self._lock = threading.Lock()
        self._loaded = False
        self._next_attempt = 0.0

    @property
    def loaded(self) -> bool:
        return self._loaded

    def load(self) -> bool:
        """Try to load now; returns whether the state is loaded."""
        with self._lock:
            if self._loaded:
                return True
            self._next_attempt = time.monotonic() + self._retry_s
            try:
                self._repository.start()
            except (StatementImportError, ProofDatasetError, StateNotLoadedError) as exc:
                logger.error("demo state not loaded, retrying in %.0f s: %s", self._retry_s, exc)
                return False
            self._loaded = True
            logger.info("demo state loaded")
            self._agent.recover()
            return True

    def ensure(self) -> None:
        """Called before serving a request: retry a failed start when the wait is over."""
        if not self._loaded and time.monotonic() >= self._next_attempt:
            self.load()
