import users


def report(ids):
    return [users.get_user(i) or "?" for i in ids]
