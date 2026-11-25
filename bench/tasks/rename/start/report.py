import users


def report(ids):
    return [users.get_usr(i) or "?" for i in ids]
