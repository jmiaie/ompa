#!/usr/bin/env python3
"""
Minimal example: using OMPA with any AI agent.

This shows how to integrate OMPA into an agent workflow.
Run this to see the full lifecycle in action.
"""
import os

from ompa import Ompa


def main():
    # Initialize with a workspace vault
    vault_path = os.path.join(os.path.dirname(__file__), "simple_vault")
    os.makedirs(vault_path, exist_ok=True)
    os.makedirs(os.path.join(vault_path, "brain"), exist_ok=True)

    print("=" * 60)
    print("OMPA Demo: Full Lifecycle")
    print("=" * 60)
    print()

    ao = Ompa(vault_path=vault_path, enable_semantic=False)

    # 1. SESSION START
    print("1. SESSION START")
    print("-" * 40)
    result = ao.session_start()
    print(result.output)
    print(f"(Tokens hint: ~{result.tokens_hint})")
    print()

    # 2. CLASSIFY MESSAGES
    print("2. MESSAGE CLASSIFICATION")
    print("-" * 40)
    messages = [
        "We decided to go with Postgres instead of MySQL",
        "We won the enterprise client deal worth $50K!",
        "There's a production outage, the API is down",
        "How do we handle authentication?",
        "Sarah from engineering joined the team yesterday",
        "Wrap up for today, good session",
    ]

    for msg in messages:
        c = ao.classify(msg)
        print(f'Message: "{msg}"')
        print(f"  Type: {c.message_type.value.upper()}")
        print(f"  Action: {c.suggested_action}")
        print()

    # 3. WRAP UP
    print("3. WRAP UP (STOP)")
    print("-" * 40)
    result = ao.stop()
    print(result.output)
    print()

    print("=" * 60)
    print("Demo complete!")
    print()
    print("To use with a real AI agent, inject session_start output")
    print("into your system prompt, then call handle_message() on each")
    print("user message and post_tool() after each file write.")


if __name__ == "__main__":
    main()
