"""Users."""

USERS = {1: "ada", 2: "grace"}


def get_user(user_id):
    return USERS.get(user_id)


get_usr = get_user  # old name, kept for callers outside this project
