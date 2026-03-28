#!/usr/bin/env python3
"""
Admin CLI for the OpenAlex Pipeline Dashboard.

Usage:
    python manage.py create-user <username>
    python manage.py list-users
    python manage.py delete-user <username>
"""
import sys
import getpass

# Make sure Flask app context is available
from app import create_app

app = create_app()


def cmd_create_user(username: str) -> None:
    with app.app_context():
        from models.user import create_user, get_user_by_username
        if get_user_by_username(username):
            print(f"Error: user '{username}' already exists.")
            sys.exit(1)
        password = getpass.getpass(f"Password for '{username}': ")
        confirm = getpass.getpass("Confirm password: ")
        if password != confirm:
            print("Error: passwords do not match.")
            sys.exit(1)
        if len(password) < 6:
            print("Error: password must be at least 6 characters.")
            sys.exit(1)
        uid = create_user(username, password)
        print(f"Created user '{username}' (id={uid})")


def cmd_list_users() -> None:
    with app.app_context():
        from models.user import list_users
        users = list_users()
        if not users:
            print("No users found.")
            return
        print(f"{'ID':<6} {'Username':<24} {'Created at'}")
        print("-" * 52)
        for u in users:
            print(f"{u['id']:<6} {u['username']:<24} {u['created_at']}")


def cmd_delete_user(username: str) -> None:
    with app.app_context():
        from models.user import delete_user
        confirm = input(f"Delete user '{username}'? This also deletes their pipelines. [y/N] ")
        if confirm.lower() != "y":
            print("Aborted.")
            return
        ok = delete_user(username)
        if ok:
            print(f"Deleted user '{username}'.")
        else:
            print(f"Error: user '{username}' not found.")
            sys.exit(1)


COMMANDS = {
    "create-user": (cmd_create_user, 1, "create-user <username>"),
    "list-users":  (cmd_list_users,  0, "list-users"),
    "delete-user": (cmd_delete_user, 1, "delete-user <username>"),
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print("Usage:")
        for _, (_, _, usage) in COMMANDS.items():
            print(f"  python manage.py {usage}")
        sys.exit(1)

    cmd_name = sys.argv[1]
    fn, n_args, usage = COMMANDS[cmd_name]

    if len(sys.argv) - 2 != n_args:
        print(f"Usage: python manage.py {usage}")
        sys.exit(1)

    extra_args = sys.argv[2:]
    fn(*extra_args)


if __name__ == "__main__":
    main()
