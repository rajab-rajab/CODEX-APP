"""Goal mode: Codex keeps working on its own, over as many turns as it needs, until a goal is done.

This is official Codex's /goal. Codex itself decides when to start the next turn and when the goal is
complete, blocked, or out of budget. The SDK has the parts for it but no simple public method for the
synchronous client, so this module wires them together, the same way the SDK's own async client does.
It uses the SDK's lower-level client (codex._client), so it is pinned to the engine range in
requirements.txt, like the "ask" approval mode."""

from typing import Optional


class Goal:
    """One running goal. Iterate over it for the same events a normal turn gives."""

    def __init__(self, codex, thread_id: str, objective: str, token_budget: Optional[int] = None):
        from openai_codex._goal import _GoalNotificationStream  # private: see the module docstring

        self.client = codex._client
        self.thread_id = thread_id
        self.state, self.first_turn_id = self.client.start_goal_operation(thread_id, objective)
        if token_budget:
            self.set_budget(token_budget)
        self.stream = _GoalNotificationStream(
            state=self.state,
            next_notification=lambda: self.client.next_goal_notification(self.state),
            unregister=lambda: self.client.unregister_goal_operation(self.state),
            cancel_goal=lambda: self.client.cancel_goal_operation(self.state))

    def set_budget(self, tokens: int) -> None:
        from openai_codex.generated.v2_all import ThreadGoalSetResponse
        self.client.request("thread/goal/set", {"threadId": self.thread_id, "tokenBudget": int(tokens)},
                            response_model=ThreadGoalSetResponse)

    def __iter__(self):
        return iter(self.stream)

    def steer(self, message) -> None:
        """Add instructions to the turn Codex is working on right now."""
        turn_id = self.state.current_turn() or self.first_turn_id
        self.client.turn_steer(self.thread_id, turn_id, message if not isinstance(message, str)
                               else [{"type": "text", "text": message}])

    def interrupt(self) -> None:
        """Pause the goal and stop the current turn."""
        self.client.cancel_goal_operation(self.state)


def start(codex, thread_id: str, objective: str, token_budget: Optional[int] = None) -> Goal:
    return Goal(codex, thread_id, objective, token_budget)
