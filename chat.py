"""
Hestia — Stage 1 (v0.1): Local model runtime + CLI chat

A minimal command-line chat loop that talks to a local model running in
Ollama. No memory persistence, no tools, no personality config yet —
those come in later stages. This script's only job is proving the core
loop works: type a message, get a response, entirely on this machine.
"""

from ollama import chat

MODEL_NAME = "qwen2.5:7b"  


def main():
    history = []  # lives only in memory — gone the moment the script exits

    print(f"Hestia v0.1 — talking to {MODEL_NAME}. Type 'exit' or 'quit' to leave.\n")

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nExiting.")
            break

        if user_input.lower() in ("exit", "quit"):
            print("Exiting.")
            break

        if not user_input:
            continue  # skip empty submissions rather than sending them to the model

        history.append({"role": "user", "content": user_input})

        print("Hestia: ", end="", flush=True)
        assistant_reply = ""

        try:
            stream = chat(model=MODEL_NAME, messages=history, stream=True)
            for chunk in stream:
                token = chunk["message"]["content"]
                print(token, end="", flush=True)
                assistant_reply += token
        except Exception as e:
            print(f"\n[Error talking to Ollama: {e}]")
            print(f"Is Ollama running? Try 'ollama run {MODEL_NAME}' in another terminal to check.")
            history.pop()  # drop the user message — no reply came back for it
            continue

        print("\n")
        history.append({"role": "assistant", "content": assistant_reply})


if __name__ == "__main__":
    main()