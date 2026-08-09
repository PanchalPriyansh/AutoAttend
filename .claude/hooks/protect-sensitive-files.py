import json
import os
import re
import sys


def is_env_file(path):
    """Return True if the path refers to an environment file."""
    if not path:
        return False

    normalized = os.path.normpath(path)
    filename = os.path.basename(normalized)

    return (
        filename == ".env"
        or filename.startswith(".env.")
    )


def references_env_file(command):
    """Return True if a shell command references an .env file."""
    if not command:
        return False

    return bool(
        re.search(
            r"(^|[\s\"'=/])\.env(?:[.\w-]*)?(?=$|[\s\"'=/])",
            command,
            re.IGNORECASE,
        )
    )


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input", {})

    # Direct file operations: Read, Write, Edit
    if tool_name in {"Read", "Write", "Edit"}:
        file_path = tool_input.get("file_path", "")

        if is_env_file(file_path):
            print(
                "BLOCKED: Access to .env files is prohibited. "
                "Sensitive environment data must not be read, written, "
                "edited, or modified by Claude.",
                file=sys.stderr,
            )
            sys.exit(2)

    # Bash commands
    if tool_name == "Bash":
        command = tool_input.get("command", "")

        if references_env_file(command):
            print(
                "BLOCKED: Bash command references an .env file. "
                "Claude must not access, read, write, edit, or modify "
                "sensitive environment files.",
                file=sys.stderr,
            )
            sys.exit(2)

    sys.exit(0)


if __name__ == "__main__":
    main()