## no_tool_call
You replied without calling a tool. Continue investigating with a tool call, or finish by calling {terminal}.

## unusable_reply
Your last reply couldn't be used ({detail}). Call a tool with valid arguments, or finish by calling {terminal}.

## last_turn
This is your last turn. Call {terminal} now, citing only what you have already been shown. If that isn't enough to decide, submit an inconclusive result instead of guessing.

## two_turns_left
You have two turns left. If you can decide now, call {terminal} in this turn, so that a rejected submission can still be fixed in the last one.

## submission_accepted
Accepted.

## submission_rejected
{terminal} was not accepted. Fix these problems and call {terminal} again:
{errors}

## duplicate_submission
Not run: call {terminal} at most once per turn.

## not_run_limit
Not run: at most {limit} tools run per turn. Call it again in your next turn if you still need it.

## not_run_ended
Not run: the investigation ended when {terminal} was accepted.
