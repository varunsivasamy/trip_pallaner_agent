class RetryableError(Exception):
    """A transient failure (network, malformed LLM output) that is worth retrying."""


class NeedsInput(Exception):
    """The plan cannot continue without an answer from the traveler."""

    def __init__(self, question: str) -> None:
        super().__init__(question)
        self.question = question
