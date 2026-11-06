"""
Hestia — terminal front end (Stage 10 / v1.0)

The command-line way to talk to Hestia. Everything Hestia does lives in
engine.py; this file only reads what you type and shows what the engine
sends back. The window, tray icon and voice are in gui.py.

    venv\\Scripts\\python chat.py            a new session
    venv\\Scripts\\python chat.py --resume   continue the most recent one
"""

import argparse
import json
import sys

import engine

# How much of each argument the tool-approval prompt shows.
CONFIRM_PREVIEW_CHARS = 300


class TerminalUI:
    """Shows the engine's output in the terminal and asks questions with input()."""

    def __init__(self, name="Hestia"):
        self.name = name
        self.replying = False  # whether "Hestia: " has been printed for this turn

    def _reply_line(self):
        if not self.replying:
            print(f"{self.name}: ", end="", flush=True)
            self.replying = True

    def text(self, token):
        self._reply_line()
        print(token, end="", flush=True)

    def tool(self, name):
        self._reply_line()
        print(f"[tool: {name}] ", end="", flush=True)

    def notice(self, message):
        print(("\n" if self.replying else "") + message, end="" if self.replying else "\n", flush=True)

    def approve(self, tool_name, arguments, after_web=False):
        """
        Ask before a TIER_WRITE tool runs. Anything other than an explicit
        'y'/'yes' — including Ctrl+C — counts as no. Each argument is shown
        on its own line; long values are cut short, with a count of what's
        hidden, so a big file write can't flood the terminal. after_web adds
        a warning: web content was read earlier this turn, and a page can
        carry instructions written to look like the user's.
        """
        print(f"\n[{self.name} wants to run '{tool_name}':")
        if after_web:
            print("    (Asked for after reading web content this turn — make sure it's what YOU asked for.)")
        for key, value in arguments.items():
            shown = json.dumps(value, ensure_ascii=False)
            if len(shown) > CONFIRM_PREVIEW_CHARS:
                hidden = len(shown) - CONFIRM_PREVIEW_CHARS
                shown = f"{shown[:CONFIRM_PREVIEW_CHARS]}... ({hidden} more characters)"
            print(f"    {key}: {shown}")
        print(" Allow? (y/N)]")
        try:
            answer = input("You: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = ""
        print(f"{self.name}: ", end="", flush=True)
        return answer in ("y", "yes")

    def confirm(self, question):
        """A destructive yes/no (forget everything): only a typed 'yes' agrees."""
        print(question)
        try:
            return input("You: ").strip().lower() == "yes"
        except (EOFError, KeyboardInterrupt):
            return False


def main():
    parser = argparse.ArgumentParser(description="Hestia chat interface")
    parser.add_argument(
        "--resume", action="store_true",
        help="Continue the most recent session instead of starting a new one",
    )
    args = parser.parse_args()

    ui = TerminalUI()
    try:
        session = engine.Session(ui, resume=args.resume)
    except engine.StartupError as e:
        print(f"[Error: {e}]")
        sys.exit(1)
    ui.name = session.name

    for problem in session.wake():  # a terminal is opened to talk: no standby
        print(f"[Tool server problem — {problem}. Its tools are unavailable this run.]")
    print(f"{session.name} v1.1 — talking to {engine.MODEL_NAME}.")
    print("Commands: 'exit'/'quit' to leave, 'new' for a fresh session,")
    print("          'remember ...' to save a fact, 'forget ...' to remove one,")
    print("          'memories' to list what's stored, 'tools' for the tool-call log.\n")

    while True:
        try:
            user_input = input("You: ")
        except (EOFError, KeyboardInterrupt):
            user_input = "exit"
            print()
        if user_input.strip().lower() in ("exit", "quit"):
            print("Exiting.")
            session.close()
            break

        ui.replying = False
        output, was_command = session.handle(user_input)
        if was_command:
            if output:
                print(output + "\n")
        else:
            print("\n")


if __name__ == "__main__":
    main()
